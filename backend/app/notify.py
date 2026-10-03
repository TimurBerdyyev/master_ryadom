"""Notifications: in-app records plus opt-in delivery to masters via Telegram or SMS.

notify() stores the in-app notification and queues an external message. Queued messages are sent
only after the transaction commits (nothing goes out for a rolled-back action), in a background
thread so a slow SMS gateway never delays the API response.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Notification, NotificationSettings, User, UserRole
from app.email import get_email_provider
from app.sms import get_sms_provider

logger = logging.getLogger("master_ryadom.notify")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify")


def _submit(fn, *args) -> None:
    """Indirection so tests can run deliveries synchronously."""
    _executor.submit(fn, *args)


# Category names are stored in Russian (see web/js/i18n.js for the site's copy of this list).
CATEGORY_NAMES = {
    "Сантехника": ("Сантехника", "Plumbing"),
    "Электрика": ("Электрик иштери", "Electrical"),
    "Строительство": ("Курулуш", "Construction"),
    "Ремонт": ("Оңдоо", "Renovation"),
    "Уборка": ("Тазалоо", "Cleaning"),
    "Компьютеры": ("Компьютерлер", "Computers"),
    "Ремонт телефонов": ("Телефон оңдоо", "Phone repair"),
    "Автомастера": ("Авто устачылар", "Car repair"),
    "Красота": ("Сулуулук", "Beauty"),
    "Парикмахеры": ("Чачтарачтар", "Hairdressers"),
    "Грузчики": ("Жүк ташуучулар", "Movers"),
    "Перевозки": ("Ташуу кызматы", "Delivery & transport"),
    "Ремонт бытовой техники": ("Тиричилик техникасын оңдоо", "Appliance repair"),
    "Другое": ("Башка", "Other"),
}

# kind -> lang -> (telegram text, sms text). SMS is kept short: one segment where possible.
TEMPLATES = {
    "new_order": {
        "ru": ("🔔 Новый заказ #{id} — {category}\n{description}\n{budget}\nОткликнуться: {url}",
               "Новый заказ #{id}: {category}. {url}"),
        "ky": ("🔔 Жаңы буйрутма #{id} — {category}\n{description}\n{budget}\nЖооп берүү: {url}",
               "Жаңы буйрутма #{id}: {category}. {url}"),
        "en": ("🔔 New order #{id} — {category}\n{description}\n{budget}\nRespond: {url}",
               "New order #{id}: {category}. {url}"),
    },
    "chosen": {
        "ru": ("✅ Клиент выбрал ваше предложение по заказу #{id}. Контакты клиента: {url}",
               "Вас выбрали для заказа #{id}. {url}"),
        "ky": ("✅ Кардар #{id} буйрутма боюнча сунушуңузду тандады. Кардардын байланыштары: {url}",
               "Сизди #{id} буйрутмага тандашты. {url}"),
        "en": ("✅ The client chose your offer for order #{id}. Client contacts: {url}",
               "You were chosen for order #{id}. {url}"),
    },
    "cancelled": {
        "ru": ("❌ Клиент отменил заказ #{id}.", "Клиент отменил заказ #{id}."),
        "ky": ("❌ Кардар #{id} буйрутманы жокко чыгарды.", "Кардар #{id} буйрутманы жокко чыгарды."),
        "en": ("❌ The client cancelled order #{id}.", "The client cancelled order #{id}."),
    },
}
SUBJECTS = {
    "new_order": {"ru": "Новый заказ #{id} — {category}", "ky": "Жаңы буйрутма #{id} — {category}",
                  "en": "New order #{id} — {category}"},
    "chosen": {"ru": "Вас выбрали для заказа #{id}", "ky": "Сизди #{id} буйрутмага тандашты",
               "en": "You were chosen for order #{id}"},
    "cancelled": {"ru": "Заказ #{id} отменён", "ky": "#{id} буйрутма жокко чыгарылды", "en": "Order #{id} was cancelled"},
}
UNSUBSCRIBE = {
    "ru": "Вы получили это письмо, потому что включили уведомления в профиле мастера. Отключить: {url}",
    "ky": "Бул катты уста профилинде билдирмелерди күйгүзгөнүңүз үчүн алдыңыз. Өчүрүү: {url}",
    "en": "You received this email because you turned on notifications in your pro profile. Turn off: {url}",
}
BUDGET = {"ru": "Бюджет: {price} сом", "ky": "Бюджет: {price} сом", "en": "Budget: {price} som"}
LINKS = {"new_order": "/feed.html", "chosen": "/order-detail.html?id={id}", "cancelled": "/orders.html"}


def notify(db: Session, user_id: int, title: str, text: str | None = None, kind: str | None = None, **params) -> None:
    """In-app notification; for masters who opted in, also a Telegram/SMS message of the given kind."""
    db.add(Notification(user_id=user_id, title=title, text=text))
    if kind:
        db.info.setdefault("outbox", []).append((user_id, kind, params))


@event.listens_for(SessionLocal, "after_commit")
def _flush_outbox(session: Session) -> None:
    for job in session.info.pop("outbox", []):
        _submit(deliver, *job)


@event.listens_for(SessionLocal, "after_rollback")
def _drop_outbox(session: Session) -> None:
    session.info.pop("outbox", None)


def render_category(category: str, lang: str) -> str:
    if lang != "ru" and category in CATEGORY_NAMES:
        return CATEGORY_NAMES[category][0 if lang == "ky" else 1]
    return category


def render(kind: str, lang: str, channel: str, params: dict) -> str:
    lang = lang if lang in ("ru", "ky", "en") else "ru"
    category = render_category(params.get("category", ""), lang)
    description = (params.get("description") or "")[:200]
    budget = BUDGET[lang].format(price=f"{params['price']:g}") if params.get("price") else ""
    url = settings.site_url.rstrip("/") + LINKS[kind].format(id=params.get("id", ""))
    telegram_text, sms_text = TEMPLATES[kind][lang]
    # Email gets the full text, like Telegram; SMS stays short.
    template = sms_text if channel == "sms" else telegram_text
    text = template.format(id=params.get("id", ""), category=category, description=description, budget=budget, url=url)
    return "\n".join(line for line in text.split("\n") if line.strip())


def deliver(user_id: int, kind: str, params: dict) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        prefs = db.query(NotificationSettings).filter(NotificationSettings.user_id == user_id).first()
        if user is None or user.role != UserRole.master or prefs is None or not prefs.enabled:
            return
        text = render(kind, prefs.lang, prefs.channel, params)
        if prefs.channel == "telegram":
            if not (settings.telegram_enabled and prefs.telegram_chat_id):
                return
            from app.telegram import send_message
            send_message(prefs.telegram_chat_id, text)
        elif prefs.channel == "sms":
            get_sms_provider().send(user.phone, text)
        elif prefs.channel == "email":
            if not user.email:
                return
            lang = prefs.lang if prefs.lang in SUBJECTS[kind] else "ru"
            category = render_category(params.get("category", ""), lang)
            subject = SUBJECTS[kind][lang].format(id=params.get("id", ""), category=category)
            footer = UNSUBSCRIBE[lang].format(url=settings.site_url.rstrip("/") + "/master-profile-edit.html")
            get_email_provider().send(user.email, subject, f"{text}\n\n—\n{footer}")
    except Exception:
        logger.exception("Не удалось доставить уведомление %s пользователю %s", kind, user_id)
    finally:
        db.close()
