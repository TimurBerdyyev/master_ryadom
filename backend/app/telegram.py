"""Telegram bot: links a master's chat to their account and delivers notifications.

Linking: the site gives the master a link https://t.me/<bot>?start=<token>; pressing Start sends
"/start <token>" to the bot, and we store that chat_id for the master.

Production: set TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME / TELEGRAM_WEBHOOK_SECRET / SITE_URL and run
    python -m app.telegram_setup          (registers the webhook once)
Local development (no public URL for a webhook):
    python -m app.telegram_poll           (polls Telegram for updates)
"""
import logging

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import NotificationSettings

logger = logging.getLogger("master_ryadom.telegram")

API = "https://api.telegram.org/bot{token}/{method}"

REPLIES = {
    "linked": {
        "ru": "Готово! Уведомления о новых заказах будут приходить сюда.",
        "ky": "Даяр! Жаңы буйрутмалар тууралуу билдирмелер ушул жерге келет.",
        "en": "Done! Notifications about new orders will arrive here.",
    },
    "stopped": {
        "ru": "Уведомления выключены. Включить снова можно в профиле на сайте.",
        "ky": "Билдирмелер өчүрүлдү. Кайра сайттагы профилден күйгүзсө болот.",
        "en": "Notifications are off. You can turn them back on in your profile on the site.",
    },
    "unknown": {
        "ru": "Чтобы подключить уведомления, нажмите «Подключить Telegram» в профиле мастера на сайте.",
        "ky": "Билдирмелерди туташтыруу үчүн сайттагы уста профилинен «Telegram туташтыруу» баскычын басыңыз.",
        "en": "To connect notifications, press “Connect Telegram” in your pro profile on the site.",
    },
}


def call(method: str, **params) -> dict:
    response = httpx.post(API.format(token=settings.telegram_bot_token, method=method), json=params, timeout=15)
    response.raise_for_status()
    return response.json()


def send_message(chat_id: str, text: str) -> None:
    call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)


def handle_update(db: Session, update: dict) -> None:
    """Process one update from Telegram (webhook or polling)."""
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return
    chat_id = str(chat_id)

    if text.startswith("/start"):
        token = text.split(maxsplit=1)[1] if " " in text else ""
        prefs = (
            db.query(NotificationSettings).filter(NotificationSettings.telegram_link_token == token).first()
            if token else None
        )
        if prefs is None:
            send_message(chat_id, REPLIES["unknown"]["ru"])
            return
        prefs.telegram_chat_id = chat_id
        prefs.telegram_link_token = None  # single use
        prefs.channel = "telegram"
        prefs.enabled = True
        db.commit()
        send_message(chat_id, REPLIES["linked"].get(prefs.lang, REPLIES["linked"]["ru"]))
    elif text.startswith("/stop"):
        prefs = db.query(NotificationSettings).filter(NotificationSettings.telegram_chat_id == chat_id).first()
        if prefs is not None:
            prefs.enabled = False
            db.commit()
            send_message(chat_id, REPLIES["stopped"].get(prefs.lang, REPLIES["stopped"]["ru"]))
