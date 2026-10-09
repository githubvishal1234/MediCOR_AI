"""
What happens after money moves (HV-5).

Deliberately separate from `payment_service.py`. That module is the
Razorpay boundary and knows nothing about clinics; this one knows nothing
about Razorpay. A second provider would replace that file and leave this
one alone, and the split is what stops provider details leaking into the
subscription rules.

**Fulfilment must be idempotent, and that is not a nicety here.** A
successful payment reaches this application at least twice by design:
Razorpay delivers a webhook at least once, *and* the browser posts the
checkout result back. Both call `fulfil_payment`. Without the guard the
clinic gets two invoices for one payment and the subscription is extended
twice. The guard is the payment's own `status`, checked and written in
the same transaction, on a row addressed by a UNIQUE `provider_order_id`.

**It mirrors the super admin path rather than inventing a second one.**
`routers/admin_plans.assign_subscription` sets the Subscription and then
copies `plan.name` and `plan.voice_minutes_limit` onto the Organization,
because the clinic-facing Billing page reads those two columns. Paying
online must land a clinic in exactly the state a human assignment would,
or the two routes drift and only one of them is ever tested.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from . import models

logger = logging.getLogger("billing")

# What a payment row can be. Narrow on purpose: nothing here tracks
# refunds, because a refund is a conversation and a status no code writes
# is the `callback_status` mistake.
STATUS_CREATED = "created"
STATUS_CAPTURED = "captured"
STATUS_FAILED = "failed"


def _add_one_month(dt: datetime) -> datetime:
    """
    The same calendar day next month, clamped to the month's length.

    Hand-rolled because `dateutil` is not a dependency and adding one for
    twelve lines would be a poor trade. Clamping matters: a payment taken
    on the 31st must not raise on a 30-day month, and rolling forward to
    the 1st would silently move a clinic's billing day.
    """
    year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    last_day = [31, 29 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


def _invoice_number(payment: models.Payment, when: datetime) -> str:
    """
    Derived from the payment, not from a counter.

    A counter would need a lock to stay unique under two concurrent
    fulfilments; deriving it means the same payment always produces the
    same number, so a double fulfilment that somehow got past the status
    guard would still not invent a second invoice identity.
    """
    return f"INV-{when.strftime('%Y%m')}-{payment.id[:8].upper()}"


def fulfil_payment(
    db: Session,
    payment: models.Payment,
    provider_payment_id: str,
    via: str,
) -> bool:
    """
    Turn a captured payment into a subscription and an invoice.

    Returns True if this call did the work, False if it was already done.
    Callers use that to tell "fulfilled" from "fulfilled again", which is
    the difference between a real event and a duplicate delivery.

    Does NOT commit. The caller owns the transaction, so the payment
    status, the subscription and the invoice all land together or not at
    all -- a partial fulfilment would either bill without granting the
    plan or grant it without a record.
    """
    if payment.status == STATUS_CAPTURED:
        logger.info(
            "payment %s already fulfilled via %s; ignoring duplicate from %s",
            payment.id, payment.fulfilled_via, via,
        )
        return False

    now = datetime.utcnow()
    payment.status = STATUS_CAPTURED
    payment.provider_payment_id = provider_payment_id
    payment.captured_at = now
    payment.fulfilled_via = via

    org = db.query(models.Organization).filter(
        models.Organization.id == payment.org_id
    ).first()
    plan = (
        db.query(models.Plan).filter(models.Plan.id == payment.plan_id).first()
        if payment.plan_id
        else None
    )

    # The plan can have been deleted from the catalogue between checkout
    # and capture. The money still moved, so the invoice is still written
    # -- only the entitlement change is skipped, and loudly.
    if org and plan:
        sub = db.query(models.Subscription).filter(
            models.Subscription.org_id == org.id
        ).first()
        if sub:
            sub.plan_id = plan.id
            sub.status = "active"
        else:
            db.add(models.Subscription(org_id=org.id, plan_id=plan.id, status="active"))

        # The same two columns admin_plans.assign_subscription mirrors,
        # for the same reason: the clinic's Billing page reads them.
        org.plan = plan.name
        org.voice_minutes_limit = plan.voice_minutes_limit
        org.next_billing_date = _add_one_month(now)
    elif not plan:
        logger.warning(
            "payment %s captured for plan_id=%r which no longer exists; "
            "invoice written, entitlement unchanged",
            payment.id, payment.plan_id,
        )

    db.add(
        models.Invoice(
            org_id=payment.org_id,
            invoice_number=_invoice_number(payment, now),
            issued_at=now,
            amount_cents=payment.amount_cents,
            status="Paid",
            # What makes this row a receipt rather than a copy of
            # something somebody typed. See the note on the column.
            payment_id=payment.id,
        )
    )

    logger.info("payment %s fulfilled via %s", payment.id, via)
    return True


def mark_failed(db: Session, payment: models.Payment) -> bool:
    """
    Record that an attempt did not succeed.

    Never touches a captured payment: Razorpay can deliver `payment.failed`
    for one attempt on an order that a later attempt paid, and letting that
    overwrite a capture would strip a clinic of a plan it paid for.
    """
    if payment.status == STATUS_CAPTURED:
        return False
    payment.status = STATUS_FAILED
    return True
