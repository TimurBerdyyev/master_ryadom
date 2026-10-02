"""Paid plan for masters: free trial after sign-up, then monthly payments.

Everything here is a no-op while settings.subscriptions_enabled is False — masters keep full
access and no subscription rows are created. Turning the flag on:
  * every existing master gets a fresh trial at the next startup (ensure_all_masters),
  * new masters get a trial at registration,
  * masters without access are hidden from search and can't see the feed or send offers (402).
"""
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.config import settings
from app.models import Master, MasterSubscription, SubscriptionPayment, SubscriptionPaymentStatus, utcnow

DAYS_PER_MONTH = 30
# months -> discount, %
PLANS = {1: 0, 3: 10, 6: 15, 12: 25}


def plan_price(months: int) -> int:
    return round(settings.subscription_price * months * (100 - PLANS[months]) / 100)


def ensure_subscription(db: Session, master: Master) -> MasterSubscription:
    """The master's subscription row, created with a fresh trial if missing (caller commits)."""
    if master.subscription is None:
        master.subscription = MasterSubscription(
            master_id=master.id,
            trial_ends_at=utcnow() + timedelta(days=settings.subscription_trial_days),
        )
        db.add(master.subscription)
        db.flush()
    return master.subscription


def ensure_all_masters(db: Session) -> int:
    """Give every master without a subscription a trial — runs at startup while the feature is on."""
    masters = db.query(Master).filter(~Master.subscription.has()).all()
    for master in masters:
        ensure_subscription(db, master)
    db.commit()
    return len(masters)


def access_until(sub: MasterSubscription) -> datetime:
    return max(sub.trial_ends_at, sub.paid_until or sub.trial_ends_at)


def subscription_state(sub: MasterSubscription, now: datetime | None = None) -> str:
    now = now or utcnow()
    if sub.paid_until and sub.paid_until > now:
        return "active"
    if sub.trial_ends_at > now:
        return "trial"
    return "expired"


def has_access(db: Session, master: Master) -> bool:
    if not settings.subscriptions_enabled:
        return True
    sub = ensure_subscription(db, master)
    db.commit()
    return subscription_state(sub) != "expired"


def require_access(db: Session, master: Master) -> None:
    if not has_access(db, master):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Подписка закончилась. Продлите её, чтобы получать заказы.",
        )


def filter_masters_with_access(query: Query) -> Query:
    """Hide masters whose trial/payment has run out (search results)."""
    if not settings.subscriptions_enabled:
        return query
    now = utcnow()
    return query.join(MasterSubscription, MasterSubscription.master_id == Master.id).filter(
        or_(MasterSubscription.trial_ends_at > now, MasterSubscription.paid_until > now)
    )


def mark_paid(db: Session, payment: SubscriptionPayment) -> None:
    """Confirm a payment and extend access; paid months stack on top of the remaining trial/paid time."""
    if payment.status != SubscriptionPaymentStatus.pending:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Платёж уже обработан")
    sub = ensure_subscription(db, payment.master)
    start = max(utcnow(), access_until(sub))
    sub.paid_until = start + timedelta(days=DAYS_PER_MONTH * payment.months)
    payment.status = SubscriptionPaymentStatus.paid
    payment.paid_at = utcnow()
