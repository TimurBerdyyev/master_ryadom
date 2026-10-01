import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import DEFAULT_JWT_SECRET, settings
from app.database import Base, SessionLocal, engine
from app.routers import admin, auth, categories, complaints, masters, notifications, orders, reviews
from app.seed import seed_categories

logger = logging.getLogger("master_ryadom")

os.makedirs(settings.upload_dir, exist_ok=True)

app = FastAPI(title="Мастер рядом API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(masters.router)
app.include_router(orders.router)
app.include_router(reviews.router)
app.include_router(notifications.router)
app.include_router(complaints.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup() -> None:
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        logger.warning(
            "JWT_SECRET использует значение по умолчанию — это небезопасно для продакшена. "
            "Задайте случайный секрет в backend/.env (JWT_SECRET)."
        )

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_categories(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
