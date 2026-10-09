"""
Razorpay — the only place this application talks to a payment provider.

Verified against Razorpay's own documentation rather than written from
memory, because every one of these three details fails silently if it is
wrong:

  * **Orders**: `POST https://api.razorpay.com/v1/orders`, HTTP Basic auth
    with `key_id:key_secret`. `amount` is in the smallest currency
    sub-unit (paise for INR, so 9900 is Rs 99.00), `currency` is ISO 4217,
    and `receipt` is **at most 40 characters** and must be unique.
  * **Checkout signature**: HMAC-SHA256 of `order_id + "|" + payment_id`,
    keyed by the **key secret**.
  * **Webhook signature**: HMAC-SHA256 of the **raw request body**, keyed
    by the **webhook secret** — a different secret, set when the webhook
    is created in the dashboard. Razorpay's documentation is explicit that
    the body must not be parsed or re-serialised first.

Using the key secret to check a webhook, or the webhook secret to check a
checkout, fails every verification in a way that looks like a broken
integration rather than a swapped secret. They are separate arguments
here so the two cannot be confused at the call site.

**No `razorpay` SDK.** The same decision, for the same reasons, as the
absent `twilio` SDK: this codebase already speaks HTTP with `httpx` and
already implements provider HMAC schemes by hand
(`routers/inbound_messages.verify_twilio_signature`,
`webhook_service`). Adding an SDK to sign two strings and POST one form
would pull new transitive dependencies into a venv shared with the voice
gateway.

**Mock mode** (`RAZORPAY_MOCK_MODE=true`) replaces exactly the paid
service and leaves everything else real: the order record, signature
verification, fulfilment, the invoice and the subscription. It is how the
whole path is proved correct before an account exists. A mock order id is
prefixed `order_mock_` so it can never be mistaken for a real one in the
database, and mock signatures are computed with the same HMAC as real
ones — against a fixed local secret — so the verification code under test
is the code that runs in production.
"""

import hashlib
import hmac
import logging
import secrets
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger("payments")

RAZORPAY_API = "https://api.razorpay.com/v1"

# Used only when RAZORPAY_MOCK_MODE is on. Not a credential: it signs
# nothing that leaves this process, and its only job is to make the mock
# path exercise the real verification function.
_MOCK_SECRET = "mock-mode-not-a-real-secret"


class PaymentError(Exception):
    """Raised when an order cannot be created. Never carries a secret."""


def is_configured() -> bool:
    """Whether real payments can be taken."""
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def is_available() -> bool:
    """Whether the checkout flow can run at all, mock included."""
    return is_configured() or settings.razorpay_mock_mode


def public_key_id() -> str:
    """
    The key id the browser needs to open Checkout.

    Publishable by design — it identifies the account, and Razorpay's own
    integration puts it in page source. The **secret** never leaves this
    process, which is why the order is created here and not in the browser.
    """
    return settings.razorpay_key_id or "rzp_test_mock"


def _receipt_for(org_id: str) -> str:
    """
    A unique receipt reference, within Razorpay's 40-character limit.

    Org ids are already 64 characters, so the naive `f"org_{org_id}"` is
    silently rejected by the API. Truncated and salted rather than
    truncated alone, because two orders for one clinic must not collide.
    """
    return f"rcpt_{org_id[:12]}_{secrets.token_hex(8)}"[:40]


async def create_order(
    *, amount_cents: int, org_id: str, plan_name: str, currency: Optional[str] = None
) -> dict:
    """
    Create an order and return `{id, amount, currency}`.

    `amount_cents` is the smallest sub-unit already — the column is minor
    units and Razorpay wants minor units, so nothing is multiplied here.
    A conversion in this function would be a place for a factor-of-100
    error to live, which in a payment path means charging a hundred times
    too much.
    """
    if amount_cents <= 0:
        raise PaymentError("A plan with no price cannot be paid for online.")

    cur = (currency or settings.billing_currency or "INR").upper()

    if settings.razorpay_mock_mode and not is_configured():
        order_id = f"order_mock_{secrets.token_hex(10)}"
        logger.info("MOCK order %s for %s %s", order_id, amount_cents, cur)
        return {"id": order_id, "amount": amount_cents, "currency": cur}

    if not is_configured():
        raise PaymentError("Online payment is not configured.")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{RAZORPAY_API}/orders",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json={
                    "amount": amount_cents,
                    "currency": cur,
                    "receipt": _receipt_for(org_id),
                    # At most 15 pairs, 256 chars each. Enough to identify
                    # the order in Razorpay's dashboard without putting
                    # anything about a patient into a third party.
                    "notes": {"org_id": org_id, "plan": plan_name[:64]},
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Razorpay order request failed: %s", e)
        raise PaymentError("Could not reach the payment provider.") from e

    if resp.status_code >= 400:
        # Razorpay's error body describes the request, not the credential;
        # the secret only ever exists in the Authorization header httpx
        # builds. Logged for diagnosis, never returned to the caller.
        logger.warning("Razorpay order rejected: %s %s", resp.status_code, resp.text)
        raise PaymentError("The payment provider rejected that request.")

    body = resp.json()
    return {
        "id": body["id"],
        "amount": body.get("amount", amount_cents),
        "currency": body.get("currency", cur),
    }


def _hmac_sha256(message: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    The browser's word that a payment succeeded, checked.

    HMAC-SHA256 of `order_id|payment_id` keyed by the **key secret**.
    Compared with `compare_digest`, and an unset secret verifies nothing
    rather than everything — it fails closed, exactly like the Twilio
    signature check. Without this, anyone who could POST to the verify
    endpoint could claim any order was paid.
    """
    secret = _MOCK_SECRET if _using_mock() else settings.razorpay_key_secret
    if not secret or not signature:
        return False
    expected = _hmac_sha256(f"{order_id}|{payment_id}".encode("utf-8"), secret)
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """
    HMAC-SHA256 of the **raw body**, keyed by the **webhook secret**.

    `raw_body` must be the exact bytes received. Re-serialising the parsed
    JSON changes key order and whitespace and the signature stops matching
    — Razorpay's documentation says so explicitly, and it is the same rule
    `webhook_service` follows when it signs outgoing deliveries over the
    bytes it actually sends.
    """
    secret = _MOCK_SECRET if _using_mock() else settings.razorpay_webhook_secret
    if not secret or not signature:
        return False
    return hmac.compare_digest(_hmac_sha256(raw_body, secret), signature)


def _using_mock() -> bool:
    return settings.razorpay_mock_mode and not is_configured()


def mock_signature(order_id: str, payment_id: str) -> str:
    """
    A signature the mock checkout can hand back.

    Exists so `simulate` and the tests drive the *real*
    `verify_checkout_signature` rather than a bypass. A verification path
    that is skipped in testing is a verification path nobody has tested.
    """
    return _hmac_sha256(f"{order_id}|{payment_id}".encode("utf-8"), _MOCK_SECRET)


def mock_webhook_signature(raw_body: bytes) -> str:
    """The webhook equivalent of `mock_signature`, for the same reason."""
    return _hmac_sha256(raw_body, _MOCK_SECRET)


def startup_problems() -> list[str]:
    """
    What a deployment should be told about payments, in plain words.

    Not fatal: a deployment that does not take payment online is a
    supported state and always has been -- plans are assigned by a super
    admin who agreed a price with someone. These are reported so the state
    is *chosen* rather than discovered when a clinic tries to pay.
    """
    problems: list[str] = []
    if _using_mock():
        problems.append(
            "RAZORPAY_MOCK_MODE is on: checkout completes without any money "
            "moving, and invoices are written as though it had. Never run "
            "this against real clinics."
        )
    elif not is_configured():
        problems.append(
            "Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET). "
            "Clinics cannot pay online; the Billing page says so and asks them "
            "to get in touch."
        )
    elif not settings.razorpay_webhook_secret:
        problems.append(
            "RAZORPAY_WEBHOOK_SECRET is not set, so webhook deliveries cannot be "
            "verified and are rejected. A payment will still be fulfilled when the "
            "browser posts back, but a clinic that closes the tab mid-payment will "
            "pay without being upgraded."
        )
    return problems
