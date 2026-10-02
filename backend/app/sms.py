"""SMS providers (confirmation codes and master notifications).

"console" writes messages to the server log — for local development only.

To connect a real SMS gateway (e.g. Nikita / SMSPRO.kg, smsc, Twilio):
  1. subclass SmsProvider and implement send() with the gateway's HTTP API;
  2. register it in PROVIDERS and set SMS_PROVIDER=<name> plus its credentials in .env.
"""
import logging

from app.config import settings

logger = logging.getLogger("master_ryadom.sms")


class SmsProvider:
    name = "base"
    # True only for the dev provider: the API may then echo codes back so sign-up works without SMS.
    is_dev = False

    def send(self, phone: str, text: str) -> None:
        """Send one message; raise on failure."""
        raise NotImplementedError


class ConsoleProvider(SmsProvider):
    name = "console"
    is_dev = True

    def send(self, phone: str, text: str) -> None:
        logger.warning("SMS → %s: %s", phone, text)


PROVIDERS: dict[str, SmsProvider] = {p.name: p for p in [ConsoleProvider()]}


def get_sms_provider() -> SmsProvider:
    try:
        return PROVIDERS[settings.sms_provider]
    except KeyError:
        raise RuntimeError(
            f"Неизвестный SMS-провайдер SMS_PROVIDER={settings.sms_provider!r}; доступны: {', '.join(PROVIDERS)}"
        )
