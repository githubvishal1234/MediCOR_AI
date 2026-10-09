"""
Call analytics — HV-9.

`/dashboard/stats` returned four numbers, two of which were not
measurements:

  * `resolution_rate_pct` counted **any** call with an outcome that wasn't
    "Transferred to Nurse" as resolved — so a `failed` call and a
    `no_answer` call both counted as successes, and a day of entirely
    failed calls could report a healthy resolution rate.

  * `staff_time_saved_hrs` was `calls_today * 5 / 60`. A constant. It is a
    restatement of the call count wearing the costume of a metric, and it
    moved only when call volume moved.

This module replaces both with figures derived from what actually
happened, and adds the breakdowns the dashboard could never answer:
"are we busier on Mondays?", "which reason drives the most transfers?".

Everything is bucketed in the **clinic's timezone**. `occurred_at` is UTC
(see app/timezones.py), so "peak hour" and "calls per day" would otherwise
be answered in a timezone the clinic does not work in — a 9 AM local rush
at a UTC+5:30 clinic shows up at 03:30 on a UTC axis.

Aggregation happens in Python over a bounded date range rather than in
SQL. That is deliberate: the UTC->local conversion needed for correct
bucketing is awkward and dialect-specific in T-SQL, the range is capped,
and `ix_call_logs_org_occurred` makes fetching it cheap.
"""

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from . import models, timezones

logger = logging.getLogger("analytics-service")

# A call that never connected cannot have been "resolved" — it is a
# failure of telephony, not an outcome of a conversation. These are
# excluded from the resolution-rate denominator and counted separately.
MISSED_STATUSES = {"failed", "no_answer"}

# Outcomes that mean the AI finished the job itself.
RESOLVED_OUTCOMES = {
    "Booked",
    "Rescheduled",
    "Cancelled",
    "FAQ Answered",
    "Completed",
}
TRANSFER_OUTCOME = "Transferred to Nurse"

# Upper bound on a query range. A year of calls at clinic volume is a few
# thousand rows; this stops a crafted `days=100000` pulling the table.
MAX_RANGE_DAYS = 366


def resolve_range(
    org: models.Organization, days: int
) -> tuple[datetime, datetime, date, date]:
    """
    A day range in the clinic's timezone, returned as UTC bounds.

    Returns (utc_start, utc_end, local_start_date, local_end_date). The
    range is inclusive of today, so `days=7` means "today and the six
    days before it" — which is what someone picking "last 7 days" means.
    """
    days = max(1, min(days, MAX_RANGE_DAYS))
    tz = timezones.org_timezone(org)
    today = datetime.now(tz).date()
    start_day = today - timedelta(days=days - 1)

    local_start = datetime.combine(start_day, datetime.min.time())
    local_end = datetime.combine(today + timedelta(days=1), datetime.min.time())
    return (
        timezones.to_utc_naive(local_start, tz),
        timezones.to_utc_naive(local_end, tz),
        start_day,
        today,
    )


def _calls_in_range(db: Session, org: models.Organization, days: int):
    start, end, start_day, end_day = resolve_range(org, days)
    calls = (
        db.query(models.CallLog)
        .filter(
            models.CallLog.org_id == org.id,
            models.CallLog.occurred_at >= start,
            models.CallLog.occurred_at < end,
        )
        .all()
    )
    return calls, start_day, end_day


def _local(dt: datetime, tz) -> datetime:
    return timezones.to_local_naive(dt, tz)


def resolution_rate(calls: list) -> Optional[float]:
    """
    Share of *connected* calls the AI finished without a human.

    The old definition counted any call with an outcome and no transfer,
    so `failed` and `no_answer` calls were scored as successes. Here a
    call that never connected is not in the denominator at all — it is a
    telephony failure, and burying it inside a quality metric hides both.

    Returns None when no call connected, rather than 0.0: "we answered
    nothing today" and "we answered badly today" are different facts, and
    a zero would read as the second.
    """
    connected = [c for c in calls if c.status not in MISSED_STATUSES]
    if not connected:
        return None
    resolved = [
        c for c in connected
        if c.outcome in RESOLVED_OUTCOMES and c.outcome != TRANSFER_OUTCOME
    ]
    return round(100.0 * len(resolved) / len(connected), 1)


def ai_handled_minutes(calls: list) -> float:
    """
    Call minutes the AI handled end to end, without a transfer.

    This replaces `staff_time_saved_hrs = calls * 5 / 60`. It is still an
    estimate of staff time saved — nobody knows how long a human would
    have taken — but it is derived from real call durations rather than a
    constant, so it responds to whether calls were long or short, and to
    whether the AI actually finished them.

    Transferred calls are excluded: a human took that call, so no time
    was saved on it.
    """
    total = 0
    for call in calls:
        if call.status in MISSED_STATUSES:
            continue
        if call.outcome == TRANSFER_OUTCOME:
            continue
        total += call.duration_seconds or 0
    return round(total / 60.0, 1)


def average_handle_seconds(calls: list) -> Optional[int]:
    """Mean duration of connected calls that recorded one."""
    durations = [
        c.duration_seconds
        for c in calls
        if c.status not in MISSED_STATUSES and c.duration_seconds
    ]
    if not durations:
        return None
    return int(sum(durations) / len(durations))


def overview(db: Session, org: models.Organization, days: int = 30) -> dict[str, Any]:
    """Headline numbers for a date range, each derived from real calls."""
    calls, start_day, end_day = _calls_in_range(db, org, days)

    missed = [c for c in calls if c.status in MISSED_STATUSES]
    transferred = [c for c in calls if c.outcome == TRANSFER_OUTCOME]
    connected = [c for c in calls if c.status not in MISSED_STATUSES]

    start, end, _, _ = resolve_range(org, days)
    appointments_booked = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.org_id == org.id,
            models.Appointment.created_at >= start,
            models.Appointment.created_at < end,
        )
        .count()
    )

    return {
        "range_days": max(1, min(days, MAX_RANGE_DAYS)),
        "start_date": start_day,
        "end_date": end_day,
        "total_calls": len(calls),
        "connected_calls": len(connected),
        "missed_calls": len(missed),
        "transferred_calls": len(transferred),
        # Share of connected calls the AI finished alone. The inverse of
        # this is the transfer rate, which is what a clinic actually
        # manages — so both are reported rather than implied.
        "resolution_rate_pct": resolution_rate(calls),
        "transfer_rate_pct": (
            round(100.0 * len(transferred) / len(connected), 1) if connected else None
        ),
        "ai_handled_minutes": ai_handled_minutes(calls),
        "average_handle_seconds": average_handle_seconds(calls),
        # Booked IN the range (by created_at) vs scheduled FOR the range
        # (by start_at) are different questions, so both are reported
        # rather than one standing in for the other.
        "appointments_booked": appointments_booked,
        **appointment_outcomes(db, org, days),
    }


def appointment_outcomes(
    db: Session, org: models.Organization, days: int = 30
) -> dict[str, Any]:
    """
    Appointment outcomes for the range, bucketed by when each appointment
    was **scheduled to happen** — not when its row was created.

    Two things here are easy to get wrong and both were considered:

    **The timezone.** `Appointment.start_at` is clinic wall-clock naive
    (see app/timezones.py), while `resolve_range` returns UTC bounds —
    correct for `occurred_at`/`created_at`, wrong for this. Comparing
    `start_at` against UTC bounds would shift a UTC+5:30 clinic's day by
    5.5 hours and quietly file early-morning appointments under the
    previous day. Local bounds are rebuilt from the local dates instead.

    **The denominator.** `no_show_rate_pct` counts no-shows against
    appointments that actually *concluded* — done + no_show. Cancelled
    ones are excluded because the patient told you, which is a different
    (and much cheaper) event, and still-upcoming ones are excluded
    because they have not happened yet. Folding either in would dilute
    the rate toward zero and make a real no-show problem look small.
    """
    _, _, start_day, end_day = resolve_range(org, days)
    local_start = datetime.combine(start_day, datetime.min.time())
    local_end = datetime.combine(end_day + timedelta(days=1), datetime.min.time())

    rows = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.org_id == org.id,
            # Rows with a null start_at are unschedulable shells (see the
            # PendingBooking docstring) and cannot be placed on a day.
            models.Appointment.start_at.isnot(None),
            models.Appointment.start_at >= local_start,
            models.Appointment.start_at < local_end,
        )
        .all()
    )

    completed = sum(1 for a in rows if a.status == "done")
    no_show = sum(1 for a in rows if a.status == "no_show")
    cancelled = sum(1 for a in rows if a.status == "cancelled")
    concluded = completed + no_show

    return {
        "appointments_scheduled": len(rows),
        "appointments_completed": completed,
        "appointments_no_show": no_show,
        "appointments_cancelled": cancelled,
        # None, not 0.0, when nothing has concluded — same reasoning as
        # `resolution_rate_pct`. A zero here reads as "no one missed an
        # appointment", which is a claim, not an absence of data.
        "no_show_rate_pct": (
            round(100.0 * no_show / concluded, 1) if concluded else None
        ),
    }


def daily_series(db: Session, org: models.Organization, days: int = 30) -> list[dict]:
    """
    Calls per clinic-local day, with missed calls broken out.

    Every day in the range appears, including zero days — a gap in a
    sparse series reads as missing data rather than a quiet Sunday.
    """
    calls, start_day, end_day = _calls_in_range(db, org, days)
    tz = timezones.org_timezone(org)

    per_day: dict[date, dict[str, int]] = {}
    cursor = start_day
    while cursor <= end_day:
        per_day[cursor] = {"total": 0, "missed": 0, "transferred": 0}
        cursor += timedelta(days=1)

    for call in calls:
        if not call.occurred_at:
            continue
        day = _local(call.occurred_at, tz).date()
        bucket = per_day.get(day)
        if bucket is None:
            continue
        bucket["total"] += 1
        if call.status in MISSED_STATUSES:
            bucket["missed"] += 1
        if call.outcome == TRANSFER_OUTCOME:
            bucket["transferred"] += 1

    return [
        {"date": day.isoformat(), **counts}
        for day, counts in sorted(per_day.items())
    ]


def hourly_distribution(db: Session, org: models.Organization, days: int = 30) -> list[dict]:
    """
    Calls by hour of the clinic's local day, across the range.

    Answers "when are we busiest?" — which the old fixed 24-hour window
    could not, because it showed only the last day and bucketed by
    "hours ago" rather than by wall-clock hour.
    """
    calls, _, _ = _calls_in_range(db, org, days)
    tz = timezones.org_timezone(org)

    counts = [0] * 24
    for call in calls:
        if call.occurred_at:
            counts[_local(call.occurred_at, tz).hour] += 1
    return [{"hour": h, "calls": counts[h]} for h in range(24)]


def weekday_distribution(db: Session, org: models.Organization, days: int = 30) -> list[dict]:
    """Calls by day of week, clinic-local. Monday=0, matching Doctor.work_days_csv."""
    calls, _, _ = _calls_in_range(db, org, days)
    tz = timezones.org_timezone(org)
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    counts = [0] * 7
    for call in calls:
        if call.occurred_at:
            counts[_local(call.occurred_at, tz).weekday()] += 1
    return [{"weekday": i, "label": labels[i], "calls": counts[i]} for i in range(7)]


def _counted(values, limit: int = 10) -> list[dict]:
    return [
        {"label": label, "count": count}
        for label, count in Counter(v for v in values if v).most_common(limit)
    ]


def breakdowns(db: Session, org: models.Organization, days: int = 30) -> dict[str, Any]:
    """
    Outcome, sentiment, reason and intent distributions.

    `intent` comes from AI-2's structured extraction and is far more
    useful for grouping than the free-text `reason` the agent records —
    but both are returned, because a clinic with LLM analysis disabled
    still has reasons.
    """
    calls, _, _ = _calls_in_range(db, org, days)
    return {
        "by_outcome": _counted(c.outcome for c in calls),
        "by_sentiment": _counted(c.sentiment for c in calls),
        "by_reason": _counted(c.reason for c in calls),
        "by_intent": _counted(getattr(c, "intent", None) for c in calls),
        "by_status": _counted(c.status for c in calls),
    }


def doctor_breakdown(db: Session, org: models.Organization, days: int = 30) -> list[dict]:
    """
    Appointments booked per doctor in the range, and how many the AI booked.

    Uses `Appointment` rather than `CallLog` because a call does not carry
    a doctor — the booking does.
    """
    start, end, _, _ = resolve_range(org, days)
    appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.org_id == org.id,
            models.Appointment.created_at >= start,
            models.Appointment.created_at < end,
        )
        .all()
    )
    doctors = {
        d.id: d.name
        for d in db.query(models.Doctor).filter(models.Doctor.org_id == org.id).all()
    }

    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"booked": 0, "ai_booked": 0, "cancelled": 0}
    )
    for appt in appointments:
        name = doctors.get(appt.doctor_id) or "Unassigned"
        stats[name]["booked"] += 1
        if appt.ai_generated:
            stats[name]["ai_booked"] += 1
        if appt.status == "cancelled":
            stats[name]["cancelled"] += 1

    return sorted(
        ({"doctor": name, **counts} for name, counts in stats.items()),
        key=lambda row: row["booked"],
        reverse=True,
    )
