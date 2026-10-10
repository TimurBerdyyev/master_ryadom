"""Email delivery: confirmation codes and (opt-in) master notifications.

EMAIL_PROVIDER=console — letters are only written to the server log (development).
EMAIL_PROVIDER=smtp    — sent through SMTP_HOST/SMTP_PORT with SMTP_USER/SMTP_PASSWORD from SMTP_FROM.
Works with any mailbox (Gmail/Yandex/Mail.ru with an app password) or a transactional service.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

import certifi

from app.config import settings

logger = logging.getLogger("master_ryadom.email")


class EmailProvider:
    name = "base"
    # True only for the dev provider: the API may then echo codes back so sign-up works without a mailbox.
    is_dev = False

    def send(self, to: str, subject: str, text: str) -> None:
        raise NotImplementedError


class ConsoleEmail(EmailProvider):
    name = "console"
    is_dev = True

    def send(self, to: str, subject: str, text: str) -> None:
        logger.warning("EMAIL → %s: %s\n%s", to, subject, text)


class SmtpEmail(EmailProvider):
    name = "smtp"

    def send(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = settings.smtp_from or settings.smtp_user
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        # certifi's CA bundle: python.org builds on macOS ship without system root certificates.
        context = ssl.create_default_context(cafile=certifi.where())
        if settings.smtp_security == "ssl":
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=20) as smtp:
                self._deliver(smtp, message)
            return
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            # TLS is required unless explicitly disabled: never fall back to plain text silently,
            # or a network attacker could strip STARTTLS and read the SMTP password.
            if settings.smtp_security != "none":
                smtp.starttls(context=context)
            self._deliver(smtp, message)

    @staticmethod
    def _deliver(smtp: smtplib.SMTP, message: EmailMessage) -> None:
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


PROVIDERS: dict[str, EmailProvider] = {p.name: p for p in [ConsoleEmail(), SmtpEmail()]}


def get_email_provider() -> EmailProvider:
    try:
        return PROVIDERS[settings.email_provider]
    except KeyError:
        raise RuntimeError(
            f"Неизвестный EMAIL_PROVIDER={settings.email_provider!r}; доступны: {', '.join(PROVIDERS)}"
        )
