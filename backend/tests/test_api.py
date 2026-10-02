"""End-to-end API tests: the full order flow plus access-control / injection checks.

Run from backend/:  python -m pytest tests -q
Uses a throwaway SQLite database, so no Postgres is needed.
"""
import base64
import json
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-0123456789"

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


def register(client, role="client", name="Тест"):
    phone = f"+996700000{next(_counter)}"
    r = client.post("/auth/register", json={"name": name, "phone": phone, "password": "secret123", "role": role})
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
                        json={"category_id": category_id(client), "description": "Течёт кран", "address": "ул. Ленина 1"}).json()

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
    order = client.post("/orders", headers=client_h, json={"category_id": category_id(client), "description": "x"}).json()

    # wrong category
    assert client.post(f"/orders/{order['id']}/offer", headers=electrician_h, json={"price": 1}).status_code == 403
    # clients can't offer
    assert client.post(f"/orders/{order['id']}/offer", headers=client_h, json={"price": 1}).status_code == 403
    # master can't offer on own order
    master_h, _ = make_master(client)
    own = client.post("/orders", headers=master_h, json={"category_id": category_id(client), "description": "x"}).json()
    assert client.post(f"/orders/{own['id']}/offer", headers=master_h, json={"price": 1}).status_code == 400
    # negative price rejected
    assert client.post(f"/orders/{order['id']}/offer", headers=master_h, json={"price": -5}).status_code == 422


def test_idor(client):
    owner_h, _ = register(client)
    stranger_h, _ = register(client)
    master_h, _ = make_master(client)
    order = client.post("/orders", headers=owner_h, json={"category_id": category_id(client), "description": "x"}).json()
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
    r = client.post("/auth/register", json={"name": "x", "phone": "+996711111111", "password": "secret123", "role": "admin"})
    assert r.status_code == 422

    # phone normalization: formatted and plain are the same account
    r = client.post("/auth/register", json={"name": "x", "phone": "+996 722 333 444", "password": "secret123"})
    assert r.status_code == 200
    assert client.post("/auth/register", json={"name": "y", "phone": "+996722333444", "password": "secret123"}).status_code == 400
    assert client.post("/auth/login", json={"phone": "+996-722-333-444", "password": "secret123"}).status_code == 200
    assert client.post("/auth/register", json={"name": "x", "phone": "abc", "password": "secret123"}).status_code == 422

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
    order = client.post("/orders", headers=h, json={"category_id": category_id(client), "description": "x"}).json()
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
    assert client.post("/orders", headers=h, json={"category_id": cid, "description": "x", "latitude": 999}).status_code == 422
    assert client.post("/orders", headers=h, json={"category_id": cid, "description": "x", "time": "<b>"}).status_code == 422
    assert client.post("/orders", headers=h, json={"category_id": cid, "description": ""}).status_code == 422
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
        client.post("/orders", headers=client_h, json={"category_id": category_id(client), "description": "x"})
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
