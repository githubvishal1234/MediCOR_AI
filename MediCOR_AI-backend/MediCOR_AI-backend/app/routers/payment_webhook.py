"""
Razorpay's own report of what happened to a payment.

**This is the authoritative path, and the browser callback is not.** A
clinic that pays and then closes the tab, loses its connection, or is
redirected by its bank never posts back to `/billing/checkout/verify` --
they would be charged and not upgraded. This endpoint arrives regardless,
which is why it exists as well as, not instead of, the callback. Both
call the same `billing_service.fulfil_payment`, which is idempotent, so
whichever gets there first does the work and the other reports that it
was already done.

It is the **fifth unauthenticated router** in the application, after the
appointment link, inbound messages, the public catalogue and the contact
form. Four rules hold it, three of them shared with the Twilio webhook:

  * **The signature is verified against the RAW body, before the body is
    parsed.** Razorpay's documentation is explicit: re-serialising the
    parsed JSON changes key order and whitespace and the HMAC stops
    matching. An unset webhook secret verifies nothing rather than
    everything -- it fails closed.
  * **The secret is the WEBHOOK secret, not the key secret.** They are
    different values from different places in the Razorpay dashboard, and
    using one for the other fails every verification in a way that looks
    like a broken integration.
  * **It always returns 200 once the signature is good.** Razorpay
    retries a non-2xx, and a retry storm against an endpoint that grants
    subscriptions is worse than a dropped event -- the same reasoning as
    `routers/inbound_messages.py`. A bad signature is the exception: that
    gets a 400, because it is not a delivery we want repeated.
  * **An unknown event is acknowledged and ignored.** Razorpay lets an
    account subscribe to many events; reacting to one we did not plan for
    is how a refund silently becomes an upgrade.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import billing_service, models, payment_service
from ..database import get_db

logger = logging.getLogger("payments.webhook")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# The only two we act on. `payment.captured` grants the plan;
# `payment.failed` records the attempt so a clinic asking "did that go
# through?" has an answer. Everything else is acknowledged and dropped.
CAPTURED = "payment.captured"
FAILED = "payment.failed"


@router.post("/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Handle one Razorpay event.

    Note the ordering, which is the whole security of this endpoint:
    read the raw bytes, verify, and only then parse. Anything that
    touched the payload before verification would be acting on
    unauthenticated input.
    """
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not payment_service.verify_webhook_signature(raw, signature):
        logger.warning("Rejected a Razorpay webhook with an invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("Razorpay webhook body was not JSON")
        return {"received": True}

    kind = event.get("event")
    if kind not in (CAPTURED, FAILED):
        logger.info("Ignoring Razorpay event %r", kind)
        return {"received": True}

    entity = (
        event.get("payload", {}).get("payment", {}).get("entity", {}) or {}
    )
    order_id = entity.get("order_id")
    payment_id = entity.get("id")
    if not order_id:
        logger.warning("Razorpay %s carried no order_id", kind)
        return {"received": True}

    payment = (
        db.query(models.Payment)
        .filter(models.Payment.provider_order_id == order_id)
        .first()
    )
    if not payment:
        # An order this deployment did not create -- a webhook aimed at
        # the wrong environment, most likely test keys pointing at
        # production. Acknowledged so Razorpay stops retrying, and
        # logged because it means somebody's dashboard is misconfigured.
        logger.warning("Razorpay %s for unknown order %s", kind, order_id)
        return {"received": True}

    try:
        if kind == CAPTURED:
            billing_service.fulfil_payment(db, payment, payment_id, via="webhook")
        else:
            billing_service.mark_failed(db, payment)
        db.commit()
    except Exception:  # noqa: BLE001
        # Never signal a failure back to Razorpay: it would retry, and a
        # retry loop here grants subscriptions. Logged for a human.
        db.rollback()
        logger.exception("Failed to process Razorpay %s for order %s", kind, order_id)

    return {"received": True}
