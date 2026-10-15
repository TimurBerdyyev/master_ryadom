"""End-to-end API tests: anonymous client requests, masters, the 20-minute response promise,
access control, migrations.

Run from backend/:  python -m pytest tests -q
Uses a throwaway SQLite database (or TEST_DATABASE_URL, e.g. a scratch Postgres).
"""
import base64
import json
import os
import tempfile

_tmp = tempfile.mkdtemp()
# A throwaway SQLite file by default; TEST_DATABASE_URL runs the suite against e.g. a scratch Postgres.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-0123456789"
os.environ["REDIS_URL"] = ""  # in-memory rate limits, reset between tests
os.environ["SMS_PROVIDER"] = "console"
os.environ["EMAIL_PROVIDER"] = "console"
# Most tests don't care about the "verified by the service" rule; test_verification_gate turns it on.
os.environ["MASTERS_REQUIRE_VERIFICATION"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import jwt  # noqa: E402

from app import rate_limit  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User, UserRole  # noqa: E402

def _image(fmt="PNG", size=(8, 8), **save):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, fmt, **save)
    return buf.getvalue()


PNG = _image()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_rate_limit():
    rate_limit._attempts.clear()


_counter = iter(range(100, 10000))


def category_id(client, name="Сантехника"):
    return next(c["id"] for c in client.get("/categories").json() if c["name"] == name)


def email_code(client, email, purpose="register", headers=None):
    """The dev email provider (EMAIL_PROVIDER=console) echoes the code back in the response."""
    r = client.post("/auth/send-code", json={"email": email, "purpose": purpose}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["debug_code"]


def email_for(phone):
    return f"user{phone.lstrip('+')}@example.com"


def new_phone():
    return f"+99670{next(_counter):07d}"


def register_master(client, name="Мастер", category="Сантехника", city="Бишкек", notify_enabled=False,
                    channel="email", lang="ru", service=True):
    phone = new_phone()
    email = email_for(phone)
    r = client.post("/auth/register", json={
        "name": name, "phone": phone, "password": "secret123", "city": city, "email": email,
        "code": email_code(client, email), "accept_agreement": True,
        "notify_enabled": notify_enabled, "notify_channel": channel, "lang": lang,
    })
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if service:
        r = client.post("/masters/me/services", headers=headers,
                        json={"category_id": category_id(client, category), "title": "Замена смесителя", "price_from": 500})
        assert r.status_code == 200, r.text
    client.patch("/masters/me", headers=headers, json={"description": "Опытный мастер"})
    return headers, phone


def master_id_of(client, headers):
    return client.get("/masters/me", headers=headers).json()["id"]


def create_request(client, city="Бишкек", category="Сантехника", description="Течёт кран", price=None, phone=None):
    rate_limit._attempts.clear()
    r = client.post("/requests", json={
        "name": "Клиент", "phone": phone or new_phone(), "category_id": category_id(client, category),
        "city": city, "description": description, "address": "ул. Ленина 1", "price": price,
    })
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["request"]


def make_admin(client):
    db = SessionLocal()
    phone = new_phone()
    db.add(User(name="Admin", phone=phone, password_hash=hash_password("adminpass"), role=UserRole.admin))
    db.commit()
    db.close()
    token = client.post("/auth/login", json={"phone": phone, "password": "adminpass"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def outbox(monkeypatch):
    """Deliver notifications synchronously and capture what would be sent."""
    from app import notify, sms, telegram
    sent = []

    class FakeSms:
        is_dev = True

        def send(self, phone, text):
            sent.append(("sms", phone, text))

    class FakeEmail:
        def send(self, to, subject, text):
            sent.append(("email", to, f"{subject}\n{text}"))

    # Pretend an SMS gateway is connected so the SMS channel can be chosen.
    monkeypatch.setattr(sms.PROVIDERS["console"], "is_dev", False)
    monkeypatch.setattr(notify, "_submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(notify, "get_sms_provider", lambda: FakeSms())
    monkeypatch.setattr(notify, "get_email_provider", lambda: FakeEmail())
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text: sent.append(("telegram", chat_id, text)))
    return sent


def expire_deliveries(order_id):
    """Pretend the response window for this request is over."""
    from datetime import timedelta

    from app.models import RequestDelivery, utcnow
    db = SessionLocal()
    db.query(RequestDelivery).filter(RequestDelivery.order_id == order_id).update(
        {RequestDelivery.deadline_at: utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()


def run_missed_check():
    from app.dispatch import check_missed
    db = SessionLocal()
    try:
        return check_missed(db)
    finally:
        db.close()


# ---------- the main flow: anonymous request -> offers -> choice -> work -> review ----------

def test_full_request_flow(client, outbox):
    master_h, master_phone = register_master(client, "Иван Сантехник", notify_enabled=True)
    other_h, _ = register_master(client, "Пётр")

    token, req = create_request(client, price=900)
    assert req["status"] == "searching" and req["offers"] == []

    feed = client.get("/orders/feed", headers=master_h).json()
    item = next(o for o in feed if o["id"] == req["id"])
    assert item["deadline_at"] and item["my_status"] == "pending"
    assert "address" not in item and "client_phone" not in item  # no client data before selection

    offer = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 800, "comment": "Через час"}).json()
    client.post(f"/orders/{req['id']}/offer", headers=other_h, json={"price": 900})
    client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 700})  # updates, no duplicate

    view = client.get(f"/requests/{token}").json()
    assert [o["price"] for o in view["offers"]] == [700, 900]
    assert view["offers"][0]["master"]["name"] == "Иван Сантехник"
    assert view["offers"][0]["master"]["phone"] == master_phone  # clients can call masters directly
    assert view["master_contact"] is None

    # a master sees only their own offer; the client's contacts stay hidden until chosen
    assert len(client.get(f"/orders/{req['id']}/offers", headers=other_h).json()) == 1
    assert client.get(f"/orders/{req['id']}", headers=master_h).status_code == 403

    outbox.clear()
    view = client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]}).json()
    assert view["status"] == "master_selected" and view["master_contact"]["phone"] == master_phone
    assert any("Вас выбрали" in m[2] for m in outbox)  # the master is notified by email
    order = client.get(f"/orders/{req['id']}", headers=master_h).json()
    assert order["client_contact"]["name"] == "Клиент" and order["address"] == "ул. Ленина 1"
    assert client.get(f"/orders/{req['id']}", headers=other_h).status_code == 403

    # the master moves the job forward; only forward
    assert client.post(f"/orders/{req['id']}/status", headers=master_h, json={"status": "in_progress"}).status_code == 200
    assert client.post(f"/orders/{req['id']}/status", headers=master_h, json={"status": "master_confirmed"}).status_code == 400
    assert client.post(f"/orders/{req['id']}/status", headers=other_h, json={"status": "completed"}).status_code == 403

    # the client confirms completion and leaves a review through the private link
    assert client.post(f"/requests/{token}/review", json={"rating": 5}).status_code == 400  # not completed yet
    assert client.post(f"/requests/{token}/complete").json()["status"] == "completed"
    view = client.post(f"/requests/{token}/review", json={"rating": 5, "text": "Отлично"}).json()
    assert view["status"] == "reviewed" and view["review_rating"] == 5
    assert client.post(f"/requests/{token}/review", json={"rating": 1}).status_code == 400

    master = client.get(f"/masters/{offer['master_id']}").json()
    assert master["rating"] == 5 and master["completed_orders"] == 1
    assert client.get(f"/masters/{offer['master_id']}/reviews").json()[0]["text"] == "Отлично"
    assert [o["id"] for o in client.get("/orders", headers=master_h).json()] == [req["id"]]


def test_request_cancel(client, outbox):
    master_h, _ = register_master(client, notify_enabled=True)
    token, req = create_request(client)
    offer = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 500}).json()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})
    outbox.clear()
    assert client.post(f"/requests/{token}/cancel").json()["status"] == "cancelled"
    assert any("отменил" in m[2] for m in outbox)
    assert client.post(f"/requests/{token}/cancel").status_code == 400
    # a cancelled job reveals nothing to the master any more
    assert client.get(f"/orders/{req['id']}", headers=master_h).json()["client_contact"] is None


def test_client_complaint_through_link(client):
    master_h, _ = register_master(client, city="Узген")
    token, req = create_request(client, city="Узген")
    offer = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 500}).json()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})
    r = client.post(f"/requests/{token}/complaint", json={"text": "Не приехал"})
    assert r.status_code == 200 and r.json()["author_id"] is None
    assert r.json()["target_user_id"] == client.get("/auth/me", headers=master_h).json()["id"]
    assert client.post("/requests/wrong-token/complaint", json={"text": "x"}).status_code == 404
    admin_h = make_admin(client)
    assert any(c["text"] == "Не приехал" for c in client.get("/admin/complaints", headers=admin_h).json())


def test_request_privacy(client):
    token, req = create_request(client)
    assert client.get("/requests/not-a-real-token").status_code == 404
    assert client.post("/requests/not-a-real-token/cancel").status_code == 404
    # only a hash of the token is stored
    from app.models import Order
    db = SessionLocal()
    stored = db.get(Order, req["id"]).client_token_hash
    db.close()
    assert stored and token not in stored and len(stored) == 64


def test_request_spam_protection(client):
    cid = category_id(client)
    base = {"name": "Bot", "category_id": cid, "city": "Бишкек", "description": "x"}
    # honeypot field filled -> rejected
    r = client.post("/requests", json={**base, "phone": new_phone(), "website": "http://spam"})
    assert r.status_code == 400
    # max 3 requests per phone per hour
    phone = new_phone()
    for _ in range(3):
        rate_limit._attempts.clear()
        assert client.post("/requests", json={**base, "phone": phone}).status_code == 200
    rate_limit._attempts.clear()
    assert client.post("/requests", json={**base, "phone": phone}).status_code == 429
    # max 5 requests per IP per hour
    rate_limit._attempts.clear()
    codes = [client.post("/requests", json={**base, "phone": new_phone()}).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_request_validation(client):
    cid = category_id(client)
    base = {"name": "C", "phone": new_phone(), "category_id": cid, "city": "Бишкек", "description": "x"}
    assert client.post("/requests", json={**base, "phone": "abc"}).status_code == 422
    assert client.post("/requests", json={**base, "city": "Атлантида"}).status_code == 422
    assert client.post("/requests", json={**base, "description": ""}).status_code == 422
    assert client.post("/requests", json={**base, "time": "<b>"}).status_code == 422
    assert client.post("/requests", json={**base, "category_id": 99999}).status_code == 404
    assert client.get("/masters", params={"sort": "drop table"}).status_code == 422


# ---------- masters: public profiles, registration, agreement, avatar ----------

def test_search_and_public_phones(client):
    _, phone = register_master(client, "Алмаз Электрик", "Электрика")
    names = lambda params: [m["user"]["name"] for m in client.get("/masters", params=params).json()]  # noqa: E731
    assert "Алмаз Электрик" in names({"q": "электр"})          # category name, case-insensitive Cyrillic
    assert "Алмаз Электрик" in names({"q": "смесител"})        # service title
    assert "Алмаз Электрик" not in names({"q": "zzz"})
    assert names({"q": "%"}) == []                             # LIKE wildcard is literal
    assert names({"q": "' OR 1=1 --"}) == []                   # SQL injection is just text
    # no login needed to call a master
    found = next(m for m in client.get("/masters", params={"q": "Алмаз"}).json())
    assert found["user"]["phone"] == phone
    assert "email" not in found["user"]

    # several services in one category -> master appears once
    h, _ = register_master(client, "Дубль")
    client.post("/masters/me/services", headers=h, json={"category_id": category_id(client), "title": "Ещё", "price_from": 1})
    ids = [m["id"] for m in client.get("/masters", params={"category_id": category_id(client)}).json()]
    assert len(ids) == len(set(ids))


def test_only_masters_register_and_must_accept_agreement(client):
    phone = new_phone()
    email = email_for(phone)
    base = {"name": "M", "phone": phone, "password": "secret123", "city": "Бишкек", "email": email,
            "code": email_code(client, email)}
    r = client.post("/auth/register", json=base)
    assert r.status_code == 400 and "договор" in r.json()["detail"]
    assert client.post("/auth/register", json={**base, "accept_agreement": True, "role": "client"}).status_code == 422
    assert client.post("/auth/register", json={**base, "accept_agreement": True, "role": "admin"}).status_code == 422
    r = client.post("/auth/register", json={**base, "accept_agreement": True})
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = client.get("/masters/me", headers=headers).json()
    assert me["agreement_accepted"] is True and me["missed_requests"] == 0
    assert client.get("/auth/me", headers=headers).json()["role"] == "master"


def test_legacy_master_accepts_agreement(client):
    headers, phone = register_master(client)
    db = SessionLocal()
    user = db.query(User).filter(User.phone == phone).one()
    user.agreement_accepted_at = None
    db.commit()
    db.close()
    assert client.get("/masters/me", headers=headers).json()["agreement_accepted"] is False
    # no requests until the master accepts the agreement
    r = client.get("/orders/feed", headers=headers)
    assert r.status_code == 403 and "договор" in r.json()["detail"]
    _, req = create_request(client)
    assert client.post(f"/orders/{req['id']}/offer", headers=headers, json={"price": 1}).status_code == 403
    assert client.post("/masters/me/accept-agreement", headers=headers).json()["agreement_accepted"] is True
    assert client.get("/orders/feed", headers=headers).status_code == 200


def test_master_avatar(client):
    headers, _ = register_master(client)
    mid = master_id_of(client, headers)
    r = client.post("/masters/me/avatar", headers=headers, files={"file": ("me.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    first = r.json()["user"]["photo"]
    assert first.startswith(f"/uploads/avatars/{mid}/")
    assert client.get(f"/masters/{mid}").json()["user"]["photo"] == first
    assert client.get(first).status_code == 200
    # replacing removes the old file; HTML disguised as an image is refused
    second = client.post("/masters/me/avatar", headers=headers, files={"file": ("me.png", PNG, "image/png")}).json()["user"]["photo"]
    assert second != first and client.get(first).status_code == 404
    bad = client.post("/masters/me/avatar", headers=headers, files={"file": ("x.png", b"<script>", "image/png")})
    assert bad.status_code == 400
    assert client.delete("/masters/me/avatar", headers=headers).json()["user"]["photo"] is None


def test_verification_gate(client, monkeypatch, outbox):
    from app.config import settings
    monkeypatch.setattr(settings, "masters_require_verification", True)
    headers, _ = register_master(client, "Непроверенный", notify_enabled=True)
    mid = master_id_of(client, headers)

    # hidden from clients, but the master can preview their own profile
    assert mid not in [m["id"] for m in client.get("/masters").json()]
    assert client.get(f"/masters/{mid}").status_code == 404
    assert client.get(f"/masters/{mid}", headers=headers).status_code == 200
    # no requests until verified
    r = client.get("/orders/feed", headers=headers)
    assert r.status_code == 403 and "проверке" in r.json()["detail"]
    outbox.clear()
    create_request(client)
    assert outbox == []

    admin_h = make_admin(client)
    client.patch(f"/admin/masters/{mid}/verify", headers=admin_h, json={"verified": True})
    assert mid in [m["id"] for m in client.get("/masters").json()]
    outbox.clear()
    _, req = create_request(client)
    assert req["id"] in [o["id"] for o in client.get("/orders/feed", headers=headers).json()]
    assert len(outbox) == 1


# ---------- the 20-minute promise ----------

def test_missed_request_fine_and_rating(client, outbox):
    silent_h, _ = register_master(client, "Молчун", notify_enabled=True, city="Токмок")
    decliner_h, _ = register_master(client, "Отказник", city="Токмок")
    offerer_h, _ = register_master(client, "Быстрый", city="Токмок")
    silent_id = master_id_of(client, silent_h)

    # give the silent master a 5-star history first
    token, req = create_request(client, city="Токмок")
    offer = client.post(f"/orders/{req['id']}/offer", headers=silent_h, json={"price": 100}).json()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})
    client.post(f"/orders/{req['id']}/status", headers=silent_h, json={"status": "completed"})
    client.post(f"/requests/{token}/review", json={"rating": 5})
    assert client.get(f"/masters/{silent_id}").json()["rating"] == 5

    _, req = create_request(client, city="Токмок")
    client.post(f"/orders/{req['id']}/decline", headers=decliner_h)
    client.post(f"/orders/{req['id']}/offer", headers=offerer_h, json={"price": 500})
    # declined requests disappear from the feed
    assert req["id"] not in [o["id"] for o in client.get("/orders/feed", headers=decliner_h).json()]

    outbox.clear()
    expire_deliveries(req["id"])
    assert run_missed_check() == 1  # only the silent master
    me = client.get("/masters/me", headers=silent_h).json()
    assert me["missed_requests"] == 1 and me["rating"] == 4.9 and me["rating_penalty"] == pytest.approx(0.1)
    assert client.get("/masters/me", headers=decliner_h).json()["missed_requests"] == 0
    assert client.get("/masters/me", headers=offerer_h).json()["missed_requests"] == 0
    assert any("штраф 200 сом" in m[2] for m in outbox)
    assert any(n["title"] == "Пропущена заявка" for n in client.get("/notifications", headers=silent_h).json())
    assert run_missed_check() == 0  # counted once

    admin_h = make_admin(client)
    violations = client.get("/admin/violations", headers=admin_h).json()
    assert any(v["master_id"] == silent_id and v["order_id"] == req["id"] and v["fine"] == 200 for v in violations)
    assert client.get("/admin/violations", headers=silent_h).status_code == 403

    # the penalty survives new reviews: rating = average - penalty
    token, req = create_request(client, city="Токмок")
    offer = client.post(f"/orders/{req['id']}/offer", headers=silent_h, json={"price": 100}).json()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})
    client.post(f"/orders/{req['id']}/status", headers=silent_h, json={"status": "completed"})
    client.post(f"/requests/{token}/review", json={"rating": 5})
    assert client.get(f"/masters/{silent_id}").json()["rating"] == 4.9


def test_no_penalty_when_request_closed_in_time(client):
    silent_h, _ = register_master(client, city="Кант")
    taker_h, _ = register_master(client, city="Кант")
    token, req = create_request(client, city="Кант")
    offer = client.post(f"/orders/{req['id']}/offer", headers=taker_h, json={"price": 300}).json()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})  # taken before the deadline
    expire_deliveries(req["id"])
    run_missed_check()
    assert client.get("/masters/me", headers=silent_h).json()["missed_requests"] == 0


def test_unanswered_requests_go_to_admin(client):
    register_master(client)
    _, req = create_request(client, phone="+996777000111")
    admin_h = make_admin(client)
    assert req["id"] not in [o["id"] for o in client.get("/admin/unanswered", headers=admin_h).json()]

    from datetime import timedelta

    from app.models import Order, utcnow
    db = SessionLocal()
    db.get(Order, req["id"]).created_at = utcnow() - timedelta(minutes=21)
    db.commit()
    db.close()
    item = next(o for o in client.get("/admin/unanswered", headers=admin_h).json() if o["id"] == req["id"])
    assert item["client_phone"] == "+996777000111" and item["masters_notified"] >= 1
    assert client.get("/admin/unanswered").status_code == 401


# ---------- access control & security ----------

def test_offer_restrictions(client):
    electrician_h, _ = register_master(client, category="Электрика")
    _, req = create_request(client)
    # wrong category
    assert client.post(f"/orders/{req['id']}/offer", headers=electrician_h, json={"price": 1}).status_code == 403
    # not a master
    assert client.post(f"/orders/{req['id']}/offer", json={"price": 1}).status_code == 401
    admin_h = make_admin(client)
    assert client.post(f"/orders/{req['id']}/offer", headers=admin_h, json={"price": 1}).status_code == 403
    master_h, _ = register_master(client)
    assert client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": -5}).status_code == 422


def test_auth_hardening(client):
    # phone normalization: formatted and plain are the same account
    phone = "+996722333444"
    email = "norm@example.com"
    r = client.post("/auth/register", json={"name": "x", "phone": "+996 722 333 444", "password": "secret123",
                                            "email": email, "code": email_code(client, email), "city": "Бишкек",
                                            "accept_agreement": True})
    assert r.status_code == 200
    r = client.post("/auth/send-code", json={"email": "other@example.com", "phone": phone, "purpose": "register"})
    assert r.status_code == 400 and "телефоном" in r.json()["detail"]
    assert client.post("/auth/login", json={"phone": "+996-722-333-444", "password": "secret123"}).status_code == 200

    # forged / tampered tokens
    forged = jwt.encode({"sub": "1"}, "wrong-secret-of-a-sufficient-length-0123456789", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': '1'})}."  # "alg: none" attack
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401

    # non-admins get 403 on admin API
    h, _ = register_master(client)
    for path in ["/admin/stats", "/admin/users", "/admin/orders", "/admin/complaints", "/admin/unanswered"]:
        assert client.get(path, headers=h).status_code == 403
    assert client.get("/admin/stats").status_code == 401

    # brute force is rate limited
    rate_limit._attempts.clear()
    codes = [client.post("/auth/login", json={"phone": "+996700000001", "password": "bad"}).status_code for _ in range(11)]
    assert codes[-1] == 429


def test_rate_limit_ignores_forged_ip(client, monkeypatch):
    """X-Forwarded-For is set by the client — changing it must not reset the login limit."""
    from app.config import settings
    body = {"phone": "+996700000777", "password": "bad"}
    rate_limit._attempts.clear()
    codes = [client.post("/auth/login", json=body, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
             for i in range(11)]
    assert codes[-1] == 429

    # Behind Cloudflare (Render) the real IP comes from CF-Connecting-IP, which the client can't forge.
    monkeypatch.setattr(settings, "client_ip_header", "cf-connecting-ip")
    rate_limit._attempts.clear()
    same = {"CF-Connecting-IP": "203.0.113.5"}
    codes = [client.post("/auth/login", json=body, headers={**same, "X-Forwarded-For": f"10.1.0.{i}"}).status_code
             for i in range(11)]
    assert codes[-1] == 429
    assert client.post("/auth/login", json=body, headers={"CF-Connecting-IP": "203.0.113.6"}).status_code == 401


def test_offer_only_in_own_city(client):
    master_h, _ = register_master(client, city="Кемин")
    _, req = create_request(client, city="Арван")
    r = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 100})
    assert r.status_code == 403 and "город" in r.json()["detail"]


def test_photo_location_is_removed(client):
    """Uploads are decoded and saved again from pixels: EXIF (GPS of the client's home) and text chunks are gone."""
    import io
    import os

    from PIL import Image

    from app.config import settings
    exif = Image.Exif()
    exif[0x010E] = "GPS-42.87,74.59"  # ImageDescription
    exif[0x0112] = 6  # "rotate 90°": the pixels get turned instead of losing the orientation
    jpeg = _image("JPEG", (40, 20), exif=exif.tobytes())
    token, _ = create_request(client)
    url = client.post(f"/requests/{token}/photos", files={"files": ("p.jpg", jpeg, "image/jpeg")}).json()[0]["url"]
    saved = open(os.path.join(settings.upload_dir, url.removeprefix("/uploads/")), "rb").read()
    assert b"GPS" not in saved and b"Exif" not in saved
    assert Image.open(io.BytesIO(saved)).size == (20, 40)

    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "GPS 42.87,74.59")
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG", pnginfo=info)
    assert b"GPS" in buf.getvalue()
    token, _ = create_request(client)
    url = client.post(f"/requests/{token}/photos", files={"files": ("p.png", buf.getvalue(), "image/png")}).json()[0]["url"]
    saved = open(os.path.join(settings.upload_dir, url.removeprefix("/uploads/")), "rb").read()
    assert b"GPS" not in saved and Image.open(io.BytesIO(saved)).format == "PNG"


def test_upload_must_really_be_an_image(client):
    token, _ = create_request(client)
    url = f"/requests/{token}/photos"
    # right signature, nothing behind it; an HTML polyglot; a PNG sent as JPEG
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert client.post(url, files={"files": ("x.png", fake, "image/png")}).status_code == 400
    assert client.post(url, files={"files": ("x.jpg", b"\xff\xd8\xff<html><script>alert(1)</script>", "image/jpeg")}).status_code == 400
    assert client.post(url, files={"files": ("x.jpg", PNG, "image/jpeg")}).status_code == 400
    # data appended after a real image (a classic polyglot) doesn't survive the re-encoding
    import os

    from app.config import settings
    r = client.post(url, files={"files": ("x.png", PNG + b"<script>alert(1)</script>", "image/png")})
    assert r.status_code == 200
    saved = open(os.path.join(settings.upload_dir, r.json()[0]["url"].removeprefix("/uploads/")), "rb").read()
    assert b"<script>" not in saved
    # a decompression bomb: tiny file, gigantic picture
    bomb = _image("PNG", (9000, 9000))
    assert client.post(url, files={"files": ("b.png", bomb, "image/png")}).status_code == 400
    # a big phone photo is accepted and scaled down
    big = _image("JPEG", (4000, 3000))
    r = client.post(url, files={"files": ("big.jpg", big, "image/jpeg")})
    assert r.status_code == 200, r.text


def test_huge_request_is_rejected(client):
    from app.main import MAX_BODY_BYTES
    r = client.post("/requests", content=b"x", headers={"Content-Type": "application/json",
                                                       "Content-Length": str(MAX_BODY_BYTES + 1)})
    assert r.status_code == 413

    # no Content-Length (chunked): the body is counted while it streams in
    def chunks():
        for _ in range(MAX_BODY_BYTES // (1024 * 1024) + 2):
            yield b"x" * (1024 * 1024)
    token, _ = create_request(client)
    r = client.post(f"/requests/{token}/photos", content=chunks(),
                    headers={"Content-Type": "multipart/form-data; boundary=zzz"})
    assert r.status_code == 413, r.text
    assert r.json()["detail"] == "Слишком большой запрос"


def test_admin_deletes_master(client, outbox):
    import os

    from app.config import settings
    master_h, phone = register_master(client, "Удаляемый", city="Кара-Суу", notify_enabled=True)
    mid = master_id_of(client, master_h)
    user_id = client.get("/auth/me", headers=master_h).json()["id"]
    avatar = client.post("/masters/me/avatar", headers=master_h, files={"file": ("a.png", PNG, "image/png")}).json()["user"]["photo"]
    client.post("/masters/me/photos", headers=master_h, files={"files": ("w.png", PNG, "image/png")})

    # one finished job with a review, one job in progress, one open request with an offer
    done_token, done = create_request(client, city="Кара-Суу")
    offer = client.post(f"/orders/{done['id']}/offer", headers=master_h, json={"price": 100}).json()
    client.post(f"/requests/{done_token}/accept", params={"offer_id": offer["id"]})
    client.post(f"/orders/{done['id']}/status", headers=master_h, json={"status": "completed"})
    client.post(f"/requests/{done_token}/review", json={"rating": 5})
    work_token, work = create_request(client, city="Кара-Суу")
    offer = client.post(f"/orders/{work['id']}/offer", headers=master_h, json={"price": 200}).json()
    client.post(f"/requests/{work_token}/accept", params={"offer_id": offer["id"]})
    client.post(f"/orders/{work['id']}/status", headers=master_h, json={"status": "in_progress"})
    client.post(f"/requests/{work_token}/complaint", json={"text": "Пропал"})
    open_token, open_req = create_request(client, city="Кара-Суу")
    client.post(f"/orders/{open_req['id']}/offer", headers=master_h, json={"price": 300})

    admin_h = make_admin(client)
    assert client.delete(f"/admin/users/{user_id}", headers=master_h).status_code == 403
    assert client.delete(f"/admin/users/{user_id}", headers=admin_h).status_code == 204
    assert client.delete(f"/admin/users/{user_id}", headers=admin_h).status_code == 404

    # the account and the public profile are gone, the old login token doesn't work
    assert client.get("/auth/me", headers=master_h).status_code == 401
    assert client.post("/auth/login", json={"phone": phone, "password": "secret123"}).status_code == 401
    assert client.get(f"/masters/{mid}").status_code == 404
    assert not os.path.exists(os.path.join(settings.upload_dir, avatar.removeprefix("/uploads/")))
    # clients keep their requests: the unfinished one is looking for a pro again
    view = client.get(f"/requests/{work_token}").json()
    assert view["status"] == "searching" and view["master_contact"] is None
    assert client.post(f"/requests/{work_token}/complete").status_code == 400
    assert client.get(f"/requests/{done_token}").json()["status"] == "reviewed"
    assert client.get(f"/requests/{open_token}").json()["offers"] == []
    assert any(c["text"] == "Пропал" for c in client.get("/admin/complaints", headers=admin_h).json())
    admin_id = client.get("/auth/me", headers=admin_h).json()["id"]
    assert client.delete(f"/admin/users/{admin_id}", headers=admin_h).status_code == 400


CLIENT_KEY = {"X-Client-Key": "client-chat-key-0123456789abcdef-AAAA"}
OTHER_KEY = {"X-Client-Key": "client-chat-key-0123456789abcdef-BBBB"}


def test_chat_between_client_and_master(client, fcm):
    master_h, _ = register_master(client, "Чат Мастер", city="Исфана")
    mid = master_id_of(client, master_h)
    client.post("/notifications/devices", headers=master_h, json={"token": "chat-phone-token-0123456789"})

    # a client without an account writes from the master's profile
    assert client.post("/chats", json={"master_id": mid, "name": "Айгуль", "text": "Здравствуйте"}).status_code == 401
    fcm.clear()
    chat = client.post("/chats", headers=CLIENT_KEY, json={"master_id": mid, "name": "Айгуль", "text": "Сколько стоит замена крана?"}).json()
    assert chat["master"]["name"] == "Чат Мастер" and chat["last_message"] == "Сколько стоит замена крана?"
    assert fcm == [("chat-phone-token-0123456789", "Новое сообщение", "Айгуль: Сколько стоит замена крана?", f"/chat.html?id={chat['id']}")]
    # a second message while still unread: no second notification
    client.post(f"/chats/{chat['id']}/messages", headers=CLIENT_KEY, json={"text": "Сегодня сможете?"})
    assert len(fcm) == 1
    # writing again from the profile reuses the same chat
    again = client.post("/chats", headers=CLIENT_KEY, json={"master_id": mid, "name": "Айгуль", "text": "Ау"}).json()
    assert again["id"] == chat["id"]

    # the master sees the chat with 3 unread, reads, answers
    chats = client.get("/chats", headers=master_h).json()
    assert [c["unread"] for c in chats if c["id"] == chat["id"]] == [3]
    assert client.get("/chats/unread", headers=master_h).json()["unread"] == 3
    texts = [m["text"] for m in client.get(f"/chats/{chat['id']}/messages", headers=master_h).json()]
    assert texts == ["Сколько стоит замена крана?", "Сегодня сможете?", "Ау"]
    assert client.get("/chats/unread", headers=master_h).json()["unread"] == 0
    reply = client.post(f"/chats/{chat['id']}/messages", headers=master_h, json={"text": "Да, 800 сом"}).json()
    assert reply["sender"] == "master"

    # the client gets only new messages (polling with after_id)
    assert client.get("/chats/unread", headers=CLIENT_KEY).json()["unread"] == 1
    new = client.get(f"/chats/{chat['id']}/messages", params={"after_id": reply["id"] - 1}, headers=CLIENT_KEY).json()
    assert [m["text"] for m in new] == ["Да, 800 сом"]
    photo = client.post(f"/chats/{chat['id']}/photos", headers=CLIENT_KEY, files={"file": ("p.png", PNG, "image/png")}).json()
    assert photo["photo"].startswith(f"/uploads/chats/{chat['id']}/")

    # privacy: another browser, another master, no key
    assert client.get(f"/chats/{chat['id']}/messages", headers=OTHER_KEY).status_code == 404
    other_h, _ = register_master(client, city="Исфана")
    assert client.get(f"/chats/{chat['id']}/messages", headers=other_h).status_code == 404
    assert client.get("/chats", headers=OTHER_KEY).json() == []
    assert client.get(f"/chats/{chat['id']}/messages", headers={"X-Client-Key": "short"}).status_code == 401
    assert client.post("/chats", headers=CLIENT_KEY, json={"master_id": mid, "name": " ", "text": "x"}).status_code == 422

    # the master blocks a spammer
    assert client.post(f"/chats/{chat['id']}/block", headers=CLIENT_KEY).status_code == 403
    assert client.post(f"/chats/{chat['id']}/block", headers=master_h).json()["blocked"] is True
    assert client.post(f"/chats/{chat['id']}/messages", headers=CLIENT_KEY, json={"text": "!!!"}).status_code == 403


def test_chat_with_unverified_master_is_impossible(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "masters_require_verification", True)
    master_h, _ = register_master(client, city="Кемин")
    mid = master_id_of(client, master_h)
    r = client.post("/chats", headers=CLIENT_KEY, json={"master_id": mid, "name": "А", "text": "Привет"})
    assert r.status_code == 404


def test_live_location_on_the_map(client):
    from datetime import timedelta

    from app.models import Master, utcnow
    near_h, _ = register_master(client, "Рядом", city="Бишкек")
    far_h, _ = register_master(client, "Далеко", city="Бишкек")
    electric_h, _ = register_master(client, "Электрик", category="Электрика", city="Бишкек")
    silent_h, _ = register_master(client, "Не делится", city="Бишкек")
    plumbing = category_id(client)
    me = (42.8746, 74.5698)  # Bishkek centre

    client.put("/masters/me/location", headers=near_h, json={"lat": 42.8800, "lon": 74.5800})
    client.put("/masters/me/location", headers=far_h, json={"lat": 42.4500, "lon": 75.2000})  # ~70 km
    client.put("/masters/me/location", headers=electric_h, json={"lat": 42.8750, "lon": 74.5700})
    assert client.put("/masters/me/location", json={"lat": 1, "lon": 1}).status_code == 401
    assert client.put("/masters/me/location", headers=near_h, json={"lat": 100, "lon": 1}).status_code == 422
    assert client.get("/masters/me", headers=near_h).json()["location_shared"] is True

    found = client.get("/masters/nearby", params={"lat": me[0], "lon": me[1], "category_id": plumbing}).json()
    names = [m["name"] for m in found]
    assert "Рядом" in names and "Далеко" not in names and "Электрик" not in names and "Не делится" not in names
    near = next(m for m in found if m["name"] == "Рядом")
    assert near["distance_km"] < 2 and near["price_from"] == 500 and near["phone"]
    assert "minutes_ago" not in near and "location_updated_at" not in near  # clients don't see when the master was online
    assert (near["lat"], near["lon"]) == (42.88, 74.58)  # rounded to ~100 m, not the exact spot

    # the last known position stays on the map, with its age (updated when the master opens the app again)
    db = SessionLocal()
    db.query(Master).filter(Master.id == master_id_of(client, near_h)).update(
        {Master.location_updated_at: utcnow() - timedelta(days=2)})
    db.commit()
    db.close()
    assert "Рядом" in [m["name"] for m in client.get("/masters/nearby", params={"lat": me[0], "lon": me[1]}).json()]

    # turning sharing off removes the master and forgets the position
    client.delete("/masters/me/location", headers=electric_h)
    assert "Электрик" not in [m["name"] for m in client.get("/masters/nearby", params={"lat": me[0], "lon": me[1]}).json()]
    assert client.get("/masters/me", headers=electric_h).json()["location_shared"] is False


def _browser_keys():
    """A fake browser's push keys, as pushManager.subscribe() would produce."""
    import os as os_

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.webpush import b64url
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os_.urandom(16)
    return private, {"p256dh": b64url(public), "auth": b64url(auth)}


def _browser_decrypt(private, keys, body: bytes) -> dict:
    """What the browser does with a push message (RFC 8291)."""
    import json as json_

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from app.webpush import b64url_decode
    salt, idlen = body[:16], body[20]
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    shared = private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ua_public = b64url_decode(keys["p256dh"])
    ikm = HKDF(hashes.SHA256(), 32, salt=b64url_decode(keys["auth"]), info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return json_.loads(plain[:-1])


@pytest.fixture
def push_service(monkeypatch, outbox):
    """Stub of the browsers' push services: decrypts what we send, like a browser would."""
    from app import webpush
    browsers, received = {}, []

    class Response:
        def __init__(self, status_code):
            self.status_code, self.text = status_code, ""

    def post(url, content=None, headers=None, timeout=None, **_):
        if url not in browsers:
            return Response(410)  # the browser unsubscribed
        private, keys = browsers[url]
        assert headers["Content-Encoding"] == "aes128gcm" and headers["Authorization"].startswith("vapid t=")
        received.append((url, _browser_decrypt(private, keys, content)))
        return Response(201)

    monkeypatch.setattr(webpush.httpx, "post", post)

    def browser(name):
        private, keys = _browser_keys()
        url = f"https://fcm.googleapis.com/fcm/send/{name}"
        browsers[url] = (private, keys)
        return {"endpoint": url, "keys": keys}

    browser.received = received
    browser.forget = lambda sub: browsers.pop(sub["endpoint"])
    return browser


def test_web_push_encryption_and_signature(client):
    import jwt as jwt_

    from app import webpush
    from app.database import SessionLocal as Session_
    private, keys = _browser_keys()
    body = webpush.encrypt('{"title": "Привет"}'.encode(), keys["p256dh"], keys["auth"])
    assert _browser_decrypt(private, keys, body) == {"title": "Привет"}

    db = Session_()
    headers = webpush._vapid_headers(db, "https://fcm.googleapis.com/fcm/send/abc")
    public_key = webpush.vapid_public_key(db)
    db.close()
    token = headers["Authorization"].split("t=")[1].split(",")[0]
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64url_decode(public_key))
    claims = jwt_.decode(token, key, algorithms=["ES256"], audience="https://fcm.googleapis.com")
    assert claims["sub"].startswith("mailto:")
    # the same key survives restarts (stored in the database)
    assert client.get("/notifications/web-push/key").json()["public_key"] == public_key


def test_web_push_to_masters_and_clients(client, push_service):
    master_h, _ = register_master(client, "Веб Мастер", city="Балыкчы")
    master_browser = push_service("master-phone")
    client_browser = push_service("client-phone")
    assert client.post("/notifications/web-push/subscribe", json=master_browser).status_code == 401
    assert client.post("/notifications/web-push/subscribe", headers=master_h, json=master_browser).status_code == 204
    r = client.post("/notifications/web-push/subscribe", headers=CLIENT_KEY, json={**client_browser, "lang": "en"})
    assert r.status_code == 204
    # SSRF: only real push services
    evil = {"endpoint": "https://169.254.169.254/latest/meta-data", "keys": client_browser["keys"]}
    assert client.post("/notifications/web-push/subscribe", headers=CLIENT_KEY, json=evil).status_code == 422
    evil["endpoint"] = "https://fcm.googleapis.com.evil.example/x"
    assert client.post("/notifications/web-push/subscribe", headers=CLIENT_KEY, json=evil).status_code == 422

    # a new request reaches the master's browser
    push_service.received.clear()
    rate_limit._attempts.clear()
    r = client.post("/requests", headers=CLIENT_KEY, json={
        "name": "Клиент", "phone": new_phone(), "category_id": category_id(client), "city": "Балыкчы", "description": "Кран"})
    req = r.json()["request"]
    assert [(u.rsplit("/", 1)[1], m["title"]) for u, m in push_service.received] == [("master-phone", f"Новый заказ #{req['id']} — Сантехника")]

    # the master's offer reaches the client's browser, in the client's language
    push_service.received.clear()
    client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 750})
    assert push_service.received == [(client_browser["endpoint"], {"title": "New offer", "body": "Веб Мастер — 750 som", "link": "/my-requests.html"})]

    # chat reply from the master
    chat = client.post("/chats", headers=CLIENT_KEY, json={"master_id": master_id_of(client, master_h), "name": "К", "text": "?"}).json()
    push_service.received.clear()
    client.post(f"/chats/{chat['id']}/messages", headers=master_h, json={"text": "Буду в 6"})
    assert push_service.received[-1][1]["link"] == f"/chat.html?id={chat['id']}" and push_service.received[-1][1]["body"] == "Буду в 6"

    # a browser that unsubscribed is forgotten
    from app.models import WebPushSubscription
    push_service.forget(client_browser)
    client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 700})
    db = SessionLocal()
    assert db.query(WebPushSubscription).filter(WebPushSubscription.endpoint == client_browser["endpoint"]).count() == 0
    db.close()


def test_master_deletes_own_account(client):
    headers, phone = register_master(client, "Ухожу", city="Арван")
    mid = master_id_of(client, headers)
    assert client.post("/auth/delete-account", json={"password": "secret123"}).status_code == 401
    r = client.post("/auth/delete-account", headers=headers, json={"password": "wrong-password"})
    assert r.status_code == 400 and "пароль" in r.json()["detail"]
    assert client.post("/auth/delete-account", headers=headers, json={"password": "secret123"}).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get(f"/masters/{mid}").status_code == 404
    assert client.post("/auth/login", json={"phone": phone, "password": "secret123"}).status_code == 401
    admin_h = make_admin(client)
    assert client.post("/auth/delete-account", headers=admin_h, json={"password": "adminpass"}).status_code == 400


def test_blocked_master(client):
    headers, _ = register_master(client, "Заблокированный")
    mid = master_id_of(client, headers)
    admin_h = make_admin(client)
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    assert client.patch(f"/admin/users/{user_id}/status", headers=admin_h, json={"status": "blocked"}).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 403
    assert mid not in [m["id"] for m in client.get("/masters").json()]
    assert client.get(f"/masters/{mid}").status_code == 404


def test_uploads(client):
    token, req = create_request(client)
    url = f"/requests/{token}/photos"
    # HTML / SVG disguised as images are rejected
    assert client.post(url, files={"files": ("x.png", b"<script>alert(1)</script>", "image/png")}).status_code == 400
    assert client.post(url, files={"files": ("x.svg", b"<svg/>", "image/svg+xml")}).status_code == 400

    r = client.post(url, files={"files": ("x.png", PNG, "image/png")})
    assert r.status_code == 200
    served = client.get(r.json()[0]["url"])
    assert served.status_code == 200
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in served.headers["content-security-policy"]
    assert len(client.get(f"/requests/{token}").json()["photos"]) == 1
    # at most 5 photos per request
    files = [("files", (f"{i}.png", PNG, "image/png")) for i in range(5)]
    assert client.post(url, files=files).status_code == 400
    # path traversal through the static mount
    assert client.get("/uploads/../app/config.py").status_code == 404


def test_no_n_plus_one_queries(client):
    """Listing endpoints must use a constant number of queries, not one per master/order."""
    from sqlalchemy import event

    from app.database import engine

    for i in range(12):
        rate_limit._attempts.clear()
        register_master(client, f"Мастер {i}")
    master_h, _ = register_master(client)
    for _ in range(4):
        create_request(client)

    import threading
    count = {"n": 0}

    def on_query(*_args):
        # Count only the endpoints (AnyIO worker threads), not background work: the missed-request check
        # (asyncio.to_thread) and notification delivery (notify_* executor) left over from earlier requests.
        if not threading.current_thread().name.startswith(("asyncio", "notify")):
            count["n"] += 1

    event.listen(engine, "before_cursor_execute", on_query)
    try:
        for path, headers in [("/masters", {}), ("/orders/feed", master_h), ("/orders", master_h)]:
            count["n"] = 0
            assert client.get(path, headers=headers).status_code == 200
            assert 0 < count["n"] <= 12, f"{path}: {count['n']} SQL queries"
    finally:
        event.remove(engine, "before_cursor_execute", on_query)


# ---------- email codes, login, password ----------

def test_register_requires_valid_code(client):
    email = "new.user@example.com"
    base = {"name": "X", "phone": new_phone(), "password": "secret123", "email": email, "city": "Бишкек",
            "accept_agreement": True}
    assert client.post("/auth/register", json=base).status_code == 422          # no code
    assert client.post("/auth/register", json={**base, "code": "123"}).status_code == 422
    assert client.post("/auth/register", json={**base, "code": "000000"}).status_code == 400  # none sent yet

    code = email_code(client, email)
    assert client.post("/auth/send-code", json={"email": email, "purpose": "register"}).status_code == 429  # throttled
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/auth/register", json={**base, "code": wrong}).json()["detail"] == "Неверный код"
    # the code belongs to this address only
    assert client.post("/auth/register", json={**base, "email": "someone.else@example.com", "code": code}).status_code == 400
    assert client.post("/auth/register", json={**base, "code": code}).status_code == 200
    r = client.post("/auth/send-code", json={"email": "NEW.user@example.com", "purpose": "register"})
    assert r.status_code == 400 and "email" in r.json()["detail"]


def test_email_validation(client):
    assert client.post("/auth/send-code", json={"email": "bad@", "purpose": "register"}).status_code == 422
    base = {"name": "C", "phone": new_phone(), "password": "secret123", "code": "123456", "city": "Бишкек",
            "accept_agreement": True}
    assert client.post("/auth/register", json=base).status_code == 422
    assert client.post("/auth/register", json={**base, "email": "not-an-email"}).status_code == 422


def test_code_brute_force_is_capped(client):
    email = "brute@example.com"
    code = email_code(client, email)
    base = {"name": "X", "phone": new_phone(), "password": "secret123", "email": email, "city": "Бишкек",
            "accept_agreement": True}
    for w in [c for c in ("000000", "111111", "222222", "333333", "444444", "555555") if c != code][:5]:
        assert client.post("/auth/register", json={**base, "code": w}).status_code == 400
    r = client.post("/auth/register", json={**base, "code": code})
    assert r.status_code == 400 and "запросите новый" in r.json()["detail"]


def test_expired_code(client):
    from datetime import timedelta

    from app.models import VerificationCode, utcnow
    email = "late@example.com"
    code = email_code(client, email)
    db = SessionLocal()
    db.query(VerificationCode).filter(VerificationCode.target == email).update(
        {VerificationCode.expires_at: utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    r = client.post("/auth/register", json={"name": "X", "phone": new_phone(), "password": "secret123",
                                            "email": email, "code": code, "city": "Бишкек", "accept_agreement": True})
    assert r.status_code == 400 and "устарел" in r.json()["detail"]


def test_code_email_is_sent_in_users_language(client, monkeypatch):
    from app import verification
    letters = []

    class Fake:
        is_dev = True

        def send(self, to, subject, text):
            letters.append((to, subject, text))

    monkeypatch.setattr(verification, "get_email_provider", lambda: Fake())
    r = client.post("/auth/send-code", json={"email": "ky@example.com", "purpose": "register", "lang": "ky"})
    code = r.json()["debug_code"]
    to, subject, text = letters[-1]
    assert to == "ky@example.com" and subject == f"Ырастоо коду: {code}" and code in text and "мүнөт" in text


def test_login_by_email_or_phone(client):
    headers, phone = register_master(client)
    email = email_for(phone)
    assert client.post("/auth/login", json={"phone": email.upper(), "password": "secret123"}).status_code == 200
    assert client.post("/auth/login", json={"phone": phone, "password": "secret123"}).status_code == 200
    assert client.post("/auth/login", json={"phone": email, "password": "wrong"}).status_code == 401
    assert client.get("/auth/me", headers=headers).json()["email"] == email


def test_password_reset(client):
    headers, phone = register_master(client)
    email = email_for(phone)
    r = client.post("/auth/send-code", json={"email": "nobody@example.com", "purpose": "reset"})
    assert r.status_code == 200 and r.json()["debug_code"] is None  # no account probing

    import time
    time.sleep(1.1)  # tokens carry whole-second timestamps; make the reset strictly later than login
    code = email_code(client, email, "reset")
    r = client.post("/auth/reset-password", json={"email": email, "code": code, "password": "newpass123"})
    assert r.status_code == 200
    new_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/auth/me", headers=headers).status_code == 401       # old sessions logged out
    assert client.get("/auth/me", headers=new_headers).status_code == 200
    assert client.post("/auth/login", json={"phone": phone, "password": "secret123"}).status_code == 401
    assert client.post("/auth/login", json={"phone": email, "password": "newpass123"}).status_code == 200
    assert client.post("/auth/reset-password", json={"email": email, "code": code, "password": "other123"}).status_code == 400


def test_change_email_needs_a_code(client):
    headers, _ = register_master(client)
    _, other_phone = register_master(client)
    assert client.post("/auth/send-code", json={"email": "fresh@example.com", "purpose": "change_email"}).status_code == 401
    r = client.post("/auth/send-code", json={"email": email_for(other_phone), "purpose": "change_email"}, headers=headers)
    assert r.status_code == 400
    code = email_code(client, "fresh@example.com", "change_email", headers=headers)
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/auth/change-email", json={"email": "fresh@example.com", "code": wrong}, headers=headers).status_code == 400
    r = client.post("/auth/change-email", json={"email": "Fresh@Example.com", "code": code}, headers=headers)
    assert r.status_code == 200 and r.json()["email"] == "fresh@example.com"


# ---------- notifications (opt-in) ----------

def test_notifications_need_consent(client, outbox):
    silent_h, _ = register_master(client, notify_enabled=False)
    sms_h, sms_phone = register_master(client, notify_enabled=True, channel="sms", lang="ky", city="Талас")
    electrician_h, _ = register_master(client, notify_enabled=True, category="Электрика", city="Талас")
    client.patch("/masters/me", headers=silent_h, json={"city": "Талас"})
    assert client.get("/notifications/settings", headers=silent_h).json()["enabled"] is False

    outbox.clear()
    _, req = create_request(client, price=800, city="Талас")
    for h in (silent_h, sms_h):  # in-app for everyone in the category
        assert any("Новый заказ" in n["title"] for n in client.get("/notifications", headers=h).json())
    assert len(outbox) == 1  # outside the site only for the master who agreed, in their language
    channel, phone, text = outbox[0]
    assert channel == "sms" and phone == sms_phone and f"#{req['id']}" in text and "Жаңы буйрутма" in text
    assert not any("Новый заказ" in n["title"] for n in client.get("/notifications", headers=electrician_h).json())

    client.put("/notifications/settings", headers=sms_h, json={"enabled": False, "channel": "sms", "lang": "ky"})
    outbox.clear()
    create_request(client, city="Талас")
    assert outbox == []


def test_new_orders_by_email(client, outbox):
    _, phone = register_master(client, notify_enabled=True, channel="email", lang="en", city="Нарын")
    outbox.clear()
    _, req = create_request(client, description="Leaking tap", price=700, city="Нарын")
    mails = [m for m in outbox if m[0] == "email"]
    assert len(mails) == 1 and mails[0][1] == email_for(phone)
    assert f"New order #{req['id']} — Plumbing" in mails[0][2]
    assert "Leaking tap" in mails[0][2] and "700 som" in mails[0][2] and "Turn off" in mails[0][2]


@pytest.fixture
def fcm(monkeypatch, outbox):
    """Firebase stub: records pushes; tokens starting with "dead" are reported as unregistered."""
    from app import push
    from app.config import settings
    sent = []

    class Response:
        def __init__(self, status_code, text=""):
            self.status_code, self.text = status_code, text

    def post(url, json=None, headers=None, timeout=None):
        message = json["message"]
        if message["token"].startswith("dead"):
            return Response(404, '{"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}')
        sent.append((message["token"], message["notification"]["title"], message["notification"]["body"], message["data"]["link"]))
        return Response(200)

    monkeypatch.setattr(settings, "firebase_service_account", '{"project_id": "test-project"}')
    monkeypatch.setattr(push, "_access_token", lambda: "access-token")
    monkeypatch.setattr(push.httpx, "post", post)
    return sent


def test_push_notifications_to_the_app(client, fcm):
    assert client.get("/config").json()["push_enabled"] is True
    # push works even if the master didn't choose email/Telegram notifications
    master_h, _ = register_master(client, city="Чолпон-Ата", notify_enabled=False)
    device = {"token": "phone-token-0123456789-aaaa", "platform": "android"}
    assert client.post("/notifications/devices", json=device).status_code == 401
    assert client.post("/notifications/devices", headers=master_h, json=device).status_code == 204
    assert client.post("/notifications/devices", headers=master_h, json=device).status_code == 204  # no duplicate
    assert client.post("/notifications/devices", headers=master_h, json={"token": "short"}).status_code == 422

    fcm.clear()
    token, req = create_request(client, city="Чолпон-Ата")
    assert fcm == [(device["token"], f"Новый заказ #{req['id']} — Сантехника", f"Новый заказ #{req['id']}: Сантехника", "/feed.html")]

    offer = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 100}).json()
    fcm.clear()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})
    assert fcm and fcm[0][3] == f"/order-detail.html?id={req['id']}"

    # the same phone logs into another account: pushes follow the new account
    other_h, _ = register_master(client, city="Чолпон-Ата")
    client.post("/notifications/devices", headers=other_h, json=device)
    fcm.clear()
    create_request(client, city="Чолпон-Ата")
    assert len(fcm) == 1  # one phone, one push

    # logout removes the phone
    client.post("/notifications/devices/remove", headers=other_h, json=device)
    fcm.clear()
    create_request(client, city="Чолпон-Ата")
    assert fcm == []


def test_push_access_token_is_signed_with_service_account(monkeypatch):
    import json as json_

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app import push
    from app.config import settings
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    monkeypatch.setattr(settings, "firebase_service_account", json_.dumps(
        {"project_id": "p", "client_email": "bot@p.iam.gserviceaccount.com", "private_key": pem}))
    monkeypatch.setattr(push, "_access", {"token": None, "expires": 0.0})
    seen = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "ya29.test", "expires_in": 3600}

    def post(url, data=None, **_kwargs):
        seen.update(url=url, data=data)
        return Response()

    monkeypatch.setattr(push.httpx, "post", post)
    assert push._access_token() == "ya29.test"
    assert push._access_token() == "ya29.test"  # cached
    claims = jwt.decode(seen["data"]["assertion"], key.public_key(), algorithms=["RS256"], audience=push.TOKEN_URL)
    assert claims["iss"] == "bot@p.iam.gserviceaccount.com" and claims["scope"] == push.SCOPE


def test_push_diagnostics(client, fcm, monkeypatch):
    from app import push
    master_h, _ = register_master(client, city="Таш-Кумыр")
    r = client.post("/notifications/devices/test", headers=master_h).json()
    assert r == {"push_enabled": True, "devices": 0, "sent": 0, "errors": []}
    client.post("/notifications/devices", headers=master_h, json={"token": "phone-token-0123456789-dddd"})
    fcm.clear()
    r = client.post("/notifications/devices/test", headers=master_h).json()
    assert r["sent"] == 1 and fcm[0][1] == "Мастер рядом"

    admin_h = make_admin(client)
    status_ = client.get("/admin/push", headers=admin_h).json()
    assert status_["enabled"] and status_["connected"] and status_["project_id"] == "test-project"
    assert any(d["name"] == "Мастер" and d["platform"] == "android" for d in status_["devices"])
    assert client.get("/admin/push", headers=master_h).status_code == 403

    # a broken key on the server is reported, not swallowed
    def broken():
        raise ValueError("Could not deserialize key data")
    monkeypatch.setattr(push, "_access_token", broken)
    r = client.post("/notifications/devices/test", headers=master_h).json()
    assert r["sent"] == 0 and "FIREBASE_SERVICE_ACCOUNT" in r["errors"][0] and "deserialize" in r["errors"][0]
    assert "deserialize" in client.get("/admin/push", headers=admin_h).json()["error"]


def test_dead_push_token_is_removed(client, fcm):
    from app.models import PushDevice
    master_h, _ = register_master(client, city="Кызыл-Кия")
    client.post("/notifications/devices", headers=master_h, json={"token": "dead-token-0123456789-bbbb"})
    create_request(client, city="Кызыл-Кия")
    db = SessionLocal()
    assert db.query(PushDevice).filter(PushDevice.token == "dead-token-0123456789-bbbb").count() == 0
    db.close()


def test_no_push_without_firebase(client, outbox, monkeypatch):
    from app import push
    called = []
    monkeypatch.setattr(push.httpx, "post", lambda *a, **k: called.append(1))
    master_h, _ = register_master(client, city="Майлуу-Суу")
    client.post("/notifications/devices", headers=master_h, json={"token": "phone-token-0123456789-cccc"})
    create_request(client, city="Майлуу-Суу")
    assert called == [] and client.get("/config").json()["push_enabled"] is False


def test_sms_channel_hidden_without_gateway(client):
    assert client.get("/config").json()["sms_enabled"] is False
    phone = new_phone()
    email = email_for(phone)
    r = client.post("/auth/register", json={
        "name": "M", "phone": phone, "password": "secret123", "city": "Бишкек", "email": email,
        "code": email_code(client, email), "accept_agreement": True, "notify_enabled": True, "notify_channel": "sms"})
    assert r.status_code == 400 and "SMS" in r.json()["detail"]
    master_h, _ = register_master(client)
    assert client.get("/notifications/settings", headers=master_h).json()["sms_available"] is False
    assert client.put("/notifications/settings", headers=master_h,
                      json={"enabled": True, "channel": "sms", "lang": "ru"}).status_code == 400


def test_telegram_linking(client, outbox, monkeypatch):
    from app.config import settings
    master_h, _ = register_master(client, notify_enabled=True, channel="email", city="Баткен")
    assert client.post("/notifications/telegram/link", headers=master_h).status_code == 400  # no bot yet

    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(settings, "telegram_bot_username", "test_bot")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "hook-secret")
    url = client.post("/notifications/telegram/link", headers=master_h).json()["url"]
    assert url.startswith("https://t.me/test_bot?start=")
    token = url.split("start=")[1]
    update = {"update_id": 1, "message": {"chat": {"id": 555}, "text": f"/start {token}"}}
    assert client.post("/telegram/webhook", json=update).status_code == 404
    assert client.post("/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 404
    assert client.post("/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}).status_code == 200
    assert outbox[-1][:2] == ("telegram", "555") and "Готово" in outbox[-1][2]
    assert client.get("/notifications/settings", headers=master_h).json()["telegram_connected"] is True

    outbox.clear()  # the link is single-use
    client.post("/telegram/webhook", json={**update, "message": {"chat": {"id": 999}, "text": f"/start {token}"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"})
    assert outbox[-1][1] == "999" and "Подключить Telegram" in outbox[-1][2]

    outbox.clear()
    create_request(client, description="Срочно", city="Баткен")
    assert any(m[0] == "telegram" and m[1] == "555" and "Срочно" in m[2] for m in outbox)


# ---------- cities ----------

def test_master_must_choose_city(client):
    phone = new_phone()
    email = email_for(phone)
    base = {"name": "M", "phone": phone, "password": "secret123", "email": email, "code": email_code(client, email),
            "accept_agreement": True}
    r = client.post("/auth/register", json=base)
    assert r.status_code == 400 and "город" in r.json()["detail"]
    assert client.post("/auth/register", json={**base, "city": "Москва"}).status_code == 422
    r = client.post("/auth/register", json={**base, "city": "ош"})  # case-insensitive -> canonical
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/masters/me", headers=headers).json()["city"] == "Ош"


def test_requests_follow_place_and_region(client, outbox):
    """Same place always; the whole region only for masters who accept it (Иссык-Кульская область)."""
    region_h, region_phone = register_master(client, notify_enabled=True, city="Каракол")  # serves the region (default)
    local_h, local_phone = register_master(client, notify_enabled=True, city="Балыкчы")
    client.patch("/masters/me", headers=local_h, json={"serves_region": False})
    bishkek_h, _ = register_master(client, notify_enabled=True, city="Бишкек")
    me = client.get("/masters/me", headers=region_h).json()
    assert me["region"] == "Иссык-Кульская область" and me["serves_region"] is True

    outbox.clear()
    _, req = create_request(client, city="Чолпон-Ата", description="Заказ")
    assert req["id"] in [o["id"] for o in client.get("/orders/feed", headers=region_h).json()]
    assert req["id"] not in [o["id"] for o in client.get("/orders/feed", headers=local_h).json()]
    assert req["id"] not in [o["id"] for o in client.get("/orders/feed", headers=bishkek_h).json()]
    assert [m[1] for m in outbox] == [email_for(region_phone)]
    assert client.post(f"/orders/{req['id']}/offer", headers=local_h, json={"price": 1}).status_code == 403
    assert client.post(f"/orders/{req['id']}/offer", headers=region_h, json={"price": 1}).status_code == 200

    outbox.clear()
    _, req = create_request(client, city="Балыкчы")
    assert sorted(m[1] for m in outbox) == sorted([email_for(region_phone), email_for(local_phone)])

    # moving to another place moves the master to its region
    client.patch("/masters/me", headers=local_h, json={"city": "Ош"})
    assert client.get("/masters/me", headers=local_h).json()["region"] == "г. Ош"

    client.patch("/masters/me", headers=region_h, json={"city": ""})  # no place -> no requests
    assert client.get("/orders/feed", headers=region_h).json() == []


def test_regions_and_places(client):
    cfg = client.get("/config").json()
    regions = {r["name"]: r for r in cfg["regions"]}
    assert len(regions) == 9 and "Чуйская область" in regions and regions["Чуйская область"]["ky"] == "Чүй облусу"
    chuy = regions["Чуйская область"]
    assert "Токмок" in chuy["cities"] and "Сокулук" in chuy["villages"]
    assert any(d["name"] == "Сокулукский район" for d in chuy["districts"])
    assert "Каракол" in cfg["cities"] and "Сокулукский район" in cfg["cities"]

    # a district or a village can be chosen as the place
    _, req = create_request(client, city="сокулукский район")
    assert req["city"] == "Сокулукский район"
    _, req = create_request(client, city="Сокулук")
    from app.models import Order
    db = SessionLocal()
    assert db.get(Order, req["id"]).region == "Чуйская область"
    db.close()

    # search by place includes masters of the region who take its requests
    master_h, _ = register_master(client, "Областной", city="Кант")
    names = [m["user"]["name"] for m in client.get("/masters", params={"city": "Сокулук"}).json()]
    assert "Областной" in names
    client.patch("/masters/me", headers=master_h, json={"serves_region": False})
    names = [m["user"]["name"] for m in client.get("/masters", params={"city": "Сокулук"}).json()]
    assert "Областной" not in names


def test_old_city_spelling_is_fixed(client, tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from app.cities import normalize_city
    from app.migrations import _config
    assert normalize_city("Арван") == "Араван" and normalize_city("АРАВАН") == "Араван"
    engine = create_engine(f"sqlite:///{tmp_path}/aravan.db")
    cfg = _config(engine)
    command.upgrade(cfg, "0010")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, name, phone, password_hash, role, status, created_at) "
                          "VALUES (1, 'M', '+996700000001', 'x', 'master', 'active', '2026-01-01')"))
        conn.execute(text("INSERT INTO masters (id, user_id, city, rating, completed_orders, verified, created_at, "
                          "missed_requests, rating_penalty, location_shared) "
                          "VALUES (1, 1, 'Арван', 0, 0, 0, '2026-01-01', 0, 0, 0)"))
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert tuple(conn.execute(text("SELECT city, region, serves_region FROM masters")).one()) == ("Араван", "Ошская область", 1)



# ---------- paid plan (off by default) ----------

def test_subscriptions_disabled_by_default(client):
    master_h, _ = register_master(client)
    sub = client.get("/subscription/me", headers=master_h).json()
    assert sub["enabled"] is False and sub["state"] == "disabled"
    assert client.get("/orders/feed", headers=master_h).status_code == 200
    assert client.post("/subscription/me/checkout", headers=master_h, json={"months": 1}).status_code == 400


def test_subscription_flow(client, monkeypatch):
    from datetime import timedelta

    from app.config import settings
    from app.models import MasterSubscription, utcnow
    monkeypatch.setattr(settings, "subscriptions_enabled", True)
    monkeypatch.setattr(settings, "subscription_price", 500)

    master_h, _ = register_master(client, "Подписчик")
    mid = master_id_of(client, master_h)
    sub = client.get("/subscription/me", headers=master_h).json()
    assert sub["state"] == "trial" and sub["days_left"] == 30
    assert {p["months"]: p["price"] for p in sub["plans"]} == {1: 500, 3: 1350, 6: 2550, 12: 4500}

    db = SessionLocal()
    db.query(MasterSubscription).filter(MasterSubscription.master_id == mid).update(
        {MasterSubscription.trial_ends_at: utcnow() - timedelta(days=1)})
    db.commit()
    db.close()
    _, req = create_request(client)
    assert client.get("/orders/feed", headers=master_h).status_code == 402
    assert client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 1}).status_code == 402
    assert mid not in [m["id"] for m in client.get("/masters", params={"q": "Подписчик"}).json()]

    payment = client.post("/subscription/me/checkout", headers=master_h, json={"months": 3}).json()["payment"]
    assert payment["amount"] == 1350 and payment["status"] == "pending"
    assert client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=master_h).status_code == 403
    admin_h = make_admin(client)
    assert client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=admin_h).status_code == 200
    assert client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=admin_h).status_code == 400
    sub = client.get("/subscription/me", headers=master_h).json()
    assert sub["state"] == "active" and 89 <= sub["days_left"] <= 90
    assert client.get("/orders/feed", headers=master_h).status_code == 200
    assert client.post("/payments/webhook/manual", content=b"{}").status_code == 404


def test_existing_masters_get_trial_when_enabled(client, monkeypatch):
    from app.config import settings
    from app.models import Master
    from app.subscriptions import ensure_all_masters
    register_master(client, "Старый мастер")
    monkeypatch.setattr(settings, "subscriptions_enabled", True)
    db = SessionLocal()
    ensure_all_masters(db)
    assert db.query(Master).filter(~Master.subscription.has()).count() == 0
    db.close()


# ---------- configuration & migrations ----------

def test_admin_from_settings(client):
    from app.create_admin import ensure_admin
    phone = new_phone()
    assert ensure_admin(phone, "first-password") is True
    assert ensure_admin(phone, "other-password") is False  # restarts don't reset the password
    db = SessionLocal()
    user = db.query(User).filter(User.phone == phone).one()
    db.close()
    assert user.role == UserRole.admin
    from app.auth import verify_password
    assert verify_password("first-password", user.password_hash)


def test_production_refuses_unsafe_settings(monkeypatch):
    from app.config import settings
    from app.main import check_production_settings
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError) as e:
        check_production_settings()
    assert "EMAIL_PROVIDER=console" in str(e.value) and "CORS_ORIGINS" in str(e.value)


def test_models_match_migrations():
    """Every model change must come with an Alembic migration (cd backend && alembic revision --autogenerate)."""
    from alembic import command

    from app.database import engine
    from app.migrations import _config
    command.check(_config(engine))


def test_pre_migration_database_is_upgraded(tmp_path):
    """A database created with create_all before migrations existed — and before some tables did."""
    from alembic import command
    from sqlalchemy import create_engine, inspect, text

    from app.migrations import _config, upgrade_database
    legacy = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    command.upgrade(_config(legacy), "0001")
    with legacy.begin() as conn:  # an old create_all database: no history, no paid-plan tables yet
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("DROP TABLE subscription_payments"))
        conn.execute(text("DROP TABLE master_subscriptions"))
        conn.execute(text("INSERT INTO categories (name) VALUES ('Сантехника')"))

    upgrade_database(legacy)

    tables = set(inspect(legacy).get_table_names())
    assert {"master_subscriptions", "subscription_payments", "verification_codes", "request_deliveries"} <= tables
    with legacy.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM categories")).scalar() == 1  # data kept


def test_city_migration_backfills_old_data(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from app.migrations import _config
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    cfg = _config(engine)
    command.upgrade(cfg, "0003")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO categories (id, name) VALUES (1, 'Сантехника')"))
        conn.execute(text("INSERT INTO users (id, name, phone, password_hash, role, status, created_at) "
                          "VALUES (1, 'M', '+996700000001', 'x', 'master', 'active', '2026-01-01')"))
        conn.execute(text("INSERT INTO masters (id, user_id, city, rating, completed_orders, verified, created_at) "
                          "VALUES (1, 1, ' бишкек ', 0, 0, 0, '2026-01-01')"))
        conn.execute(text("INSERT INTO orders (id, client_id, category_id, description, address, status, created_at) VALUES "
                          "(1, 1, 1, 'a', 'Ош, ул. Ленина 1', 'searching', '2026-01-01'), "
                          "(2, 1, 1, 'b', 'ул. Без города', 'searching', '2026-01-01')"))
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT city FROM masters WHERE id = 1")).scalar() == "Бишкек"
        assert dict(conn.execute(text("SELECT id, city FROM orders")).all()) == {1: "Ош", 2: None}
        # existing masters get a clean reliability record
        assert tuple(conn.execute(text("SELECT missed_requests, rating_penalty FROM masters WHERE id = 1")).one()) == (0, 0)
        # regions are filled in from the places (migration 0011)
        assert conn.execute(text("SELECT region FROM masters WHERE id = 1")).scalar() == "г. Бишкек"
        assert dict(conn.execute(text("SELECT id, region FROM orders")).all()) == {1: "г. Ош", 2: None}


def test_email_migration_normalises_and_dedupes(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from app.migrations import _config
    engine = create_engine(f"sqlite:///{tmp_path}/emails.db")
    cfg = _config(engine)
    command.upgrade(cfg, "0004")
    with engine.begin() as conn:
        for uid, phone, email in [(1, "+996700000001", " Usta@Mail.COM "), (2, "+996700000002", "usta@mail.com"),
                                  (3, "+996700000003", None)]:
            conn.execute(text("INSERT INTO users (id, name, phone, email, password_hash, role, status, created_at) "
                              "VALUES (:id, 'U', :phone, :email, 'x', 'client', 'active', '2026-01-01')"),
                         {"id": uid, "phone": phone, "email": email})
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT id, email FROM users")).all()) == {1: "usta@mail.com", 2: None, 3: None}


def test_seo_pages(client):
    """Server-rendered pages for search engines: service × place, master profiles, sitemap, robots."""
    import re
    master_h, _ = register_master(client, "Сео <b>Мастер</b>", city="Токмок")
    mid = master_id_of(client, master_h)

    r = client.get("/uslugi/santehnik/tokmok")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    page = r.text
    assert "<h1>Сантехник в Токмоке</h1>" in page
    assert "Сео &lt;b&gt;Мастер&lt;/b&gt;" in page and "<b>Мастер" not in page  # names escaped
    assert f'href="/master/{mid}"' in page and "от 500 сом" in page
    assert '"@type": "FAQPage"' in page and 'content="index, follow"' in page
    assert re.search(r'<link rel="canonical" href="[^"]+/uslugi/santehnik/tokmok"', page)
    # the region's masters cover its villages too; an empty village page isn't indexed
    village = client.get("/uslugi/santehnik/lebedinovka").text
    assert "в селе Лебединовка" in village and "Сео &lt;b&gt;" in village
    assert 'content="noindex, follow"' in client.get("/uslugi/master-krasoty/lebedinovka").text  # nobody there
    assert "<h1>Сантехник в Чуйской области</h1>" in client.get("/uslugi/santehnik/chuyskaya-oblast").text
    assert "Аламудунском районе" in client.get("/uslugi/elektrik/alamudunskiy-rayon").text
    assert client.get("/uslugi/santehnik/nowhere").status_code == 404
    assert client.get("/uslugi/unknown").status_code == 404
    assert client.get("/uslugi/santehnik").status_code == 200 and client.get("/uslugi").status_code == 200

    profile = client.get(f"/master/{mid}").text
    assert "Сео &lt;b&gt;Мастер&lt;/b&gt;" in profile and "Замена смесителя" in profile and '"@type": "Plumber"' in profile
    assert client.get("/master/999999").status_code == 404

    sitemap = client.get("/sitemap.xml").text
    assert "/uslugi/santehnik/bishkek</loc>" in sitemap and f"/master/{mid}</loc>" in sitemap
    assert "/uslugi/santehnik/tokmok</loc>" in sitemap
    robots = client.get("/robots.txt").text
    assert "Disallow: /order-detail.html" in robots and "Sitemap: " in robots
    links = client.get("/.well-known/assetlinks.json").json()
    assert links[0]["target"]["package_name"] == "kg.masterryadom.app"


def test_seo_hides_blocked_and_unverified_masters(client, monkeypatch):
    from app.config import settings
    master_h, _ = register_master(client, "Непроверенный", city="Нарын")
    mid = master_id_of(client, master_h)
    monkeypatch.setattr(settings, "masters_require_verification", True)
    assert client.get(f"/master/{mid}").status_code == 404
    assert "Непроверенный" not in client.get("/uslugi/santehnik/naryn").text
    assert f"/master/{mid}<" not in client.get("/sitemap.xml").text


def test_seo_slugs_unique():
    from app.cities import CITIES
    from app.seed import DEFAULT_CATEGORIES
    from app.seo_content import PLACE_SLUG, category_info, locative
    assert all(c in PLACE_SLUG for c in CITIES)
    slugs = [category_info(c)["slug"] for c in DEFAULT_CATEGORIES]
    assert len(slugs) == len(set(slugs))
    assert locative("Бишкек") == "в Бишкеке" and locative("Иссык-Кульская область") == "в Иссык-Кульской области"


def test_redirect_to_new_domain(monkeypatch):
    """REDIRECT_TO on the old deployment sends everything to the new address."""
    import importlib
    import app.main as main_module
    from app.config import settings
    monkeypatch.setattr(settings, "redirect_to", "https://new.example.kg/")
    try:
        moved = importlib.reload(main_module).app
        with TestClient(moved) as c:
            r = c.get("/uslugi/santehnik/bishkek?x=1", follow_redirects=False)
            assert r.status_code == 301 and r.headers["location"] == "https://new.example.kg/uslugi/santehnik/bishkek?x=1"
            assert c.get("/health").status_code == 200
    finally:
        monkeypatch.setattr(settings, "redirect_to", "")
        importlib.reload(main_module)


def _race(funcs):
    """Run the calls at the same moment in threads; returns their results or exceptions."""
    import threading
    barrier = threading.Barrier(len(funcs))
    results = [None] * len(funcs)

    def run(i, fn):
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as exc:  # 409/400 from the losers; "database is locked" on SQLite
            results[i] = exc
    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(funcs)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return results


def test_stale_order_change_is_refused(client):
    """A check made before another request committed doesn't let a write through (the core of every race fix)."""
    from fastapi import HTTPException
    from app.models import Order, OrderStatus
    from app.routers.orders import OFFERABLE_STATUSES, move_order
    master_h, _ = register_master(client)
    token, req = create_request(client)
    offer = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 700}).json()

    stale = SessionLocal()
    order = stale.get(Order, req["id"])
    assert order.status == OrderStatus.offers_received
    assert client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]}).status_code == 200
    with pytest.raises(HTTPException) as err:  # the master's late offer saw "offers_received" a moment ago
        move_order(stale, order, OFFERABLE_STATUSES, status=OrderStatus.offers_received)
    assert err.value.status_code == 409
    stale.close()
    other_h, _ = register_master(client)
    assert client.post(f"/orders/{req['id']}/offer", headers=other_h, json={"price": 1}).status_code == 400
    assert client.get(f"/requests/{token}").json()["status"] == "master_selected"


def test_simultaneous_accepts_pick_one_master(client):
    from app.models import OfferStatus, Order, OrderOffer
    from app.routers.requests import accept_offer
    token, req = create_request(client)
    offers = []
    for price in (500, 600, 700):
        h, _ = register_master(client)
        offers.append(client.post(f"/orders/{req['id']}/offer", headers=h, json={"price": price}).json())

    def accept(offer_id):
        def call():
            db = SessionLocal()
            try:
                return accept_offer(token, offer_id, db)
            finally:
                db.close()
        return call
    results = _race([accept(o["id"]) for o in offers])
    assert sum(not isinstance(r, Exception) for r in results) == 1, results

    db = SessionLocal()
    order = db.get(Order, req["id"])
    accepted = db.query(OrderOffer).filter(OrderOffer.order_id == order.id, OrderOffer.status == OfferStatus.accepted).all()
    assert len(accepted) == 1 and order.master_id == accepted[0].master_id and order.price == accepted[0].price
    db.close()


def test_double_review_counts_once(client):
    from app.models import Master, Review
    from app.routers.requests import review
    from app.schemas import ReviewCreate
    master_h, _ = register_master(client)
    token, req = create_request(client)
    offer = client.post(f"/orders/{req['id']}/offer", headers=master_h, json={"price": 700}).json()
    client.post(f"/requests/{token}/accept", params={"offer_id": offer["id"]})
    client.post(f"/orders/{req['id']}/status", headers=master_h, json={"status": "in_progress"})
    assert client.post(f"/requests/{token}/complete").status_code == 200

    def leave():
        db = SessionLocal()
        try:
            return review(token, ReviewCreate(rating=5), db)
        finally:
            db.close()
    results = _race([leave, leave, leave])
    assert sum(not isinstance(r, Exception) for r in results) == 1, results
    db = SessionLocal()
    master = db.get(Master, master_id_of(client, master_h))
    assert db.query(Review).filter(Review.order_id == req["id"]).count() == 1 and master.completed_orders == 1
    db.close()


def test_answer_and_miss_dont_overwrite_each_other(client):
    """A master who answered isn't fined by a timer check that read the request a moment earlier."""
    from app.dispatch import check_missed, mark_answered
    from app.models import Master, RequestDelivery
    master_h, _ = register_master(client)
    _, req = create_request(client)
    mid = master_id_of(client, master_h)
    expire_deliveries(req["id"])

    db = SessionLocal()
    mark_answered(db, req["id"], mid, "declined")  # answered just before the check commits its "missed"
    db.commit()
    db.close()
    run_missed_check()
    db = SessionLocal()
    delivery = db.query(RequestDelivery).filter_by(order_id=req["id"], master_id=mid).one()
    assert delivery.status == "declined" and db.get(Master, mid).missed_requests == 0
    db.close()
