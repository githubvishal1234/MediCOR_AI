"""
Inbound patient messages (PF-07).

Until now every message this product sent was one-way. The confirmation
text even said "Reply or call us if you need to reschedule" -- and
replying did nothing at all, silently, which is why that wording was
removed rather than left as a promise the product could not keep. This is
the other half.

**What a patient can say.** One word, at the start of their reply:
CONFIRM, CANCEL, or STOP. Anything else is acknowledged and left for a
human, because guessing at free text on a clinical record is exactly the
sort of confident mistake this codebase avoids elsewhere (see the
exact-match rule on repeat-prescription medication names).

**Why this is not just another public endpoint.** It is unauthenticated
by necessity -- Twilio calls it, not a user -- and it acts on clinical
records. Three things carry that weight:

  * **Every request's signature is verified** against the account auth
    token, using Twilio's documented scheme, before anything is read. An
    unsigned or mis-signed request is refused. Without this, anyone who
    learned the URL could cancel any appointment they could guess a phone
    number for.

  * **The sender is matched to a patient by phone, and one match is
    required.** Two patients on one handset is a shared family phone, and
    acting on the wrong person's appointment is worse than doing nothing.

  * **It acts only on that patient's single nearest upcoming
    appointment.** There is no way to address any other.

**Why it always returns 200.** Twilio retries non-2xx responses, and a
retry storm against an endpoint that mutates appointments is worse than a
dropped message. Failures are logged and answered politely; the patient
is told to call if anything was ambiguous.
"""

import hmac
import logging
from base64 import b64encode
from datetime import datetime, timedelta
from hashlib import sha1
from typing import Optional
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import audit_service, models, schemas
from ..config import settings
from ..database import get_db
from ..realtime import appointment_summary, manager
from ..rate_limit import enforce_rate_limit

logger = logging.getLogger("inbound-messages")

router = APIRouter(prefix="/messages", tags=["patient-self-service"])

# Deliberately tiny. A patient replying to a reminder types one word; a
# longer phrase is far more likely to be a sentence we should not act on.
_CONFIRM = {"confirm", "yes", "y", "ok", "okay"}
_CANCEL = {"cancel", "no", "n"}
_STOP = {"stop", "unsubscribe", "optout", "opt-out"}
# PF-15. Deliberately its own word rather than reusing "yes": a patient
# may hold a waiting-list offer AND an upcoming appointment at the same
# time, and "yes" would be genuinely ambiguous between confirming the one
# and taking the other. The offer message says exactly what to reply.
_BOOK = {"book", "take", "accept"}


def _twiml(message: Optional[str]) -> Response:
    """
    Twilio expects TwiML. An empty <Response/> sends nothing back, which
    is what we want when there is nothing useful to say.
    """
    # Escaped: the message quotes staff-editable text (an appointment's
    # day label), and a raw "&" breaks the reply while an injected
    # "<Message to=...>" would text any number from the clinic's line.
    body = f"<Response><Message>{escape(message)}</Message></Response>" if message else "<Response/>"
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?>{body}',
                    media_type="application/xml")


def verify_twilio_signature(url: str, form: dict, signature: Optional[str]) -> bool:
    """
    Twilio's request signature: HMAC-SHA1 over the full URL with every
    POST parameter appended in alphabetical order, keyed by the account
    auth token, base64 encoded.

    Compared with `hmac.compare_digest` rather than `==`, for the same
    reason `verify_platform_key` is -- this is the only thing standing
    between a stranger and cancelling appointments.
    """
    token = settings.twilio_auth_token
    if not token or not signature:
        return False
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    expected = b64encode(
        hmac.new(token.encode("utf-8"), payload.encode("utf-8"), sha1).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _public_url(request: Request) -> str:
    """
    The URL Twilio signed, which is the one it called -- not necessarily
    the one this process sees behind a proxy. `PUBLIC_BASE_URL` exists so
    a deployment can state it; without it we use the request URL and note
    that a proxy rewriting the host will cause signature failures.
    """
    base = (settings.public_base_url or "").strip().rstrip("/")
    if base:
        return f"{base}{request.url.path}"
    return str(request.url)


def _normalise(phone: str) -> str:
    """Strip the channel prefix and spacing; Twilio sends either form."""
    p = (phone or "").strip()
    if p.startswith("whatsapp:"):
        p = p[len("whatsapp:"):]
    return p.replace(" ", "")


def _find_patient(db: Session, phone: str) -> Optional[models.Patient]:
    """
    Exactly one patient, or nobody.

    Matched across all organisations because an inbound message carries no
    tenant: the phone number is the only identifier there is. That makes
    the single-match rule load-bearing rather than merely careful -- two
    patients sharing a handset, in the same clinic or different ones, must
    resolve to neither.
    """
    if not phone:
        return None
    matches = (
        db.query(models.Patient)
        .filter(models.Patient.phone == phone, models.Patient.status != "Archived")
        .limit(2)
        .all()
    )
    if len(matches) != 1:
        if len(matches) > 1:
            logger.info("Inbound message from a number matching several patients - ignoring")
        return None
    return matches[0]


def _next_appointment(db: Session, patient: models.Patient) -> Optional[models.Appointment]:
    """Their soonest upcoming appointment, and only that one."""
    return (
        db.query(models.Appointment)
        .filter(
            models.Appointment.patient_id == patient.id,
            models.Appointment.org_id == patient.org_id,
            models.Appointment.status == "upcoming",
            models.Appointment.start_at.isnot(None),
        )
        .order_by(models.Appointment.start_at.asc())
        .first()
    )


def _pending_feedback_call(db: Session, patient: models.Patient) -> Optional[models.CallLog]:
    """
    The call we most recently asked this patient to rate, if we have and
    they have not already answered it.

    Bounded by the same window the ask uses: a digit arriving a week later
    is far more likely to be a stray keystroke than an answer.
    """
    since = datetime.utcnow() - timedelta(hours=settings.feedback_request_max_age_hours)
    return (
        db.query(models.CallLog)
        .outerjoin(models.PatientFeedback, models.PatientFeedback.call_id == models.CallLog.id)
        .filter(
            models.CallLog.org_id == patient.org_id,
            models.CallLog.patient_id == patient.id,
            models.CallLog.feedback_requested_at.isnot(None),
            models.CallLog.feedback_requested_at >= since,
            models.PatientFeedback.id.is_(None),
        )
        .order_by(models.CallLog.feedback_requested_at.desc())
        .first()
    )


def _accept_waiting_list_offer(db: Session, patient: models.Patient, request: Request):
    """
    Take the slot this patient was offered (PF-15).

    The slot was never held, so this can genuinely lose: a walk-in may
    have taken it in the meantime. That is caught by running the ordinary
    `_validate_slot` -- the same one staff and the voice agent run -- and
    the entry goes back on the list rather than being lost, so the next
    sweep can offer them something else.
    """
    from .appointments import (
        _format_day_label,
        _format_time_label,
        _validate_slot,
    )

    entry = (
        db.query(models.WaitingListEntry)
        .filter(
            models.WaitingListEntry.patient_id == patient.id,
            models.WaitingListEntry.org_id == patient.org_id,
            models.WaitingListEntry.status == "offered",
            models.WaitingListEntry.offered_start_at.isnot(None),
        )
        .order_by(models.WaitingListEntry.offered_at.desc())
        .first()
    )
    if entry is None:
        return _twiml("Thanks - there's no appointment offer open at the moment.")

    if entry.offer_expires_at and entry.offer_expires_at < datetime.utcnow():
        entry.status = "waiting"
        entry.offered_start_at = None
        entry.offer_expires_at = None
        db.commit()
        return _twiml(
            "Sorry - that offer has expired. You're still on the list and we'll "
            "let you know when something else comes free."
        )

    doctor = None
    if entry.doctor_id:
        doctor = (
            db.query(models.Doctor)
            .filter(
                models.Doctor.id == entry.doctor_id,
                models.Doctor.org_id == patient.org_id,
            )
            .first()
        )
    if doctor is None:
        # The offer was made against whichever clinician was free; resolve
        # one that can actually take this slot.
        doctor = (
            db.query(models.Doctor)
            .filter(
                models.Doctor.org_id == patient.org_id,
                models.Doctor.active == True,  # noqa: E712 - SQL Server BIT
            )
            .first()
        )
    if doctor is None:
        return _twiml("Sorry - please call the clinic and we'll book you in.")

    start_at = entry.offered_start_at
    try:
        end_at = _validate_slot(db, patient.org_id, doctor, start_at)
    except HTTPException:
        # Somebody else took it first. Back on the list, not lost.
        entry.status = "waiting"
        entry.offered_start_at = None
        entry.offer_expires_at = None
        db.commit()
        return _twiml(
            "Sorry - that time was taken just before your reply. You're still on "
            "the list and we'll offer you the next one."
        )

    appt = models.Appointment(
        org_id=patient.org_id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        title=entry.reason or "Appointment",
        patient_name=patient.name,
        reason=entry.reason,
        start_at=start_at,
        end_at=end_at,
        day_label=_format_day_label(start_at),
        time_label=_format_time_label(start_at),
        status="upcoming",
        # The patient asked for this one explicitly, in writing.
        confirmation_status="confirmed",
        confirmed_at=datetime.utcnow(),
        confirmation_source="reply",
    )
    db.add(appt)
    db.flush()

    entry.status = "booked"
    entry.resulting_appointment_id = appt.id
    entry.offered_start_at = None
    entry.offer_expires_at = None

    audit_service.record(
        db, patient.org_id, "appointment.created",
        who="Patient (waiting-list offer)",
        actor_type=audit_service.ACTOR_PATIENT,
        target_type="appointment", target_id=appt.id,
        detail=f"Accepted a waiting-list offer for {appt.day_label} {appt.time_label}",
        request=request,
    )
    db.commit()
    db.refresh(appt)
    manager.broadcast(patient.org_id, "appointment.booked", appointment_summary(appt))

    return _twiml(
        f"Booked - {appt.day_label} at {appt.time_label}. See you then."
    )


@router.post("/inbound")
async def inbound_message(request: Request, db: Session = Depends(get_db)):
    """
    Twilio delivers an inbound SMS or WhatsApp message here.

    Always answers 200: Twilio retries anything else, and a retry storm
    against an endpoint that cancels appointments is worse than a lost
    message.
    """
    enforce_rate_limit(
        request,
        bucket="inbound_message",
        max_attempts=settings.inbound_message_rate_limit_attempts,
        window_seconds=settings.inbound_message_rate_limit_window_seconds,
    )

    form = dict((await request.form()))
    signature = request.headers.get("X-Twilio-Signature")

    if not verify_twilio_signature(_public_url(request), form, signature):
        # Logged, not answered. Telling an unsigned caller why would help
        # them get it right next time.
        logger.warning("Rejected an inbound message with a bad or missing signature")
        return _twiml(None)

    sender = _normalise(form.get("From", ""))
    text = (form.get("Body") or "").strip().lower()
    first_word = text.split()[0].strip(".,!?") if text.split() else ""

    patient = _find_patient(db, sender)
    if patient is None:
        # Deliberately vague: confirming whether a number belongs to a
        # patient would make this endpoint a membership oracle.
        return _twiml("Thanks. Please call the clinic if you need anything.")

    if first_word in _STOP:
        # An opt-out is honoured even though nothing else here would be.
        # It is the one instruction a patient is always entitled to give.
        patient.contact_consent = "declined"
        patient.contact_consent_at = datetime.utcnow()
        patient.contact_consent_source = "reply"
        audit_service.record(
            db, patient.org_id, "patient.updated",
            who="Patient (message reply)",
            actor_type=audit_service.ACTOR_PATIENT,
            target_type="patient", target_id=patient.id,
            detail="contact_consent",
            request=request,
        )
        db.commit()
        return _twiml("You will not receive further messages from us.")

    # PF-10: a bare number is a rating, and cannot be confused with
    # CONFIRM / CANCEL / STOP -- which is exactly why the ask uses a
    # number. Checked before the appointment keywords so a "1" is never
    # read as anything else.
    if first_word.isdigit():
        rating = int(first_word)
        if not (schemas.FEEDBACK_RATING_MIN <= rating <= schemas.FEEDBACK_RATING_MAX):
            return _twiml(
                f"Please reply with a number from {schemas.FEEDBACK_RATING_MIN} "
                f"to {schemas.FEEDBACK_RATING_MAX}."
            )

        call = _pending_feedback_call(db, patient)
        if call is None:
            # A number with nothing outstanding to rate. Acknowledged and
            # dropped rather than filed against an arbitrary call.
            return _twiml("Thanks for your message. Please call the clinic if you need us.")

        # Whatever else they typed is kept verbatim for a person to read.
        # Never parsed, never acted on -- the same rule the rest of this
        # module follows for anything that is not an exact keyword.
        rest = " ".join(text.split()[1:]).strip() or None
        db.add(
            models.PatientFeedback(
                org_id=patient.org_id,
                call_id=call.id,
                patient_id=patient.id,
                rating=rating,
                comment=rest,
                source="reply",
            )
        )
        db.commit()
        logger.info("Recorded patient feedback for call=%s", call.id)
        return _twiml("Thank you - that helps us a lot.")

    if first_word in _BOOK:
        return _accept_waiting_list_offer(db, patient, request)

    appt = _next_appointment(db, patient)
    if appt is None:
        return _twiml("Thanks. We could not find an upcoming appointment - please call us.")

    if first_word in _CONFIRM:
        if appt.confirmation_status != "confirmed":
            appt.confirmation_status = "confirmed"
            appt.confirmed_at = datetime.utcnow()
            appt.confirmation_source = "reply"
            audit_service.record(
                db, appt.org_id, "appointment.confirmed",
                who="Patient (message reply)",
                actor_type=audit_service.ACTOR_PATIENT,
                target_type="appointment", target_id=appt.id,
                detail="Confirmed attendance by reply",
                request=request,
            )
            db.commit()
            db.refresh(appt)
            manager.broadcast(appt.org_id, "appointment.updated", appointment_summary(appt))
        return _twiml(f"Thank you - your appointment on {appt.day_label} is confirmed.")

    if first_word in _CANCEL:
        appt.status = "cancelled"
        appt.confirmation_status = None
        appt.confirmed_at = None
        appt.confirmation_source = None
        # The self-service link for a cancelled appointment has no
        # remaining purpose, so it stops being a live credential -- same
        # rule as cancelling through the link itself.
        appt.link_token_hash = None
        appt.link_expires_at = None
        audit_service.record(
            db, appt.org_id, "appointment.cancelled",
            who="Patient (message reply)",
            actor_type=audit_service.ACTOR_PATIENT,
            target_type="appointment", target_id=appt.id,
            detail="Cancelled by the patient by reply",
            request=request,
        )
        db.commit()
        db.refresh(appt)
        manager.broadcast(appt.org_id, "appointment.cancelled", appointment_summary(appt))
        return _twiml(
            f"Your appointment on {appt.day_label} has been cancelled. "
            "Please call us to rebook."
        )

    # Anything else. Not parsed, not guessed at -- acknowledged, and left
    # for a person. A wrong guess here cancels a real appointment.
    return _twiml("Thanks for your message. Please call the clinic and we will help.")
