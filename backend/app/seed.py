from sqlalchemy.orm import Session

from app.models import Category

DEFAULT_CATEGORIES = [
    "Сантехника",
    "Электрика",
    "Строительство",
    "Ремонт",
    "Уборка",
    "Компьютеры",
    "Ремонт телефонов",
    "Автомастера",
    "Красота",
    "Парикмахеры",
    "Грузчики",
    "Перевозки",
    "Ремонт бытовой техники",
    "Другое",
]


def seed_categories(db: Session) -> None:
    if db.query(Category).count() > 0:
        return
    for name in DEFAULT_CATEGORIES:
        db.add(Category(name=name))
    db.commit()
