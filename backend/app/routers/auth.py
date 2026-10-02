from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models import Master, User, UserRole, UserStatus
from app.rate_limit import rate_limit
from app.schemas import Token, UserLogin, UserOut, UserRegister
from app.subscriptions import ensure_subscription

router = APIRouter(prefix="/auth", tags=["auth"])

register_rate_limit = rate_limit("register", max_attempts=10, window_seconds=600)
login_rate_limit = rate_limit("login", max_attempts=10, window_seconds=600)


@router.post("/register", response_model=Token, dependencies=[Depends(register_rate_limit)])
def register(data: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.phone == data.phone).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким телефоном уже существует")

    user = User(
        name=data.name,
        phone=data.phone,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пользователь с таким телефоном уже существует")

    if data.role == UserRole.master:
        master = Master(user_id=user.id)
        db.add(master)
        db.flush()
        if settings.subscriptions_enabled:
            ensure_subscription(db, master)  # free trial starts at sign-up

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
