import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import DEFAULT_JWT_SECRET, settings
from app.database import Base, SessionLocal, engine
from app.routers import admin, auth, categories, complaints, masters, notifications, orders, reviews
from app.seed import seed_categories

logger = logging.getLogger("master_ryadom")

os.makedirs(settings.upload_dir, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        logger.warning(
            "JWT_SECRET использует значение по умолчанию — это небезопасно для продакшена. "
            "Задайте случайный секрет в backend/.env (JWT_SECRET)."
        )

    Base.metadata.create_all(bind=engine)
    # create_all skips tables that already exist, so indexes added to models later would never
    # reach an existing database — create any missing ones explicitly.
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)

    db = SessionLocal()
    try:
        seed_categories(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Мастер рядом API", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith("/uploads/"):
        # Uploaded files are only ever images: forbid any script/plugin execution even if one slips through.
        response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    return response


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


@app.get("/health")
def health():
    return {"status": "ok"}
