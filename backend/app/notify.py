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
from app import push, webpush
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
    "missed": {
        "ru": ("⚠️ Вы не ответили на заказ #{id} вовремя. По договору — штраф {fine} сом и снижение рейтинга.\n{url}",
               "Нет ответа на заказ #{id}: штраф {fine} сом. {url}"),
        "ky": ("⚠️ #{id} буйрутмага убагында жооп берген жоксуз. Келишим боюнча — {fine} сом айып жана рейтинг төмөндөйт.\n{url}",
               "#{id} буйрутмага жооп жок: {fine} сом айып. {url}"),
        "en": ("⚠️ You didn't answer order #{id} in time. Under the agreement: a {fine} som fine and a lower rating.\n{url}",
               "No answer to order #{id}: {fine} som fine. {url}"),
    },
    "message": {
        "ru": ("💬 {name}: {text}\nОтветить: {url}", "{name}: {text}"),
        "ky": ("💬 {name}: {text}\nЖооп берүү: {url}", "{name}: {text}"),
        "en": ("💬 {name}: {text}\nReply: {url}", "{name}: {text}"),
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
    "message": {"ru": "Новое сообщение", "ky": "Жаңы билдирүү", "en": "New message"},
    "missed": {"ru": "Пропущена заявка #{id}", "ky": "#{id} арыз өткөрүлүп жиберилди", "en": "Missed request #{id}"},
    "cancelled": {"ru": "Заказ #{id} отменён", "ky": "#{id} буйрутма жокко чыгарылды", "en": "Order #{id} was cancelled"},
}
UNSUBSCRIBE = {
    "ru": "Вы получили это письмо, потому что включили уведомления в профиле мастера. Отключить: {url}",
    "ky": "Бул катты уста профилинде билдирмелерди күйгүзгөнүңүз үчүн алдыңыз. Өчүрүү: {url}",
    "en": "You received this email because you turned on notifications in your pro profile. Turn off: {url}",
}
# Frequent notifications that go out only as push and Telegram, never as email or SMS.
PUSH_AND_TELEGRAM_ONLY = {"message"}
BUDGET = {"ru": "Бюджет: {price} сом", "ky": "Бюджет: {price} сом", "en": "Budget: {price} som"}
LINKS = {"message": "/chat.html?id={id}", "missed": "/master-profile-edit.html#overview", "new_order": "/feed.html", "chosen": "/order-detail.html?id={id}", "cancelled": "/orders.html"}


def notify(db: Session, user_id: int, title: str, text: str | None = None, kind: str | None = None, **params) -> None:
    """In-app notification; for masters who opted in, also a Telegram/SMS message of the given kind."""
    db.add(Notification(user_id=user_id, title=title, text=text))
    if kind:
        db.info.setdefault("outbox", []).append((user_id, kind, params))


# Clients have no account: they get Web Push by the key their browser subscribed with, in its language.
CLIENT_TEXTS = {
    "offer": {"ru": ("Новое предложение", "{name} — {price}"), "ky": ("Жаңы сунуш", "{name} — {price}"),
              "en": ("New offer", "{name} — {price}")},
    "status": {"ru": ("Заявка #{id}", "{status}"), "ky": ("Арыз #{id}", "{status}"), "en": ("Request #{id}", "{status}")},
    "reply": {"ru": ("{name}", "{message}"), "ky": ("{name}", "{message}"), "en": ("{name}", "{message}")},
}
CLIENT_LINKS = {"offer": "/my-requests.html", "status": "/my-requests.html", "reply": "/chat.html?id={id}"}
STATUS_WORDS = {
    "master_confirmed": ("Мастер подтвердил заказ", "Уста буйрутманы ырастады", "The pro confirmed the job"),
    "master_en_route": ("Мастер выехал к вам", "Уста сизге жолго чыкты", "The pro is on the way"),
    "in_progress": ("Мастер приступил к работе", "Уста ишти баштады", "The pro started the work"),
    "completed": ("Работа выполнена — оцените мастера", "Иш аткарылды — устага баа бериңиз", "Work completed — rate the pro"),
}


def notify_client(db: Session, client_key_hash: str | None, kind: str, **params) -> None:
    """Web Push to a client's browsers (sent after commit, like notify())."""
    if client_key_hash:
        db.info.setdefault("client_outbox", []).append((client_key_hash, kind, params))


def deliver_client(client_key_hash: str, kind: str, params: dict) -> None:
    from app.models import WebPushSubscription
    db = SessionLocal()
    try:
        for subscription in db.query(WebPushSubscription).filter(
                WebPushSubscription.client_key_hash == client_key_hash).limit(20).all():
            lang = subscription.lang if subscription.lang in ("ru", "ky", "en") else "ru"
            values = dict(params)
            if "price" in values:
                values["price"] = f"{values['price']:g} " + ("som" if lang == "en" else "сом")
            if "status" in values:
                values["status"] = STATUS_WORDS[values["status"]][("ru", "ky", "en").index(lang)]
            title, body = (part.format(**values) for part in CLIENT_TEXTS[kind][lang])
            webpush.send(db, subscription, title, body, CLIENT_LINKS[kind].format(id=params.get("id", "")))
    except Exception:
        logger.exception("Не удалось доставить уведомление клиенту (%s)", kind)
    finally:
        db.close()


@event.listens_for(SessionLocal, "after_commit")
def _flush_outbox(session: Session) -> None:
    for job in session.info.pop("outbox", []):
        _submit(deliver, *job)
    for job in session.info.pop("client_outbox", []):
        _submit(deliver_client, *job)


@event.listens_for(SessionLocal, "after_rollback")
def _drop_outbox(session: Session) -> None:
    session.info.pop("outbox", None)
    session.info.pop("client_outbox", None)


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
    if channel == "push":
        url = ""  # tapping the notification opens the page itself
    telegram_text, sms_text = TEMPLATES[kind][lang]
    # Email gets the full text, like Telegram; SMS and push stay short.
    template = sms_text if channel in ("sms", "push") else telegram_text
    text = template.format(id=params.get("id", ""), category=category, description=description, budget=budget, url=url,
                           fine=params.get("fine", ""), name=params.get("name", ""), text=params.get("message", ""))
    text = "\n".join(line for line in text.split("\n") if line.strip())
    return text.strip().rstrip(".").strip() if channel == "push" else text


def deliver(user_id: int, kind: str, params: dict) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        prefs = db.query(NotificationSettings).filter(NotificationSettings.user_id == user_id).first()
        if user is None or user.role != UserRole.master:
            return
        # Push to the app: the master allowed notifications on the phone — that's the consent.
        push_lang = prefs.lang if prefs and prefs.lang in SUBJECTS[kind] else "ru"
        title = SUBJECTS[kind][push_lang].format(id=params.get("id", ""), category=render_category(params.get("category", ""), push_lang))
        body = render(kind, push_lang, "push", params)
        link = LINKS[kind].format(id=params.get("id", ""))
        push.send_to_user(db, user.id, title, body, link)  # Android app (Firebase)
        webpush.send_to(db, title, body, link, user_id=user.id)  # site installed as an app / browser
        if prefs is None or not prefs.enabled:
            return
        if kind in PUSH_AND_TELEGRAM_ONLY and prefs.channel != "telegram":
            return  # chat messages would flood email/SMS
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
