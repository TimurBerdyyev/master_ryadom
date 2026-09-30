from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Master, Service
from app.schemas import MasterOut, MasterRegister

router = APIRouter(prefix="/masters", tags=["masters"])


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


@router.get("", response_model=list[MasterOut])
def search_masters(
    category_id: int | None = None,
    city: str | None = None,
    verified: bool | None = None,
    min_rating: float | None = None,
    lat: float | None = None,
    lon: float | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Master)
    if category_id is not None:
        query = query.join(Service).filter(Service.category_id == category_id)
    if city:
        query = query.filter(Master.city == city)
    if verified is not None:
        query = query.filter(Master.verified == verified)
    if min_rating is not None:
        query = query.filter(Master.rating >= min_rating)

    masters = query.all()

    if lat is not None and lon is not None:
        def distance(m: Master) -> float:
            if m.latitude is None or m.longitude is None:
                return float("inf")
            return haversine_km(lat, lon, m.latitude, m.longitude)

        masters.sort(key=distance)
    else:
        masters.sort(key=lambda m: m.rating, reverse=True)

    return masters


@router.get("/{master_id}", response_model=MasterOut)
def get_master(master_id: int, db: Session = Depends(get_db)):
    master = db.get(Master, master_id)
    if not master:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мастер не найден")
    return master
