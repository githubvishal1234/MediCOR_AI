"""
Call analytics — HV-9.

Read-only aggregation over calls and appointments the system already
records. Staff-only: this is a clinic reviewing its own operation, and
the voice agent has no use for it, so it is not exposed through
`get_org_context`.

Every endpoint takes `days` and buckets in the clinic's timezone. See
`app/analytics_service.py` for why the aggregation runs in Python rather
than SQL, and for what was wrong with the two `/dashboard/stats` figures
this replaces.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import analytics_service, models, schemas
from ..database import get_db
from ..deps import get_current_user

logger = logging.getLogger("analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _org(db: Session, user: models.User) -> models.Organization:
    org = (
        db.query(models.Organization)
        .filter(models.Organization.id == user.org_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.get("/overview", response_model=schemas.AnalyticsOverviewOut)
def overview(
    days: int = 30,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Headline numbers for the range.

    `resolution_rate_pct` is the share of **connected** calls the AI
    finished without a transfer — calls that never connected are excluded
    from the denominator and reported separately as `missed_calls`. The
    previous definition counted a failed call as a success.
    """
    return schemas.AnalyticsOverviewOut(
        **analytics_service.overview(db, _org(db, user), days)
    )


@router.get("/daily", response_model=list[schemas.AnalyticsDailyPointOut])
def daily(
    days: int = 30,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Calls per clinic-local day. Zero days are included, not skipped."""
    return analytics_service.daily_series(db, _org(db, user), days)


@router.get("/hourly", response_model=list[schemas.AnalyticsHourPointOut])
def hourly(
    days: int = 30,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Calls by hour of the clinic's local day — "when are we busiest?"."""
    return analytics_service.hourly_distribution(db, _org(db, user), days)


@router.get("/weekday", response_model=list[schemas.AnalyticsWeekdayPointOut])
def weekday(
    days: int = 30,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Calls by day of week, clinic-local. Monday = 0."""
    return analytics_service.weekday_distribution(db, _org(db, user), days)


@router.get("/breakdowns", response_model=schemas.AnalyticsBreakdownsOut)
def breakdowns(
    days: int = 30,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Outcome, sentiment, reason, intent and status distributions."""
    return schemas.AnalyticsBreakdownsOut(
        **analytics_service.breakdowns(db, _org(db, user), days)
    )


@router.get("/doctors", response_model=list[schemas.AnalyticsDoctorRowOut])
def doctors(
    days: int = 30,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Appointments per doctor, and how many of them the AI booked."""
    return analytics_service.doctor_breakdown(db, _org(db, user), days)
