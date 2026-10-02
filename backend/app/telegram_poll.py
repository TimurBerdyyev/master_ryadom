"""Local development: receive Telegram updates by polling (a webhook needs a public HTTPS URL).

    cd backend && python -m app.telegram_poll
"""
import logging
import time

import httpx

from app.config import settings
from app.database import SessionLocal
from app.telegram import call, handle_update


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if not settings.telegram_enabled:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN и TELEGRAM_BOT_USERNAME в backend/.env")
    call("deleteWebhook")  # polling and a webhook can't be active at the same time
    print(f"Слушаю @{settings.telegram_bot_username}… (Ctrl+C — выход)")
    offset = 0
    while True:
        try:
            updates = httpx.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates",
                json={"offset": offset, "timeout": 30},
                timeout=40,
            ).json().get("result", [])
        except httpx.HTTPError as e:
            print("Ошибка сети:", e)
            time.sleep(3)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            db = SessionLocal()
            try:
                handle_update(db, update)
            finally:
                db.close()


if __name__ == "__main__":
    main()
