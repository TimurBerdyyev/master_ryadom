"""Sending requests to masters and enforcing the response promise.

A new request goes to every matching master (category, city, verified, with access) and starts a
RESPONSE_MINUTES timer for each of them. A master answers by offering a price or declining.
Masters who stay silent while the request is still open get a recorded miss: a fine and a rating
penalty, as the master agreement says. Requests nobody answered in time show up for the admin
("мы сами найдём вам мастера").
"""
import logging
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Category, Master, Order, OrderStatus, RequestDelivery, Review, Service, User, UserStatus, utcnow
from app.notify import notify
from app.subscriptions import filter_masters_with_access

logger = logging.getLogger("master_ryadom")

OPEN_STATUSES = (OrderStatus.searching, OrderStatus.offers_received)
# Most masters notified about one request (protects the SMS/email budget on big categories).
MAX_MASTERS_PER_REQUEST = 300


def eligible_masters(db: Session, order: Order) -> list[Master]:
    query = (
        db.query(Master)
        .join(User, Master.user_id == User.id)
        .filter(User.status == UserStatus.active)
        # Only masters bound by the agreement (20-minute answers, fines) get requests.
        .filter(User.agreement_accepted_at.is_not(None))
        .filter(Master.services.any(Service.category_id == order.category_id))
        .filter(Master.city == order.city)
    )
    if order.client_id is not None:
        query = query.filter(User.id != order.client_id)
    if settings.masters_require_verification:
        query = query.filter(Master.verified.is_(True))
    return filter_masters_with_access(query).limit(MAX_MASTERS_PER_REQUEST).all()


def send_request(db: Session, order: Order) -> int:
    """Notify matching masters and start their response timers (caller commits)."""
    category = db.get(Category, order.category_id)
    deadline = utcnow() + timedelta(minutes=settings.response_minutes)
    masters = eligible_masters(db, order)
    for master in masters:
        db.add(RequestDelivery(order_id=order.id, master_id=master.id, deadline_at=deadline))
        notify(
            db, master.user_id, "Новый заказ в вашей категории", f"Заказ #{order.id}: {category.name}",
            kind="new_order", id=order.id, category=category.name, description=order.description,
            price=float(order.price) if order.price else None,
        )
    return len(masters)


def mark_answered(db: Session, order_id: int, master_id: int, status: str) -> None:
    """The master offered a price or declined — the timer for them stops."""
    delivery = (
        db.query(RequestDelivery)
        .filter(RequestDelivery.order_id == order_id, RequestDelivery.master_id == master_id,
                RequestDelivery.status == "pending")
        .first()
    )
    if delivery is not None:
        delivery.status = status
        delivery.resolved_at = utcnow()


def close_request(db: Session, order: Order) -> None:
    """The request was taken or cancelled: nobody can miss it any more."""
    now = utcnow()
    db.query(RequestDelivery).filter(
        RequestDelivery.order_id == order.id, RequestDelivery.status == "pending", RequestDelivery.deadline_at >= now
    ).update({RequestDelivery.status: "closed", RequestDelivery.resolved_at: now}, synchronize_session=False)


def recalc_rating(db: Session, master: Master) -> None:
    """Shown rating = average review score minus penalties for missed requests (never below 0)."""
    average = db.query(func.avg(Review.rating)).filter(Review.master_id == master.id).scalar() or 0
    master.rating = round(max(0.0, float(average) - (master.rating_penalty or 0)), 2)


def check_missed(db: Session) -> int:
    """Record misses for every expired timer; returns how many were recorded."""
    now = utcnow()
    expired = (
        db.query(RequestDelivery)
        .filter(RequestDelivery.status == "pending", RequestDelivery.deadline_at < now)
        .all()
    )
    missed = 0
    for delivery in expired:
        delivery.resolved_at = now
        if delivery.order.status not in OPEN_STATUSES:
            delivery.status = "closed"
            continue
        delivery.status = "missed"
        delivery.fine = settings.missed_request_fine
        master = delivery.master
        master.missed_requests = (master.missed_requests or 0) + 1
        master.rating_penalty = (master.rating_penalty or 0) + settings.missed_request_rating_penalty
        recalc_rating(db, master)
        notify(
            db, master.user_id, "Пропущена заявка",
            f"Заказ #{delivery.order_id}: нет ответа за {settings.response_minutes} мин — "
            f"штраф {settings.missed_request_fine} сом, рейтинг снижен",
            kind="missed", id=delivery.order_id, fine=settings.missed_request_fine,
        )
        missed += 1
    db.commit()
    if missed:
        logger.info("Зафиксировано пропущенных заявок: %s", missed)
    return missed


def unanswered_requests(db: Session) -> list[Order]:
    """Open requests older than the response window with no offers — the service finds a master itself."""
    cutoff = utcnow() - timedelta(minutes=settings.response_minutes)
    return (
        db.query(Order)
        .filter(Order.status == OrderStatus.searching, Order.created_at < cutoff)
        .order_by(Order.created_at)
        .limit(200)
        .all()
    )
