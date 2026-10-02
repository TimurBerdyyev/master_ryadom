from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
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

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
