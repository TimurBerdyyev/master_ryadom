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

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


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


def test_feed_and_notifications_follow_city(client, outbox):
    bishkek_h, _ = register_master(client, notify_enabled=True, city="Бишкек")
    osh_h, osh_phone = register_master(client, notify_enabled=True, city="Кара-Балта")
    outbox.clear()
    _, req = create_request(client, city="Кара-Балта", description="Заказ")
    assert req["id"] in [o["id"] for o in client.get("/orders/feed", headers=osh_h).json()]
    assert req["id"] not in [o["id"] for o in client.get("/orders/feed", headers=bishkek_h).json()]
    assert [m[1] for m in outbox] == [email_for(osh_phone)]

    client.patch("/masters/me", headers=osh_h, json={"city": ""})  # no city -> no requests
    assert client.get("/orders/feed", headers=osh_h).json() == []
    assert "Каракол" in client.get("/config").json()["cities"]


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
