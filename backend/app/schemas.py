from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models import ComplaintStatus, OfferStatus, OrderStatus, UserRole, UserStatus


class UserRegister(BaseModel):
    name: str
    phone: str
    password: str
    role: Literal[UserRole.client, UserRole.master] = UserRole.client


class UserLogin(BaseModel):
    phone: str
    password: str


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


class MasterRegister(BaseModel):
    description: str | None = None
    experience_years: int | None = None
    city: str | None = None
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class MasterProfileUpdate(BaseModel):
    description: str | None = None
    experience_years: int | None = None
    city: str | None = None
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ServiceCreate(BaseModel):
    category_id: int
    title: str
    price_from: float


class OrderCreate(BaseModel):
    category_id: int
    description: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    price: float | None = None
    date: datetime | None = None
    time: str | None = None


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


class OrderOfferCreate(BaseModel):
    price: float
    comment: str | None = None


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
    rating: int
    text: str | None = None


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
    text: str
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
    name: str
    icon: str | None = None


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
