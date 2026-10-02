from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, get_current_user_optional
from app.database import get_db
from app.models import Category, Master, Photo, Service, User, UserRole, UserStatus
from app.schemas import MasterOut, MasterProfileUpdate, PhotoOut, ServiceCreate, ServiceOut
from app.uploads import delete_upload, save_upload

router = APIRouter(prefix="/masters", tags=["masters"])

MAX_MASTER_PHOTOS = 20


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def require_master(current_user: User = Depends(get_current_user)) -> Master:
    if current_user.role != UserRole.master or not current_user.master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только мастерам")
    return current_user.master


# Load everything MasterOut serializes in a few batched queries instead of 3 per master (N+1).
MASTER_LOAD_OPTIONS = (selectinload(Master.user), selectinload(Master.services), selectinload(Master.photos))
MAX_PAGE_SIZE = 100


def mask_phone(masters_out: list[MasterOut]) -> list[MasterOut]:
    for m in masters_out:
        m.user.phone = None
    return masters_out


@router.get("", response_model=list[MasterOut])
def search_masters(
    q: str | None = Query(None, max_length=100),
    category_id: int | None = None,
    city: str | None = Query(None, max_length=255),
    verified: bool | None = None,
    min_rating: float | None = Query(None, ge=0, le=5),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    sort: str = Query("rating", pattern="^(rating|orders|distance)$"),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Master)
        .options(*MASTER_LOAD_OPTIONS)
        .join(User, Master.user_id == User.id)
        .filter(User.status == UserStatus.active)
    )
    if category_id is not None:
        # .any() instead of a join: a master with several services in one category must appear once
        query = query.filter(Master.services.any(Service.category_id == category_id))
    if q and q.strip():
        # Bound parameter, not string formatting — escape LIKE wildcards so "%" matches literally
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        query = query.filter(or_(
            User.name.ilike(pattern, escape="\\"),
            Master.description.ilike(pattern, escape="\\"),
            Master.services.any(Service.title.ilike(pattern, escape="\\")),
            Master.services.any(Service.category.has(Category.name.ilike(pattern, escape="\\"))),
        ))
    if city and city.strip():
        query = query.filter(Master.city.ilike(city.strip()))
    if verified is not None:
        query = query.filter(Master.verified == verified)
    if min_rating is not None:
        query = query.filter(Master.rating >= min_rating)

    if sort == "distance" and lat is not None and lon is not None:
        # Distance can't be ordered in SQL portably, so sort in Python and page afterwards.
        def distance(m: Master) -> float:
            if m.latitude is None or m.longitude is None:
                return float("inf")
            return haversine_km(lat, lon, m.latitude, m.longitude)

        masters = sorted(query.all(), key=distance)[offset:offset + limit]
    else:
        if sort == "orders":
            query = query.order_by(Master.completed_orders.desc(), Master.rating.desc(), Master.id)
        else:
            query = query.order_by(Master.rating.desc(), Master.completed_orders.desc(), Master.id)
        masters = query.offset(offset).limit(limit).all()

    result = [MasterOut.model_validate(m) for m in masters]
    if current_user is None:
        result = mask_phone(result)
    return result


@router.get("/me", response_model=MasterOut)
def get_my_master_profile(master: Master = Depends(require_master)):
    return master


@router.patch("/me", response_model=MasterOut)
def update_my_master_profile(
    data: MasterProfileUpdate,
    master: Master = Depends(require_master),
    db: Session = Depends(get_db),
):
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(master, field, value)
    db.commit()
    db.refresh(master)
    return master


@router.post("/me/services", response_model=ServiceOut)
def add_my_service(
    data: ServiceCreate,
    master: Master = Depends(require_master),
    db: Session = Depends(get_db),
):
    service = Service(master_id=master.id, **data.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


@router.delete("/me/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_service(
    service_id: int,
    master: Master = Depends(require_master),
    db: Session = Depends(get_db),
):
    service = db.get(Service, service_id)
    if not service or service.master_id != master.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Услуга не найдена")
    db.delete(service)
    db.commit()


@router.post("/me/photos", response_model=list[PhotoOut])
async def upload_my_photos(
    files: list[UploadFile] = File(...),
    master: Master = Depends(require_master),
    db: Session = Depends(get_db),
):
    existing_count = len(master.photos)
    if existing_count + len(files) > MAX_MASTER_PHOTOS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Максимум {MAX_MASTER_PHOTOS} фотографий работ (уже загружено {existing_count})",
        )

    photos = []
    for file in files:
        url = await save_upload(file, f"masters/{master.id}")
        photo = Photo(master_id=master.id, url=url)
        db.add(photo)
        photos.append(photo)

    db.commit()
    for photo in photos:
        db.refresh(photo)
    return photos


@router.delete("/me/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_photo(
    photo_id: int,
    master: Master = Depends(require_master),
    db: Session = Depends(get_db),
):
    photo = db.get(Photo, photo_id)
    if not photo or photo.master_id != master.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото не найдено")

    delete_upload(photo.url)
    db.delete(photo)
    db.commit()


@router.get("/{master_id}", response_model=MasterOut)
def get_master(
    master_id: int,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    master = db.query(Master).options(*MASTER_LOAD_OPTIONS).filter(Master.id == master_id).first()
    if not master or master.user.status != UserStatus.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мастер не найден")

    result = MasterOut.model_validate(master)
    if current_user is None:
        result.user.phone = None
    return result
