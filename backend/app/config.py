import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    # "production" makes unsafe settings (default JWT secret, dev SMS provider, CORS *) a startup error.
    environment: str = "development"
    database_url: str = "postgresql://master_ryadom:master_ryadom@db:5432/master_ryadom"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    cors_origins: str = "*"
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 5

    # Paid plan for masters. Off until launch: while disabled every master has full access
    # and no subscription records are created. See app/subscriptions.py.
    subscriptions_enabled: bool = False
    subscription_trial_days: int = 30
    subscription_price: int = 500  # сом за 1 месяц; скидки за 3/6/12 месяцев — PLANS в subscriptions.py
    payment_provider: str = "manual"  # см. app/payments.py

    # SMS for phone confirmation codes and (opt-in) master notifications. "console" only logs
    # messages and shows the code on the page — for local development, never for production.
    sms_provider: str = "console"  # см. app/sms.py
    code_ttl_minutes: int = 10  # email confirmation codes

    # Email for (opt-in) master notifications. "console" only logs letters; "smtp" sends via the server below
    # (any mailbox provider: Gmail/Yandex/Mail.ru app password, or a transactional service).
    email_provider: str = "console"  # см. app/email.py
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # e.g. "Мастер рядом <noreply@master-ryadom.kg>"
    # starttls (port 587) | ssl (port 465) | none (only for a local relay on the same machine)
    smtp_security: str = "starttls"

    # Telegram bot for master notifications (opt-in). Empty token = Telegram option is hidden.
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  # without @, e.g. master_ryadom_bot
    telegram_webhook_secret: str = ""  # random string; Telegram sends it back in a header
    # Public address of the web client, used for links inside SMS/Telegram messages.
    # Render sets RENDER_EXTERNAL_URL (https://<name>.onrender.com) — a sensible default there.
    site_url: str = os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8080"
    # Single-service hosting (Render): the backend also serves the web client from this folder
    # and the API moves under /api, exactly like behind nginx. Empty = API only (docker compose).
    serve_web_dir: str = ""

    @field_validator("database_url")
    @classmethod
    def sqlalchemy_scheme(cls, value: str) -> str:
        # Some hosts hand out "postgres://", which SQLAlchemy 2 no longer accepts.
        return "postgresql://" + value[len("postgres://"):] if value.startswith("postgres://") else value

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_bot_username)

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
