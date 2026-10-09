"""
HV-9 — call analytics.

Two of the four numbers on `/dashboard/stats` were not measurements:

  * `resolution_rate_pct` counted **any** call with an outcome that wasn't
    a transfer as resolved, so `failed` and `no_answer` calls scored as
    successes. A day of entirely failed calls could report a healthy rate.

  * `staff_time_saved_hrs` was `calls_today * 5 / 60` — a constant, so it
    was the call count in disguise and moved only with volume.

The two tests named `test_the_old_*` pin exactly what was wrong, so the
definitions cannot quietly regress to the flattering versions.

    pytest tests/test_hv9_analytics.py -v
"""

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import analytics_service, models
from app.database import Base


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def org(db):
    o = models.Organization(name="Northside Clinic", timezone="UTC")
    db.add(o)
    db.commit()
    return o


_n = [0]


def _call(db, org, status="completed", outcome="Booked", seconds=180, when=None, reason=None):
    _n[0] += 1
    call = models.CallLog(
        id=f"c{_n[0]}",
        org_id=org.id,
        direction="inbound",
        status=status,
        outcome=outcome,
        reason=reason,
        duration_seconds=seconds,
        occurred_at=when or datetime.utcnow(),
    )
    db.add(call)
    db.commit()
    return call


# ---------- the two broken metrics ----------

def test_the_old_resolution_rate_counted_failed_calls_as_successes(db, org):
    """
    Pins the defect. Under the old rule —

        resolved = [c for c in calls if c.outcome and c.outcome != "Transferred to Nurse"]

    — three calls that never connected, each carrying some outcome, scored
    100% resolved. The new definition reports None, because nothing
    connected, and surfaces them as missed instead.
    """
    for _ in range(3):
        _call(db, org, status="failed", outcome="Booked")

    old_rule = [c for c in db.query(models.CallLog).all() if c.outcome and c.outcome != "Transferred to Nurse"]
    assert len(old_rule) == 3  # the old rule saw three successes

    calls = db.query(models.CallLog).all()
    assert analytics_service.resolution_rate(calls) is None  # nothing connected
    assert analytics_service.overview(db, org, 30)["missed_calls"] == 3


def test_resolution_rate_excludes_calls_that_never_connected(db, org):
    _call(db, org, status="completed", outcome="Booked")
    _call(db, org, status="completed", outcome="FAQ Answered")
    _call(db, org, status="completed", outcome="Transferred to Nurse")
    _call(db, org, status="no_answer", outcome=None)

    # 2 of 3 CONNECTED calls resolved; the no_answer is not in the denominator.
    assert analytics_service.resolution_rate(db.query(models.CallLog).all()) == 66.7


def test_resolution_rate_is_none_not_zero_when_nothing_connected(db, org):
    """
    "We answered nothing" and "we answered badly" are different facts. A
    0.0 would read as the second.
    """
    _call(db, org, status="no_answer", outcome=None)
    assert analytics_service.resolution_rate(db.query(models.CallLog).all()) is None


def test_the_old_time_saved_was_a_constant_the_new_one_tracks_duration(db, org):
    """
    Two calls of very different lengths produced an identical
    `staff_time_saved_hrs` under the old rule, because it only counted
    calls. The new figure moves with actual duration.
    """
    _call(db, org, seconds=30)
    short_total = analytics_service.ai_handled_minutes(db.query(models.CallLog).all())

    db.query(models.CallLog).delete()
    db.commit()
    _call(db, org, seconds=1800)
    long_total = analytics_service.ai_handled_minutes(db.query(models.CallLog).all())

    old_rule = 1 * 5 / 60  # identical for both, by construction
    assert short_total != long_total
    assert short_total == 0.5 and long_total == 30.0
    assert round(old_rule, 1) == 0.1


def test_time_saved_excludes_transferred_and_missed_calls(db, org):
    """A human took the transferred call, so no time was saved on it."""
    _call(db, org, outcome="Booked", seconds=600)
    _call(db, org, outcome="Transferred to Nurse", seconds=600)
    _call(db, org, status="failed", outcome=None, seconds=600)
    assert analytics_service.ai_handled_minutes(db.query(models.CallLog).all()) == 10.0


def test_average_handle_time_ignores_calls_that_never_connected(db, org):
    _call(db, org, status="completed", seconds=120)
    _call(db, org, status="completed", seconds=240)
    _call(db, org, status="no_answer", seconds=5)
    assert analytics_service.average_handle_seconds(db.query(models.CallLog).all()) == 180


def test_average_handle_time_is_none_with_no_durations(db, org):
    _call(db, org, seconds=None)
    assert analytics_service.average_handle_seconds(db.query(models.CallLog).all()) is None


# ---------- ranges ----------

def test_the_range_includes_today_and_is_inclusive(db, org):
    """"Last 7 days" means today and the six before it."""
    _, _, start_day, end_day = analytics_service.resolve_range(org, 7)
    assert (end_day - start_day).days == 6


def test_an_absurd_range_is_capped(db, org):
    assert analytics_service.overview(db, org, 999_999)["range_days"] == analytics_service.MAX_RANGE_DAYS


def test_a_zero_or_negative_range_still_returns_a_day(db, org):
    assert analytics_service.overview(db, org, 0)["range_days"] == 1
    assert analytics_service.overview(db, org, -5)["range_days"] == 1


def test_calls_outside_the_range_are_excluded(db, org):
    _call(db, org, when=datetime.utcnow())
    _call(db, org, when=datetime.utcnow() - timedelta(days=40))
    assert analytics_service.overview(db, org, 7)["total_calls"] == 1
    assert analytics_service.overview(db, org, 60)["total_calls"] == 2


# ---------- series ----------

def test_the_daily_series_includes_zero_days(db, org):
    """
    A gap in a sparse series reads as missing data rather than a quiet
    Sunday, so every day in the range is present.
    """
    _call(db, org, when=datetime.utcnow())
    series = analytics_service.daily_series(db, org, 7)
    assert len(series) == 7
    assert sum(p["total"] for p in series) == 1
    assert any(p["total"] == 0 for p in series)


def test_the_daily_series_breaks_out_missed_and_transferred(db, org):
    _call(db, org, status="completed", outcome="Booked")
    _call(db, org, status="failed", outcome=None)
    _call(db, org, status="completed", outcome="Transferred to Nurse")
    today = analytics_service.daily_series(db, org, 1)[0]
    assert today == {**today, "total": 3, "missed": 1, "transferred": 1}


def test_hourly_and_weekday_cover_every_bucket(db, org):
    _call(db, org)
    assert len(analytics_service.hourly_distribution(db, org, 7)) == 24
    weekdays = analytics_service.weekday_distribution(db, org, 7)
    assert len(weekdays) == 7
    assert weekdays[0]["label"] == "Mon"  # Monday=0, matching Doctor.work_days_csv


def test_buckets_use_the_clinics_local_hour_not_utc(db):
    """
    A 9 AM local rush at a UTC+5:30 clinic would otherwise appear at 03:30
    on a UTC axis, making "peak hour" answer a question nobody asked.
    """
    ist = models.Organization(name="IST Clinic", timezone="Asia/Kolkata")
    db.add(ist)
    db.commit()
    # 03:30 UTC == 09:00 IST, today in IST.
    from app import timezones as tzmod

    tz = tzmod.org_timezone(ist)
    local_9am = datetime.combine(datetime.now(tz).date(), datetime.min.time()).replace(hour=9)
    _call(db, ist, when=tzmod.to_utc_naive(local_9am, tz))

    hours = analytics_service.hourly_distribution(db, ist, 2)
    assert hours[9]["calls"] == 1
    assert hours[3]["calls"] == 0


# ---------- breakdowns ----------

def test_breakdowns_count_and_rank(db, org):
    _call(db, org, outcome="Booked", reason="appointment")
    _call(db, org, outcome="Booked", reason="appointment")
    _call(db, org, outcome="FAQ Answered", reason="opening hours")
    b = analytics_service.breakdowns(db, org, 30)
    assert b["by_outcome"][0] == {"label": "Booked", "count": 2}
    assert b["by_reason"][0] == {"label": "appointment", "count": 2}


def test_breakdowns_skip_nulls_rather_than_counting_them(db, org):
    _call(db, org, outcome=None, reason=None)
    b = analytics_service.breakdowns(db, org, 30)
    assert b["by_outcome"] == []
    assert b["by_reason"] == []


def test_doctor_breakdown_separates_ai_bookings(db, org):
    doctor = models.Doctor(org_id=org.id, name="Dr. Patel")
    db.add(doctor)
    db.commit()
    for ai in (True, True, False):
        db.add(models.Appointment(
            org_id=org.id, doctor_id=doctor.id, title="Visit",
            ai_generated=ai, created_at=datetime.utcnow(), status="upcoming",
        ))
    db.commit()

    rows = analytics_service.doctor_breakdown(db, org, 30)
    assert rows[0]["doctor"] == "Dr. Patel"
    assert rows[0]["booked"] == 3
    assert rows[0]["ai_booked"] == 2


def test_appointments_without_a_doctor_are_not_dropped(db, org):
    db.add(models.Appointment(
        org_id=org.id, title="Walk-in", created_at=datetime.utcnow(), status="upcoming",
    ))
    db.commit()
    rows = analytics_service.doctor_breakdown(db, org, 30)
    assert rows[0]["doctor"] == "Unassigned"


# ---------- isolation ----------

def test_analytics_never_cross_org_boundaries(db):
    a = models.Organization(name="Clinic A", timezone="UTC")
    b = models.Organization(name="Clinic B", timezone="UTC")
    db.add_all([a, b])
    db.commit()
    _call(db, a)
    _call(db, a)
    assert analytics_service.overview(db, a, 30)["total_calls"] == 2
    assert analytics_service.overview(db, b, 30)["total_calls"] == 0


def test_an_org_with_no_calls_returns_zeros_not_an_error(db, org):
    o = analytics_service.overview(db, org, 30)
    assert o["total_calls"] == 0
    assert o["resolution_rate_pct"] is None
    assert o["ai_handled_minutes"] == 0.0
    assert analytics_service.daily_series(db, org, 7)
