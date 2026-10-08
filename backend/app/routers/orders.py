"""Orders from the master's side. Clients manage their requests through private links (routers/requests.py)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.dispatch import mark_answered
from app.models import Master, Order, OrderOffer, OrderStatus, OfferStatus, RequestDelivery, User, UserRole
from app.notify import notify
from app.schemas import (
    ContactOut,
    MasterBriefOut,
    OrderFeedOut,
    OrderOfferCreate,
    OrderOfferDetailOut,
    OrderOfferOut,
    OrderOut,
    OrderStatusUpdate,
)
from app.subscriptions import require_access

router = APIRouter(prefix="/orders", tags=["orders"])

OFFERABLE_STATUSES = [OrderStatus.searching, OrderStatus.offers_received]
MAX_ORDER_PHOTOS = 5

# Work progress the assigned master reports, in order. The master may skip steps forward
# (e.g. confirmed -> in_progress) but never go back.
MASTER_PROGRESS = [
    OrderStatus.master_selected,
    OrderStatus.master_confirmed,
    OrderStatus.master_en_route,
    OrderStatus.in_progress,
    OrderStatus.completed,
]
# The client may confirm completion themselves once the master has taken the job.
CLIENT_CAN_COMPLETE_FROM = [OrderStatus.master_confirmed, OrderStatus.master_en_route, OrderStatus.in_progress]

STATUS_TITLES = {
    OrderStatus.master_confirmed: "Мастер подтвердил заказ",
    OrderStatus.master_en_route: "Мастер выехал к вам",
    OrderStatus.in_progress: "Мастер приступил к работе",
    OrderStatus.completed: "Заказ выполнен",
}

NOT_VERIFIED = "Профиль на проверке: заявки начнут приходить, когда сервис подтвердит вас"
NO_AGREEMENT = "Примите договор с сервисом, чтобы получать заявки"


def _my_master(current_user: User) -> Master | None:
    if current_user.role == UserRole.master and current_user.master:
        return current_user.master
    return None


def _require_master(current_user: User) -> Master:
    master = _my_master(current_user)
    if master is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только мастерам")
    return master


def _require_working_master(db: Session, current_user: User) -> Master:
    """A master allowed to get requests: verified by the service (if required) and with an active plan."""
    master = _require_master(current_user)
    if current_user.agreement_accepted_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NO_AGREEMENT)
    if settings.masters_require_verification and not master.verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_VERIFIED)
    require_access(db, master)
    return master


def _is_assigned_master(order: Order, current_user: User) -> bool:
    master = _my_master(current_user)
    return master is not None and order.master_id == master.id


def _get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    return order


def _client_contact(order: Order) -> ContactOut | None:
    if order.client_name and order.client_phone:
        return ContactOut(name=order.client_name, phone=order.client_phone)
    if order.client is not None:  # orders created with an account before clients stopped registering
        return ContactOut(name=order.client.name, phone=order.client.phone)
    return None


def _order_out(order: Order, current_user: User) -> OrderOut:
    """The client's contacts and address go only to the master they selected (and admins)."""
    out = OrderOut.model_validate(order)
    if current_user.role == UserRole.admin or (
        _is_assigned_master(order, current_user) and order.status != OrderStatus.cancelled
    ):
        out.client_contact = _client_contact(order)
    else:
        out.address = None
    return out


@router.get("", response_model=list[OrderOut])
def my_jobs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Orders where clients chose this master."""
    master = _require_master(current_user)
    orders = (
        db.query(Order)
        .options(selectinload(Order.photos), selectinload(Order.client))
        .filter(Order.master_id == master.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return [_order_out(o, current_user) for o in orders]


@router.get("/feed", response_model=list[OrderFeedOut])
def order_feed(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Open requests in the master's categories and city, with the time left to answer."""
    master = _require_working_master(db, current_user)
    category_ids = {s.category_id for s in master.services}
    if not category_ids or not master.city:
        return []

    orders = (
        db.query(Order)
        .options(selectinload(Order.photos), selectinload(Order.offers))
        .filter(
            Order.status.in_(OFFERABLE_STATUSES),
            Order.category_id.in_(category_ids),
            or_(Order.client_id.is_(None), Order.client_id != current_user.id),
            # Orders created before cities existed have none — keep them visible to everyone.
            or_(Order.city == master.city, Order.city.is_(None)),
        )
        .order_by(Order.created_at.desc())
        .limit(100)
        .all()
    )
    deliveries = {
        d.order_id: d
        for d in db.query(RequestDelivery).filter(
            RequestDelivery.master_id == master.id, RequestDelivery.order_id.in_([o.id for o in orders])
        )
    }
    result = []
    for order in orders:
        out = OrderFeedOut.model_validate(order)
        out.offers_count = len(order.offers)
        mine = next((o for o in order.offers if o.master_id == master.id), None)
        out.my_offer = OrderOfferOut.model_validate(mine) if mine else None
        delivery = deliveries.get(order.id)
        if delivery is not None:
            out.deadline_at = delivery.deadline_at
            out.my_status = delivery.status
        if out.my_status == "declined":
            continue  # the master said no — don't show it again
        result.append(out)
    return result


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    if not (_is_assigned_master(order, current_user) or current_user.role == UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому заказу")
    return _order_out(order, current_user)


@router.get("/{order_id}/offers", response_model=list[OrderOfferDetailOut])
def list_offers(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    query = (
        db.query(OrderOffer)
        .options(selectinload(OrderOffer.master).selectinload(Master.user))
        .filter(OrderOffer.order_id == order.id)
        .order_by(OrderOffer.price, OrderOffer.id)
    )
    if current_user.role != UserRole.admin:
        # Masters only ever see their own offer, never competitors' prices.
        query = query.filter(OrderOffer.master_id == _require_master(current_user).id)
    return [
        OrderOfferDetailOut(
            **OrderOfferOut.model_validate(o).model_dump(),
            master=MasterBriefOut(
                id=o.master.id, name=o.master.user.name, rating=o.master.rating, verified=o.master.verified,
                completed_orders=o.master.completed_orders, phone=o.master.user.phone, photo=o.master.user.photo,
            ),
        )
        for o in query.all()
    ]


@router.post("/{order_id}/offer", response_model=OrderOfferOut)
def offer_order(
    order_id: int,
    data: OrderOfferCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    master = _require_working_master(db, current_user)
    order = _get_order(db, order_id)
    if order.status not in OFFERABLE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заказ больше не принимает предложения")
    if order.client_id is not None and order.client_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя предложить цену на свой заказ")
    if order.category_id not in {s.category_id for s in master.services}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Добавьте в профиль услугу этой категории, чтобы откликаться на такие заказы",
        )

    # One offer per master per order: a repeated offer updates the price instead of spamming the client.
    offer = db.query(OrderOffer).filter(OrderOffer.order_id == order.id, OrderOffer.master_id == master.id).first()
    if offer is not None:
        if offer.status != OfferStatus.pending:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Это предложение уже обработано")
        offer.price = data.price
        offer.comment = data.comment
    else:
        offer = OrderOffer(order_id=order.id, master_id=master.id, price=data.price, comment=data.comment)
        db.add(offer)

    order.status = OrderStatus.offers_received
    mark_answered(db, order.id, master.id, "offered")
    if order.client_id is not None:
        notify(db, order.client_id, "Новое предложение по заказу",
               f"{current_user.name} предлагает {data.price:g} сом за заказ #{order.id}")
    db.commit()
    db.refresh(offer)
    return offer


@router.post("/{order_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
def decline_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """'Not for me' — answering in time without an offer is not a violation."""
    master = _require_master(current_user)
    _get_order(db, order_id)
    mark_answered(db, order_id, master.id, "declined")
    db.commit()


@router.post("/{order_id}/status", response_model=OrderOut)
def update_status(
    order_id: int,
    data: OrderStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _get_order(db, order_id)
    if not _is_assigned_master(order, current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому заказу")
    new_status = data.status
    if order.status not in MASTER_PROGRESS or new_status not in MASTER_PROGRESS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимая смена статуса")
    if MASTER_PROGRESS.index(new_status) <= MASTER_PROGRESS.index(order.status):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Статус можно менять только вперёд")

    order.status = new_status
    if order.client_id is not None and new_status in STATUS_TITLES:
        notify(db, order.client_id, STATUS_TITLES[new_status], f"Заказ #{order.id}")
    db.commit()
    db.refresh(order)
    return _order_out(order, current_user)
