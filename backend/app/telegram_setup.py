"""Production: point the Telegram bot at our webhook (run once after deploy or after changing SITE_URL).

    python -m app.telegram_setup
"""
from app.config import settings
from app.telegram import call


def main() -> None:
    if not settings.telegram_enabled or not settings.telegram_webhook_secret:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME и TELEGRAM_WEBHOOK_SECRET в .env")
    url = settings.site_url.rstrip("/") + "/api/telegram/webhook"
    result = call("setWebhook", url=url, secret_token=settings.telegram_webhook_secret, allowed_updates=["message"])
    print("Вебхук:", url, "→", result)


if __name__ == "__main__":
    main()
