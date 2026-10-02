from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import Category, Master, Order, OrderOffer, OrderStatus, OfferStatus, Photo, Service, User, UserRole, UserStatus
from app.schemas import (
    ContactOut,
    MasterBriefOut,
    OrderCreate,
    OrderFeedOut,
    OrderOfferCreate,
    OrderOfferDetailOut,
    OrderOfferOut,
    OrderOut,
    OrderStatusUpdate,
    PhotoOut,
)
from app.notify import notify
from app.subscriptions import filter_masters_with_access, require_access
from app.uploads import delete_upload, save_upload

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


def _my_master(current_user: User) -> Master | None:
    if current_user.role == UserRole.master and current_user.master:
        return current_user.master
    return None


def _is_assigned_master(order: Order, current_user: User) -> bool:
    master = _my_master(current_user)
    return master is not None and order.master_id == master.id


def _check_order_access(order: Order, current_user: User) -> None:
    is_owner = order.client_id == current_user.id
    is_admin = current_user.role == UserRole.admin
    if not (is_owner or _is_assigned_master(order, current_user) or is_admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому заказу")


def _get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    return order


def _order_out(order: Order, current_user: User) -> OrderOut:
    """Contacts are exchanged only between the client and the master they selected."""
    out = OrderOut.model_validate(order)
    if order.master_id is None or order.status == OrderStatus.cancelled:
        return out
    if order.client_id == current_user.id and order.master:
        out.master_contact = ContactOut(name=order.master.user.name, phone=order.master.user.phone)
    if _is_assigned_master(order, current_user):
        out.client_contact = ContactOut(name=order.client.name, phone=order.client.phone)
    return out


# Masters notified about one new order at most (protects SMS budget and the DB on huge categories).
MAX_NEW_ORDER_NOTIFICATIONS = 300


def _notify_masters_about_new_order(db: Session, order: Order) -> None:
    category = db.get(Category, order.category_id)
    masters = (
        filter_masters_with_access(
            db.query(Master)
            .join(User, Master.user_id == User.id)
            .filter(User.status == UserStatus.active, User.id != order.client_id)
            .filter(Master.services.any(Service.category_id == order.category_id))
        )
        .limit(MAX_NEW_ORDER_NOTIFICATIONS)
        .all()
    )
    for master in masters:
        notify(
            db, master.user_id, "Новый заказ в вашей категории", f"Заказ #{order.id}: {category.name}",
            kind="new_order", id=order.id, category=category.name, description=order.description,
            price=float(order.price) if order.price else None,
        )


@router.post("", response_model=OrderOut)
def create_order(data: OrderCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = Order(client_id=current_user.id, status=OrderStatus.searching, **data.model_dump())
    db.add(order)
    db.flush()
    _notify_masters_about_new_order(db, order)
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    master = _my_master(current_user)
    query = db.query(Order).options(
        selectinload(Order.photos),
        selectinload(Order.client),
        selectinload(Order.master).selectinload(Master.user),
    )
    if master is not None:
        query = query.filter(Order.master_id == master.id)
    else:
        query = query.filter(Order.client_id == current_user.id)
    return [_order_out(o, current_user) for o in query.order_by(Order.created_at.desc()).all()]


@router.get("/feed", response_model=list[OrderFeedOut])
def order_feed(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Open orders in the categories the master works in."""
    master = _my_master(current_user)
    if master is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только мастерам")
    require_access(db, master)

    category_ids = {s.category_id for s in master.services}
    if not category_ids:
        return []

    orders = (
        db.query(Order)
        .options(selectinload(Order.photos), selectinload(Order.offers))
        .filter(
            Order.status.in_(OFFERABLE_STATUSES),
            Order.category_id.in_(category_ids),
            Order.client_id != current_user.id,
        )
        .order_by(Order.created_at.desc())
        .limit(100)
        .all()
    )
    result = []
    for order in orders:
        out = OrderFeedOut.model_validate(order)
        out.offers_count = len(order.offers)
        mine = next((o for o in order.offers if o.master_id == master.id), None)
        out.my_offer = OrderOfferOut.model_validate(mine) if mine else None
        result.append(out)
    return result


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    _check_order_access(order, current_user)
    return _order_out(order, current_user)


@router.get("/{order_id}/offers", response_model=list[OrderOfferDetailOut])
def list_offers(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    offers = (
        db.query(OrderOffer)
        .options(selectinload(OrderOffer.master).selectinload(Master.user))
        .filter(OrderOffer.order_id == order.id)
        .order_by(OrderOffer.price, OrderOffer.id)
        .all()
    )

    if order.client_id != current_user.id and current_user.role != UserRole.admin:
        # Masters only ever see their own offer, never competitors' prices.
        master = _my_master(current_user)
        if master is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому заказу")
        offers = [o for o in offers if o.master_id == master.id]

    return [
        OrderOfferDetailOut(
            **OrderOfferOut.model_validate(o).model_dump(),
            master=MasterBriefOut(
                id=o.master.id,
                name=o.master.user.name,
                rating=o.master.rating,
                verified=o.master.verified,
                completed_orders=o.master.completed_orders,
            ),
        )
        for o in offers
    ]


@router.post("/{order_id}/offer", response_model=OrderOfferOut)
def offer_order(
    order_id: int,
    data: OrderOfferCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    master = _my_master(current_user)
    if master is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Только мастер может предложить цену")
    require_access(db, master)

    order = _get_order(db, order_id)
    if order.status not in OFFERABLE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заказ больше не принимает предложения")
    if order.client_id == current_user.id:
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
        title = "Мастер изменил предложение"
    else:
        offer = OrderOffer(order_id=order.id, master_id=master.id, price=data.price, comment=data.comment)
        db.add(offer)
        title = "Новое предложение по заказу"

    order.status = OrderStatus.offers_received
    notify(db, order.client_id, title, f"{current_user.name} предлагает {data.price:g} сом за заказ #{order.id}")
    db.commit()
    db.refresh(offer)
    return offer


@router.post("/{order_id}/accept", response_model=OrderOut)
def accept_offer(
    order_id: int,
    offer_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _get_order(db, order_id)
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")
    if order.status not in OFFERABLE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заказ уже не принимает выбор предложения")

    offer = db.get(OrderOffer, offer_id)
    if not offer or offer.order_id != order.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предложение не найдено")
    if offer.status != OfferStatus.pending:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Это предложение уже обработано")

    offer.status = OfferStatus.accepted
    order.master_id = offer.master_id
    order.price = offer.price
    order.status = OrderStatus.master_selected

    for other in db.query(OrderOffer).filter(OrderOffer.order_id == order.id, OrderOffer.id != offer.id):
        other.status = OfferStatus.rejected

    master = db.get(Master, offer.master_id)
    notify(db, master.user_id, "Вас выбрали для заказа", f"Клиент выбрал ваше предложение по заказу #{order.id}",
           kind="chosen", id=order.id)
    db.commit()
    db.refresh(order)
    return _order_out(order, current_user)


@router.post("/{order_id}/status", response_model=OrderOut)
def update_status(
    order_id: int,
    data: OrderStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _get_order(db, order_id)
    new_status = data.status

    if _is_assigned_master(order, current_user):
        if order.status not in MASTER_PROGRESS or new_status not in MASTER_PROGRESS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимая смена статуса")
        if MASTER_PROGRESS.index(new_status) <= MASTER_PROGRESS.index(order.status):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Статус можно менять только вперёд")
        notify_user_id = order.client_id
    elif order.client_id == current_user.id:
        if new_status != OrderStatus.completed or order.status not in CLIENT_CAN_COMPLETE_FROM:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимая смена статуса")
        notify_user_id = order.master.user_id
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому заказу")

    order.status = new_status
    if new_status in STATUS_TITLES:
        text = f"Заказ #{order.id}"
        if new_status == OrderStatus.completed and notify_user_id == order.client_id:
            text += " — оставьте, пожалуйста, отзыв о мастере"
        notify(db, notify_user_id, STATUS_TITLES[new_status], text)
    db.commit()
    db.refresh(order)
    return _order_out(order, current_user)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")
    if order.status in (OrderStatus.completed, OrderStatus.reviewed, OrderStatus.cancelled):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Этот заказ нельзя отменить")

    order.status = OrderStatus.cancelled
    if order.master is not None:
        notify(db, order.master.user_id, "Клиент отменил заказ", f"Заказ #{order.id} отменён", kind="cancelled", id=order.id)
    db.commit()
    db.refresh(order)
    return _order_out(order, current_user)


@router.post("/{order_id}/photos", response_model=list[PhotoOut])
async def upload_order_photos(
    order_id: int,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _get_order(db, order_id)
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")

    existing_count = len(order.photos)
    if existing_count + len(files) > MAX_ORDER_PHOTOS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Максимум {MAX_ORDER_PHOTOS} фотографий на заказ (уже загружено {existing_count})",
        )

    photos = []
    for file in files:
        url = await save_upload(file, f"orders/{order.id}")
        photo = Photo(order_id=order.id, url=url)
        db.add(photo)
        photos.append(photo)

    db.commit()
    for photo in photos:
        db.refresh(photo)
    return photos


@router.delete("/{order_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order_photo(
    order_id: int,
    photo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _get_order(db, order_id)
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")

    photo = db.get(Photo, photo_id)
    if not photo or photo.order_id != order.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото не найдено")

    delete_upload(photo.url)
    db.delete(photo)
    db.commit()
