"""Sending requests to masters and enforcing the response promise.

A new request goes to every matching master (category, city, verified, with access) and starts a
RESPONSE_MINUTES timer for each of them. A master answers by offering a price or declining.
Masters who stay silent while the request is still open get a recorded miss: a fine and a rating
penalty, as the master agreement says. Requests nobody answered in time show up for the admin
("мы сами найдём вам мастера").
"""
import logging
from datetime import timedelta

from sqlalchemy import and_, func, or_
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
        # Same place, or the same region for masters who take requests from the whole region.
        .filter(or_(Master.city == order.city,
                    and_(Master.serves_region.is_(True), Master.region.is_not(None), Master.region == order.region)))
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
    """The master offered a price or declined — the timer for them stops.

    One UPDATE on a pending row: it can't overwrite a "missed" that check_missed has just recorded, and
    check_missed can't fine an answer recorded a moment earlier (it only takes rows that are still pending).
    """
    db.query(RequestDelivery).filter(
        RequestDelivery.order_id == order_id, RequestDelivery.master_id == master_id,
        RequestDelivery.status == "pending",
    ).update({RequestDelivery.status: status, RequestDelivery.resolved_at: utcnow()}, synchronize_session=False)


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
        open_order = delivery.order.status in OPEN_STATUSES
        # Claim the row only if it is still pending: the master may have answered after we read it.
        claimed = db.query(RequestDelivery).filter(
            RequestDelivery.id == delivery.id, RequestDelivery.status == "pending",
        ).update({
            RequestDelivery.status: "missed" if open_order else "closed",
            RequestDelivery.resolved_at: now,
            RequestDelivery.fine: settings.missed_request_fine if open_order else delivery.fine,
        }, synchronize_session=False)
        if not claimed or not open_order:
            continue
        master = delivery.master
        # Counters in SQL: no lost update when two misses (or a review) touch the same master at once.
        db.query(Master).filter(Master.id == master.id).update({
            Master.missed_requests: func.coalesce(Master.missed_requests, 0) + 1,
            Master.rating_penalty: func.coalesce(Master.rating_penalty, 0) + settings.missed_request_rating_penalty,
        }, synchronize_session=False)
        db.flush()
        db.expire(master, ["missed_requests", "rating_penalty"])
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
