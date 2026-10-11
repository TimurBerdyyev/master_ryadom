"""Web Push: notifications to the site installed as an app (Android Chrome, iPhone "На экран Домой") and to
browsers — without Firebase and without an APK.

Standard protocol: the payload is encrypted for the browser (RFC 8291, "aes128gcm") and the request is
signed with our VAPID key (RFC 8292). The VAPID key pair is generated once and kept in the database
(app_settings), so nothing has to be configured on the server.
"""
import base64
import json
import logging
import os
import time
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting, WebPushSubscription

logger = logging.getLogger("master_ryadom.webpush")

RECORD_SIZE = 4096
_vapid_cache: dict = {}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_private_key(db: Session) -> ec.EllipticCurvePrivateKey:
    """Our VAPID key: from settings if given, otherwise generated once and stored in the database."""
    if "key" in _vapid_cache:
        return _vapid_cache["key"]
    pem = settings.vapid_private_key
    if not pem:
        row = db.get(AppSetting, "vapid_private_key")
        if row is None:
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
            db.add(AppSetting(key="vapid_private_key", value=pem))
            db.commit()
        else:
            pem = row.value
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    _vapid_cache["key"] = key
    return key


def vapid_public_key(db: Session) -> str:
    """What the browser needs for pushManager.subscribe(applicationServerKey)."""
    return b64url(_public_bytes(vapid_private_key(db).public_key()))


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """RFC 8291 aes128gcm: one record, encrypted for the browser's key."""
    ua_public = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    browser_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(ephemeral.public_key())
    shared = ephemeral.exchange(ec.ECDH(), browser_key)

    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 = last record, no padding
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


def _vapid_headers(db: Session, endpoint: str) -> dict:
    parts = urlsplit(endpoint)
    token = jwt.encode(
        {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600,
         "sub": f"mailto:{settings.vapid_contact or 'admin@example.com'}"},
        vapid_private_key(db), algorithm="ES256",
    )
    return {"Authorization": f"vapid t={token}, k={vapid_public_key(db)}"}


def send(db: Session, subscription: WebPushSubscription, title: str, body: str, link: str) -> bool:
    """Deliver one notification; a subscription the browser dropped (404/410) is deleted. Never raises."""
    payload = json.dumps({"title": title, "body": body, "link": link}, ensure_ascii=False).encode()
    try:
        response = httpx.post(
            subscription.endpoint,
            content=encrypt(payload, subscription.p256dh, subscription.auth),
            headers={**_vapid_headers(db, subscription.endpoint), "Content-Encoding": "aes128gcm",
                     "Content-Type": "application/octet-stream", "TTL": "86400", "Urgency": "high"},
            timeout=15,
        )
    except Exception:
        logger.warning("Web Push: не удалось отправить на %s", urlsplit(subscription.endpoint).netloc)
        return False
    if response.status_code in (404, 410):
        db.delete(subscription)
        db.commit()
        return False
    if response.status_code >= 300:
        logger.warning("Web Push: %s ответил %s: %s", urlsplit(subscription.endpoint).netloc,
                       response.status_code, response.text[:200])
        return False
    return True


def send_to(db: Session, title: str, body: str, link: str, user_id: int | None = None,
            client_key_hash: str | None = None) -> int:
    query = db.query(WebPushSubscription)
    if user_id is not None:
        query = query.filter(WebPushSubscription.user_id == user_id)
    elif client_key_hash is not None:
        query = query.filter(WebPushSubscription.client_key_hash == client_key_hash)
    else:
        return 0
    return sum(send(db, s, title, body, link) for s in query.limit(20).all())
