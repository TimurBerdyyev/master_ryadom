from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

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
from app.dispatch import unanswered_requests
from app.models import RequestDelivery
from app.routers.orders import _client_contact
from app.schemas import (
    AdminUnansweredOut,
    AdminViolationOut,
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
    completed = db.query(func.count(Order.id), func.coalesce(func.sum(Order.price), 0)).filter(
        Order.status.in_(COMPLETED_ORDER_STATUSES)
    ).one()
    orders_completed, revenue_total = completed[0], float(completed[1])

    return AdminStats(
        users_total=db.query(User).count(),
        clients_total=db.query(User).filter(User.role == UserRole.client).count(),
        masters_total=db.query(Master).count(),
        masters_verified=db.query(Master).filter(Master.verified.is_(True)).count(),
        orders_active=db.query(Order).filter(Order.status.in_(ACTIVE_ORDER_STATUSES)).count(),
        orders_completed=orders_completed,
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
    query = db.query(Master).options(selectinload(Master.user), selectinload(Master.services), selectinload(Master.photos))
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
    query = db.query(Order).options(selectinload(Order.photos), selectinload(Order.client))
    if status_ is not None:
        query = query.filter(Order.status == status_)
    result = []
    for order in query.order_by(Order.created_at.desc()).all():
        out = OrderOut.model_validate(order)
        out.client_contact = _client_contact(order)
        result.append(out)
    return result


@router.get("/unanswered", response_model=list[AdminUnansweredOut])
def list_unanswered(db: Session = Depends(get_db)):
    """Requests no master answered in time — the service finds a master and calls the client itself."""
    result = []
    for order in unanswered_requests(db):
        contact = _client_contact(order)
        result.append(AdminUnansweredOut(
            id=order.id, category_id=order.category_id, city=order.city, description=order.description,
            client_name=contact.name if contact else None, client_phone=contact.phone if contact else None,
            created_at=order.created_at,
            masters_notified=db.query(RequestDelivery).filter(RequestDelivery.order_id == order.id).count(),
        ))
    return result


@router.get("/violations", response_model=list[AdminViolationOut])
def list_violations(db: Session = Depends(get_db)):
    """Missed 20-minute responses with their fines (as set in the master agreement)."""
    misses = (
        db.query(RequestDelivery)
        .options(selectinload(RequestDelivery.master).selectinload(Master.user))
        .filter(RequestDelivery.status == "missed")
        .order_by(RequestDelivery.deadline_at.desc())
        .limit(500)
        .all()
    )
    return [
        AdminViolationOut(
            id=d.id, order_id=d.order_id, master_id=d.master_id, master_name=d.master.user.name,
            master_phone=d.master.user.phone, deadline_at=d.deadline_at, fine=float(d.fine or 0),
        )
        for d in misses
    ]


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
