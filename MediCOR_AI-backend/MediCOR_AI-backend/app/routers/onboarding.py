"""
Onboarding status — Phase 2.

Registration deliberately creates an EMPTY clinic: no placeholder
doctors, no invented services, no made-up fees. The voice agent reads
those fields straight out to patients, so seeded filler would have it
confidently quoting a price nobody set, or offering appointments with a
doctor who does not exist.

The cost of that choice is that a brand-new clinic's agent knows
nothing. This endpoint is the counterweight — it tells the dashboard
exactly what is still missing, so the gap is visible and fixable rather
than silent.

Every check reads existing tables. Nothing here writes.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas, timezones
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/status", response_model=schemas.OnboardingStatusOut)
def onboarding_status(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    org_id = user.org_id
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    info = db.query(models.ClinicInfo).filter(models.ClinicInfo.org_id == org_id).first()

    doctor_count = (
        db.query(func.count(models.Doctor.id))
        .filter(models.Doctor.org_id == org_id, models.Doctor.active == True)  # noqa: E712 - SQL Server BIT compatibility
        .scalar()
        or 0
    )
    service_count = (
        db.query(func.count(models.Service.id))
        .filter(models.Service.org_id == org_id, models.Service.active == True)  # noqa: E712 - SQL Server BIT compatibility
        .scalar()
        or 0
    )
    number_count = (
        db.query(func.count(models.PhoneNumber.id))
        .filter(models.PhoneNumber.org_id == org_id, models.PhoneNumber.active == True)  # noqa: E712 - SQL Server BIT compatibility
        .scalar()
        or 0
    )

    tz_name = timezones.org_timezone_name(org)
    has_timings = bool(info and (info.timings_text or "").strip())
    has_contact = bool(info and ((info.phone or "").strip() or (info.address or "").strip()))

    steps = [
        schemas.OnboardingStepOut(
            key="timezone",
            label="Set your clinic's timezone",
            done=tz_name != "UTC",
            detail=(
                f"Currently {tz_name}. Appointment times, working hours and "
                "'today' on your dashboard are all read in this zone."
            ),
        ),
        schemas.OnboardingStepOut(
            key="doctors",
            label="Add your doctors",
            done=doctor_count > 0,
            detail=(
                f"{doctor_count} active."
                if doctor_count
                else "Nothing can be booked until at least one doctor exists, "
                "by phone or from the dashboard."
            ),
        ),
        schemas.OnboardingStepOut(
            key="clinic_hours",
            label="Add your opening hours",
            done=has_timings,
            detail=(
                "Your receptionist can answer 'what time do you open?'."
                if has_timings
                else "Until this is set, the AI answers 'I don't have that detail' "
                "to the most common question it gets."
            ),
        ),
        schemas.OnboardingStepOut(
            key="contact",
            label="Add your address and phone number",
            done=has_contact,
            detail=(
                "Callers can be given your address and number."
                if has_contact
                else "Callers asking where you are will not get an answer."
            ),
        ),
        schemas.OnboardingStepOut(
            key="services",
            label="List your services",
            done=service_count > 0,
            detail=(
                f"{service_count} listed."
                if service_count
                else "Add what you offer so the AI can answer 'do you do X?' "
                "and quote the right fee."
            ),
        ),
        schemas.OnboardingStepOut(
            key="phone_number",
            label="Connect a phone number",
            done=number_count > 0,
            detail=(
                f"{number_count} number(s) routed to this clinic."
                if number_count
                else "Ask your MediCOR contact to assign your number — calls "
                "cannot reach this clinic until one is routed here."
            ),
        ),
    ]

    return schemas.OnboardingStatusOut(
        complete=all(step.done for step in steps),
        steps=steps,
    )
