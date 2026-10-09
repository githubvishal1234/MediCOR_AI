"""
Clinic-local time — Phase 1.

THE STORAGE CONVENTION, stated once, because the codebase mixes two
kinds of datetime and confusing them is what caused the original bug:

  * `Appointment.start_at` / `end_at`, and `PendingBooking.start_at`,
    hold **clinic-local wall-clock time**, naive. "10:00" means ten in
    the morning at the clinic, whatever that is in UTC.

  * `occurred_at`, `created_at`, `started_at`, `ended_at` and friends
    hold **UTC**, naive, because they are written by
    `datetime.utcnow()`. These are instants, not wall-clock readings.

Before Phase 1 nothing distinguished the two. `_validate_slot` compared
a wall-clock appointment time against `datetime.utcnow()`, and
`get_availability` built a doctor's 9-5 grid as if the clinic were on
UTC. For a clinic at UTC+5:30 that meant the offered slot grid was
shifted by five and a half hours and "cannot book an appointment in the
past" fired across most of the working day.

This module supplies the small set of conversions needed to keep the two
kinds apart:

  * `now_local(tz)`          — the clinic's current wall-clock reading,
                               for comparing against stored appointment
                               times.
  * `local_day_bounds_utc()` — the clinic's calendar day expressed as a
                               UTC range, for filtering the UTC columns.
  * `to_local_naive` / `to_utc_naive` — conversions at the boundary.

Why wall-clock storage rather than UTC: the existing rows already hold
wall-clock time, the voice agent's `_resolve_start_at` fallback depends
on availability returning naive `YYYY-MM-DDTHH:MM:SS` strings, and the
dashboard calendar parses the same field with `new Date(...)`. Switching
the column to UTC would require a data backfill plus changes to every
appointment response shape and the agent's slot-matching logic. That
migration belongs with the multi-tenant work, when clinics in different
zones actually share a deployment — see `docs/TIMEZONES.md`.

Note on DST: wall-clock storage is ambiguous for the one hour that
repeats at a DST fall-back transition. Clinic working hours make this
practically unreachable (no clinic books 1:30 AM), and the current
deployment region (Asia/Kolkata) has no DST at all.
"""

import logging
from datetime import date as date_cls, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

logger = logging.getLogger("timezones")

DEFAULT_TIMEZONE = "UTC"

UTC = timezone.utc


@lru_cache(maxsize=256)
def _load(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def resolve(name: Optional[str]) -> ZoneInfo:
    """
    A `ZoneInfo` for an IANA name, falling back to UTC.

    Never raises. A bad or missing timezone must not take down booking —
    falling back to UTC reproduces the pre-Phase-1 behaviour, which is
    wrong for a non-UTC clinic but is not an outage.

    On Windows there is no system tz database, so an unhelpful
    `ZoneInfoNotFoundError` here almost always means the `tzdata` package
    is missing from the environment (it is in requirements.txt).
    """
    if not name:
        return _load(DEFAULT_TIMEZONE)
    try:
        return _load(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Unknown timezone %r — falling back to UTC. On Windows this "
            "usually means the `tzdata` package is not installed.",
            name,
        )
        return _load(DEFAULT_TIMEZONE)


def is_valid(name: Optional[str]) -> bool:
    """Whether `name` is a usable IANA timezone in this environment."""
    if not name:
        return False
    try:
        _load(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


@lru_cache(maxsize=1)
def all_timezone_names() -> tuple[str, ...]:
    """Every IANA zone this environment knows, sorted — for a picker UI."""
    return tuple(sorted(available_timezones()))


def org_timezone_name(org) -> str:
    """The org's configured IANA name, or the UTC default."""
    return (getattr(org, "timezone", None) or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def org_timezone(org) -> ZoneInfo:
    """`ZoneInfo` for an Organization row (accepts None)."""
    return resolve(org_timezone_name(org) if org is not None else None)


# ---------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------

def now_local(tz: ZoneInfo) -> datetime:
    """
    The clinic's current wall-clock reading, naive.

    This is what stored appointment times must be compared against —
    never `datetime.utcnow()`, which is a different clock entirely
    unless the clinic happens to sit on UTC.
    """
    return datetime.now(tz).replace(tzinfo=None)


def to_utc_naive(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """Clinic wall-clock -> UTC, both naive. Used when filtering UTC columns."""
    return local_naive.replace(tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def to_local_naive(utc_naive: datetime, tz: ZoneInfo) -> datetime:
    """UTC -> clinic wall-clock, both naive. Used when displaying UTC columns."""
    return utc_naive.replace(tzinfo=UTC).astimezone(tz).replace(tzinfo=None)


def normalize_incoming(value: datetime, tz: ZoneInfo) -> datetime:
    """
    Coerce a caller-supplied datetime into clinic wall-clock, naive.

    A naive value is taken at face value as clinic-local — which is what
    the voice agent sends, because `get_availability` hands it naive
    clinic-local slots and its `_resolve_start_at` fallback rebuilds
    strings in that same shape. An aware value (any client that sends a
    real offset) is converted into the clinic's zone first, so both
    styles land on the same instant.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(tz).replace(tzinfo=None)


def local_day_bounds_utc(tz: ZoneInfo, day: Optional[date_cls] = None) -> tuple[datetime, datetime]:
    """
    The clinic's calendar day as a half-open UTC range [start, end).

    For filtering the genuinely-UTC columns (`CallLog.occurred_at`,
    `Appointment.created_at`) by "today at the clinic". Using
    `utcnow().replace(hour=0)` instead — the pre-Phase-1 approach — rolls
    the dashboard's day over at 5:30 AM local for a UTC+5:30 clinic.
    """
    target = day or datetime.now(tz).date()
    local_start = datetime.combine(target, datetime.min.time())
    local_end = local_start + timedelta(days=1)
    return to_utc_naive(local_start, tz), to_utc_naive(local_end, tz)


def utc_offset_label(tz: ZoneInfo) -> str:
    """Current offset as a display string, e.g. "UTC+05:30"."""
    offset = datetime.now(tz).utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"
