"""
Usage metering and plan limits — HV-4.

`Organization.voice_minutes_used` was **never incremented anywhere**. It is
set once by `seed.py` to 3450 and read by the clinic's Billing page, which
therefore displayed "3,450 / 5,000 minutes" forever regardless of call
volume. Meanwhile `routers/admin_usage.py` computed real usage from
`sum(CallLog.duration_seconds) / 60` — so the super admin and the clinic
saw two different, contradictory numbers for the same thing (finding C8).

**This module resolves that by computing, not counting.** Voice minutes are
derived from `CallLog` on demand; the stored column is no longer read for
display anywhere. A cached counter and a computed total can drift, and when
they do there is no way to tell which is right — deriving from the call log
means the number is correct by construction, and the call log is the thing
being billed for anyway.

The cost of computing is one indexed SUM over one org's calls in one
month, served by `ix_call_logs_org_occurred` (migration 0005).

The second half of this module turns plan limits from stored decoration
into request-time checks: seat and record caps, the `ehr_access` /
`knowledge_base_access` feature gates, and `Subscription.status`, which
nothing consulted at all — so cancelling a subscription used to change
nothing. Staff writes get a 403 with a message; paths the voice agent
reaches degrade instead, because a caller must never hear about a
billing problem.

What this module deliberately does NOT do: block calls at the limit.
Whether an over-limit clinic is cut off or billed for overage is a
commercial decision, and hard-blocking inbound calls touches the voice
path — that needs an explicit decision, so `over_limit` is reported and
acted on by nothing.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, timezones

logger = logging.getLogger("usage-service")

# Percentages at which a clinic is warned. Crossing one raises a single
# notification — see `notify_if_threshold_crossed` for why it fires on
# the crossing rather than on every call while over.
WARNING_THRESHOLDS = (80, 100)


def current_period_bounds(org: models.Organization) -> tuple[datetime, datetime]:
    """
    The clinic's current billing month, as a UTC range [start, end).

    Calendar month in the **clinic's own timezone**, not UTC: a clinic at
    UTC+5:30 would otherwise see its month roll over at 5:30 AM on the
    1st, and a call at midnight on the 1st would be billed to the
    previous month. `CallLog.occurred_at` is UTC (see app/timezones.py for
    the storage convention), so the local month boundaries are converted
    back to UTC for the query.
    """
    tz = timezones.org_timezone(org)
    today = datetime.now(tz).date()
    local_start = datetime.combine(date(today.year, today.month, 1), datetime.min.time())
    # First day of next month, without needing calendar arithmetic.
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    local_end = datetime.combine(next_month, datetime.min.time())
    return (
        timezones.to_utc_naive(local_start, tz),
        timezones.to_utc_naive(local_end, tz),
    )


def voice_minutes_used(
    db: Session,
    org: models.Organization,
    exclude_call_id: Optional[str] = None,
) -> float:
    """
    Voice minutes consumed by this clinic in the current billing month.

    Computed from `CallLog.duration_seconds`. `exclude_call_id` lets a
    caller ask "what was this before the call that just ended?", which is
    how threshold crossings are detected without storing a high-water mark.
    """
    start, end = current_period_bounds(org)
    q = db.query(func.sum(models.CallLog.duration_seconds)).filter(
        models.CallLog.org_id == org.id,
        models.CallLog.occurred_at >= start,
        models.CallLog.occurred_at < end,
    )
    if exclude_call_id:
        q = q.filter(models.CallLog.id != exclude_call_id)
    total_seconds = q.scalar() or 0
    return round(total_seconds / 60.0, 1)


def effective_limit(db: Session, org: models.Organization) -> int:
    """
    The clinic's voice-minute allowance.

    An assigned `Plan` wins over `Organization.voice_minutes_limit`: the
    plan catalogue is what a super admin manages, and the org column is
    kept in sync for the clinic-facing page. Reading the plan first means
    a plan edit takes effect without a backfill.
    """
    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.org_id == org.id)
        .first()
    )
    if subscription is not None:
        plan = (
            db.query(models.Plan)
            .filter(models.Plan.id == subscription.plan_id)
            .first()
        )
        if plan is not None and plan.voice_minutes_limit:
            return plan.voice_minutes_limit
    return org.voice_minutes_limit or 0


def usage_snapshot(db: Session, org: models.Organization) -> dict[str, Any]:
    """Everything the Billing page and the limit checks need, in one query pass."""
    used = voice_minutes_used(db, org)
    limit = effective_limit(db, org)
    pct = round((used / limit) * 100, 1) if limit else 0.0
    start, end = current_period_bounds(org)
    return {
        "minutes_used": used,
        "minutes_limit": limit,
        "usage_pct": pct,
        "over_limit": bool(limit) and used >= limit,
        "period_start": start,
        "period_end": end - timedelta(seconds=1),
    }


def notify_if_threshold_crossed(
    db: Session, org: models.Organization, finished_call_id: str
) -> None:
    """
    Warns the clinic when a finished call pushes it past 80% or 100%.

    Fires on the **crossing**, by comparing usage with and without the
    call that just ended — not on "currently above the threshold", which
    would raise a warning on every subsequent call for the rest of the
    month and train staff to ignore it.

    Never raises: this is a side effect of a call ending, and the call
    update must succeed regardless.
    """
    try:
        limit = effective_limit(db, org)
        if not limit:
            return

        after = voice_minutes_used(db, org)
        before = voice_minutes_used(db, org, exclude_call_id=finished_call_id)
        pct_after = (after / limit) * 100
        pct_before = (before / limit) * 100

        crossed = [t for t in WARNING_THRESHOLDS if pct_before < t <= pct_after]
        if not crossed:
            return

        from .realtime import notify

        threshold = max(crossed)
        if threshold >= 100:
            title = "Voice minutes exhausted"
            message = (
                f"This month's {limit:,} included minutes are used up "
                f"({after:,.0f} used). Calls are still being answered."
            )
            level = "warning"
        else:
            title = f"{threshold}% of voice minutes used"
            message = f"{after:,.0f} of {limit:,} minutes used this month."
            level = "warning"

        notify(org.id, level=level, title=title, message=message,
               data={"usage_pct": round(pct_after, 1), "minutes_limit": limit})
    except Exception:
        logger.exception("Usage threshold check failed for org=%s", org.id)


# ---------- Entitlement: which plan an org is actually allowed to use ----------
#
# `Plan.user_limit`, `patient_limit`, `ehr_access` and
# `knowledge_base_access` were stored, shown in the super-admin UI, and
# **never checked at request time** — so a plan's limits were decoration.
# `Subscription.status` was likewise never consulted, so cancelling a
# subscription did nothing at all. These are the checks that make both
# real.
#
# Voice minutes are deliberately absent here: blocking calls at the limit
# is a commercial decision that touches the voice path, so `over_limit` is
# reported and enforced by nothing.

# Statuses that still entitle an org to its plan.
#
# `past_due` deliberately keeps access. Cutting a clinic's phone AI off
# over a late invoice is a dunning decision, not an entitlement one, and
# dunning belongs with HV-5 (payments) where there is a payment provider
# to define "late" in the first place. `canceled` does revoke: somebody
# has explicitly ended the relationship.
ENTITLING_SUBSCRIPTION_STATUSES = frozenset({"active", "past_due"})

# Human labels for the gated features, used in the 403 a clinic sees.
FEATURE_LABELS = {
    "ehr_access": "EHR integration",
    "knowledge_base_access": "the document knowledge base",
}


def _subscription_for(db: Session, org_id: str) -> Optional[models.Subscription]:
    return (
        db.query(models.Subscription)
        .filter(models.Subscription.org_id == org_id)
        .first()
    )


def _plan_for(db: Session, org_id: str) -> Optional[models.Plan]:
    """
    The org's plan, or None when it has no *entitling* subscription.

    A `canceled` subscription returns None here, which for the count
    limits means "no cap" — the same as an org that never had a plan.
    That is deliberate: a cap is what a plan grants, and revoking access
    to gated features (see `has_feature`) is a different question from
    retroactively capping records a clinic already stores.
    """
    subscription = _subscription_for(db, org_id)
    if subscription is None:
        return None
    if (subscription.status or "active") not in ENTITLING_SUBSCRIPTION_STATUSES:
        return None
    return db.query(models.Plan).filter(models.Plan.id == subscription.plan_id).first()


def check_user_limit(db: Session, org_id: str) -> Optional[str]:
    """
    Error message if adding another user would exceed the plan, else None.

    Counts active users only — a deactivated colleague should not consume
    a seat, and the alternative would make a clinic delete history to hire.
    """
    plan = _plan_for(db, org_id)
    if plan is None or not plan.user_limit:
        return None
    current = (
        db.query(models.User)
        .filter(
            models.User.org_id == org_id,
            models.User.is_active == True,  # noqa: E712 - SQL Server BIT compatibility
        )
        .count()
    )
    if current >= plan.user_limit:
        return (
            f"Your {plan.name} plan includes {plan.user_limit} team members and you "
            f"have {current}. Upgrade your plan to invite more."
        )
    return None


def check_patient_limit(db: Session, org_id: str) -> Optional[str]:
    """
    Error message if adding another patient would exceed the plan, else None.

    Archived patients still count: the record is retained and still
    reachable, so it is still stored data the plan covers.
    """
    plan = _plan_for(db, org_id)
    if plan is None or not plan.patient_limit:
        return None
    current = db.query(models.Patient).filter(models.Patient.org_id == org_id).count()
    if current >= plan.patient_limit:
        return (
            f"Your {plan.name} plan includes {plan.patient_limit:,} patient records "
            f"and you have {current:,}. Upgrade your plan to add more."
        )
    return None


def has_feature(db: Session, org_id: str, feature: str) -> bool:
    """
    Whether the org is entitled to a gated feature.

    `feature` is "ehr_access" or "knowledge_base_access". Three cases,
    and the difference between the first two is the whole point:

      * **No subscription row at all → True.** Most orgs have none, and
        denying them would have broken every existing clinic the moment
        this shipped. Absence of a plan is not a decision to withhold.
      * **Subscription not entitling (`canceled`) → False.** Here somebody
        *has* decided. Falling through to the grandfather rule would mean a
        cancelled clinic ended up with more access than a paying Starter
        one, which is backwards.
      * **Entitling subscription → whatever the plan says.** `Plan.ehr_access`
        and `knowledge_base_access` both default to False in the model, so a
        plan grants these explicitly.
    """
    subscription = _subscription_for(db, org_id)
    if subscription is None:
        return True
    if (subscription.status or "active") not in ENTITLING_SUBSCRIPTION_STATUSES:
        return False
    plan = db.query(models.Plan).filter(models.Plan.id == subscription.plan_id).first()
    if plan is None:
        return True
    return bool(getattr(plan, feature, True))


def feature_error(db: Session, org_id: str, feature: str) -> Optional[str]:
    """
    Message for the 403 a clinic sees when its plan excludes `feature`,
    or None when it is entitled.

    Staff-facing writes raise this. Endpoints the **voice agent** reaches
    must not: see `rag_service.answer_question` and
    `ehr_service.get_active_integration`, which degrade to the same
    "nothing available" state they already had for an unconfigured org.
    A live caller must never hear about a billing problem.
    """
    if has_feature(db, org_id, feature):
        return None
    label = FEATURE_LABELS.get(feature, feature.replace("_", " "))
    subscription = _subscription_for(db, org_id)
    if subscription is not None and (
        (subscription.status or "active") not in ENTITLING_SUBSCRIPTION_STATUSES
    ):
        return (
            f"Your subscription is {subscription.status}, so {label} is no longer "
            "available. Contact us to reactivate it."
        )
    plan = _plan_for(db, org_id)
    plan_name = plan.name if plan is not None else "current"
    return f"Your {plan_name} plan doesn't include {label}. Upgrade to enable it."


def entitlements(db: Session, org_id: str) -> dict[str, Any]:
    """
    What the org's plan grants, for the Billing page.

    The page used to describe every plan as including "premium EHR
    integrations and dedicated support" in hardcoded copy, which was
    false for the seeded Starter plan the moment feature gates started
    being enforced. Sending the real flags means the description can
    match what the clinic will actually be allowed to do.
    """
    subscription = _subscription_for(db, org_id)

    # `plan_name` comes from the assigned plan whether or not the
    # subscription still entitles it, so a cancelled clinic reads
    # "Starter / Cancelled" rather than falling back to
    # `Organization.plan` — a display column a super admin keeps in sync
    # by hand, which is exactly the kind of drift C8 was about.
    assigned = (
        db.query(models.Plan).filter(models.Plan.id == subscription.plan_id).first()
        if subscription is not None
        else None
    )
    # The limits, by contrast, track *enforcement*: `_plan_for` returns
    # None for a cancelled subscription and the caps then don't apply, so
    # showing them would promise a cap nothing checks.
    entitled = _plan_for(db, org_id)

    return {
        "subscription_status": subscription.status if subscription else None,
        "plan_name": assigned.name if assigned else None,
        "user_limit": entitled.user_limit if entitled else None,
        "patient_limit": entitled.patient_limit if entitled else None,
        "ehr_access": has_feature(db, org_id, "ehr_access"),
        "knowledge_base_access": has_feature(db, org_id, "knowledge_base_access"),
    }
