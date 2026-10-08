from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Review
from app.schemas import ReviewOut

router = APIRouter(tags=["reviews"])

# Clients leave reviews through their request link: POST /requests/{token}/review.


@router.get("/masters/{master_id}/reviews", response_model=list[ReviewOut])
def list_master_reviews(master_id: int, db: Session = Depends(get_db)):
    return db.query(Review).filter(Review.master_id == master_id).order_by(Review.created_at.desc()).all()
