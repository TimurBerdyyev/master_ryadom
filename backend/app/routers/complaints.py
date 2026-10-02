from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Complaint, Order, User
from app.rate_limit import rate_limit
from app.schemas import ComplaintCreate, ComplaintOut

router = APIRouter(prefix="/complaints", tags=["complaints"])

complaint_rate_limit = rate_limit("complaint", max_attempts=10, window_seconds=3600)


@router.post("", response_model=ComplaintOut, dependencies=[Depends(complaint_rate_limit)])
def create_complaint(data: ComplaintCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.target_user_id is not None and db.get(User, data.target_user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    if data.order_id is not None:
        order = db.get(Order, data.order_id)
        # Only participants of an order may complain about it (and learn that it exists).
        is_participant = order is not None and (
            order.client_id == current_user.id or (order.master is not None and order.master.user_id == current_user.id)
        )
        if not is_participant:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Заказ не найден")

    complaint = Complaint(author_id=current_user.id, **data.model_dump())
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint
