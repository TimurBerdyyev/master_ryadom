from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import ComplaintStatus, OfferStatus, OrderStatus, UserRole, UserStatus

# bcrypt silently ignores bytes past 72; reject earlier with a clear error instead of a
# confusing login mismatch for passwords that differ only after that point.
PASSWORD_MAX_LENGTH = 72


class UserRegister(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    role: Literal[UserRole.client, UserRole.master] = UserRole.client


class UserLogin(BaseModel):
    phone: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None = None
    role: UserRole
    photo: str | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    icon: str | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    price_from: float
    category_id: int


class PhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    created_at: datetime


class MasterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str | None = None
    experience_years: int | None = None
    city: str | None = None
    district: str | None = None
    rating: float
    completed_orders: int
    verified: bool
    user: UserOut
    services: list[ServiceOut] = []
    photos: list[PhotoOut] = []


class MasterRegister(BaseModel):
    description: str | None = Field(None, max_length=2000)
    experience_years: int | None = Field(None, ge=0, le=80)
    city: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    latitude: float | None = None
    longitude: float | None = None


class MasterProfileUpdate(BaseModel):
    description: str | None = Field(None, max_length=2000)
    experience_years: int | None = Field(None, ge=0, le=80)
    city: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    latitude: float | None = None
    longitude: float | None = None


class ServiceCreate(BaseModel):
    category_id: int
    title: str = Field(min_length=1, max_length=255)
    price_from: float = Field(ge=0, le=10_000_000)


class OrderCreate(BaseModel):
    category_id: int
    description: str = Field(min_length=1, max_length=2000)
    address: str | None = Field(None, max_length=500)
    latitude: float | None = None
    longitude: float | None = None
    price: float | None = Field(None, ge=0, le=10_000_000)
    date: datetime | None = None
    time: str | None = Field(None, max_length=16)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    category_id: int
    master_id: int | None = None
    description: str
    address: str | None = None
    price: float | None = None
    date: datetime | None = None
    time: str | None = None
    status: OrderStatus
    created_at: datetime
    photos: list[PhotoOut] = []


class OrderOfferCreate(BaseModel):
    price: float = Field(ge=0, le=10_000_000)
    comment: str | None = Field(None, max_length=1000)


class OrderOfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    master_id: int
    price: float
    comment: str | None = None
    status: OfferStatus
    created_at: datetime


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str | None = Field(None, max_length=2000)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    master_id: int
    client_id: int
    rating: int
    text: str | None = None
    created_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    text: str | None = None
    is_read: bool
    created_at: datetime


class ComplaintCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    target_user_id: int | None = None
    order_id: int | None = None


class ComplaintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    target_user_id: int | None = None
    order_id: int | None = None
    text: str
    status: ComplaintStatus
    created_at: datetime


class ComplaintStatusUpdate(BaseModel):
    status: ComplaintStatus


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    icon: str | None = Field(None, max_length=500)


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    email: str | None = None
    role: UserRole
    status: UserStatus
    created_at: datetime


class UserStatusUpdate(BaseModel):
    status: UserStatus


class MasterVerifyUpdate(BaseModel):
    verified: bool


class AdminStats(BaseModel):
    users_total: int
    clients_total: int
    masters_total: int
    masters_verified: int
    orders_active: int
    orders_completed: int
    orders_cancelled: int
    revenue_total: float
    commission_total: float
    complaints_open: int
