from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, get_current_user_optional, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.email import get_email_provider
from app.models import Master, NotificationSettings, User, UserRole, UserStatus, utcnow
from app.rate_limit import rate_limit
from app.schemas import (
    ChangeEmailIn,
    MeOut,
    PasswordResetIn,
    SendCodeIn,
    SendCodeOut,
    Token,
    UserLogin,
    UserRegister,
)
from app.sms import get_sms_provider
from app.subscriptions import ensure_subscription
from app.verification import check_code, send_code

router = APIRouter(prefix="/auth", tags=["auth"])

register_rate_limit = rate_limit("register", max_attempts=10, window_seconds=600)
login_rate_limit = rate_limit("login", max_attempts=10, window_seconds=600)
send_code_rate_limit = rate_limit("send-code", max_attempts=20, window_seconds=3600)
reset_rate_limit = rate_limit("reset", max_attempts=10, window_seconds=600)

PHONE_TAKEN = "Пользователь с таким телефоном уже существует"
EMAIL_TAKEN = "Пользователь с таким email уже существует"


def _email_taken(db: Session, email: str, except_user_id: int | None = None) -> bool:
    query = db.query(User).filter(User.email == email)
    if except_user_id is not None:
        query = query.filter(User.id != except_user_id)
    return query.first() is not None


@router.post("/send-code", response_model=SendCodeOut, dependencies=[Depends(send_code_rate_limit)])
def send_email_code(
    data: SendCodeIn,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if data.purpose == "register":
        if _email_taken(db, data.email):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, EMAIL_TAKEN)
        if data.phone and db.query(User).filter(User.phone == data.phone).first():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, PHONE_TAKEN)
    elif data.purpose == "reset":
        if not _email_taken(db, data.email):
            # Same answer as for a real account, so the endpoint can't be used to probe addresses.
            return SendCodeOut()
    elif data.purpose == "change_email":
        if current_user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Не удалось подтвердить учётные данные")
        if _email_taken(db, data.email, except_user_id=current_user.id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, EMAIL_TAKEN)
    code = send_code(db, data.email, data.purpose, data.lang)
    return SendCodeOut(debug_code=code if get_email_provider().is_dev else None)


@router.post("/reset-password", response_model=Token, dependencies=[Depends(reset_rate_limit)])
def reset_password(data: PasswordResetIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код устарел, запросите новый")
    check_code(db, data.email, "reset", data.code)
    if user.status == UserStatus.blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт заблокирован")
    user.password_hash = hash_password(data.password)
    user.password_changed_at = utcnow()
    db.commit()
    return Token(access_token=create_access_token(user.id))


@router.post("/change-email", response_model=MeOut)
def change_email(data: ChangeEmailIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if _email_taken(db, data.email, except_user_id=current_user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, EMAIL_TAKEN)
    check_code(db, data.email, "change_email", data.code)
    current_user.email = data.email
    db.commit()
    return current_user


@router.post("/register", response_model=Token, dependencies=[Depends(register_rate_limit)])
def register(data: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.phone == data.phone).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, PHONE_TAKEN)
    if _email_taken(db, data.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, EMAIL_TAKEN)
    if not data.city:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите город, в котором вы работаете")
    if not data.accept_agreement:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Примите условия договора с сервисом")
    if data.notify_enabled and data.notify_channel == "sms" and get_sms_provider().is_dev:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SMS-уведомления пока недоступны")
    check_code(db, data.email, "register", data.code)

    user = User(
        name=data.name,
        phone=data.phone,
        password_hash=hash_password(data.password),
        role=UserRole.master,
        email=data.email,
        agreement_accepted_at=utcnow(),
        agreement_version=settings.agreement_version,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # Two simultaneous sign-ups with the same phone/email: the unique index catches the loser.
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, PHONE_TAKEN)

    master = Master(user_id=user.id, city=data.city)
    db.add(master)
    db.flush()
    if settings.subscriptions_enabled:
        ensure_subscription(db, master)  # free trial starts at sign-up
    # Notifications outside the site only with the master's explicit consent given in the form.
    db.add(NotificationSettings(
        user_id=user.id, enabled=data.notify_enabled, channel=data.notify_channel, lang=data.lang
    ))

    db.commit()
    db.refresh(user)
    return Token(access_token=create_access_token(user.id))


@router.post("/login", response_model=Token, dependencies=[Depends(login_rate_limit)])
def login(data: UserLogin, db: Session = Depends(get_db)):
    # One field for both: "+996 700 …" or "name@mail.com"
    column = User.email if "@" in data.phone else User.phone
    user = db.query(User).filter(column == data.phone).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный телефон или пароль")
    if user.status == UserStatus.blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт заблокирован")
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
