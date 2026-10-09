from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import analytics_service, models, schemas, timezones
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _org_tz(db: Session, org_id: str):
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    return timezones.org_timezone(org)


@router.get("/stats", response_model=schemas.DashboardStatsOut)
def stats(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    # `occurred_at` / `created_at` are genuine UTC instants, but "today"
    # means the clinic's calendar day. Express that day as a UTC range
    # rather than slicing at UTC midnight — which rolled the dashboard
    # over at 5:30 AM local for a UTC+5:30 clinic.
    tz = _org_tz(db, user.org_id)
    today_start, today_end = timezones.local_day_bounds_utc(tz)

    calls_today = (
        db.query(models.CallLog)
        .filter(
            models.CallLog.org_id == user.org_id,
            models.CallLog.occurred_at >= today_start,
            models.CallLog.occurred_at < today_end,
        )
        .all()
    )
    calls_handled_today = len(calls_today)

    appointments_booked_today = (
        db.query(func.count(models.Appointment.id))
        .filter(
            models.Appointment.org_id == user.org_id,
            models.Appointment.ai_generated == True,  # noqa: E712 - required for SQL Server BIT comparison ("IS 1" is invalid T-SQL; "= 1" is not)
            models.Appointment.created_at >= today_start,
            models.Appointment.created_at < today_end,
        )
        .scalar()
        or 0
    )

    # HV-9: both of these used to be unreliable in ways that flattered the
    # product, so they are now computed by analytics_service from the same
    # definitions the Analytics page uses.
    #
    # `resolution_rate_pct` counted ANY call with an outcome that wasn't a
    # transfer as resolved — so a `failed` call and a `no_answer` call both
    # scored as successes, and a day of entirely failed calls could report
    # a healthy rate. Calls that never connected are now excluded from the
    # denominator entirely. None (nothing connected) renders as 0.0 here to
    # keep this endpoint's response shape unchanged.
    resolution_rate_pct = analytics_service.resolution_rate(calls_today) or 0.0

    # `staff_time_saved_hrs` was `calls_today * 5 / 60` — a constant, so it
    # was really the call count in disguise and moved only with volume. It
    # is now actual call minutes the AI handled without transferring:
    # still an estimate of time saved, but derived from what happened.
    staff_time_saved_hrs = round(analytics_service.ai_handled_minutes(calls_today) / 60, 1)

    return schemas.DashboardStatsOut(
        calls_handled_today=calls_handled_today,
        appointments_booked_today=appointments_booked_today,
        resolution_rate_pct=resolution_rate_pct,
        staff_time_saved_hrs=staff_time_saved_hrs,
    )


@router.get("/call-volume")
def call_volume(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Hourly call counts for the last 24 hours."""
    now_utc = datetime.utcnow()
    since = now_utc - timedelta(hours=24)
    calls = (
        db.query(models.CallLog)
        .filter(models.CallLog.org_id == user.org_id, models.CallLog.occurred_at >= since)
        .all()
    )
    # The buckets are "hours ago", which is the same in any zone. Only the
    # label for the current hour needs to be clinic-local.
    buckets = [0] * 24
    for c in calls:
        if not c.occurred_at:
            continue
        hours_ago = int((now_utc - c.occurred_at).total_seconds() // 3600)
        if 0 <= hours_ago < 24:
            buckets[23 - hours_ago] += 1
    current_hour = timezones.now_local(_org_tz(db, user.org_id)).hour
    return {"hours": buckets, "current_hour": current_hour}