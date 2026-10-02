"""One-time SMS codes: sending with throttling and checking with an attempt limit."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models import PhoneCode, utcnow
from app.sms import get_sms_provider

RESEND_AFTER_SECONDS = 60
MAX_CODES_PER_HOUR = 5
MAX_ATTEMPTS = 5

MESSAGES = {
    "register": "Мастер рядом: код подтверждения {code}. Никому его не сообщайте.",
    "reset": "Мастер рядом: код для смены пароля {code}. Если это не вы — просто проигнорируйте.",
}


def _hash(phone: str, purpose: str, code: str) -> str:
    key = settings.jwt_secret.encode()
    return hmac.new(key, f"{phone}:{purpose}:{code}".encode(), hashlib.sha256).hexdigest()


def send_code(db: Session, phone: str, purpose: str) -> str:
    """Create and send a code; returns it (the API exposes it only with the dev SMS provider)."""
    now = utcnow()
    recent = (
        db.query(PhoneCode)
        .filter(PhoneCode.phone == phone, PhoneCode.created_at > now - timedelta(hours=1))
        .order_by(PhoneCode.created_at.desc())
        .all()
    )
    same_purpose = [c for c in recent if c.purpose == purpose]
    if same_purpose and (now - same_purpose[0].created_at).total_seconds() < RESEND_AFTER_SECONDS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Код уже отправлен, подождите минуту")
    if len(recent) >= MAX_CODES_PER_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток, попробуйте позже")

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(PhoneCode(
        phone=phone,
        purpose=purpose,
        code_hash=_hash(phone, purpose, code),
        expires_at=now + timedelta(minutes=settings.sms_code_ttl_minutes),
    ))
    db.commit()
    get_sms_provider().send(phone, MESSAGES[purpose].format(code=code))
    return code


def check_code(db: Session, phone: str, purpose: str, code: str) -> None:
    """Consume a valid code or raise 400. Wrong guesses are counted, so a code can't be brute-forced."""
    entry = (
        db.query(PhoneCode)
        .filter(PhoneCode.phone == phone, PhoneCode.purpose == purpose, PhoneCode.used.is_(False))
        .order_by(PhoneCode.created_at.desc())
        .first()
    )
    if entry is None or entry.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код устарел, запросите новый")
    if entry.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Слишком много неверных попыток, запросите новый код")
    if not hmac.compare_digest(entry.code_hash, _hash(phone, purpose, code)):
        entry.attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    entry.used = True
