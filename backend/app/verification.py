"""One-time email codes: sending with throttling and checking with an attempt limit.

Codes confirm that an email address really belongs to the user (sign-up, changing the address)
and authorise password resets.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.email import get_email_provider
from app.models import VerificationCode, utcnow

RESEND_AFTER_SECONDS = 60
MAX_CODES_PER_HOUR = 5
MAX_ATTEMPTS = 5

SUBJECTS = {
    "ru": "Код подтверждения: {code}",
    "ky": "Ырастоо коду: {code}",
    "en": "Your confirmation code: {code}",
}
BODIES = {
    "register": {
        "ru": "Ваш код для регистрации на «Мастер рядом»: {code}\n\nКод действует {ttl} минут. Никому его не сообщайте.",
        "ky": "«Мастер рядом» сайтында катталуу үчүн кодуңуз: {code}\n\nКод {ttl} мүнөт жарактуу. Аны эч кимге айтпаңыз.",
        "en": "Your Мастер рядом sign-up code: {code}\n\nThe code is valid for {ttl} minutes. Don't share it with anyone.",
    },
    "reset": {
        "ru": "Код для смены пароля на «Мастер рядом»: {code}\n\nКод действует {ttl} минут. Если вы не запрашивали смену пароля — просто проигнорируйте письмо.",
        "ky": "«Мастер рядом» сайтында сырсөздү өзгөртүү коду: {code}\n\nКод {ttl} мүнөт жарактуу. Эгер сиз сураган эмес болсоңуз — катты көңүлгө албаңыз.",
        "en": "Your Мастер рядом password reset code: {code}\n\nThe code is valid for {ttl} minutes. If you didn't ask to reset your password, just ignore this email.",
    },
    "change_email": {
        "ru": "Код для подтверждения нового email на «Мастер рядом»: {code}\n\nКод действует {ttl} минут.",
        "ky": "«Мастер рядом» сайтында жаңы email'ди ырастоо коду: {code}\n\nКод {ttl} мүнөт жарактуу.",
        "en": "Your code to confirm the new email on Мастер рядом: {code}\n\nThe code is valid for {ttl} minutes.",
    },
}


def _hash(target: str, purpose: str, code: str) -> str:
    key = settings.jwt_secret.encode()
    return hmac.new(key, f"{target}:{purpose}:{code}".encode(), hashlib.sha256).hexdigest()


def send_code(db: Session, email: str, purpose: str, lang: str = "ru") -> str:
    """Create and email a code; returns it (the API exposes it only with the dev email provider)."""
    now = utcnow()
    recent = (
        db.query(VerificationCode)
        .filter(VerificationCode.target == email, VerificationCode.created_at > now - timedelta(hours=1))
        .order_by(VerificationCode.created_at.desc())
        .all()
    )
    same_purpose = [c for c in recent if c.purpose == purpose]
    if same_purpose and (now - same_purpose[0].created_at).total_seconds() < RESEND_AFTER_SECONDS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Код уже отправлен, подождите минуту")
    if len(recent) >= MAX_CODES_PER_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток, попробуйте позже")

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(VerificationCode(
        target=email,
        purpose=purpose,
        code_hash=_hash(email, purpose, code),
        expires_at=now + timedelta(minutes=settings.code_ttl_minutes),
    ))
    db.commit()
    lang = lang if lang in SUBJECTS else "ru"
    get_email_provider().send(
        email,
        SUBJECTS[lang].format(code=code),
        BODIES[purpose][lang].format(code=code, ttl=settings.code_ttl_minutes),
    )
    return code


def check_code(db: Session, email: str, purpose: str, code: str) -> None:
    """Consume a valid code or raise 400. Wrong guesses are counted, so a code can't be brute-forced."""
    entry = (
        db.query(VerificationCode)
        .filter(VerificationCode.target == email, VerificationCode.purpose == purpose,
                VerificationCode.used.is_(False))
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if entry is None or entry.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код устарел, запросите новый")
    if entry.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Слишком много неверных попыток, запросите новый код")
    if not hmac.compare_digest(entry.code_hash, _hash(email, purpose, code)):
        entry.attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    entry.used = True
