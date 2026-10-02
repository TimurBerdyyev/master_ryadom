"""Payment providers for master subscriptions.

Only "manual" exists for now: a payment request is created, the master pays outside the site
(cash, bank transfer) and an admin confirms it in the admin panel.

To connect a real acquirer (FreedomPay/PayBox, Finik, MBANK, Optima, …):
  1. subclass PaymentProvider and implement create_checkout() + parse_webhook();
  2. register it in PROVIDERS and set PAYMENT_PROVIDER=<name> in .env;
  3. point the acquirer's callback URL to  POST /api/payments/webhook/<name>.
"""
from dataclasses import dataclass

from app.config import settings
from app.models import Master, SubscriptionPayment


@dataclass
class WebhookResult:
    external_id: str  # provider's payment id, saved in SubscriptionPayment.external_id
    paid: bool


class PaymentProvider:
    name = "base"

    def create_checkout(self, payment: SubscriptionPayment, master: Master) -> str | None:
        """Register the payment with the provider (set payment.external_id) and return the URL
        to send the master to, or None if the payment is confirmed manually."""
        raise NotImplementedError

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult | None:
        """Verify the provider's signature and extract the result; None for invalid/unsupported calls."""
        raise NotImplementedError


class ManualProvider(PaymentProvider):
    name = "manual"

    def create_checkout(self, payment: SubscriptionPayment, master: Master) -> str | None:
        return None

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> WebhookResult | None:
        return None


PROVIDERS: dict[str, PaymentProvider] = {p.name: p for p in [ManualProvider()]}


def get_provider() -> PaymentProvider:
    try:
        return PROVIDERS[settings.payment_provider]
    except KeyError:
        raise RuntimeError(
            f"Неизвестная платёжная система PAYMENT_PROVIDER={settings.payment_provider!r}; "
            f"доступны: {', '.join(PROVIDERS)}"
        )
