from datetime import date as date_cls, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit_service, webhook_service, ehr_service, models, notification_service, schemas, timezones
from ..config import settings
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context
from ..realtime import appointment_summary, manager, notify

router = APIRouter(prefix="/appointments", tags=["appointments"])

# Upper bound on how many open slots one doctor-day can return.
#
# This caps the RESULT, but the scan below now always walks the whole
# working day. Before Phase 0 the same constant (at 10) terminated the
# scan loop itself, so a 9-5 doctor on 30-minute slots was never offered
# anything after ~2 PM — the afternoon was invisible to callers and staff
# alike. Configurable via AVAILABILITY_MAX_SLOTS.
MAX_SLOTS_RETURNED = settings.availability_max_slots


def _audit_detail(appt: models.Appointment) -> str:
    """
    One line naming who the appointment is for and when.

    The `when` is the clinic wall-clock label already stored on the row,
    not a re-derived UTC timestamp — an audit entry that disagrees with
    the calendar the staff are looking at is worse than no entry.
    """
    when = f"{appt.day_label or ''} {appt.time_label or ''}".strip()
    return f"{appt.patient_name or 'Unknown patient'}{f' · {when}' if when else ''}"


def _format_day_label(dt: datetime) -> str:
    return f"{dt.strftime('%a, %b')} {dt.day}"


def _format_time_label(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _org_tz(db: Session, org_id: str):
    """
    The clinic's zone. Appointment times are stored as clinic-local
    wall-clock readings (see app/timezones.py), so every "is this in the
    past" and "what time is it now" question in this module has to be
    asked in this zone rather than in UTC.
    """
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    return timezones.org_timezone(org)


def _get_org_appointment_or_404(db: Session, appointment_id: str, org_id: str) -> models.Appointment:
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == appointment_id, models.Appointment.org_id == org_id)
        .first()
    )
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appt


def _get_org_patient_or_404(db: Session, patient_id: str, org_id: str) -> models.Patient:
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id, models.Patient.org_id == org_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _get_org_doctor_or_404(
    db: Session, doctor_id: str, org_id: str, active_only: bool = True
) -> models.Doctor:
    q = db.query(models.Doctor).filter(
        models.Doctor.id == doctor_id, models.Doctor.org_id == org_id
    )
    if active_only:
        q = q.filter(models.Doctor.active == True)  # noqa: E712 - SQL Server BIT compatibility
    doctor = q.first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


def _validate_slot(
    db: Session,
    org_id: str,
    doctor: models.Doctor,
    start_at: datetime,
    exclude_appointment_id: Optional[str] = None,
) -> datetime:
    """
    Shared slot validation for booking and rescheduling: rejects past times,
    times outside the doctor's working days/hours, and conflicts with any
    other non-cancelled appointment for that doctor. Returns the computed
    end_at on success. `exclude_appointment_id` lets a reschedule ignore
    its own existing (pre-move) booking when conflict-checking.

    `start_at` is a clinic-local wall-clock reading, so the past check
    uses the clinic's own clock. Comparing it against datetime.utcnow()
    (the pre-Phase-1 behaviour) rejected most of the working day for any
    clinic east of UTC.
    """
    if start_at < timezones.now_local(_org_tz(db, org_id)):
        raise HTTPException(status_code=400, detail="Cannot book an appointment in the past")
    if start_at.weekday() not in {int(d) for d in doctor.work_days_csv.split(",") if d.strip() != ""}:
        raise HTTPException(status_code=400, detail="Doctor does not work on that day")
    if not (doctor.work_start_hour <= start_at.hour < doctor.work_end_hour):
        raise HTTPException(status_code=400, detail="Requested time is outside doctor's working hours")

    end_at = start_at + timedelta(minutes=doctor.slot_minutes)

    conflict_q = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor.id,
        models.Appointment.org_id == org_id,
        models.Appointment.status != "cancelled",
        models.Appointment.start_at < end_at,
        models.Appointment.end_at > start_at,
    )
    if exclude_appointment_id:
        conflict_q = conflict_q.filter(models.Appointment.id != exclude_appointment_id)
    if conflict_q.first():
        raise HTTPException(status_code=409, detail="That slot is no longer available")

    return end_at


@router.get("", response_model=list[schemas.AppointmentOut])
def list_appointments(
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    `limit`/`offset` are optional and mirror the pagination already on
    `GET /calls` and `GET /patients` (Phase 1 #6) — omitting `limit`
    preserves the previous behaviour of returning every appointment for
    the org, so no existing caller changes. `limit` is capped at 500
    server-side regardless of what the client asks for.
    """
    q = (
        db.query(models.Appointment)
        .filter(models.Appointment.org_id == user.org_id)
        .order_by(models.Appointment.start_at)
    )
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(min(limit, 500))
    return q.all()


@router.post("", response_model=schemas.AppointmentOut)
def create_appointment(
    payload: schemas.AppointmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    data = payload.model_dump()

    # Phase 0 (security): `patient_id` and `doctor_id` arrive straight from
    # the request body. Without these checks, staff at org B could attach
    # org A's patient and doctor ids to a row in their own tenant — a
    # cross-tenant reference that then flows into realtime events, the
    # calendar, and EHR sync. Org-scope both before they reach the model.
    if data.get("patient_id"):
        _get_org_patient_or_404(db, data["patient_id"], user.org_id)
    if data.get("doctor_id"):
        # active_only=False: this freeform endpoint is also used for admin
        # corrections and back-dated entries, which may legitimately
        # reference a doctor who has since been deactivated. Tenant
        # ownership is the security boundary here, not the active flag.
        _get_org_doctor_or_404(db, data["doctor_id"], user.org_id, active_only=False)

    appt = models.Appointment(org_id=user.org_id, **data)
    db.add(appt)
    db.flush()
    audit_service.record(
        db, user.org_id, "appointment.created", user=user,
        target_type="appointment", target_id=appt.id,
        detail=_audit_detail(appt), request=request,
    )
    db.commit()
    db.refresh(appt)
    manager.broadcast(user.org_id, "appointment.created", appointment_summary(appt))
    return appt


@router.patch("/{appointment_id}", response_model=schemas.AppointmentOut)
def update_appointment(
    appointment_id: str,
    payload: schemas.AppointmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == appointment_id, models.Appointment.org_id == user.org_id)
        .first()
    )
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    data = payload.model_dump(exclude_unset=True)
    before = {k: getattr(appt, k, None) for k in data}
    for k, v in data.items():
        setattr(appt, k, v)
    audit_service.record(
        db, user.org_id, "appointment.updated", user=user,
        target_type="appointment", target_id=appt.id,
        detail=", ".join(filter(None, [
            _audit_detail(appt), audit_service.changed_fields(before, data),
        ])),
        request=request,
    )
    db.commit()
    db.refresh(appt)
    manager.broadcast(user.org_id, "appointment.updated", appointment_summary(appt))
    return appt


@router.delete("/{appointment_id}", status_code=204)
def delete_appointment(
    appointment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == appointment_id, models.Appointment.org_id == user.org_id)
        .first()
    )
    if appt:
        # Before the delete: the row is about to stop existing, and an
        # appointment vanishing from the calendar with no record of who
        # removed it is exactly the question this log answers.
        audit_service.record(
            db, user.org_id, "appointment.cancelled", user=user,
            target_type="appointment", target_id=appt.id,
            detail=f"Deleted — {_audit_detail(appt)}", request=request,
        )
        db.delete(appt)
        db.commit()
    return None


@router.get("/availability", response_model=schemas.AvailabilityOut)
def get_availability(
    doctor_id: str,
    date: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Computes open slots for a doctor on a given date (YYYY-MM-DD), based on
    the doctor's working hours/days and existing (non-cancelled) bookings.
    This is the single source of truth for availability — the voice agent
    and dashboard both call this rather than reasoning about schedules themselves.
    """
    doctor = (
        db.query(models.Doctor)
        .filter(models.Doctor.id == doctor_id, models.Doctor.org_id == ctx.org_id, models.Doctor.active == True,  # noqa: E712 - SQL Server BIT compatibility
        )
        .first()
    )
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    try:
        target_date = date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")

    allowed_weekdays = {int(d) for d in doctor.work_days_csv.split(",") if d.strip() != ""}
    if target_date.weekday() not in allowed_weekdays:
        return schemas.AvailabilityOut(doctor_id=doctor.id, doctor_name=doctor.name, date=date, slots=[])

    day_start = datetime.combine(target_date, datetime.min.time()).replace(hour=doctor.work_start_hour)
    day_end = datetime.combine(target_date, datetime.min.time()).replace(hour=doctor.work_end_hour)
    step = timedelta(minutes=doctor.slot_minutes)

    booked = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.doctor_id == doctor.id,
            models.Appointment.org_id == ctx.org_id,
            models.Appointment.status != "cancelled",
            models.Appointment.start_at.isnot(None),
            models.Appointment.start_at >= day_start,
            models.Appointment.start_at < day_end,
        )
        .all()
    )
    booked_ranges = [
        (a.start_at, a.end_at or (a.start_at + step)) for a in booked
    ]

    # Clinic-local now: the slot grid below is built from the doctor's
    # working hours, which are clinic wall-clock hours.
    now = timezones.now_local(_org_tz(db, ctx.org_id))
    slots: list[schemas.AvailabilitySlotOut] = []
    cursor = day_start
    # Scan the FULL working day. The result is capped afterwards, so a
    # busy morning can no longer hide the afternoon (see MAX_SLOTS_RETURNED).
    while cursor + step <= day_end:
        slot_end = cursor + step
        if cursor > now:  # never offer a slot that's already passed
            overlaps = any(cursor < b_end and slot_end > b_start for b_start, b_end in booked_ranges)
            if not overlaps:
                slots.append(schemas.AvailabilitySlotOut(start_at=cursor, label=_format_time_label(cursor)))
        cursor += step

    return schemas.AvailabilityOut(
        doctor_id=doctor.id,
        doctor_name=doctor.name,
        date=date,
        slots=slots[:MAX_SLOTS_RETURNED],
    )


@router.post("/book", response_model=schemas.AppointmentOut)
def book_appointment(
    payload: schemas.AppointmentBookIn,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Validated appointment booking: confirms the patient and doctor exist,
    the requested slot is within working hours, is still free, and only
    then creates the appointment. This is what the voice agent (and any
    future structured booking UI) should call — unlike the freeform
    POST /appointments used by staff for manual/admin entries, this endpoint
    owns the actual scheduling rules.
    """
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == payload.patient_id, models.Patient.org_id == ctx.org_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    doctor = (
        db.query(models.Doctor)
        .filter(models.Doctor.id == payload.doctor_id, models.Doctor.org_id == ctx.org_id, models.Doctor.active == True,  # noqa: E712 - SQL Server BIT compatibility
        )
        .first()
    )
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    start_at = payload.start_at
    end_at = _validate_slot(db, ctx.org_id, doctor, start_at)

    appt = models.Appointment(
        org_id=ctx.org_id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        title=f"Appointment with {doctor.name}",
        patient_name=patient.name,
        reason=payload.reason,
        day_label=_format_day_label(start_at),
        time_label=_format_time_label(start_at),
        start_at=start_at,
        end_at=end_at,
        location="Main Clinic",
        status="upcoming",
        ai_generated=(ctx.actor == "service"),
    )
    db.add(appt)
    db.flush()
    # `ctx` rather than a user: this endpoint is dual-auth, and a booking
    # the AI made must be attributable to the AI.
    audit_service.record(
        db, ctx.org_id, "appointment.created", ctx=ctx,
        target_type="appointment", target_id=appt.id,
        detail=_audit_detail(appt), request=request,
    )
    db.commit()
    db.refresh(appt)

    manager.broadcast(ctx.org_id, "appointment.booked", appointment_summary(appt))
    if ctx.actor == "service":
        notify(
            ctx.org_id,
            level="success",
            title="Appointment booked",
            message=f"{appt.patient_name} · {doctor.name} · {appt.day_label} {appt.time_label}",
            data={"appointment_id": appt.id},
        )
    ehr_service.schedule_appointment_sync(background_tasks, appt.id, ctx.org_id, ctx.actor)

    notification_service.schedule_appointment_notification(
        background_tasks, "booked", appt.id, ctx.org_id
    )
    webhook_service.schedule(
        background_tasks, ctx.org_id, "appointment.booked",
        {"appointment_id": appt.id, "patient_id": appt.patient_id,
         "doctor_id": appt.doctor_id, "start_at": appt.start_at},
    )

    return appt


@router.get("/patient/{patient_id}", response_model=list[schemas.AppointmentOut])
def list_patient_appointments(
    patient_id: str,
    include_past: bool = False,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Lists a patient's appointments — used by the voice agent to identify
    which booking a caller means before rescheduling/cancelling. Defaults
    to non-cancelled, upcoming appointments only.
    """
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id, models.Patient.org_id == ctx.org_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    q = db.query(models.Appointment).filter(
        models.Appointment.patient_id == patient_id,
        models.Appointment.org_id == ctx.org_id,
    )
    if not include_past:
        q = q.filter(
            models.Appointment.status != "cancelled",
            # start_at is clinic-local wall time, so "upcoming" is decided
            # by the clinic's clock, not UTC.
            models.Appointment.start_at >= timezones.now_local(_org_tz(db, ctx.org_id)),
        )
    return q.order_by(models.Appointment.start_at).all()


@router.post("/{appointment_id}/reschedule", response_model=schemas.AppointmentOut)
def reschedule_appointment(
    appointment_id: str,
    payload: schemas.AppointmentRescheduleIn,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Moves an existing (non-cancelled) appointment to a new time and,
    optionally, a new doctor. Re-runs the same working-hours/conflict
    validation as booking. The voice agent and any future structured
    reschedule UI should call this rather than the freeform PATCH, which
    does not validate schedules.
    """
    appt = _get_org_appointment_or_404(db, appointment_id, ctx.org_id)
    if appt.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot reschedule a cancelled appointment")

    # Captured before the move so the patient notification can say what
    # changed, not just assert a new time.
    previous_when = f"{appt.day_label or ''} {appt.time_label or ''}".strip() or None

    doctor_id = payload.doctor_id or appt.doctor_id
    if not doctor_id:
        raise HTTPException(status_code=400, detail="Appointment has no doctor assigned; doctor_id is required")
    doctor = (
        db.query(models.Doctor)
        .filter(models.Doctor.id == doctor_id, models.Doctor.org_id == ctx.org_id, models.Doctor.active == True,  # noqa: E712 - SQL Server BIT compatibility
        )
        .first()
    )
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    end_at = _validate_slot(db, ctx.org_id, doctor, payload.start_at, exclude_appointment_id=appt.id)

    appt.doctor_id = doctor.id
    appt.start_at = payload.start_at
    appt.end_at = end_at
    appt.day_label = _format_day_label(payload.start_at)
    appt.time_label = _format_time_label(payload.start_at)
    appt.status = "upcoming"
    audit_service.record(
        db, ctx.org_id, "appointment.rescheduled", ctx=ctx,
        target_type="appointment", target_id=appt.id,
        detail=f"{_audit_detail(appt)} (was {previous_when or 'unknown'})",
        request=request,
    )
    db.commit()
    db.refresh(appt)

    manager.broadcast(ctx.org_id, "appointment.rescheduled", appointment_summary(appt))
    if ctx.actor == "service":
        notify(
            ctx.org_id,
            level="info",
            title="Appointment rescheduled",
            message=f"{appt.patient_name} · moved to {appt.day_label} {appt.time_label}",
            data={"appointment_id": appt.id},
        )
    ehr_service.schedule_appointment_sync(background_tasks, appt.id, ctx.org_id, ctx.actor)

    # Booking and cancellation already told the patient; rescheduling did
    # not, which meant the change most likely to cause a missed
    # appointment was the one nobody was notified about.
    notification_service.schedule_appointment_notification(
        background_tasks, "rescheduled", appt.id, ctx.org_id, previous_when=previous_when
    )
    webhook_service.schedule(
        background_tasks, ctx.org_id, "appointment.rescheduled",
        {"appointment_id": appt.id, "patient_id": appt.patient_id,
         "doctor_id": appt.doctor_id, "start_at": appt.start_at},
    )
    return appt


@router.post("/{appointment_id}/cancel", response_model=schemas.AppointmentOut)
def cancel_appointment(
    appointment_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """Cancels an appointment (soft cancel — status flip, record kept for history)."""
    appt = _get_org_appointment_or_404(db, appointment_id, ctx.org_id)
    if appt.status == "cancelled":
        raise HTTPException(status_code=400, detail="Appointment is already cancelled")
    appt.status = "cancelled"
    audit_service.record(
        db, ctx.org_id, "appointment.cancelled", ctx=ctx,
        target_type="appointment", target_id=appt.id,
        detail=_audit_detail(appt), request=request,
    )
    db.commit()
    db.refresh(appt)

    manager.broadcast(ctx.org_id, "appointment.cancelled", appointment_summary(appt))
    if ctx.actor == "service":
        notify(
            ctx.org_id,
            level="warning",
            title="Appointment cancelled",
            message=f"{appt.patient_name} · {appt.day_label or ''} {appt.time_label or ''}".strip(),
            data={"appointment_id": appt.id},
        )
    ehr_service.schedule_appointment_sync(background_tasks, appt.id, ctx.org_id, ctx.actor)

    notification_service.schedule_appointment_notification(
        background_tasks, "cancelled", appt.id, ctx.org_id
    )
    webhook_service.schedule(
        background_tasks, ctx.org_id, "appointment.cancelled",
        {"appointment_id": appt.id, "patient_id": appt.patient_id,
         "doctor_id": appt.doctor_id, "start_at": appt.start_at},
    )

    return appt


@router.post("/{appointment_id}/no-show", response_model=schemas.AppointmentOut)
def mark_no_show(
    appointment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Records that the patient did not arrive.

    Its own route rather than a `PATCH ... {"status": "no_show"}` for the
    same reasons `/cancel` is: the transition needs guarding, and it
    deserves its own audit code so "who wrote off this slot" is directly
    answerable on the Security page.

    Staff-only on purpose. This is `get_current_user`, not
    `get_org_context`, so the **voice agent cannot reach it** — an AI
    deciding a patient was a no-show from a phone call is a judgement it
    has no evidence for. Someone at the clinic marks this.

    No `require_admin` either: whoever works the front desk is who knows
    the patient did not turn up, and that is usually not an admin.
    """
    appt = _get_org_appointment_or_404(db, appointment_id, user.org_id)

    if appt.status == "no_show":
        raise HTTPException(status_code=400, detail="Appointment is already marked as a no-show")
    if appt.status == "cancelled":
        # A cancelled appointment cannot also be a no-show: the patient
        # told the clinic. Allowing both would double-count one slot and
        # inflate the no-show rate with appointments nobody missed.
        raise HTTPException(
            status_code=400,
            detail="A cancelled appointment cannot be marked as a no-show",
        )

    appt.status = "no_show"
    audit_service.record(
        db, user.org_id, "appointment.no_show", user=user,
        target_type="appointment", target_id=appt.id,
        detail=_audit_detail(appt), request=request,
    )
    db.commit()
    db.refresh(appt)

    manager.broadcast(user.org_id, "appointment.updated", appointment_summary(appt))
    return appt


@router.get("/{appointment_id}/pre-visit", response_model=schemas.PreVisitStaffOut)
def get_pre_visit_for_staff(
    appointment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    What the patient told us before this visit (PF-13).

    Staff only. The whole point is that a clinician reads it before the
    appointment rather than collecting it at the desk while other patients
    wait.

    The `proposed_*` fields are named that way on purpose: they are what
    the patient said, not what the record holds. Nothing applies them
    automatically, because a patient-reported medication list is not a
    verified one and an unauthenticated link must never be able to
    redirect a patient's contact details.
    """
    appt = _get_org_appointment_or_404(db, appointment_id, user.org_id)
    row = (
        db.query(models.PreVisitSubmission)
        .filter(
            models.PreVisitSubmission.appointment_id == appt.id,
            models.PreVisitSubmission.org_id == user.org_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Nothing submitted for this appointment")

    # Reading it is a clinical-record access, and this one carries what a
    # patient said about their medication -- so it is audited like the
    # other individual-record reads. Recorded after the 404, so probing
    # ids cannot manufacture access entries.
    if row.reviewed_at is None:
        row.reviewed_at = datetime.utcnow()
    audit_service.record(
        db, user.org_id, "patient.history_viewed", user=user,
        target_type="appointment", target_id=appt.id,
        detail="Pre-visit information",
        status=audit_service.STATUS_LOGGED,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return row


@router.get("/pending/bookings", response_model=list[schemas.PendingBookingOut])
def list_pending_bookings(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return (
        db.query(models.PendingBooking)
        .filter(
            models.PendingBooking.org_id == user.org_id,
            models.PendingBooking.status == "pending",
        )
        .all()
    )


@router.post("/pending/bookings", response_model=schemas.PendingBookingOut)
def create_pending_booking(
    payload: schemas.PendingBookingCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    data = payload.model_dump()
    # Same tenant-scoping rule as POST /appointments — these ids end up on
    # a real Appointment when the booking is verified.
    if data.get("patient_id"):
        _get_org_patient_or_404(db, data["patient_id"], user.org_id)
    if data.get("doctor_id"):
        _get_org_doctor_or_404(db, data["doctor_id"], user.org_id)

    booking = models.PendingBooking(org_id=user.org_id, **data)
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


@router.post("/pending/bookings/{booking_id}/verify", response_model=schemas.PendingBookingOut)
def verify_pending_booking(
    booking_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    booking = (
        db.query(models.PendingBooking)
        .filter(models.PendingBooking.id == booking_id, models.PendingBooking.org_id == user.org_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "pending":
        raise HTTPException(status_code=400, detail=f"Booking is already {booking.status}")

    # Phase 0 fix: this used to create an Appointment carrying only a title,
    # a patient name and a time *label* — no start_at, no end_at, no
    # patient_id, no doctor_id. The calendar places appointments by
    # start_at and skips rows without one, and _validate_slot's conflict
    # query filters start_at IS NOT NULL, so the result was an appointment
    # that staff believed existed, that rendered nowhere, and whose slot
    # stayed bookable by someone else. Build a real, validated appointment
    # instead — the same rules the voice agent's /book path applies.
    if not booking.start_at or not booking.doctor_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "This request has no confirmed doctor and time, so it cannot be "
                "turned into an appointment. Book it from the appointment form "
                "instead, then decline this request."
            ),
        )

    doctor = _get_org_doctor_or_404(db, booking.doctor_id, user.org_id)
    patient = (
        _get_org_patient_or_404(db, booking.patient_id, user.org_id)
        if booking.patient_id
        else None
    )
    end_at = _validate_slot(db, user.org_id, doctor, booking.start_at)

    booking.status = "verified"
    appt = models.Appointment(
        org_id=user.org_id,
        patient_id=patient.id if patient else None,
        doctor_id=doctor.id,
        title=booking.type_label or f"Appointment with {doctor.name}",
        patient_name=patient.name if patient else booking.patient_name,
        reason=booking.reason,
        day_label=_format_day_label(booking.start_at),
        time_label=_format_time_label(booking.start_at),
        start_at=booking.start_at,
        end_at=end_at,
        location="Main Clinic",
        status="upcoming",
        ai_generated=True,
    )
    db.add(appt)
    db.commit()
    db.refresh(booking)
    db.refresh(appt)
    manager.broadcast(user.org_id, "appointment.booked", appointment_summary(appt))
    return booking


@router.post("/pending/bookings/{booking_id}/decline", response_model=schemas.PendingBookingOut)
def decline_pending_booking(
    booking_id: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    booking = (
        db.query(models.PendingBooking)
        .filter(models.PendingBooking.id == booking_id, models.PendingBooking.org_id == user.org_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking.status = "declined"
    db.commit()
    db.refresh(booking)
    return booking