# How this codebase handles time

There are **two kinds of datetime** in the database and they mean
different things. Confusing them is what caused the original scheduling
bug, so this is worth reading before touching anything date-related.

## The two kinds

### 1. Clinic wall-clock time (naive)

- `appointments.start_at`, `appointments.end_at`
- `pending_bookings.start_at`

`10:00` means **ten in the morning at the clinic**. It is a reading off
the wall clock, not an instant. To know what instant it refers to you
need the clinic's timezone, which lives on `organizations.timezone`.

Doctor working hours (`work_start_hour`, `work_end_hour`) are also
clinic wall-clock hours.

### 2. UTC instants (naive)

- `call_logs.occurred_at`, `.started_at`, `.ended_at`
- `appointments.created_at`, `patients.created_at`, `users.created_at`
- every other `default=datetime.utcnow` column

These are written by `datetime.utcnow()` and are genuine points in time.

## The rule

> Compare wall-clock to wall-clock, and instant to instant. Never mix.

- Is an appointment in the past? → `start_at < timezones.now_local(tz)`
- Did a call happen today? → `occurred_at` between the two values from
  `timezones.local_day_bounds_utc(tz)`

`app/timezones.py` has the conversions. Do not reach for
`datetime.utcnow()` when the other side of the comparison is an
appointment time.

## What the bug was

`_validate_slot` did `if start_at < datetime.utcnow()`, and
`get_availability` built the day's slot grid from the doctor's working
hours and filtered it against `datetime.utcnow()`. Both compared a
wall-clock reading against a UTC instant.

For a clinic at UTC+05:30 configured 9 AM–5 PM, that meant:

- the slot grid was effectively evaluated as if the clinic ran
  2:30 PM–10:30 PM, and
- "Cannot book an appointment in the past" fired across most of the real
  working day.

For a UTC clinic the two clocks coincide, which is why this never showed
up in the demo org.

## Configuring a clinic

Clinic Profile → Timezone, or `PUT /knowledge-base/clinic-info` with an
IANA name (`Asia/Kolkata`, not `IST` and not `+05:30`).

The default is `UTC`, which reproduces the pre-Phase-1 behaviour exactly,
so an existing install changes nothing until it sets a real zone.

An unusable name is **rejected** by the API rather than silently falling
back — a silent fallback would shift every future booking without telling
anyone.

### `tzdata` is required on Windows

Windows ships no system IANA database. Without the `tzdata` package (it
is pinned in `requirements.txt`), `ZoneInfo("Asia/Kolkata")` raises and
`timezones.resolve()` falls back to UTC — reintroducing the original bug
as a packaging problem. `tests/test_phase1_scheduling.py` asserts it is
installed.

## Existing data

No backfill is needed. `start_at` values written before Phase 1 already
hold clinic wall-clock time — the old code stored exactly what the slot
grid produced, which was built from clinic working hours. Phase 1 changed
what those values are *compared against*, not what they contain.

## Known limits

**DST.** A wall-clock time is ambiguous for the hour that repeats at a
fall-back transition, and nonexistent for the hour skipped at spring
forward. Clinic working hours make this practically unreachable (no
clinic books at 1:30 AM), and the current deployment region
(`Asia/Kolkata`) has no DST at all.

**Cross-timezone reporting.** Because appointment times are wall-clock,
ordering appointments across clinics in *different* zones requires
joining to each org's timezone. That does not matter while one
deployment serves one clinic. When the multi-tenant runtime lands and a
single deployment serves clinics in several zones, this is the point to
revisit: normalise `start_at` to UTC, keep a `start_at_local` for
display, and backfill using each org's timezone.

That migration was deliberately **not** done in Phase 1: it needs a data
backfill, a change to every appointment response shape, and a change to
the voice agent's `_resolve_start_at` slot-matching (which depends on
availability returning naive `YYYY-MM-DDTHH:MM:SS` clinic-local strings).
Doing it now would have meant touching the voice path for no present
benefit.
