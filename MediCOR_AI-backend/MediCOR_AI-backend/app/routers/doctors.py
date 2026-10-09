"""
Doctors — read for everyone in the org, write for admins.

Doctors drive availability, conflict checking and the whole booking
flow, so before Phase 1 a clinic that wasn't the seeded demo org had no
doctors and could not book anything without direct database access.

Two read shapes, on purpose:

  * GET /doctors      — active doctors, lean fields, dual-auth. This is
                        what the voice agent calls mid-call, so it stays
                        small; every field costs prompt tokens.
  * GET /doctors/all  — full records including inactive, admin JWT only.
                        For the clinic's management screen.

Deletion is a soft delete (`active = False`). A doctor with appointments
in the history must not vanish — `Appointment.doctor_id` points at them,
and past bookings would lose their doctor.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context, require_admin

router = APIRouter(prefix="/doctors", tags=["doctors"])


def _get_org_doctor_or_404(db: Session, doctor_id: str, org_id: str) -> models.Doctor:
    doctor = (
        db.query(models.Doctor)
        .filter(models.Doctor.id == doctor_id, models.Doctor.org_id == org_id)
        .first()
    )
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


@router.get("", response_model=list[schemas.DoctorOut])
def list_doctors(db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)):
    return (
        db.query(models.Doctor)
        .filter(models.Doctor.org_id == ctx.org_id, models.Doctor.active == True)  # noqa: E712 - SQL Server BIT compatibility
        .order_by(models.Doctor.name)
        .all()
    )


@router.get("/all", response_model=list[schemas.DoctorAdminOut])
def list_all_doctors(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Full doctor records for the management screen. Readable by any staff
    member (they need it to book); only admins can write, below.
    """
    q = db.query(models.Doctor).filter(models.Doctor.org_id == user.org_id)
    if not include_inactive:
        q = q.filter(models.Doctor.active == True)  # noqa: E712 - SQL Server BIT compatibility
    return q.order_by(models.Doctor.name).all()


@router.post("", response_model=schemas.DoctorAdminOut, status_code=201)
def create_doctor(
    payload: schemas.DoctorCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    doctor = models.Doctor(
        org_id=user.org_id,
        name=payload.name.strip(),
        specialty=payload.specialty,
        slot_minutes=payload.slot_minutes,
        work_start_hour=payload.work_start_hour,
        work_end_hour=payload.work_end_hour,
        work_days_csv=payload.work_days_csv,
        active=payload.active,
        consultation_fee_label=payload.consultation_fee_label,
    )
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


@router.patch("/{doctor_id}", response_model=schemas.DoctorAdminOut)
def update_doctor(
    doctor_id: str,
    payload: schemas.DoctorUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    doctor = _get_org_doctor_or_404(db, doctor_id, user.org_id)
    data = payload.model_dump(exclude_unset=True)

    # work_days arrives as a list but is stored as a csv string.
    if "work_days" in data:
        days = data.pop("work_days")
        if days is not None:
            doctor.work_days_csv = ",".join(str(d) for d in days)

    for key, value in data.items():
        if value is not None:
            setattr(doctor, key, value.strip() if key == "name" else value)

    # Validate the resulting window, not just the fields that were sent —
    # patching only work_end_hour could otherwise invert the working day.
    if doctor.work_end_hour <= doctor.work_start_hour:
        raise HTTPException(
            status_code=422,
            detail="The end of the working day must be after the start.",
        )

    db.commit()
    db.refresh(doctor)
    return doctor


@router.delete("/{doctor_id}", status_code=204)
def deactivate_doctor(
    doctor_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """
    Soft delete. The doctor stops appearing in booking flows immediately
    but their existing appointments keep a valid reference — a hard
    delete would orphan every past booking they ever had.

    Upcoming appointments are deliberately left alone rather than
    cancelled: silently cancelling real patient appointments as a side
    effect of an admin edit would be far worse than leaving them for
    staff to reschedule deliberately.
    """
    doctor = _get_org_doctor_or_404(db, doctor_id, user.org_id)
    doctor.active = False
    db.commit()
    return None
