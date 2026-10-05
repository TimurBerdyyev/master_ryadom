from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, selectinload

from app.auth import require_admin
from app.cities import CITIES
from app.config import settings
from app.database import get_db
from app.models import Master, MasterSubscription, SubscriptionPayment, SubscriptionPaymentStatus, utcnow
from app.payments import PROVIDERS, get_provider
from app.sms import get_sms_provider
from app.routers.masters import require_master
from app.schemas import (
    AdminSubscriptionOut,
    AdminSubscriptionPaymentOut,
    CheckoutIn,
    CheckoutOut,
    ExtendIn,
    PublicConfigOut,
    SubscriptionOut,
    SubscriptionPaymentOut,
    SubscriptionPlanOut,
)
from app.subscriptions import PLANS, access_until, ensure_subscription, mark_paid, plan_price, subscription_state

router = APIRouter(tags=["subscriptions"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _days_left(sub: MasterSubscription) -> int:
    seconds = (access_until(sub) - utcnow()).total_seconds()
    return max(0, ceil(seconds / 86400))


def _require_enabled() -> None:
    if not settings.subscriptions_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Подписка пока не включена")


@router.get("/config", response_model=PublicConfigOut)
def public_config():
    """Feature flags the web client needs before login (e.g. to mention the free trial)."""
    return PublicConfigOut(
        cities=CITIES,
        telegram_enabled=settings.telegram_enabled,
        sms_enabled=not get_sms_provider().is_dev,
        subscriptions_enabled=settings.subscriptions_enabled,
        subscription_trial_days=settings.subscription_trial_days,
        subscription_price=settings.subscription_price,
    )


@router.get("/subscription/me", response_model=SubscriptionOut)
def my_subscription(master: Master = Depends(require_master), db: Session = Depends(get_db)):
    plans = [SubscriptionPlanOut(months=m, price=plan_price(m), discount=d) for m, d in PLANS.items()]
    if not settings.subscriptions_enabled:
        return SubscriptionOut(enabled=False, state="disabled", plans=plans)

    sub = ensure_subscription(db, master)
    db.commit()
    payments = (
        db.query(SubscriptionPayment)
        .filter(SubscriptionPayment.master_id == master.id)
        .order_by(SubscriptionPayment.created_at.desc())
        .limit(20)
        .all()
    )
    return SubscriptionOut(
        enabled=True,
        state=subscription_state(sub),
        trial_ends_at=sub.trial_ends_at,
        paid_until=sub.paid_until,
        access_until=access_until(sub),
        days_left=_days_left(sub),
        plans=plans,
        payments=payments,
        manual_payments=get_provider().name == "manual",
    )


@router.post("/subscription/me/checkout", response_model=CheckoutOut)
def checkout(data: CheckoutIn, master: Master = Depends(require_master), db: Session = Depends(get_db)):
    _require_enabled()
    if data.months not in PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недоступный срок подписки")

    provider = get_provider()
    # One open request at a time: a new choice replaces the previous unpaid one.
    for old in db.query(SubscriptionPayment).filter(
        SubscriptionPayment.master_id == master.id,
        SubscriptionPayment.status == SubscriptionPaymentStatus.pending,
    ):
        old.status = SubscriptionPaymentStatus.cancelled

    payment = SubscriptionPayment(
        master_id=master.id, months=data.months, amount=plan_price(data.months), provider=provider.name
    )
    db.add(payment)
    db.flush()
    payment_url = provider.create_checkout(payment, master)
    db.commit()
    db.refresh(payment)
    return CheckoutOut(payment=SubscriptionPaymentOut.model_validate(payment), payment_url=payment_url)


@router.post("/payments/webhook/{provider_name}")
async def payment_webhook(provider_name: str, request: Request, db: Session = Depends(get_db)):
    """Callback from a payment provider. The provider verifies its own signature in parse_webhook()."""
    provider = PROVIDERS.get(provider_name)
    result = provider.parse_webhook(await request.body(), dict(request.headers)) if provider else None
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Платёж не найден")

    payment = (
        db.query(SubscriptionPayment)
        .filter(SubscriptionPayment.provider == provider_name, SubscriptionPayment.external_id == result.external_id)
        .first()
    )
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Платёж не найден")
    # Providers retry callbacks: an already processed payment is acknowledged, not re-applied.
    if result.paid and payment.status == SubscriptionPaymentStatus.pending:
        mark_paid(db, payment)
        db.commit()
    return {"ok": True}


# ---------- admin ----------


@admin_router.get("/subscriptions", response_model=list[AdminSubscriptionOut])
def admin_subscriptions(db: Session = Depends(get_db)):
    subs = db.query(MasterSubscription).options(
        selectinload(MasterSubscription.master).selectinload(Master.user)
    ).all()
    result = [
        AdminSubscriptionOut(
            master_id=s.master_id,
            name=s.master.user.name,
            phone=s.master.user.phone,
            state=subscription_state(s),
            trial_ends_at=s.trial_ends_at,
            paid_until=s.paid_until,
            access_until=access_until(s),
            days_left=_days_left(s),
        )
        for s in subs
    ]
    return sorted(result, key=lambda r: r.access_until)


@admin_router.get("/subscription-payments", response_model=list[AdminSubscriptionPaymentOut])
def admin_subscription_payments(
    status_: SubscriptionPaymentStatus | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    query = db.query(SubscriptionPayment).options(selectinload(SubscriptionPayment.master).selectinload(Master.user))
    if status_ is not None:
        query = query.filter(SubscriptionPayment.status == status_)
    return [
        AdminSubscriptionPaymentOut(
            **SubscriptionPaymentOut.model_validate(p).model_dump(),
            master_id=p.master_id,
            master_name=p.master.user.name,
            master_phone=p.master.user.phone,
        )
        for p in query.order_by(SubscriptionPayment.created_at.desc()).limit(200)
    ]


@admin_router.post("/subscription-payments/{payment_id}/confirm", response_model=SubscriptionPaymentOut)
def admin_confirm_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.get(SubscriptionPayment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Платёж не найден")
    mark_paid(db, payment)
    db.commit()
    db.refresh(payment)
    return payment


@admin_router.post("/subscriptions/{master_id}/extend", response_model=SubscriptionPaymentOut)
def admin_extend(master_id: int, data: ExtendIn, db: Session = Depends(get_db)):
    """Grant months for free (promo, compensation) — recorded as a zero payment for the audit trail."""
    _require_enabled()
    master = db.get(Master, master_id)
    if master is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мастер не найден")
    payment = SubscriptionPayment(master_id=master.id, months=data.months, amount=0, provider="admin")
    db.add(payment)
    db.flush()
    mark_paid(db, payment)
    db.commit()
    db.refresh(payment)
    return payment
