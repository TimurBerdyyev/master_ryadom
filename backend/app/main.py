import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import DEFAULT_JWT_SECRET, settings
from app.database import SessionLocal
from app.migrations import upgrade_database
from app.payments import get_provider
from app.email import get_email_provider
from app.sms import get_sms_provider
from app.routers import admin, auth, categories, complaints, masters, notifications, orders, reviews, subscriptions
from app.seed import seed_categories
from app.subscriptions import ensure_all_masters

logger = logging.getLogger("master_ryadom")

os.makedirs(settings.upload_dir, exist_ok=True)


def check_production_settings() -> None:
    """Refuse to start a production server with settings that are only safe for development."""
    if settings.environment != "production":
        return
    problems = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET or len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET не задан или короче 32 символов")
    if get_email_provider().is_dev:
        problems.append("EMAIL_PROVIDER=console — настройте отправку почты (SMTP_*), иначе коды подтверждения не дойдут")
    if "*" in settings.cors_origins_list:
        problems.append("CORS_ORIGINS=* — укажите домен сайта")
    if "localhost" in settings.site_url:
        problems.append("SITE_URL указывает на localhost")
    if problems:
        raise RuntimeError("ENVIRONMENT=production, но настройки небезопасны:\n  - " + "\n  - ".join(problems))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_production_settings()
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        logger.warning(
            "JWT_SECRET использует значение по умолчанию — это небезопасно для продакшена. "
            "Задайте случайный секрет в backend/.env (JWT_SECRET)."
        )

    upgrade_database()

    # Fail fast on a typo in PAYMENT_PROVIDER / SMS_PROVIDER / EMAIL_PROVIDER.
    get_provider()
    get_sms_provider()
    if get_email_provider().is_dev:
        logger.warning(
            "EMAIL_PROVIDER=console: письма не отправляются, коды подтверждения видны на странице. "
            "Только для разработки — на сервере настройте SMTP (app/email.py)."
        )

    db = SessionLocal()
    try:
        seed_categories(db)
        if settings.subscriptions_enabled:
            started = ensure_all_masters(db)
            if started:
                logger.info("Подписки: пробный период начат для %s мастеров", started)
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
    if request.url.path.startswith(("/uploads/", "/api/uploads/")):
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
app.include_router(notifications.telegram_router)
app.include_router(complaints.router)
app.include_router(admin.router)
app.include_router(subscriptions.router)
app.include_router(subscriptions.admin_router)


@app.get("/health")
def health():
    return {"status": "ok"}


def _single_service_site(api: FastAPI) -> FastAPI:
    """Web client at / and the API at /api in one process (what nginx does in docker compose)."""
    site = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    site.middleware("http")(security_headers)
    site.mount("/api", api)
    site.mount("/", StaticFiles(directory=settings.serve_web_dir, html=True), name="web")
    return site


if settings.serve_web_dir:
    app = _single_service_site(app)
