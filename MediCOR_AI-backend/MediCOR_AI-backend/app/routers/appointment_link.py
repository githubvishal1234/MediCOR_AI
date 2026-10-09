"""
Patient self-service for ONE appointment, via a signed link (PF-06).

This is the only unauthenticated router in the product that acts on
clinical records, so the boundary is drawn deliberately narrow.

**What the token is.** A random 32-byte value, stored only as its SHA-256
hash, carried in a link we send to the patient. Exactly the construction
staff invitations already use. It differs in one way: it is not
single-use. An invitation is accepted once; an appointment link has to
keep working until the appointment happens, because a patient will open
it, think about it, and come back. Expiry does the bounding instead.

**What it is scoped to.** One appointment. Not a patient, not a session.
A token that resolved to a patient would, if forwarded to a family group
or leaked from a phone, expose their whole record and every booking they
have. `LinkAppointmentOut` is the complete list of what a holder can ever
see, and it deliberately contains no record number, no phone, no email,
no date of birth, no other appointments and no clinical history. The
patient's own first name is included only so they can tell the link is
theirs -- a full name would let a forwarded link confirm to a stranger
that a specific named person is a patient of this clinic.

**What it can do.** Confirm attendance, cancel, and reschedule within the
clinic's genuine availability. Nothing else -- and every one of those runs
the same validation the staff and voice-agent paths run, by calling the
same helpers rather than reimplementing the rules. A link cannot create an
appointment, cannot touch a different one, and cannot read or write a
patient record.

**Why every response is deliberately vague about failure.** An unknown
token, an expired token, a cancelled appointment and a past appointment
all return the same 404. Distinguishing them would turn this endpoint into
an oracle for guessing tokens, which is exactly the reasoning behind the
identical error the invitation flow returns.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from .. import audit_service, models, schemas
from ..config import settings
from ..database import get_db
from ..rate_limit import enforce_rate_limit
from ..realtime import appointment_summary, manager
from ..security import hash_appointment_link_token
from .appointments import (
    _format_day_label,
    _format_time_label,
    _validate_slot,
    get_availability,
)

router = APIRouter(prefix="/appointment-link", tags=["patient-self-service"])

# One message for every failure mode. See the module docstring.
_INVALID = HTTPException(
    status_code=404,
    detail="This link is no longer valid. Please call the clinic.",
)


def _rate_limit(request: Request) -> None:
    enforce_rate_limit(
        request,
        bucket="appointment_link",
        max_attempts=settings.appointment_link_rate_limit_attempts,
        window_seconds=settings.appointment_link_rate_limit_window_seconds,
    )


def _resolve(db: Session, token: str) -> models.Appointment:
    """
    Token -> appointment, or the one generic failure.

    Note what is checked here rather than at each call site: expiry, and
    that the appointment is still live. Putting it in the resolver means a
    route added later cannot forget it.
    """
    token = (token or "").strip()
    if not token:
        raise _INVALID

    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.link_token_hash == hash_appointment_link_token(token))
        .first()
    )
    if appt is None:
        raise _INVALID
    if appt.link_expires_at is None or appt.link_expires_at < datetime.utcnow():
        raise _INVALID
    # A cancelled appointment has nothing left to self-serve, and a
    # cancelled-then-reopened link would be a confusing thing to hold.
    if appt.status == "cancelled":
        raise _INVALID

    org = db.query(models.Organization).filter(models.Organization.id == appt.org_id).first()
    if org is None or org.suspended:
        # A suspended clinic is cut off everywhere else; this is not the
        # one door left open.
        raise _INVALID
    return appt


def _doctor(db: Session, appt: models.Appointment) -> Optional[models.Doctor]:
    if not appt.doctor_id:
        return None
    return db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first()


def _view(db: Session, appt: models.Appointment) -> schemas.LinkAppointmentOut:
    """
    The complete set of facts a link holder may ever see.

    Built field by field rather than from the ORM object, so that adding a
    column to `Appointment` can never silently widen what a public,
    unauthenticated caller receives.
    """
    doctor = _doctor(db, appt)
    org = db.query(models.Organization).filter(models.Organization.id == appt.org_id).first()

    first_name = None
    if appt.patient_id:
        patient = (
            db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first()
        )
        if patient and patient.name:
            first_name = patient.name.strip().split()[0]

    return schemas.LinkAppointmentOut(
        clinic_name=(org.name if org else "the clinic"),
        patient_first_name=first_name,
        doctor_name=(doctor.name if doctor else None),
        title=appt.title,
        day_label=appt.day_label,
        time_label=appt.time_label,
        location=appt.location,
        status=appt.status,
        confirmation_status=appt.confirmation_status,
        can_reschedule=bool(appt.doctor_id),
        pre_visit_submitted=(
            db.query(models.PreVisitSubmission)
            .filter(models.PreVisitSubmission.appointment_id == appt.id)
            .first()
            is not None
        ),
    )


@router.get("", response_model=schemas.LinkAppointmentOut)
def view_appointment(
    request: Request,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """What the patient sees when they open the link."""
    _rate_limit(request)
    appt = _resolve(db, token)
    # Deliberately NOT audited. A patient looking at their own appointment
    # is not a record access by a third party, and logging every page load
    # would bury the accesses that matter -- the same reasoning that keeps
    # list endpoints out of the audit log.
    return _view(db, appt)


@router.post("/confirm", response_model=schemas.LinkAppointmentOut)
def confirm_appointment(
    request: Request,
    payload: schemas.LinkTokenIn,
    db: Session = Depends(get_db),
):
    """
    The patient says they are coming (PF-08).

    Idempotent: confirming twice is not an error, because a patient who
    taps the link again should not be told off for it.
    """
    _rate_limit(request)
    appt = _resolve(db, payload.token)

    if appt.confirmation_status != "confirmed":
        appt.confirmation_status = "confirmed"
        appt.confirmed_at = datetime.utcnow()
        appt.confirmation_source = "link"
        audit_service.record(
            db, appt.org_id, "appointment.confirmed",
            who="Patient (self-service link)",
            actor_type=audit_service.ACTOR_PATIENT,
            target_type="appointment", target_id=appt.id,
            detail="Confirmed attendance",
            request=request,
        )
        db.commit()
        db.refresh(appt)
        manager.broadcast(appt.org_id, "appointment.updated", appointment_summary(appt))

    return _view(db, appt)


class _PatientCtx:
    """
    The minimum an `OrgContext` consumer needs, for the one helper that
    takes one. It carries an org and an actor label and nothing else -- in
    particular no user, so nothing downstream can mistake a link holder
    for a signed-in member of staff.
    """

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self.actor = "patient"
        self.user = None


@router.get("/pre-visit", response_model=schemas.PreVisitOut)
def get_pre_visit(
    request: Request,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Read back what this patient already told us, so they can check it.

    Returns their own submission and nothing else. Deliberately not the
    `proposed_*` contact fields: those are read by staff,
    and echoing a phone number back through a link that may have been
    forwarded serves no purpose for the patient who typed it.
    """
    _rate_limit(request)
    appt = _resolve(db, token)
    row = (
        db.query(models.PreVisitSubmission)
        .filter(models.PreVisitSubmission.appointment_id == appt.id)
        .first()
    )
    if row is None:
        return schemas.PreVisitOut()
    return schemas.PreVisitOut(
        visit_reason=row.visit_reason,
        medications=row.medications,
        allergies=row.allergies,
        notes=row.notes,
        submitted_at=row.submitted_at,
    )


@router.post("/pre-visit", response_model=schemas.PreVisitOut)
def submit_pre_visit(
    request: Request,
    payload: schemas.PreVisitIn,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Take what the patient tells us before their visit.

    **Nothing here touches the patient record.** This endpoint is reached
    with an unauthenticated appointment token; if it could change a phone
    number, a forwarded or leaked link would become an account-takeover
    path -- redirect the messages, then use inbound replies to cancel or
    book. And a patient-reported medication list is not a verified one, so
    writing it into a medical record would put unverified clinical text
    where verified text is expected.

    So it is recorded beside the appointment, staff read it before the
    visit, and a person decides what belongs in the record.

    Re-submitting replaces the previous answer rather than adding a second
    one: a patient correcting themselves should not leave two versions for
    a nurse to reconcile at the desk.
    """
    _rate_limit(request)
    appt = _resolve(db, token)

    row = (
        db.query(models.PreVisitSubmission)
        .filter(models.PreVisitSubmission.appointment_id == appt.id)
        .first()
    )
    if row is None:
        row = models.PreVisitSubmission(
            org_id=appt.org_id, appointment_id=appt.id, patient_id=appt.patient_id
        )
        db.add(row)

    row.visit_reason = payload.visit_reason
    row.medications = payload.medications
    row.allergies = payload.allergies
    row.notes = payload.notes
    row.proposed_phone = payload.phone
    row.proposed_email = payload.email
    row.submitted_at = datetime.utcnow()
    # A correction is new information; staff should look again.
    row.reviewed_at = None

    # Audited as a record change with FIELD NAMES ONLY -- never the
    # values. A member id or a medication list in an audit detail would
    # put clinical and financial text in a table with longer retention and
    # a wider audience than the record it describes.
    audit_service.record(
        db, appt.org_id, "patient.record_changed",
        who="Patient (pre-visit form)",
        actor_type=audit_service.ACTOR_PATIENT,
        target_type="appointment", target_id=appt.id,
        detail="Pre-visit information submitted",
        request=request,
    )
    db.commit()
    db.refresh(row)

    manager.broadcast(appt.org_id, "appointment.updated", appointment_summary(appt))
    return schemas.PreVisitOut(
        visit_reason=row.visit_reason,
        medications=row.medications,
        allergies=row.allergies,
        notes=row.notes,
        submitted_at=row.submitted_at,
    )


@router.get("/availability", response_model=list[schemas.AvailabilitySlotOut])
def link_availability(
    request: Request,
    token: str = Query(...),
    date: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    Open slots for THIS appointment's doctor on one date.

    Reuses the staff/agent availability computation rather than repeating
    it, so a patient is offered exactly the slots anyone else would be.
    The doctor is taken from the appointment, never from the request -- a
    link holder must not be able to enumerate the clinic's other
    clinicians.
    """
    _rate_limit(request)
    appt = _resolve(db, token)
    doctor = _doctor(db, appt)
    if doctor is None:
        raise HTTPException(status_code=400, detail="This appointment cannot be moved online.")

    result = get_availability(
        doctor_id=doctor.id, date=date, db=db, ctx=_PatientCtx(appt.org_id)
    )
    return result.slots


@router.post("/reschedule", response_model=schemas.LinkAppointmentOut)
def reschedule_via_link(
    request: Request,
    payload: schemas.LinkRescheduleIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Move this appointment, subject to exactly the same rules as every
    other path: not in the past, on a day the doctor works, inside their
    hours, and not clashing with an existing booking.
    """
    _rate_limit(request)
    appt = _resolve(db, payload.token)
    doctor = _doctor(db, appt)
    if doctor is None:
        raise HTTPException(status_code=400, detail="This appointment cannot be moved online.")

    previous_when = f"{appt.day_label or ''} {appt.time_label or ''}".strip() or None
    end_at = _validate_slot(
        db, appt.org_id, doctor, payload.start_at, exclude_appointment_id=appt.id
    )

    appt.start_at = payload.start_at
    appt.end_at = end_at
    appt.day_label = _format_day_label(payload.start_at)
    appt.time_label = _format_time_label(payload.start_at)
    appt.status = "upcoming"
    # Moving the appointment retracts any confirmation: they confirmed the
    # old time, not this one.
    appt.confirmation_status = None
    appt.confirmed_at = None
    appt.confirmation_source = None

    audit_service.record(
        db, appt.org_id, "appointment.rescheduled",
        who="Patient (self-service link)",
        actor_type=audit_service.ACTOR_PATIENT,
        target_type="appointment", target_id=appt.id,
        detail=f"Moved to {appt.day_label} {appt.time_label} (was {previous_when or 'unknown'})",
        request=request,
    )
    db.commit()
    db.refresh(appt)
    manager.broadcast(appt.org_id, "appointment.rescheduled", appointment_summary(appt))
    return _view(db, appt)


@router.post("/cancel", response_model=schemas.LinkAppointmentOut)
def cancel_via_link(
    request: Request,
    payload: schemas.LinkTokenIn,
    db: Session = Depends(get_db),
):
    """
    Cancel this appointment, releasing the slot back into availability.

    The link is retired at the same time. A cancelled appointment has
    nothing further to self-serve, and leaving a live token attached to it
    is a credential with no remaining purpose.
    """
    _rate_limit(request)
    appt = _resolve(db, payload.token)

    appt.status = "cancelled"
    appt.confirmation_status = None
    appt.confirmed_at = None
    appt.confirmation_source = None
    appt.link_token_hash = None
    appt.link_expires_at = None

    audit_service.record(
        db, appt.org_id, "appointment.cancelled",
        who="Patient (self-service link)",
        actor_type=audit_service.ACTOR_PATIENT,
        target_type="appointment", target_id=appt.id,
        detail="Cancelled by the patient online",
        request=request,
    )
    db.commit()
    db.refresh(appt)
    manager.broadcast(appt.org_id, "appointment.cancelled", appointment_summary(appt))
    return _view(db, appt)


# ---------------------------------------------------------------------
# Issuing a link
# ---------------------------------------------------------------------

def issue_link_token(appt: models.Appointment) -> str:
    """
    Mint a fresh link for this appointment and return the plaintext token.

    The caller is responsible for committing. Reissuing replaces whatever
    was there, which is what makes a forwarded old link stop working.

    Expiry is the appointment's own start plus a short grace, not a fixed
    number of days: the link's whole purpose ends when the appointment
    does. Appointments with no start time fall back to a bounded default
    so a token can never be immortal.
    """
    from ..security import generate_appointment_link_token

    token, token_hash = generate_appointment_link_token()
    appt.link_token_hash = token_hash
    if appt.start_at:
        appt.link_expires_at = appt.start_at + timedelta(
            hours=settings.appointment_link_grace_hours
        )
    else:
        appt.link_expires_at = datetime.utcnow() + timedelta(
            days=settings.appointment_link_max_days
        )
    return token
