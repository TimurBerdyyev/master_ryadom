import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Notification, NotificationSettings, User, UserRole
from app.sms import get_sms_provider
from app.schemas import (
    NotificationOut,
    NotificationSettingsIn,
    NotificationSettingsOut,
    TelegramLinkOut,
    UnreadCountOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
telegram_router = APIRouter(tags=["notifications"])


def _mine(db: Session, current_user: User):
    return db.query(Notification).filter(Notification.user_id == current_user.id)


def _master_prefs(db: Session, current_user: User) -> NotificationSettings:
    if current_user.role != UserRole.master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только мастерам")
    prefs = db.query(NotificationSettings).filter(NotificationSettings.user_id == current_user.id).first()
    if prefs is None:  # masters registered before notification settings existed
        prefs = NotificationSettings(user_id=current_user.id, enabled=False)
        db.add(prefs)
        db.flush()
    return prefs


def _settings_out(prefs: NotificationSettings, user: User) -> NotificationSettingsOut:
    return NotificationSettingsOut(
        enabled=prefs.enabled,
        channel=prefs.channel,
        lang=prefs.lang,
        email=user.email,
        telegram_connected=bool(prefs.telegram_chat_id),
        telegram_available=settings.telegram_enabled,
        sms_available=not get_sms_provider().is_dev,
    )


@router.get("", response_model=list[NotificationOut])
def list_notifications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _mine(db, current_user).order_by(Notification.created_at.desc()).limit(100).all()


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return UnreadCountOut(unread=_mine(db, current_user).filter(Notification.is_read.is_(False)).count())


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _mine(db, current_user).filter(Notification.is_read.is_(False)).update({Notification.is_read: True})
    db.commit()


@router.get("/settings", response_model=NotificationSettingsOut)
def get_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = _master_prefs(db, current_user)
    db.commit()
    return _settings_out(prefs, current_user)


@router.put("/settings", response_model=NotificationSettingsOut)
def update_settings(
    data: NotificationSettingsIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.enabled and data.channel == "telegram" and not settings.telegram_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Telegram-бот пока не подключён")
    if data.enabled and data.channel == "sms" and get_sms_provider().is_dev:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SMS-уведомления пока недоступны")
    if data.enabled and data.channel == "email" and not current_user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите email")
    prefs = _master_prefs(db, current_user)
    prefs.enabled = data.enabled
    prefs.channel = data.channel
    prefs.lang = data.lang
    db.commit()
    return _settings_out(prefs, current_user)


@router.post("/telegram/link", response_model=TelegramLinkOut)
def telegram_link(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One-time deep link: opening it and pressing Start in Telegram binds the chat to this master."""
    if not settings.telegram_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Telegram-бот пока не подключён")
    prefs = _master_prefs(db, current_user)
    prefs.telegram_link_token = secrets.token_urlsafe(24)
    db.commit()
    return TelegramLinkOut(url=f"https://t.me/{settings.telegram_bot_username}?start={prefs.telegram_link_token}")


@telegram_router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    secret: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
    db: Session = Depends(get_db),
):
    # Telegram echoes the secret set in setWebhook; anything else is not from Telegram.
    if not settings.telegram_enabled or not settings.telegram_webhook_secret or not secret \
            or not secrets.compare_digest(secret, settings.telegram_webhook_secret):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    from app.telegram import handle_update
    handle_update(db, await request.json())
    return {"ok": True}
