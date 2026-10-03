import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.cities import normalize_city
from app.models import ComplaintStatus, OfferStatus, OrderStatus, SubscriptionPaymentStatus, UserRole, UserStatus

# bcrypt silently ignores bytes past 72; reject earlier with a clear error instead of a
# confusing login mismatch for passwords that differ only after that point.
PASSWORD_MAX_LENGTH = 72

PHONE_RE = re.compile(r"^\+?\d{9,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def normalize_phone(value: str) -> str:
    """Strip formatting so "+996 700-00-00-01" and "+996700000001" are the same account."""
    return re.sub(r"[\s\-()]", "", value.strip())


Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


def _valid_city(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    city = normalize_city(value)
    if city is None:
        raise ValueError("Выберите город из списка")
    return city


def _valid_email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip().lower()
    if len(value) > 255 or not EMAIL_RE.match(value):
        raise ValueError("Проверьте email — например name@gmail.com")
    return value


def _required_city(value: str) -> str:
    city = _valid_city(value)
    if city is None:
        raise ValueError("Выберите город из списка")
    return city


class UserRegister(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    role: Literal[UserRole.client, UserRole.master] = UserRole.client
    code: str = Field(pattern=r"^\d{6}$")  # SMS confirmation code from /auth/send-code
    # Masters only: explicit consent to notifications outside the site and the chosen channel.
    notify_enabled: bool = False
    notify_channel: Literal["telegram", "sms", "email"] = "email"
    lang: Literal["ru", "ky", "en"] = "ru"
    city: str | None = Field(None, max_length=100)  # required for masters: they get orders from this city
    email: str | None = Field(None, max_length=255)  # required for masters: new orders can be emailed to them

    _city = field_validator("city")(_valid_city)
    _email = field_validator("email")(_valid_email)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Имя не может быть пустым")
        return value

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str) -> str:
        value = normalize_phone(value)
        if not PHONE_RE.match(value):
            raise ValueError("Телефон должен содержать от 9 до 15 цифр, например +996700000000")
        return value


def _valid_phone(value: str) -> str:
    value = normalize_phone(value)
    if not PHONE_RE.match(value):
        raise ValueError("Телефон должен содержать от 9 до 15 цифр, например +996700000000")
    return value


class SendCodeIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)
    purpose: Literal["register", "reset"]

    _phone = field_validator("phone")(_valid_phone)


class SendCodeOut(BaseModel):
    sent: bool = True
    # Only with the development SMS provider (SMS_PROVIDER=console), so sign-up works without real SMS.
    debug_code: str | None = None


class PasswordResetIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)
    code: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)

    _phone = field_validator("phone")(_valid_phone)


class UserLogin(BaseModel):
    phone: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_phone(value)


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
    latitude: Latitude | None = None
    longitude: Longitude | None = None


class MasterProfileUpdate(BaseModel):
    description: str | None = Field(None, max_length=2000)
    experience_years: int | None = Field(None, ge=0, le=80)
    city: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    _city = field_validator("city")(_valid_city)


class ServiceCreate(BaseModel):
    category_id: int
    title: str = Field(min_length=1, max_length=255)
    price_from: float = Field(ge=0, le=10_000_000)


class OrderCreate(BaseModel):
    category_id: int
    city: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    address: str | None = Field(None, max_length=500)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    price: float | None = Field(None, ge=0, le=10_000_000)
    date: datetime | None = None
    time: str | None = Field(None, max_length=16, pattern=r"^\d{1,2}:\d{2}$")

    _city = field_validator("city")(_required_city)


class ContactOut(BaseModel):
    """Name + phone of the other side of an order, shown only once a master is selected."""
    name: str
    phone: str


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    category_id: int
    master_id: int | None = None
    city: str | None = None
    description: str
    address: str | None = None
    price: float | None = None
    date: datetime | None = None
    time: str | None = None
    status: OrderStatus
    created_at: datetime
    photos: list[PhotoOut] = []
    client_contact: ContactOut | None = None
    master_contact: ContactOut | None = None


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


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


class MasterBriefOut(BaseModel):
    id: int
    name: str
    rating: float
    verified: bool
    completed_orders: int


class OrderOfferDetailOut(OrderOfferOut):
    master: MasterBriefOut


class OrderFeedOut(BaseModel):
    """Open order as seen by masters: no address or client data until they are selected."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    city: str | None = None
    description: str
    price: float | None = None
    date: datetime | None = None
    time: str | None = None
    status: OrderStatus
    created_at: datetime
    photos: list[PhotoOut] = []
    offers_count: int = 0
    my_offer: OrderOfferOut | None = None


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


class UnreadCountOut(BaseModel):
    unread: int


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


class PublicConfigOut(BaseModel):
    cities: list[str] = []
    telegram_enabled: bool = False
    subscriptions_enabled: bool
    subscription_trial_days: int
    subscription_price: int


class SubscriptionPlanOut(BaseModel):
    months: int
    price: int
    discount: int


class SubscriptionPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    months: int
    amount: float
    status: SubscriptionPaymentStatus
    provider: str
    created_at: datetime
    paid_at: datetime | None = None


class SubscriptionOut(BaseModel):
    enabled: bool
    state: Literal["disabled", "trial", "active", "expired"]
    trial_ends_at: datetime | None = None
    paid_until: datetime | None = None
    access_until: datetime | None = None
    days_left: int = 0
    plans: list[SubscriptionPlanOut] = []
    payments: list[SubscriptionPaymentOut] = []
    manual_payments: bool = True


class CheckoutIn(BaseModel):
    months: int


class CheckoutOut(BaseModel):
    payment: SubscriptionPaymentOut
    payment_url: str | None = None


class AdminSubscriptionOut(BaseModel):
    master_id: int
    name: str
    phone: str
    state: Literal["trial", "active", "expired"]
    trial_ends_at: datetime
    paid_until: datetime | None = None
    access_until: datetime
    days_left: int


class AdminSubscriptionPaymentOut(SubscriptionPaymentOut):
    master_id: int
    master_name: str
    master_phone: str


class ExtendIn(BaseModel):
    months: int = Field(ge=1, le=24)


class NotificationSettingsOut(BaseModel):
    enabled: bool
    channel: Literal["telegram", "sms", "email"]
    lang: Literal["ru", "ky", "en"]
    email: str | None = None
    telegram_connected: bool
    telegram_available: bool


class NotificationSettingsIn(BaseModel):
    enabled: bool
    channel: Literal["telegram", "sms", "email"]
    lang: Literal["ru", "ky", "en"] = "ru"
    email: str | None = Field(None, max_length=255)

    _email = field_validator("email")(_valid_email)


class TelegramLinkOut(BaseModel):
    url: str
