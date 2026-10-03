from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models import Master, NotificationSettings, User, UserRole, UserStatus, utcnow
from app.phone_codes import check_code, send_code
from app.rate_limit import rate_limit
from app.schemas import PasswordResetIn, SendCodeIn, SendCodeOut, Token, UserLogin, UserOut, UserRegister
from app.sms import get_sms_provider
from app.subscriptions import ensure_subscription

router = APIRouter(prefix="/auth", tags=["auth"])

register_rate_limit = rate_limit("register", max_attempts=10, window_seconds=600)
login_rate_limit = rate_limit("login", max_attempts=10, window_seconds=600)
send_code_rate_limit = rate_limit("send-code", max_attempts=20, window_seconds=3600)
reset_rate_limit = rate_limit("reset", max_attempts=10, window_seconds=600)


@router.post("/send-code", response_model=SendCodeOut, dependencies=[Depends(send_code_rate_limit)])
def send_phone_code(data: SendCodeIn, db: Session = Depends(get_db)):
    exists = db.query(User).filter(User.phone == data.phone).first() is not None
    if data.purpose == "register" and exists:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким телефоном уже существует")
    if data.purpose == "reset" and not exists:
        # Same answer as for a real account, so the endpoint can't be used to probe phone numbers.
        return SendCodeOut()
    code = send_code(db, data.phone, data.purpose)
    return SendCodeOut(debug_code=code if get_sms_provider().is_dev else None)


@router.post("/reset-password", response_model=Token, dependencies=[Depends(reset_rate_limit)])
def reset_password(data: PasswordResetIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == data.phone).first()
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код устарел, запросите новый")
    check_code(db, data.phone, "reset", data.code)
    if user.status == UserStatus.blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт заблокирован")
    user.password_hash = hash_password(data.password)
    user.password_changed_at = utcnow()
    db.commit()
    return Token(access_token=create_access_token(user.id))


@router.post("/register", response_model=Token, dependencies=[Depends(register_rate_limit)])
def register(data: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.phone == data.phone).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким телефоном уже существует")
    if data.role == UserRole.master and not data.city:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите город, в котором вы работаете")
    if data.role == UserRole.master and not data.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите email")
    check_code(db, data.phone, "register", data.code)

    user = User(
        name=data.name,
        phone=data.phone,
        password_hash=hash_password(data.password),
        role=data.role,
        email=data.email,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким телефоном уже существует")

    if data.role == UserRole.master:
        master = Master(user_id=user.id, city=data.city)
        db.add(master)
        db.flush()
        if settings.subscriptions_enabled:
            ensure_subscription(db, master)  # free trial starts at sign-up
        # Notifications outside the site only with the master's explicit consent given in the form.
        db.add(NotificationSettings(
            user_id=user.id, enabled=data.notify_enabled, channel=data.notify_channel, lang=data.lang
        ))

    try:
        db.commit()
    except IntegrityError:
        # Two simultaneous sign-ups with the same phone: the unique index catches the loser.
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким телефоном уже существует")
    db.refresh(user)
    return Token(access_token=create_access_token(user.id))


@router.post("/login", response_model=Token, dependencies=[Depends(login_rate_limit)])
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == data.phone).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный телефон или пароль")
    if user.status == UserStatus.blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Аккаунт заблокирован")
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
