"""Push notifications to the app through Firebase Cloud Messaging (HTTP v1 API).

The server signs a short JWT with the Firebase service account key (FIREBASE_SERVICE_ACCOUNT), exchanges it
for an OAuth access token (cached ~1 hour) and posts one message per phone. Tokens Firebase reports as
dead (app removed, token rotated) are deleted.
"""
import json
import logging
import threading
import time

import httpx
import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models import PushDevice

logger = logging.getLogger("master_ryadom.push")

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
# Android notification channel created by the app (web/js/push.js): loud, shown as a heads-up banner.
CHANNEL_ID = "orders"

_access = {"token": None, "expires": 0.0}
_lock = threading.Lock()


def _service_account() -> dict:
    return json.loads(settings.firebase_service_account)


def _access_token() -> str:
    with _lock:
        if _access["token"] and _access["expires"] - 60 > time.time():
            return _access["token"]
        account = _service_account()
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": account["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600},
            account["private_key"], algorithm="RS256",
        )
        response = httpx.post(TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}, timeout=15)
        response.raise_for_status()
        body = response.json()
        _access["token"] = body["access_token"]
        _access["expires"] = time.time() + int(body.get("expires_in", 3600))
        return _access["token"]


def _message(token: str, title: str, body: str, link: str) -> dict:
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            # The app opens this page when the notification is tapped (web/js/push.js).
            "data": {"link": link},
            "android": {
                "priority": "high",
                "notification": {"channel_id": CHANNEL_ID, "icon": "ic_stat_notify", "color": "#12875A",
                                 "sound": "default"},
            },
        }
    }


def send_to_user(db: Session, user_id: int, title: str, body: str, link: str, errors: list | None = None) -> int:
    """Send to every phone of the user; returns how many accepted. Never raises.

    Problems are logged and, if `errors` is given, appended to it (shown by the test button).
    """
    def problem(text: str) -> None:
        logger.warning("Push: %s", text)
        if errors is not None:
            errors.append(text)

    if not settings.push_enabled:  # push simply isn't set up: not worth a log line on every notification
        if errors is not None:
            errors.append("на сервере не задан FIREBASE_SERVICE_ACCOUNT")
        return 0
    devices = db.query(PushDevice).filter(PushDevice.user_id == user_id).all()
    if not devices:
        return 0
    try:
        url = f"https://fcm.googleapis.com/v1/projects/{_service_account()['project_id']}/messages:send"
        headers = {"Authorization": f"Bearer {_access_token()}"}
    except Exception as exc:
        logger.exception("Push: не удалось получить доступ к Firebase")
        problem(f"нет доступа к Firebase — проверьте FIREBASE_SERVICE_ACCOUNT ({type(exc).__name__}: {str(exc)[:150]})")
        return 0
    sent = 0
    for device in devices:
        try:
            response = httpx.post(url, json=_message(device.token, title, body, link), headers=headers, timeout=15)
        except httpx.HTTPError as exc:
            problem(f"Firebase недоступен ({type(exc).__name__})")
            continue
        if response.status_code == 200:
            sent += 1
        elif response.status_code in (400, 404) and ("UNREGISTERED" in response.text or "INVALID_ARGUMENT" in response.text):
            db.delete(device)  # the app was removed or the token is no longer valid
            problem("Firebase не знает этот телефон (приложение переустановлено?) — откройте приложение заново")
        else:
            problem(f"ошибка Firebase {response.status_code}: {response.text[:200]}")
    db.commit()
    return sent


def status(db: Session) -> dict:
    """For the admin panel: is the server able to talk to Firebase, which phones are registered."""
    from app.models import User
    result = {"enabled": settings.push_enabled, "project_id": None, "connected": False, "error": None, "devices": []}
    if settings.push_enabled:
        try:
            result["project_id"] = _service_account().get("project_id")
            _access_token()
            result["connected"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    rows = (
        db.query(PushDevice, User).join(User, User.id == PushDevice.user_id)
        .order_by(PushDevice.last_seen_at.desc()).limit(200).all()
    )
    result["devices"] = [
        {"user_id": u.id, "name": u.name, "role": u.role.value, "platform": d.platform, "last_seen_at": d.last_seen_at}
        for d, u in rows
    ]
    return result
