"""Create (or promote) an admin account. Run from backend/: python -m app.create_admin <phone> <password> <name>"""
import sys

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import User, UserRole
from app.schemas import normalize_phone


def main() -> None:
    if len(sys.argv) != 4:
        print("Использование: python -m app.create_admin <телефон> <пароль> <имя>")
        raise SystemExit(1)

    phone, password, name = normalize_phone(sys.argv[1]), sys.argv[2], sys.argv[3]

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if user:
            user.role = UserRole.admin
            user.password_hash = hash_password(password)
            print(f"Пользователь {phone} повышен до администратора")
        else:
            user = User(name=name, phone=phone, password_hash=hash_password(password), role=UserRole.admin)
            db.add(user)
            print(f"Создан администратор {phone}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
