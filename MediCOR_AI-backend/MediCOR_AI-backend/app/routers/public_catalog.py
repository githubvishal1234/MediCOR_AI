"""
The plan catalogue, as the public pricing page may state it.

**This is the third unauthenticated router in the application**, after
`appointment_link.py` and `inbound_messages.py`, and unlike both of those
it touches no clinical record and no tenant at all. It reads one table --
`plans` -- which is platform configuration a super admin maintains, not
anybody's data.

It exists because the landing page was asserting prices instead. The
hardcoded figures had drifted from the catalogue in every particular:
$199 / $499 against a real $99 / $299 / $799, "500 minutes" against
1,000, a plan named "Professional" that does not exist, and "1 EHR
integration" on the one plan whose `ehr_access` is False. A visitor was
being quoted a price no super admin had set and a clinic would not be
billed -- the `recording_url` defect in its most expensive form.

Two boundaries to keep:

  * **Only active plans, and only the fields on `PublicPlanOut`.** A
    deactivated plan is one the platform has stopped selling; publishing
    it would advertise something nobody can buy. The schema is an
    explicit field list for the same reason `LinkAppointmentOut` is --
    derived from the model, a new column would start reaching anonymous
    callers the day it was added.
  * **Nothing here is org-scoped, so nothing here may ever join to one.**
    If this file ever needs `org_id`, it is the wrong file.
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..rate_limit import client_ip, enforce_rate_limit

logger = logging.getLogger("public")

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/plans", response_model=list[schemas.PublicPlanOut])
def list_public_plans(db: Session = Depends(get_db)):
    """
    Every plan currently on sale, cheapest first.

    Cheapest first because that is the order a pricing page reads in, and
    deciding it here means two clients cannot disagree about it. An empty
    list is a valid answer -- a platform that has deactivated everything
    is not an error -- and the page is expected to render something
    sensible for it rather than an empty grid.
    """
    plans = (
        db.query(models.Plan)
        .filter(models.Plan.is_active == True)  # noqa: E712 -- BIT column
        .order_by(models.Plan.monthly_price_cents.asc(), models.Plan.name.asc())
        .all()
    )
    return [
        schemas.PublicPlanOut(
            name=p.name,
            description=p.description,
            monthly_price_cents=p.monthly_price_cents or 0,
            currency=settings.billing_currency,
            voice_minutes_limit=p.voice_minutes_limit or 0,
            user_limit=p.user_limit,
            patient_limit=p.patient_limit,
            ehr_access=bool(p.ehr_access),
            knowledge_base_access=bool(p.knowledge_base_access),
        )
        for p in plans
    ]


# ---------------------------------------------------------------------
# Reaching a person
# ---------------------------------------------------------------------


@router.get("/contact", response_model=schemas.PublicContactInfoOut)
def contact_info():
    """
    The phone number and address, if this deployment has been given any.

    Returns nulls rather than placeholders when it has not, and every
    page that reads this renders the form alone in that case. A fake
    number on a public page is not a smaller problem than no number --
    somebody dials it.
    """
    return schemas.PublicContactInfoOut(
        phone=settings.support_phone.strip() or None,
        email=settings.support_email.strip() or None,
        hours=settings.support_hours.strip() or None,
    )


@router.post("/contact", response_model=schemas.ContactEnquiryAck)
def submit_enquiry(
    payload: schemas.ContactEnquiryIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    A visitor asking to be contacted.

    **Unauthenticated and world-reachable**, which sets every rule here:

      * Rate limited per IP before anything is written, like every other
        public write in this codebase. Five an hour is generous for a
        person and useless for a spammer.
      * Nothing is trusted or interpreted. The address is not verified,
        `clinic_name` is not matched against `organizations`, and the
        message is stored verbatim and never read for keywords -- the
        same rule the inbound-message webhook works under.
      * The reply says only that it arrived. No id, no reference number,
        no promised response time: an id would invite a lookup endpoint
        over other people's correspondence, and a response time is a
        promise this application cannot keep.

    Unlike the inbound Twilio webhook this does NOT swallow failures into
    a 200. Twilio retries a non-2xx and a retry storm was the worse
    outcome there; here the caller is a browser with a person watching
    it, and telling them it sent when it did not is how an enquiry is
    lost silently.
    """
    enforce_rate_limit(
        request,
        "public_contact",
        settings.contact_rate_limit_attempts,
        settings.contact_rate_limit_window_seconds,
    )

    enquiry = models.ContactEnquiry(
        name=payload.name.strip(),
        email=str(payload.email).strip(),
        phone=(payload.phone or "").strip() or None,
        clinic_name=(payload.clinic_name or "").strip() or None,
        message=payload.message.strip(),
        status="new",
        source_ip=client_ip(request),
    )
    db.add(enquiry)
    db.commit()

    logger.info("Contact enquiry received from %s", enquiry.email)
    return schemas.ContactEnquiryAck(received=True)
