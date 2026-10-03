"""End-to-end API tests: the full order flow plus access-control / injection checks.

Run from backend/:  python -m pytest tests -q
Uses a throwaway SQLite database, so no Postgres is needed.
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


_counter = iter(range(100, 1000))


def sms_code(client, phone, purpose="register"):
    """The dev SMS provider (SMS_PROVIDER=console) echoes the code back in the response."""
    r = client.post("/auth/send-code", json={"phone": phone, "purpose": purpose})
    assert r.status_code == 200, r.text
    return r.json()["debug_code"]


def register(client, role="client", name="Тест"):
    phone = f"+996700000{next(_counter)}"
    r = client.post("/auth/register", json={"name": name, "phone": phone, "password": "secret123", "role": role,
                                            "code": sms_code(client, phone),
                                            "city": "Бишкек" if role == "master" else None,
                                            "email": f"m{phone[-4:]}@example.com" if role == "master" else None})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, phone


def category_id(client, name="Сантехника"):
    return next(c["id"] for c in client.get("/categories").json() if c["name"] == name)


def make_master(client, name="Иван Сантехник", category="Сантехника"):
    headers, phone = register(client, "master", name)
    r = client.post("/masters/me/services", headers=headers,
                    json={"category_id": category_id(client, category), "title": "Замена смесителя", "price_from": 500})
    assert r.status_code == 200, r.text
    client.patch("/masters/me", headers=headers, json={"city": "Бишкек", "description": "Опытный мастер"})
    return headers, phone


def test_full_order_flow(client):
    client_h, client_phone = register(client)
    master_h, master_phone = make_master(client)
    other_master_h, _ = make_master(client, "Пётр")

    order = client.post("/orders", headers=client_h,
                        json={"city": "Бишкек", "category_id": category_id(client), "description": "Течёт кран", "address": "ул. Ленина 1"}).json()

    feed = client.get("/orders/feed", headers=master_h).json()
    item = next(o for o in feed if o["id"] == order["id"])
    assert "address" not in item and "client_id" not in item  # no client data before selection

    offer = client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": 800, "comment": "Приеду через час"}).json()
    client.post(f"/orders/{order['id']}/offer", headers=other_master_h, json={"price": 900})

    # repeated offer updates instead of duplicating
    client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": 700})
    offers = client.get(f"/orders/{order['id']}/offers", headers=client_h).json()
    assert len(offers) == 2 and offers[0]["price"] == 700 and offers[0]["master"]["name"] == "Иван Сантехник"

    # a master sees only their own offer
    assert len(client.get(f"/orders/{order['id']}/offers", headers=other_master_h).json()) == 1

    # no contacts before selection
    assert client.get(f"/orders/{order['id']}", headers=client_h).json()["master_contact"] is None

    r = client.post(f"/orders/{order['id']}/accept", params={"offer_id": offer["id"]}, headers=client_h)
    assert r.status_code == 200 and r.json()["status"] == "master_selected"
    assert r.json()["master_contact"]["phone"] == master_phone
    assert client.get(f"/orders/{order['id']}", headers=master_h).json()["client_contact"]["phone"] == client_phone

    # the rejected master loses access
    assert client.get(f"/orders/{order['id']}", headers=other_master_h).status_code == 403

    # status may only move forward
    assert client.post(f"/orders/{order['id']}/status", headers=master_h, json={"status": "in_progress"}).status_code == 200
    assert client.post(f"/orders/{order['id']}/status", headers=master_h, json={"status": "master_confirmed"}).status_code == 400
    assert client.post(f"/orders/{order['id']}/status", headers=other_master_h, json={"status": "completed"}).status_code == 403
    assert client.post(f"/orders/{order['id']}/status", headers=master_h, json={"status": "completed"}).status_code == 200

    r = client.post(f"/orders/{order['id']}/review", headers=client_h, json={"rating": 5, "text": "Отлично"})
    assert r.status_code == 200
    assert client.post(f"/orders/{order['id']}/review", headers=client_h, json={"rating": 1}).status_code == 400

    master_id = offer["master_id"]
    m = client.get(f"/masters/{master_id}").json()
    assert m["rating"] == 5 and m["completed_orders"] == 1

    notes = client.get("/notifications", headers=client_h).json()
    assert any("предлагает" in (n["text"] or "") for n in notes)
    assert client.get("/notifications/unread-count", headers=client_h).json()["unread"] > 0
    client.post("/notifications/read-all", headers=client_h)
    assert client.get("/notifications/unread-count", headers=client_h).json()["unread"] == 0


def test_search(client):
    make_master(client, "Алмаз Электрик", "Электрика")
    names = lambda params: [m["user"]["name"] for m in client.get("/masters", params=params).json()]
    assert "Алмаз Электрик" in names({"q": "электр"})          # category name, case-insensitive Cyrillic
    assert "Алмаз Электрик" in names({"q": "смесител"})        # service title
    assert "Алмаз Электрик" not in names({"q": "zzz"})
    assert names({"q": "%"}) == []                             # LIKE wildcard is literal
    assert names({"q": "' OR 1=1 --"}) == []                   # SQL injection is just text

    # several services in one category -> master appears once
    h, _ = make_master(client, "Дубль")
    client.post("/masters/me/services", headers=h, json={"category_id": category_id(client), "title": "Ещё", "price_from": 1})
    ids = [m["id"] for m in client.get("/masters", params={"category_id": category_id(client)}).json()]
    assert len(ids) == len(set(ids))

    # anonymous users never see phones
    assert all(m["user"]["phone"] is None for m in client.get("/masters").json())


def test_offer_restrictions(client):
    client_h, _ = register(client)
    electrician_h, _ = make_master(client, "Электрик", "Электрика")
    order = client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"}).json()

    # wrong category
    assert client.post(f"/orders/{order['id']}/offer", headers=electrician_h, json={"price": 1}).status_code == 403
    # clients can't offer
    assert client.post(f"/orders/{order['id']}/offer", headers=client_h, json={"price": 1}).status_code == 403
    # master can't offer on own order
    master_h, _ = make_master(client)
    own = client.post("/orders", headers=master_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"}).json()
    assert client.post(f"/orders/{own['id']}/offer", headers=master_h, json={"price": 1}).status_code == 400
    # negative price rejected
    assert client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": -5}).status_code == 422


def test_idor(client):
    owner_h, _ = register(client)
    stranger_h, _ = register(client)
    master_h, _ = make_master(client)
    order = client.post("/orders", headers=owner_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"}).json()
    offer = client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": 100}).json()

    oid = order["id"]
    assert client.get(f"/orders/{oid}", headers=stranger_h).status_code == 403
    assert client.get(f"/orders/{oid}/offers", headers=stranger_h).status_code == 403
    assert client.post(f"/orders/{oid}/accept", params={"offer_id": offer["id"]}, headers=stranger_h).status_code == 403
    assert client.post(f"/orders/{oid}/cancel", headers=stranger_h).status_code == 403
    assert client.post(f"/orders/{oid}/status", headers=stranger_h, json={"status": "completed"}).status_code == 403
    assert client.post(f"/orders/{oid}/photos", headers=stranger_h, files={"files": ("a.png", PNG, "image/png")}).status_code == 403
    # stranger can't learn the order exists through a complaint
    assert client.post("/complaints", headers=stranger_h, json={"text": "x", "order_id": oid}).status_code == 404
    # client can't jump the order to "completed" before a master works on it
    assert client.post(f"/orders/{oid}/status", headers=owner_h, json={"status": "completed"}).status_code == 400


def test_auth_hardening(client):
    # cannot self-register as admin
    r = client.post("/auth/register", json={"name": "x", "phone": "+996711111111", "password": "secret123", "role": "admin",
                                            "code": sms_code(client, "+996711111111")})
    assert r.status_code == 422

    # phone normalization: formatted and plain are the same account
    r = client.post("/auth/register", json={"name": "x", "phone": "+996 722 333 444", "password": "secret123",
                                            "code": sms_code(client, "+996722333444")})
    assert r.status_code == 200
    assert client.post("/auth/send-code", json={"phone": "+996 722 333 444", "purpose": "register"}).status_code == 400
    assert client.post("/auth/login", json={"phone": "+996-722-333-444", "password": "secret123"}).status_code == 200
    assert client.post("/auth/send-code", json={"phone": "abc", "purpose": "register"}).status_code == 422

    # forged / tampered tokens
    forged = jwt.encode({"sub": "1"}, "wrong-secret-of-a-sufficient-length-0123456789", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': '1'})}."  # "alg: none" attack
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401

    # non-admins get 403 on admin API
    h, _ = register(client)
    for path in ["/admin/stats", "/admin/users", "/admin/orders", "/admin/complaints"]:
        assert client.get(path, headers=h).status_code == 403
    assert client.get("/admin/stats").status_code == 401

    # brute force is rate limited
    rate_limit._attempts.clear()
    codes = [client.post("/auth/login", json={"phone": "+996700000001", "password": "bad"}).status_code for _ in range(11)]
    assert codes[-1] == 429


def test_blocked_user(client):
    h, phone = register(client)
    db = SessionLocal()
    admin = User(name="A", phone="+996799999999", password_hash=hash_password("adminpass"), role=UserRole.admin)
    db.add(admin)
    db.commit()
    db.close()
    admin_h = {"Authorization": "Bearer " + client.post("/auth/login", json={"phone": "+996799999999", "password": "adminpass"}).json()["access_token"]}

    user_id = client.get("/auth/me", headers=h).json()["id"]
    assert client.patch(f"/admin/users/{user_id}/status", headers=admin_h, json={"status": "blocked"}).status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 403
    # blocked users are treated as anonymous: no phones
    assert all(m["user"]["phone"] is None for m in client.get("/masters", headers=h).json())


def test_uploads(client):
    h, _ = register(client)
    order = client.post("/orders", headers=h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"}).json()
    url = f"/orders/{order['id']}/photos"

    # HTML disguised as PNG is rejected
    r = client.post(url, headers=h, files={"files": ("x.png", b"<script>alert(1)</script>", "image/png")})
    assert r.status_code == 400
    assert client.post(url, headers=h, files={"files": ("x.svg", b"<svg/>", "image/svg+xml")}).status_code == 400

    r = client.post(url, headers=h, files={"files": ("x.png", PNG, "image/png")})
    assert r.status_code == 200
    photo_url = r.json()[0]["url"]
    served = client.get(photo_url)
    assert served.status_code == 200
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in served.headers["content-security-policy"]

    # path traversal through the static mount
    assert client.get("/uploads/../app/config.py").status_code == 404


def test_validation(client):
    h, _ = register(client)
    cid = category_id(client)
    assert client.post("/orders", headers=h, json={"city": "Бишкек", "category_id": cid, "description": "x", "latitude": 999}).status_code == 422
    assert client.post("/orders", headers=h, json={"city": "Бишкек", "category_id": cid, "description": "x", "time": "<b>"}).status_code == 422
    assert client.post("/orders", headers=h, json={"city": "Бишкек", "category_id": cid, "description": ""}).status_code == 422
    assert client.get("/masters", params={"sort": "drop table"}).status_code == 422


def test_no_n_plus_one_queries(client):
    """Listing endpoints must use a constant number of queries, not one per master/order."""
    from sqlalchemy import event

    from app.database import engine

    for i in range(15):
        rate_limit._attempts.clear()  # many sign-ups from one "IP" in a single test
        make_master(client, f"Мастер {i}")
    rate_limit._attempts.clear()
    client_h, _ = register(client)
    for _ in range(10):
        client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"})
    master_h, _ = make_master(client)

    count = {"n": 0}

    def on_query(*_args):
        count["n"] += 1

    event.listen(engine, "before_cursor_execute", on_query)
    try:
        for path, headers in [("/masters", {}), ("/orders/feed", master_h), ("/orders", client_h)]:
            count["n"] = 0
            assert client.get(path, headers=headers).status_code == 200
            assert count["n"] <= 10, f"{path}: {count['n']} SQL queries"
    finally:
        event.remove(engine, "before_cursor_execute", on_query)


# ---------- master subscriptions ----------

def make_admin(client):
    db = SessionLocal()
    phone = f"+99679{next(_counter):07d}"
    db.add(User(name="Admin", phone=phone, password_hash=hash_password("adminpass"), role=UserRole.admin))
    db.commit()
    db.close()
    token = client.post("/auth/login", json={"phone": phone, "password": "adminpass"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def expire_trial(master_id, days_ago=1):
    from datetime import timedelta

    from app.models import MasterSubscription, utcnow
    db = SessionLocal()
    sub = db.query(MasterSubscription).filter(MasterSubscription.master_id == master_id).one()
    sub.trial_ends_at = utcnow() - timedelta(days=days_ago)
    db.commit()
    db.close()


def test_subscriptions_disabled_by_default(client):
    from app.config import settings
    assert settings.subscriptions_enabled is False
    assert client.get("/config").json()["subscriptions_enabled"] is False

    master_h, _ = make_master(client)
    sub = client.get("/subscription/me", headers=master_h).json()
    assert sub["enabled"] is False and sub["state"] == "disabled"
    assert client.get("/orders/feed", headers=master_h).status_code == 200
    assert client.post("/subscription/me/checkout", headers=master_h, json={"months": 1}).status_code == 400


def test_subscription_flow(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "subscriptions_enabled", True)
    monkeypatch.setattr(settings, "subscription_price", 500)

    master_h, _ = make_master(client, "Подписчик")
    sub = client.get("/subscription/me", headers=master_h).json()
    assert sub["state"] == "trial" and sub["days_left"] == 30
    assert {p["months"]: p["price"] for p in sub["plans"]} == {1: 500, 3: 1350, 6: 2550, 12: 4500}
    master_id = client.get("/masters/me", headers=master_h).json()["id"]
    assert master_id in [m["id"] for m in client.get("/masters", params={"q": "Подписчик"}).json()]

    # clients have no subscription page
    client_h, _ = register(client)
    assert client.get("/subscription/me", headers=client_h).status_code == 403

    # trial over -> no feed, no offers, hidden from search
    order = client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"}).json()
    expire_trial(master_id)
    assert client.get("/subscription/me", headers=master_h).json()["state"] == "expired"
    assert client.get("/orders/feed", headers=master_h).status_code == 402
    assert client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": 1}).status_code == 402
    assert master_id not in [m["id"] for m in client.get("/masters", params={"q": "Подписчик"}).json()]

    # checkout: invalid plan, then a new request replaces the previous pending one
    assert client.post("/subscription/me/checkout", headers=master_h, json={"months": 2}).status_code == 400
    first = client.post("/subscription/me/checkout", headers=master_h, json={"months": 1}).json()
    r = client.post("/subscription/me/checkout", headers=master_h, json={"months": 3})
    assert r.status_code == 200 and r.json()["payment_url"] is None  # manual provider
    payment = r.json()["payment"]
    assert payment["amount"] == 1350 and payment["status"] == "pending"
    statuses = {p["id"]: p["status"] for p in client.get("/subscription/me", headers=master_h).json()["payments"]}
    assert statuses[first["payment"]["id"]] == "cancelled"

    # only an admin confirms payments
    assert client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=master_h).status_code == 403
    admin_h = make_admin(client)
    pending = client.get("/admin/subscription-payments", params={"status": "pending"}, headers=admin_h).json()
    assert payment["id"] in [p["id"] for p in pending]
    assert client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=admin_h).status_code == 200
    assert client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=admin_h).status_code == 400

    sub = client.get("/subscription/me", headers=master_h).json()
    assert sub["state"] == "active" and 89 <= sub["days_left"] <= 90
    assert client.get("/orders/feed", headers=master_h).status_code == 200
    assert master_id in [m["id"] for m in client.get("/masters", params={"q": "Подписчик"}).json()]

    # admin can grant months; they stack on top of the paid period
    assert client.post(f"/admin/subscriptions/{master_id}/extend", headers=admin_h, json={"months": 1}).status_code == 200
    assert 119 <= client.get("/subscription/me", headers=master_h).json()["days_left"] <= 120
    rows = client.get("/admin/subscriptions", headers=admin_h).json()
    assert any(r["master_id"] == master_id and r["state"] == "active" for r in rows)

    # webhook: unknown provider / manual provider accept nothing
    assert client.post("/payments/webhook/nope", content=b"{}").status_code == 404
    assert client.post("/payments/webhook/manual", content=b"{}").status_code == 404


def test_paying_during_trial_keeps_remaining_days(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "subscriptions_enabled", True)
    master_h, _ = make_master(client)
    payment = client.post("/subscription/me/checkout", headers=master_h, json={"months": 1}).json()["payment"]
    client.post(f"/admin/subscription-payments/{payment['id']}/confirm", headers=make_admin(client))
    # 30 trial days left + 30 paid days
    assert 59 <= client.get("/subscription/me", headers=master_h).json()["days_left"] <= 60


def test_existing_masters_get_trial_when_enabled(client, monkeypatch):
    from app.config import settings
    from app.database import SessionLocal as Session_
    from app.models import Master
    from app.subscriptions import ensure_all_masters

    make_master(client, "Старый мастер")  # registered while the feature was off -> no subscription row
    monkeypatch.setattr(settings, "subscriptions_enabled", True)
    db = Session_()
    ensure_all_masters(db)
    assert db.query(Master).filter(~Master.subscription.has()).count() == 0
    db.close()



# ---------- SMS codes and password reset ----------

def test_register_requires_valid_code(client):
    phone = "+996755000001"
    base = {"name": "X", "phone": phone, "password": "secret123"}
    # no code / wrong format
    assert client.post("/auth/register", json=base).status_code == 422
    assert client.post("/auth/register", json={**base, "code": "123"}).status_code == 422
    # no code was sent yet
    assert client.post("/auth/register", json={**base, "code": "000000"}).status_code == 400

    code = sms_code(client, phone)
    # resend is throttled
    assert client.post("/auth/send-code", json={"phone": phone, "purpose": "register"}).status_code == 429
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/auth/register", json={**base, "code": wrong}).json()["detail"] == "Неверный код"
    assert client.post("/auth/register", json={**base, "code": code}).status_code == 200
    # a code works once
    assert client.post("/auth/register", json={**base, "phone": "+996755000001", "code": code}).status_code == 400


def test_code_brute_force_is_capped(client):
    phone = "+996755000002"
    code = sms_code(client, phone)
    base = {"name": "X", "phone": phone, "password": "secret123"}
    wrong = [c for c in ("000000", "111111", "222222", "333333", "444444", "555555") if c != code][:5]
    for w in wrong:
        assert client.post("/auth/register", json={**base, "code": w}).status_code == 400
    # even the right code is refused after 5 wrong attempts
    r = client.post("/auth/register", json={**base, "code": code})
    assert r.status_code == 400 and "запросите новый" in r.json()["detail"]


def test_expired_code(client, monkeypatch):
    from datetime import timedelta

    from app.models import PhoneCode, utcnow
    phone = "+996755000003"
    code = sms_code(client, phone)
    db = SessionLocal()
    db.query(PhoneCode).filter(PhoneCode.phone == phone).update({PhoneCode.expires_at: utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    r = client.post("/auth/register", json={"name": "X", "phone": phone, "password": "secret123", "code": code})
    assert r.status_code == 400 and "устарел" in r.json()["detail"]


def test_password_reset(client):
    headers, phone = register(client)
    # unknown phone: same answer, nothing sent (no account probing)
    r = client.post("/auth/send-code", json={"phone": "+996755999999", "purpose": "reset"})
    assert r.status_code == 200 and r.json()["debug_code"] is None

    import time
    time.sleep(1.1)  # tokens carry whole-second timestamps; make the reset strictly later than login
    code = sms_code(client, phone, "reset")
    # a sign-up code can't be used for a reset and vice versa
    assert client.post("/auth/register", json={"name": "Z", "phone": phone, "password": "x" * 8, "code": code}).status_code == 400
    r = client.post("/auth/reset-password", json={"phone": phone, "code": code, "password": "newpass123"})
    assert r.status_code == 200
    new_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # old sessions are logged out, the new token and password work, the old password doesn't
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/auth/me", headers=new_headers).status_code == 200
    assert client.post("/auth/login", json={"phone": phone, "password": "secret123"}).status_code == 401
    assert client.post("/auth/login", json={"phone": phone, "password": "newpass123"}).status_code == 200
    # code is single-use
    assert client.post("/auth/reset-password", json={"phone": phone, "code": code, "password": "other123"}).status_code == 400


def test_models_match_migrations():
    """Every model change must come with an Alembic migration (cd backend && alembic revision --autogenerate)."""
    from alembic import command

    from app.database import engine
    from app.migrations import _config
    command.check(_config(engine))


def test_pre_migration_database_is_upgraded(tmp_path):
    """A database created with create_all before migrations existed — and before some tables did."""
    from sqlalchemy import create_engine, inspect, text

    from app.database import Base
    from app.migrations import upgrade_database

    legacy = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    old_tables = [t for t in Base.metadata.sorted_tables
                  if t.name not in {"master_subscriptions", "subscription_payments", "phone_codes", "notification_settings"}]
    Base.metadata.create_all(legacy, tables=old_tables)
    with legacy.begin() as conn:  # columns added by later migrations didn't exist back then
        conn.execute(text("ALTER TABLE users DROP COLUMN password_changed_at"))
        conn.execute(text("DROP INDEX ix_orders_city"))
        conn.execute(text("ALTER TABLE orders DROP COLUMN city"))
        conn.execute(text("INSERT INTO categories (name) VALUES ('Сантехника')"))

    upgrade_database(legacy)

    tables = set(inspect(legacy).get_table_names())
    assert {"master_subscriptions", "subscription_payments", "phone_codes", "notification_settings"} <= tables
    assert "password_changed_at" in {c["name"] for c in inspect(legacy).get_columns("users")}
    with legacy.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM categories")).scalar() == 1  # data kept


# ---------- master notifications (Telegram / SMS, opt-in) ----------

@pytest.fixture
def outbox(monkeypatch):
    """Deliver notifications synchronously and capture what would be sent."""
    from app import notify, telegram
    sent = []

    class FakeSms:
        is_dev = True

        def send(self, phone, text):
            sent.append(("sms", phone, text))

    class FakeEmail:
        def send(self, to, subject, text):
            sent.append(("email", to, f"{subject}\n{text}"))

    monkeypatch.setattr(notify, "_submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(notify, "get_sms_provider", lambda: FakeSms())
    monkeypatch.setattr(notify, "get_email_provider", lambda: FakeEmail())
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text: sent.append(("telegram", chat_id, text)))
    return sent


def register_master(client, notify_enabled=False, channel="sms", lang="ru", category="Сантехника", city="Бишкек"):
    phone = f"+996700000{next(_counter)}"
    r = client.post("/auth/register", json={
        "name": "Мастер", "phone": phone, "password": "secret123", "role": "master", "city": city,
        "email": f"master{phone[-4:]}@example.com",
        "code": sms_code(client, phone), "notify_enabled": notify_enabled, "notify_channel": channel, "lang": lang,
    })
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.post("/masters/me/services", headers=headers,
                json={"category_id": category_id(client, category), "title": "Услуга", "price_from": 100})
    return headers, phone


def test_notifications_need_consent(client, outbox):
    silent_h, _ = register_master(client, notify_enabled=False)
    sms_h, sms_phone = register_master(client, notify_enabled=True, channel="sms", lang="ky")
    electrician_h, _ = register_master(client, notify_enabled=True, category="Электрика")
    assert client.get("/notifications/settings", headers=silent_h).json()["enabled"] is False

    client_h, _ = register(client)
    outbox.clear()
    order = client.post("/orders", headers=client_h,
                        json={"city": "Бишкек", "category_id": category_id(client), "description": "Течёт кран", "price": 800}).json()

    # everyone in the category gets the in-app notification…
    for h in (silent_h, sms_h):
        assert any("Новый заказ" in n["title"] for n in client.get("/notifications", headers=h).json())
    # …but only the master who agreed gets an SMS, in their language; other categories get nothing
    assert len(outbox) == 1
    channel, phone, text = outbox[0]
    assert channel == "sms" and phone == sms_phone
    assert f"#{order['id']}" in text and "Жаңы буйрутма" in text
    assert not any("Новый заказ" in n["title"] for n in client.get("/notifications", headers=electrician_h).json())

    # the master can switch notifications off
    client.put("/notifications/settings", headers=sms_h, json={"enabled": False, "channel": "sms", "lang": "ky"})
    outbox.clear()
    client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "Ещё"})
    assert outbox == []

    # clients have no notification settings
    assert client.get("/notifications/settings", headers=client_h).status_code == 403


def test_chosen_and_cancelled_notifications(client, outbox):
    master_h, phone = register_master(client, notify_enabled=True, channel="sms")
    client_h, _ = register(client)
    order = client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"}).json()
    offer = client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": 500}).json()
    outbox.clear()
    client.post(f"/orders/{order['id']}/accept", params={"offer_id": offer["id"]}, headers=client_h)
    assert any("Вас выбрали" in m[2] for m in outbox)
    client.post(f"/orders/{order['id']}/cancel", headers=client_h)
    assert any("отменил заказ" in m[2] for m in outbox)


def test_telegram_linking(client, outbox, monkeypatch):
    from app.config import settings
    master_h, _ = register_master(client, notify_enabled=True, channel="telegram")

    # bot not configured -> Telegram can't be chosen
    assert client.post("/notifications/telegram/link", headers=master_h).status_code == 400
    assert client.get("/notifications/settings", headers=master_h).json()["telegram_available"] is False

    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(settings, "telegram_bot_username", "test_bot")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "hook-secret")
    url = client.post("/notifications/telegram/link", headers=master_h).json()["url"]
    assert url.startswith("https://t.me/test_bot?start=")
    token = url.split("start=")[1]

    update = {"update_id": 1, "message": {"chat": {"id": 555}, "text": f"/start {token}"}}
    # webhook calls without Telegram's secret header are rejected
    assert client.post("/telegram/webhook", json=update).status_code == 404
    assert client.post("/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 404
    r = client.post("/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"})
    assert r.status_code == 200
    assert outbox[-1][:2] == ("telegram", "555") and "Готово" in outbox[-1][2]
    assert client.get("/notifications/settings", headers=master_h).json()["telegram_connected"] is True

    # the link is single-use
    outbox.clear()
    client.post("/telegram/webhook", json={**update, "message": {"chat": {"id": 999}, "text": f"/start {token}"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"})
    assert outbox[-1][1] == "999" and "Подключить Telegram" in outbox[-1][2]

    # new orders now arrive in Telegram
    client_h, _ = register(client)
    outbox.clear()
    client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "Срочно"})
    assert any(m[0] == "telegram" and m[1] == "555" and "Срочно" in m[2] for m in outbox)



def test_production_refuses_unsafe_settings(monkeypatch):
    from app.config import settings
    from app.main import check_production_settings
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError) as e:
        check_production_settings()
    assert "SMS_PROVIDER=console" in str(e.value) and "CORS_ORIGINS" in str(e.value)



# ---------- cities: masters see orders from their own city ----------

def test_master_must_choose_city(client):
    phone = "+996755100001"
    base = {"name": "M", "phone": phone, "password": "secret123", "role": "master", "code": sms_code(client, phone),
            "email": "osh.master@example.com"}
    r = client.post("/auth/register", json=base)
    assert r.status_code == 400 and "город" in r.json()["detail"]
    assert client.post("/auth/register", json={**base, "city": "Москва"}).status_code == 422
    r = client.post("/auth/register", json={**base, "city": "ош"})  # case-insensitive -> canonical
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/masters/me", headers=headers).json()["city"] == "Ош"


def test_orders_need_a_known_city(client):
    h, _ = register(client)
    cid = category_id(client)
    assert client.post("/orders", headers=h, json={"category_id": cid, "description": "x"}).status_code == 422
    assert client.post("/orders", headers=h, json={"category_id": cid, "description": "x", "city": "Атлантида"}).status_code == 422
    r = client.post("/orders", headers=h, json={"category_id": cid, "description": "x", "city": " каракол "})
    assert r.status_code == 200 and r.json()["city"] == "Каракол"
    assert "Каракол" in client.get("/config").json()["cities"]


def test_feed_and_notifications_follow_city(client, outbox):
    bishkek_h, bishkek_phone = register_master(client, notify_enabled=True, channel="sms", city="Бишкек")
    osh_h, osh_phone = register_master(client, notify_enabled=True, channel="sms", city="Ош")
    client_h, _ = register(client)
    outbox.clear()
    osh_order = client.post("/orders", headers=client_h,
                            json={"city": "Ош", "category_id": category_id(client), "description": "Ош заказ"}).json()

    osh_feed = [o["id"] for o in client.get("/orders/feed", headers=osh_h).json()]
    bishkek_feed = [o["id"] for o in client.get("/orders/feed", headers=bishkek_h).json()]
    assert osh_order["id"] in osh_feed and osh_order["id"] not in bishkek_feed
    # only the Osh master is notified (in-app and SMS)
    assert [m[1] for m in outbox] == [osh_phone]
    assert not any(f"#{osh_order['id']}" in (n["text"] or "") for n in client.get("/notifications", headers=bishkek_h).json())

    # a master without a city sees nothing until they set one
    client.patch("/masters/me", headers=osh_h, json={"city": ""})
    assert client.get("/orders/feed", headers=osh_h).json() == []
    client.patch("/masters/me", headers=osh_h, json={"city": "Ош"})
    assert osh_order["id"] in [o["id"] for o in client.get("/orders/feed", headers=osh_h).json()]


def test_legacy_orders_without_city_stay_visible(client):
    from app.models import Order
    master_h, _ = make_master(client)
    client_h, _ = register(client)
    order = client.post("/orders", headers=client_h,
                        json={"city": "Бишкек", "category_id": category_id(client), "description": "старый"}).json()
    db = SessionLocal()
    db.get(Order, order["id"]).city = None  # as if created before cities existed
    db.commit()
    db.close()
    assert order["id"] in [o["id"] for o in client.get("/orders/feed", headers=master_h).json()]


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



# ---------- email ----------

def test_master_email_required_and_validated(client):
    phone = "+996755200001"
    base = {"name": "M", "phone": phone, "password": "secret123", "role": "master", "city": "Бишкек",
            "code": sms_code(client, phone)}
    r = client.post("/auth/register", json=base)
    assert r.status_code == 400 and r.json()["detail"] == "Укажите email"
    assert client.post("/auth/register", json={**base, "email": "not-an-email"}).status_code == 422
    r = client.post("/auth/register", json={**base, "email": "  Usta@Example.COM "})
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/notifications/settings", headers=headers).json()["email"] == "usta@example.com"

    # clients don't need an email
    client_phone = "+996755200002"
    r = client.post("/auth/register", json={"name": "C", "phone": client_phone, "password": "secret123",
                                            "code": sms_code(client, client_phone)})
    assert r.status_code == 200


def test_new_orders_by_email(client, outbox):
    master_h, _ = register_master(client, notify_enabled=True, channel="email", lang="en")
    email = client.get("/notifications/settings", headers=master_h).json()["email"]
    client_h, _ = register(client)
    outbox.clear()
    order = client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client),
                                                           "description": "Leaking tap", "price": 700}).json()
    mails = [m for m in outbox if m[0] == "email"]
    assert len(mails) == 1 and mails[0][1] == email
    assert f"New order #{order['id']} — Plumbing" in mails[0][2]  # subject in the master's language
    assert "Leaking tap" in mails[0][2] and "700 som" in mails[0][2] and "Turn off" in mails[0][2]

    # the master changes the address in settings
    client.put("/notifications/settings", headers=master_h,
               json={"enabled": True, "channel": "email", "lang": "en", "email": "new@example.com"})
    outbox.clear()
    client.post("/orders", headers=client_h, json={"city": "Бишкек", "category_id": category_id(client), "description": "x"})
    assert [m[1] for m in outbox if m[0] == "email"] == ["new@example.com"]
