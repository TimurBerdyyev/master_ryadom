from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import (
    Category,
    Complaint,
    ComplaintStatus,
    Master,
    Order,
    OrderStatus,
    Review,
    User,
    UserRole,
    UserStatus,
)
from app.schemas import (
    AdminStats,
    AdminUserOut,
    CategoryCreate,
    CategoryOut,
    ComplaintOut,
    ComplaintStatusUpdate,
    MasterOut,
    MasterVerifyUpdate,
    OrderOut,
    ReviewOut,
    UserStatusUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

ACTIVE_ORDER_STATUSES = [
    OrderStatus.created,
    OrderStatus.searching,
    OrderStatus.offers_received,
    OrderStatus.master_selected,
    OrderStatus.master_confirmed,
    OrderStatus.master_en_route,
    OrderStatus.in_progress,
]
COMPLETED_ORDER_STATUSES = [OrderStatus.completed, OrderStatus.reviewed]
COMMISSION_RATE = 0.10


@router.get("/stats", response_model=AdminStats)
def get_stats(db: Session = Depends(get_db)):
    completed_orders = db.query(Order).filter(Order.status.in_(COMPLETED_ORDER_STATUSES)).all()
    revenue_total = sum(float(o.price or 0) for o in completed_orders)

    return AdminStats(
        users_total=db.query(User).count(),
        clients_total=db.query(User).filter(User.role == UserRole.client).count(),
        masters_total=db.query(Master).count(),
        masters_verified=db.query(Master).filter(Master.verified.is_(True)).count(),
        orders_active=db.query(Order).filter(Order.status.in_(ACTIVE_ORDER_STATUSES)).count(),
        orders_completed=len(completed_orders),
        orders_cancelled=db.query(Order).filter(Order.status == OrderStatus.cancelled).count(),
        revenue_total=revenue_total,
        commission_total=revenue_total * COMMISSION_RATE,
        complaints_open=db.query(Complaint).filter(Complaint.status == ComplaintStatus.open).count(),
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    role: UserRole | None = None,
    status_: UserStatus | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if role is not None:
        query = query.filter(User.role == role)
    if status_ is not None:
        query = query.filter(User.status == status_)
    return query.order_by(User.created_at.desc()).all()


@router.patch("/users/{user_id}/status", response_model=AdminUserOut)
def update_user_status(user_id: int, data: UserStatusUpdate, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    if user.role == UserRole.admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя заблокировать администратора")
    user.status = data.status
    db.commit()
    db.refresh(user)
    return user


@router.get("/masters", response_model=list[MasterOut])
def list_masters(verified: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(Master)
    if verified is not None:
        query = query.filter(Master.verified.is_(verified))
    return query.order_by(Master.created_at.desc()).all()


@router.patch("/masters/{master_id}/verify", response_model=MasterOut)
def verify_master(master_id: int, data: MasterVerifyUpdate, db: Session = Depends(get_db)):
    master = db.get(Master, master_id)
    if not master:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мастер не найден")
    master.verified = data.verified
    db.commit()
    db.refresh(master)
    return master


@router.get("/orders", response_model=list[OrderOut])
def list_orders(status_: OrderStatus | None = Query(None, alias="status"), db: Session = Depends(get_db)):
    query = db.query(Order)
    if status_ is not None:
        query = query.filter(Order.status == status_)
    return query.order_by(Order.created_at.desc()).all()


@router.get("/reviews", response_model=list[ReviewOut])
def list_reviews(db: Session = Depends(get_db)):
    return db.query(Review).order_by(Review.created_at.desc()).all()


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(review_id: int, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Отзыв не найден")
    db.delete(review)
    db.commit()


@router.get("/complaints", response_model=list[ComplaintOut])
def list_complaints(status_: ComplaintStatus | None = Query(None, alias="status"), db: Session = Depends(get_db)):
    query = db.query(Complaint)
    if status_ is not None:
        query = query.filter(Complaint.status == status_)
    return query.order_by(Complaint.created_at.desc()).all()


@router.patch("/complaints/{complaint_id}", response_model=ComplaintOut)
def update_complaint(complaint_id: int, data: ComplaintStatusUpdate, db: Session = Depends(get_db)):
    complaint = db.get(Complaint, complaint_id)
    if not complaint:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Жалоба не найдена")
    complaint.status = data.status
    db.commit()
    db.refresh(complaint)
    return complaint


@router.post("/categories", response_model=CategoryOut)
def create_category(data: CategoryCreate, db: Session = Depends(get_db)):
    if db.query(Category).filter(Category.name == data.name).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Такая категория уже есть")
    category = Category(**data.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категория не найдена")
    if category.services or category.orders:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Нельзя удалить категорию, пока к ней привязаны услуги или заказы",
        )
    db.delete(category)
    db.commit()
