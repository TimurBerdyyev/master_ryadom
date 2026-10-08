"""Client requests without an account.

The client leaves a name and a phone; the response contains a one-time token that forms their private
link (/order-detail.html?token=...). Only the token's SHA-256 hash is stored, so a database leak doesn't
give access to anyone's request. Everything the client does later goes through that token.
"""
import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.dispatch import close_request, recalc_rating, send_request
from app.models import Category, Complaint, Master, Order, OrderOffer, OfferStatus, OrderStatus, Photo, Review, utcnow
from app.notify import notify
from app.rate_limit import rate_limit
from app.routers.orders import CLIENT_CAN_COMPLETE_FROM, MAX_ORDER_PHOTOS, OFFERABLE_STATUSES, STATUS_TITLES
from app.schemas import (
    ContactOut,
    MasterBriefOut,
    OrderOfferDetailOut,
    OrderOfferOut,
    ComplaintOut,
    PhotoOut,
    RequestComplaintCreate,
    RequestCreate,
    RequestCreatedOut,
    RequestOut,
    ReviewCreate,
)
from app.uploads import save_upload

router = APIRouter(prefix="/requests", tags=["requests"])

create_rate_limit = rate_limit("request", max_attempts=5, window_seconds=3600)
complaint_rate_limit = rate_limit("request-complaint", max_attempts=10, window_seconds=3600)
MAX_REQUESTS_PER_PHONE_PER_HOUR = 3


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _get(db: Session, token: str) -> Order:
    order = (
        db.query(Order)
        .options(selectinload(Order.photos), selectinload(Order.offers).selectinload(OrderOffer.master).selectinload(Master.user))
        .filter(Order.client_token_hash == _hash(token))
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return order


def _out(order: Order) -> RequestOut:
    offers = sorted(order.offers, key=lambda o: (float(o.price), o.id))
    master = next((o.master for o in offers if o.master_id == order.master_id), None) if order.master_id else None
    return RequestOut(
        id=order.id,
        category_id=order.category_id,
        city=order.city,
        description=order.description,
        address=order.address,
        price=float(order.price) if order.price is not None else None,
        date=order.date,
        time=order.time,
        status=order.status,
        created_at=order.created_at,
        response_deadline=order.created_at + timedelta(minutes=settings.response_minutes),
        client_name=order.client_name,
        client_phone=order.client_phone,
        photos=[PhotoOut.model_validate(p) for p in order.photos],
        offers=[
            OrderOfferDetailOut(
                **OrderOfferOut.model_validate(o).model_dump(),
                master=MasterBriefOut(
                    id=o.master.id, name=o.master.user.name, rating=o.master.rating, verified=o.master.verified,
                    completed_orders=o.master.completed_orders, phone=o.master.user.phone, photo=o.master.user.photo,
                ),
            )
            for o in offers
        ],
        master_id=order.master_id,
        master_contact=ContactOut(name=master.user.name, phone=master.user.phone)
        if master and order.status != OrderStatus.cancelled else None,
        review_rating=order.review.rating if order.review else None,
    )


@router.post("", response_model=RequestCreatedOut, dependencies=[Depends(create_rate_limit)])
def create_request(data: RequestCreate, db: Session = Depends(get_db)):
    if data.website:  # honeypot filled in: a bot
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Не удалось отправить заявку")
    if db.get(Category, data.category_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категория не найдена")
    recent = db.query(Order).filter(
        Order.client_phone == data.phone, Order.created_at > utcnow() - timedelta(hours=1)
    ).count()
    if recent >= MAX_REQUESTS_PER_PHONE_PER_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок, попробуйте позже")

    token = secrets.token_urlsafe(32)
    order = Order(
        client_name=data.name,
        client_phone=data.phone,
        client_token_hash=_hash(token),
        category_id=data.category_id,
        city=data.city,
        description=data.description,
        address=data.address,
        price=data.price,
        date=data.date,
        time=data.time,
        status=OrderStatus.searching,
    )
    db.add(order)
    db.flush()
    send_request(db, order)
    db.commit()
    return RequestCreatedOut(token=token, request=_out(_get(db, token)))


@router.get("/{token}", response_model=RequestOut)
def get_request(token: str, db: Session = Depends(get_db)):
    return _out(_get(db, token))


@router.post("/{token}/accept", response_model=RequestOut)
def accept_offer(token: str, offer_id: int, db: Session = Depends(get_db)):
    order = _get(db, token)
    if order.status not in OFFERABLE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заказ уже не принимает выбор предложения")
    offer = next((o for o in order.offers if o.id == offer_id), None)
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предложение не найдено")
    if offer.status != OfferStatus.pending:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Это предложение уже обработано")

    offer.status = OfferStatus.accepted
    for other in order.offers:
        if other.id != offer.id:
            other.status = OfferStatus.rejected
    order.master_id = offer.master_id
    order.price = offer.price
    order.status = OrderStatus.master_selected
    close_request(db, order)
    notify(db, offer.master.user_id, "Вас выбрали для заказа", f"Клиент выбрал ваше предложение по заказу #{order.id}",
           kind="chosen", id=order.id)
    db.commit()
    return _out(_get(db, token))


@router.post("/{token}/complete", response_model=RequestOut)
def complete(token: str, db: Session = Depends(get_db)):
    order = _get(db, token)
    if order.status not in CLIENT_CAN_COMPLETE_FROM:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимая смена статуса")
    order.status = OrderStatus.completed
    notify(db, order.master.user_id, STATUS_TITLES[OrderStatus.completed], f"Заказ #{order.id}")
    db.commit()
    return _out(_get(db, token))


@router.post("/{token}/cancel", response_model=RequestOut)
def cancel(token: str, db: Session = Depends(get_db)):
    order = _get(db, token)
    if order.status in (OrderStatus.completed, OrderStatus.reviewed, OrderStatus.cancelled):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Этот заказ нельзя отменить")
    order.status = OrderStatus.cancelled
    close_request(db, order)
    if order.master is not None:
        notify(db, order.master.user_id, "Клиент отменил заказ", f"Заказ #{order.id} отменён", kind="cancelled", id=order.id)
    db.commit()
    return _out(_get(db, token))


@router.post("/{token}/review", response_model=RequestOut)
def review(token: str, data: ReviewCreate, db: Session = Depends(get_db)):
    order = _get(db, token)
    if order.status != OrderStatus.completed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Отзыв можно оставить только для завершённого заказа")
    db.add(Review(order_id=order.id, master_id=order.master_id, rating=data.rating, text=data.text))
    order.status = OrderStatus.reviewed
    db.flush()
    master = order.master
    master.completed_orders += 1
    recalc_rating(db, master)
    db.commit()
    return _out(_get(db, token))


@router.post("/{token}/photos", response_model=list[PhotoOut])
async def upload_photos(token: str, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    order = _get(db, token)
    existing = len(order.photos)
    if existing + len(files) > MAX_ORDER_PHOTOS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Максимум {MAX_ORDER_PHOTOS} фотографий на заказ (уже загружено {existing})",
        )
    photos = []
    for file in files:
        url = await save_upload(file, f"orders/{order.id}")
        photo = Photo(order_id=order.id, url=url)
        db.add(photo)
        photos.append(photo)
    db.commit()
    for photo in photos:
        db.refresh(photo)
    return photos


@router.post("/{token}/complaint", response_model=ComplaintOut, dependencies=[Depends(complaint_rate_limit)])
def complain(token: str, data: RequestComplaintCreate, db: Session = Depends(get_db)):
    """A client's complaint about this request (and its master, if one was chosen) goes to the admin."""
    order = _get(db, token)
    complaint = Complaint(
        author_id=None, order_id=order.id, text=data.text,
        target_user_id=order.master.user_id if order.master is not None else None,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint
