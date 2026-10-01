from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Complaint, User
from app.schemas import ComplaintCreate, ComplaintOut

router = APIRouter(prefix="/complaints", tags=["complaints"])


@router.post("", response_model=ComplaintOut)
def create_complaint(data: ComplaintCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    complaint = Complaint(author_id=current_user.id, **data.model_dump())
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint
