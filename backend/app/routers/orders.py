from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Master, Notification, Order, OrderOffer, OrderStatus, OfferStatus, User, UserRole
from app.schemas import OrderCreate, OrderOfferCreate, OrderOfferOut, OrderOut

router = APIRouter(prefix="/orders", tags=["orders"])


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
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
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

    offer = db.get(OrderOffer, offer_id)
    if not offer or offer.order_id != order.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предложение не найдено")

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

    order.status = OrderStatus.cancelled
    db.commit()
    db.refresh(order)
    return order
