from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Notification, User
from app.schemas import NotificationOut, UnreadCountOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _mine(db: Session, current_user: User):
    return db.query(Notification).filter(Notification.user_id == current_user.id)


@router.get("", response_model=list[NotificationOut])
def list_notifications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _mine(db, current_user).order_by(Notification.created_at.desc()).limit(100).all()


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return UnreadCountOut(unread=_mine(db, current_user).filter(Notification.is_read.is_(False)).count())


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _mine(db, current_user).filter(Notification.is_read.is_(False)).update({Notification.is_read: True})
    db.commit()
