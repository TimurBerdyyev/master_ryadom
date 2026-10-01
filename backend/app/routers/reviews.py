from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Master, Order, OrderStatus, Review, User
from app.schemas import ReviewCreate, ReviewOut

router = APIRouter(tags=["reviews"])


@router.post("/orders/{order_id}/review", response_model=ReviewOut)
def create_review(
    order_id: int,
    data: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")
    if order.client_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш заказ")
    if order.status != OrderStatus.completed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Отзыв можно оставить только для завершённого заказа")

    review = Review(
        order_id=order.id,
        master_id=order.master_id,
        client_id=current_user.id,
        rating=data.rating,
        text=data.text,
    )
    db.add(review)
    order.status = OrderStatus.reviewed

    master = db.get(Master, order.master_id)
    reviews = db.query(Review).filter(Review.master_id == master.id).all()
    ratings = [r.rating for r in reviews] + [data.rating]
    master.rating = sum(ratings) / len(ratings)
    master.completed_orders += 1

    db.commit()
    db.refresh(review)
    return review


@router.get("/masters/{master_id}/reviews", response_model=list[ReviewOut])
def list_master_reviews(master_id: int, db: Session = Depends(get_db)):
    return db.query(Review).filter(Review.master_id == master_id).order_by(Review.created_at.desc()).all()
