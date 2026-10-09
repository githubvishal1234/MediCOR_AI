import html
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import (
    audit_service,
    billing_service,
    models,
    payment_service,
    schemas,
    usage_service,
)
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_admin

router = APIRouter(prefix="/billing", tags=["billing"])


# Symbols for the currencies this deployment can actually be set to.
# Anything else prints the ISO code, which is correct and unambiguous --
# guessing a symbol for an unknown currency is how a document ends up
# claiming a charge in the wrong money.
_CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "GBP": "£", "EUR": "€"}


def _format_money(cents: int, currency: str) -> str:
    """
    Minor units to a readable amount.

    The document used to hardcode "$" while BILLING_CURRENCY defaults to
    INR, so a rupee charge printed as dollars -- on the one page a clinic
    forwards to their accountant.
    """
    code = (currency or "INR").upper()
    symbol = _CURRENCY_SYMBOLS.get(code)
    figure = f"{(cents or 0) / 100:,.2f}"
    return f"{symbol}{figure}" if symbol else f"{code} {figure}"

def _format_billing_date(dt) -> str:
    """
    Cross-platform 'Mon D, YYYY' formatter (e.g. 'Sep 1, 2026').
    '%-d' is a glibc/Linux-only strftime directive — Python's strftime on
    Windows raises ValueError('Invalid format string') for it, which is what
    was crashing this endpoint. Build the day number without relying on any
    platform-specific directive instead (same approach already used for
    appointment day labels in routers/appointments.py).
    """
    return f"{dt.strftime('%b')} {dt.day}, {dt.strftime('%Y')}"


def _format_period(start, end) -> str:
    """
    The billing month as a label, e.g. "Sep 1 - Sep 30, 2026".

    Same no-'%-d' constraint as above. The year is stated once when both
    ends share it, which they do for every month that isn't a December.
    """
    if start.year == end.year:
        return (
            f"{start.strftime('%b')} {start.day} - "
            f"{end.strftime('%b')} {end.day}, {end.strftime('%Y')}"
        )
    return f"{_format_billing_date(start)} - {_format_billing_date(end)}"


@router.get("/plan", response_model=schemas.PlanOut)
def get_plan(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """
    The clinic's plan and its usage this billing month.

    HV-4: `minutes_used` is now **computed from CallLog** rather than read
    from `Organization.voice_minutes_used`, which was never incremented by
    anything and displayed a seeded 3,450 forever. Super admin already
    computed the real figure the same way, so the two views agreed on
    nothing; they now agree by construction (finding C8).
    """
    org = db.query(models.Organization).filter(models.Organization.id == user.org_id).first()
    usage = usage_service.usage_snapshot(db, org)
    grants = usage_service.entitlements(db, user.org_id)
    return schemas.PlanOut(
        # The assigned plan's name wins over `Organization.plan`, which is
        # a display column a super admin keeps in sync by hand. If the two
        # disagree, the subscription is the one enforcement reads.
        plan=grants["plan_name"] or org.plan,
        # `status` was hardcoded to the schema default, so the page showed
        # a green "Active" chip to a clinic whose subscription had been
        # cancelled. It now reflects the row enforcement actually reads.
        status=(grants["subscription_status"] or "active").replace("_", " ").title(),
        minutes_used=usage["minutes_used"],
        minutes_limit=usage["minutes_limit"] or 1,
        usage_pct=usage["usage_pct"],
        over_limit=usage["over_limit"],
        period_label=_format_period(usage["period_start"], usage["period_end"]),
        next_billing_date=_format_billing_date(org.next_billing_date) if org.next_billing_date else None,
        payment_label=org.payment_label,
        payment_expires=org.payment_expires,
        subscription_status=grants["subscription_status"],
        ehr_access=grants["ehr_access"],
        knowledge_base_access=grants["knowledge_base_access"],
        user_limit=grants["user_limit"],
        patient_limit=grants["patient_limit"],
    )


@router.get("/invoices", response_model=list[schemas.InvoiceOut])
def list_invoices(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return (
        db.query(models.Invoice)
        .filter(models.Invoice.org_id == user.org_id)
        .order_by(models.Invoice.issued_at.desc())
        .all()
    )


INVOICE_STYLE = """
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         color: #111827; margin: 0; padding: 40px; }
  .sheet { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .muted { color: #6b7280; font-size: 13px; }
  .row { display: flex; justify-content: space-between; gap: 24px;
         align-items: flex-start; margin-bottom: 32px; }
  table { width: 100%; border-collapse: collapse; margin-top: 24px; font-size: 14px; }
  th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #e5e7eb; }
  td.amount, th.amount { text-align: right; font-variant-numeric: tabular-nums; }
  tfoot td { font-weight: 700; border-bottom: none; border-top: 2px solid #111827; }
  .note { margin-top: 40px; padding: 12px 14px; background: #f9fafb;
          border-left: 3px solid #d1d5db; font-size: 12px; color: #4b5563; }
  @media print { body { padding: 0; } .noprint { display: none; } }
"""


@router.get("/invoices/{invoice_id}/document")
def invoice_document(
    invoice_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    One invoice as a self-contained printable document (GAP-B2).

    WHAT THIS IS, STATED PRECISELY, BECAUSE IT MATTERS

    Nothing in this application issues invoices. There is no billing
    run, no payment provider, and `POST /billing/upgrade` returns 501 by
    design for exactly that reason. The only code that has ever written
    an `invoices` row is `app/seed.py`, for the demo organisation.

    So this renders a row the clinic's own database already holds. It
    does not compute a charge, does not assert that a payment was taken,
    and does not present itself as a tax document -- it reproduces four
    stored fields under the clinic's name, and says on its face where
    those came from. Anything more would be manufacturing a financial
    record for a transaction that never happened, which is a worse
    outcome than the missing download button this closes.

    When real billing lands (HV-5), the row becomes authoritative and
    this document becomes an ordinary receipt with no change needed
    here. Whoever does that work should delete the provenance note
    below, and only then.

    WHY HTML AND NOT PDF

    A PDF needs a rendering library this project does not have, and
    adding a dependency to produce a document that a browser's
    Print-to-PDF already produces from this exact markup is a poor
    trade. It is also one file with no external references, so it prints
    and archives identically wherever it is opened.

    `get_current_user`, not `require_admin`: `GET /invoices` is already
    open to medical staff, and a document of a row they can already read
    in a table is not a new disclosure. Narrowing this alone would be
    inconsistent rather than safer.
    """
    invoice = (
        db.query(models.Invoice)
        .filter(models.Invoice.id == invoice_id, models.Invoice.org_id == user.org_id)
        .first()
    )
    # Recorded after the 404 and before anything is rendered: probing ids
    # must not manufacture "document accessed" rows.
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    org = db.query(models.Organization).filter(
        models.Organization.id == user.org_id
    ).first()

    audit_service.record(
        db, user.org_id, "invoice.downloaded", user=user,
        target_type="invoice", target_id=invoice.id,
        status=audit_service.STATUS_LOGGED, request=request, commit=True,
    )

    # Everything interpolated below is escaped. `invoice_number` and
    # `status` are unbounded free-text columns a super admin can set, and
    # an invoice is a document a clinic forwards to their accountant --
    # markup smuggled through either would be rendered in someone else's
    # browser.
    e = html.escape
    raw_number = invoice.invoice_number or invoice.id
    number = e(raw_number)
    # Separately sanitised, because a header is not HTML: escaping makes
    # `<` safe but leaves a newline intact, and a newline in a response
    # header is header injection. `invoice_number` is an unbounded
    # free-text column, so restrict the filename to characters that
    # cannot mean anything here.
    safe_name = "".join(
        c for c in raw_number if c.isalnum() or c in "-_."
    )[:64] or "invoice"
    issued = invoice.issued_at or datetime.utcnow()

    # The payment that produced this row, if one did. NULL means the
    # invoice predates online payment and describes a charge this
    # application never took -- which is what the note below turns on.
    payment = (
        db.query(models.Payment).filter(models.Payment.id == invoice.payment_id).first()
        if invoice.payment_id
        else None
    )
    currency = (payment.currency if payment else None) or settings.billing_currency
    amount = _format_money(invoice.amount_cents or 0, currency)
    # TWO DOCUMENTS, ONE ENDPOINT, AND NEITHER MAY LIE.
    #
    # Before HV-5 nothing in this application had ever taken a payment,
    # so every invoice was a copy of a row somebody typed and the note
    # said exactly that. Now some rows are backed by a captured Razorpay
    # payment and some are not, and the difference is `payment_id`.
    #
    # A paid row may honestly call itself a receipt. An unpaid one still
    # may not, and that wording is unchanged -- so the guarantee the
    # original note gave is preserved for every row it ever applied to.
    if payment and payment.status == "captured":
        ref = e(payment.provider_payment_id or payment.provider_order_id)
        note = (
            f"Receipt for a payment of {amount} received on "
            f"{e(_format_billing_date(payment.captured_at or issued))} "
            f"via Razorpay, reference {ref}. This is not a tax document: "
            "no tax is calculated or withheld by this application. "
            "For a billing query, contact your account administrator."
        )
    else:
        note = (
            "This document reproduces the invoice record held in your "
            "MediCOR AI account. Charges are not processed by this "
            "application, so it is a copy of your record rather than a "
            "payment receipt or a tax document. For a billing query, "
            "contact your account administrator."
        )

    body = f"""<div class="sheet">
  <div class="row">
    <div>
      <h1>Invoice {number}</h1>
      <p class="muted">Issued {e(_format_billing_date(issued))}</p>
    </div>
    <div class="muted" style="text-align:right">
      <strong style="color:#111827">{e(org.name if org else 'Clinic')}</strong><br>
      Status: {e(invoice.status or 'Unknown')}
    </div>
  </div>

  <table>
    <thead>
      <tr><th>Description</th><th class="amount">Amount</th></tr>
    </thead>
    <tbody>
      <tr>
        <td>MediCOR AI subscription</td>
        <td class="amount">{amount}</td>
      </tr>
    </tbody>
    <tfoot>
      <tr><td>Total</td><td class="amount">{amount}</td></tr>
    </tfoot>
  </table>

  <p class="note">{note}</p>
</div>"""

    page = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>Invoice {number}</title><style>{INVOICE_STYLE}</style>"
        f"</head><body>{body}</body></html>"
    )
    return Response(
        content=page,
        media_type="text/html; charset=utf-8",
        headers={
            # `inline` rather than `attachment`: the browser is the thing
            # that turns this into a PDF, and a forced download would put
            # an .html file in Downloads that the clinic then has to find
            # and open before they can print it.
            "Content-Disposition": f'inline; filename="invoice-{safe_name}.html"',
        },
    )


@router.post("/upgrade", response_model=schemas.PlanOut)
def upgrade_plan(db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    """
    Plan upgrades are not self-serve — there is no payment provider wired up.

    This used to set `plan = "Enterprise"` and `voice_minutes_limit = 15000`
    and commit, with **no payment of any kind**: any clinic admin could
    click Upgrade and get Enterprise for free. That is not a missing
    feature so much as a live hole, so the endpoint now refuses instead of
    silently granting an upgrade nobody paid for.

    HV-5 landed as `POST /billing/checkout` (Razorpay), which is the
    self-serve path. This endpoint stays a 501 rather than becoming an
    alias for it: it took no payment argument and no plan argument, so
    anything it did would still be a guess at what the clinic wanted.
    A super admin assigning the plan from the platform console remains
    the other route.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "This endpoint no longer grants plans. Choose a plan and pay for "
            "it from the Billing page, or contact us and we'll move your clinic "
            "onto the right one."
        ),
    )


# ---------------------------------------------------------------------
# Paying for a plan (HV-5, Razorpay)
# ---------------------------------------------------------------------
#
# Two endpoints, both `require_admin`: choosing and paying for a plan is
# an administrative act, not something a receptionist does mid-shift.
#
# The split exists because the browser cannot be trusted with either
# half. `checkout` decides the amount from the catalogue and creates the
# order server-side; `checkout/verify` proves the result came from
# Razorpay before anything is granted. A single endpoint taking "I paid,
# here is the amount" would be a self-service upgrade with extra steps.


@router.post("/checkout", response_model=schemas.CheckoutOut)
async def create_checkout(
    payload: schemas.CheckoutIn,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """
    Start a payment for a plan.

    **The price comes from the catalogue, never from the request.**
    `CheckoutIn` carries a plan name and nothing else; the amount is read
    from `Plan.monthly_price_cents` here. That is the whole reason this
    endpoint exists rather than the browser calling Razorpay directly.

    503 rather than 500 when Razorpay is unconfigured, because that is a
    deployment state and not a fault: a clinic that cannot pay online is
    told so plainly and asked to get in touch, which is what the platform
    did for every clinic before this existed.
    """
    if not payment_service.is_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Online payment isn't switched on for this deployment yet. "
                "Get in touch and we'll move your clinic onto the right plan."
            ),
        )

    plan = (
        db.query(models.Plan)
        .filter(models.Plan.name == payload.plan_name)
        .filter(models.Plan.is_active == True)  # noqa: E712 -- BIT column
        .first()
    )
    if not plan:
        # Same answer for "no such plan" and "not on sale": a deactivated
        # plan is one the platform stopped selling, and saying which is
        # which invites shopping for a retired price.
        raise HTTPException(status_code=404, detail="That plan isn't available.")

    org = db.query(models.Organization).filter(
        models.Organization.id == user.org_id
    ).first()

    try:
        order = await payment_service.create_order(
            amount_cents=plan.monthly_price_cents or 0,
            org_id=user.org_id,
            plan_name=plan.name,
        )
    except payment_service.PaymentError as e:
        # The message is ours, never the provider's body -- that can
        # carry request detail a clinic has no use for.
        raise HTTPException(status_code=502, detail=str(e))

    payment = models.Payment(
        org_id=user.org_id,
        plan_id=plan.id,
        plan_name=plan.name,
        provider="razorpay",
        provider_order_id=order["id"],
        amount_cents=order["amount"],
        currency=order["currency"],
        status=billing_service.STATUS_CREATED,
        created_by=user.id,
    )
    db.add(payment)
    db.flush()
    audit_service.record(
        db, user.org_id, "billing.checkout_started", user=user,
        target_type="payment", target_id=payment.id,
        status=audit_service.STATUS_LOGGED, request=request,
    )
    db.commit()

    return schemas.CheckoutOut(
        key_id=payment_service.public_key_id(),
        order_id=order["id"],
        amount_cents=order["amount"],
        currency=order["currency"],
        plan_name=plan.name,
        org_name=org.name if org else "",
        prefill_email=user.email,
        mock=not payment_service.is_configured(),
    )


@router.post("/checkout/verify", response_model=schemas.CheckoutVerifyOut)
def verify_checkout(
    payload: schemas.CheckoutVerifyIn,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """
    The browser's report that a payment succeeded, checked and acted on.

    Three things have to hold before anything is granted, and all three
    are checked here rather than trusted:

      * the signature verifies against the key secret;
      * the order exists in `payments`;
      * the order belongs to the caller's own clinic. Without that last
        check an admin at clinic A could pay for a plan and then replay
        the order id to upgrade clinic B.

    The webhook is the authoritative path -- it arrives even if the
    browser is closed mid-payment -- so this may well find the work
    already done. That is a success, reported as `newly_fulfilled=false`
    rather than an error.
    """
    if not payment_service.verify_checkout_signature(
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
        payload.razorpay_signature,
    ):
        audit_service.record(
            db, user.org_id, "billing.payment_rejected", user=user,
            status=audit_service.STATUS_BLOCKED, request=request,
            detail="checkout signature did not verify",
        )
        db.commit()
        raise HTTPException(status_code=400, detail="That payment could not be verified.")

    payment = (
        db.query(models.Payment)
        .filter(models.Payment.provider_order_id == payload.razorpay_order_id)
        .first()
    )
    if not payment or payment.org_id != user.org_id:
        # One answer for both, so a valid signature cannot be used to
        # probe which order ids exist on other clinics.
        raise HTTPException(status_code=404, detail="That payment could not be found.")

    newly = billing_service.fulfil_payment(
        db, payment, payload.razorpay_payment_id, via="checkout"
    )
    if newly:
        audit_service.record(
            db, user.org_id, "billing.payment_captured", user=user,
            target_type="payment", target_id=payment.id,
            status=audit_service.STATUS_LOGGED, request=request,
            detail=f"plan={payment.plan_name!r}",
        )
    db.commit()

    return schemas.CheckoutVerifyOut(
        paid=True, plan_name=payment.plan_name, newly_fulfilled=newly
    )


@router.get("/payments", response_model=list[schemas.PaymentOut])
def list_payments(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    This clinic's payment attempts, newest first.

    `get_current_user` rather than `require_admin`, matching `/invoices`
    beside it: medical staff can already see the plan and its invoices,
    and narrowing this alone would be an inconsistency rather than a
    boundary. Nothing here is a credential -- no card details are stored
    or returned, because none are ever received.
    """
    rows = (
        db.query(models.Payment)
        .filter(models.Payment.org_id == user.org_id)
        .order_by(models.Payment.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        schemas.PaymentOut(
            id=p.id,
            plan_name=p.plan_name,
            amount_cents=p.amount_cents,
            currency=p.currency or "INR",
            status=p.status or "created",
            created_at=p.created_at,
            captured_at=p.captured_at,
        )
        for p in rows
    ]
