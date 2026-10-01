from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Master, Notification, Order, OrderOffer, OrderStatus, OfferStatus, Photo, User, UserRole
from app.schemas import OrderCreate, OrderOfferCreate, OrderOfferOut, OrderOut, PhotoOut
from app.uploads import delete_upload, save_upload

router = APIRouter(prefix="/orders", tags=["orders"])

OFFERABLE_STATUSES = [OrderStatus.searching, OrderStatus.offers_received]
MAX_ORDER_PHOTOS = 5


def _check_order_access(order: Order, current_user: User) -> None:
    is_owner = order.client_id == current_user.id
    is_assigned_master = current_user.role == UserRole.master and current_user.master and order.master_id == current_user.master.id
    is_admin = current_user.role == UserRole.admin
    if not (is_owner or is_assigned_master or is_admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому заказу")


@router.post("", response_model=OrderOut)
def create_order(data: OrderCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = Order(client_id=current_user.id, status=OrderStatus.searching, **data.model_dump())
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.role == UserRole.master:
        return db.query(Order).filter(Order.master_id == current_user.master.id).all()
    return db.query(Order).filter(Order.client_id == current_user.id).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    _check_order_access(order, current_user)
    return order


@router.post("/{order_id}/offer", response_model=OrderOfferOut)
def offer_order(
    order_id: int,
    data: OrderOfferCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role != UserRole.master or not current_user.master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Только мастер может предложить цену")

    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    if order.status not in OFFERABLE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заказ больше не принимает предложения")

    offer = OrderOffer(order_id=order.id, master_id=current_user.master.id, price=data.price, comment=data.comment)
    db.add(offer)
    order.status = OrderStatus.offers_received
    db.add(Notification(
        user_id=order.client_id,
        title="Новое предложение по заказу",
        text=f"Мастер предложил цену {data.price}",
    ))
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
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
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
    db.add(Notification(
        user_id=master.user_id,
        title="Вас выбрали для заказа",
        text=f"Клиент выбрал ваше предложение по заказу #{order.id}",
    ))
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")
    if order.status in (OrderStatus.completed, OrderStatus.reviewed, OrderStatus.cancelled):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Этот заказ нельзя отменить")

    order.status = OrderStatus.cancelled
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/photos", response_model=list[PhotoOut])
async def upload_order_photos(
    order_id: int,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
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
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")

    photo = db.get(Photo, photo_id)
    if not photo or photo.order_id != order.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото не найдено")

    delete_upload(photo.url)
    db.delete(photo)
    db.commit()
