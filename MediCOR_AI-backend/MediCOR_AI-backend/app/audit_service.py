"""
The clinic's own audit trail — HV-1.

`audit_log` and `GET /security/audit-log` and the Security page all
existed. The **write path** did not: the table was populated by exactly
two things, `seed.py` and `ehr_service.py`, so the page a clinic reads to
check its HIPAA posture was rendering seeded demo rows. Nothing recorded
who viewed a patient record. Every router now writes through this module.

Four decisions worth understanding before adding a call site.

**1. `record()` does not commit by default.** It adds the row to the
caller's session, so the log entry and the change it describes land in
the same transaction — a booking that rolls back does not leave behind a
record claiming it happened. Read-only access events have nothing else
pending, so those pass `commit=True`. This is the one real difference
from `ehr_service._log_audit`, which committed unconditionally and would
therefore have committed whatever the caller had half-finished.

**2. It never raises.** An audit insert failing must not turn a
successful booking into a 500, the same rule `notify()` and the EHR/
notification services follow. That does mean the trail is fail-*open*:
under a database fault an action can succeed unlogged. Fail-closed is
the stricter posture and it is a deliberate non-choice — refusing to
answer a phone call because an audit row would not insert is the wrong
trade for a clinic, and the failure is logged at exception level so it is
visible in the application log rather than silent.

**3. The actor label is denormalised at write time.** `who` holds a
rendered string rather than being joined from `users`, because an
investigator reads this log precisely when an account has been deleted.
`actor_user_id` is still stored for the cases where the account survives.

**4. Impersonation is attributed to the platform, not the clinic.** A
super admin impersonating a clinic gets an ordinary staff token, which is
what lets every existing route work unchanged — and also meant the
clinic could never see that we had been in. The token now carries an
`imp` claim, `deps` resolves it onto `user.impersonated_by`, and this
module renders that as `actor_type="platform"` with both identities in
`who`. Without that, a platform admin's actions appear in the clinic's
own log as its admin's actions, which is worse than not logging them.

What is deliberately **not** logged: list/index reads. `GET /patients`
returns names, but logging a row per list render would bury the
individual-record reads that actually matter under dashboard noise. The
line is drawn at reading one identified record.
"""

import csv
import io
import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from . import models
from .rate_limit import client_ip as _resolve_client_ip

logger = logging.getLogger("audit")

# ---------------------------------------------------------------------------
# Status vocabulary. Unchanged from what the Security page already colours:
# green, red, neutral. "Logged" is the read case — nothing changed, but
# PHI was seen, and that is the entry a breach investigation needs.
STATUS_SUCCESS = "Success"
STATUS_BLOCKED = "Blocked"
STATUS_LOGGED = "Logged"

# ---------------------------------------------------------------------------
# Closed action vocabulary.
#
# Closed for the same reason `evaluation_service.POLICY_CODES` is: the
# Security page filters on these exact strings, so a typo in one router
# would silently create a category that the filter offers and nothing
# else ever matches. `record()` refuses an unknown code loudly in the log
# and still writes the row — dropping the entry would be the worse
# failure of the two.
ACTIONS = frozenset({
    # Authentication
    "auth.login",
    "auth.login_failed",
    "auth.invite_accepted",
    # Two codes, not one, and deliberately so: "somebody asked to reset
    # this account" and "the password actually changed" are different
    # facts, and only the second one means a credential moved. A flood of
    # the first with none of the second is somebody probing addresses.
    "auth.password_reset_requested",
    "auth.password_reset",
    # Patient records — PHI
    "patient.viewed",
    "patient.created",
    "patient.updated",
    "patient.archived",
    "patient.restored",
    "patient.record_changed",   # prescriptions, visit notes; `detail` says which
    "patient.history_viewed",   # clinical history, incl. anything mirrored from an EHR
    # Calls — transcripts are PHI
    "call.transcript_viewed",
    # Bulk CSV export of call logs + transcripts. Distinct from
    # `call.transcript_viewed`: an export of 200 calls already writes 200
    # of those, but nothing said they were one bulk pull rather than a
    # day of ordinary lookups — which is exactly the distinction a breach
    # investigation cares about.
    "call_log.exported",
    # Scheduling
    "appointment.created",
    "appointment.updated",
    "appointment.cancelled",
    "appointment.rescheduled",
    # Marked as not-arrived. Its own code rather than folding into
    # `appointment.updated`, because the Security page filters on this
    # vocabulary and "who wrote off this patient's slot" is a question
    # worth being able to ask directly.
    "appointment.no_show",
    # The patient said they are coming. Distinct from appointment.updated
    # because it is the patient speaking rather than the clinic, and
    # because "who confirmed, and how" is the question a no-show dispute
    # turns on. Written today by the self-service link (PF-06/PF-08).
    "appointment.confirmed",
    # A repeat-prescription request taken by phone, and the staff decision
    # on it. Two codes, not one, because "the patient asked" and "the
    # clinic agreed" are different facts and only the second carries
    # clinical weight — see routers/refills.py.
    "refill.requested",
    "refill.handled",
    # What the AI says to callers
    "agent_settings.updated",
    "knowledge_base.changed",
    "clinic_info.updated",
    # Credentials and integrations
    "api_key.created",
    "api_key.revoked",
    "ehr.integration_changed",
    "ehr.sync",
    # Team
    "team.member_invited",
    "team.member_changed",
    # Bulk extraction of the log itself
    "audit_log.exported",
    # GAP-B2. A billing document is a financial record about the clinic
    # rather than about a patient, so it is not PHI -- but "who took a
    # copy of our invoices, and when" is exactly the question a finance
    # team asks after a dispute, and the answer should not be "nobody
    # wrote that down". Same reasoning as `call_log.exported`.
    "invoice.downloaded",
    # Money. `checkout_started` and `payment_captured` are separate
    # facts for the same reason `refill.requested` and
    # `refill.handled` are: "somebody tried to pay" and "the payment
    # succeeded" are different, and a run of the first with none of
    # the second is a broken checkout rather than a quiet month.
    "billing.checkout_started",
    "billing.payment_captured",
    "billing.payment_rejected",
    # Us, in their data
    "platform.impersonation_started",
})

# Actor types. "platform" is the one that matters — see the module note.
ACTOR_USER = "user"
ACTOR_SERVICE = "service"
ACTOR_PLATFORM = "platform"
ACTOR_ANONYMOUS = "anonymous"
# The patient themselves, acting through a self-service link. Its own
# actor type rather than "anonymous": the action is attributable to a
# specific person holding a token scoped to one appointment, which is a
# materially different fact from an unauthenticated request, and "did the
# patient cancel this, or did we?" is a question the log should answer.
ACTOR_PATIENT = "patient"


def client_ip(request: Optional[Request]) -> Optional[str]:
    """
    The IP the rate limiter keyed on, not whatever the client claimed.

    Same resolver as `routers/admin.py` uses, which as of Phase 0 only
    trusts `X-Forwarded-For` when the direct peer is a configured trusted
    proxy. Reading the header directly would make audit-log IPs forgeable.
    """
    if request is None:
        return None
    resolved = _resolve_client_ip(request)
    return None if resolved == "unknown" else resolved


def user_agent(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    # Bounded to the column width rather than letting the insert fail on a
    # long or hostile header. A truncated agent string is still evidence.
    ua = request.headers.get("user-agent")
    return ua[:400] if ua else None


def describe_user(user: Optional[models.User]) -> str:
    """The `who` label for a staff account, or a stand-in when it is gone."""
    if user is None:
        return "Unknown account"
    name = (user.full_name or "").strip()
    return f"{name} ({user.email})" if name else user.email


def _actor_from_user(user: Optional[models.User]) -> tuple[str, str, Optional[str]]:
    """
    Resolves (actor_type, who, actor_user_id) for a staff request.

    An impersonated session reports as `platform` and names both parties.
    `impersonated_by` is set by `deps.get_current_user` /
    `get_org_context` from the token's `imp` claim; it is always present
    as an attribute so no call site has to guard for it.
    """
    if user is None:
        return ACTOR_ANONYMOUS, "Unknown account", None
    impersonator = getattr(user, "impersonated_by", None)
    if impersonator:
        return (
            ACTOR_PLATFORM,
            f"MediCare Platform Admin ({impersonator}) acting as {describe_user(user)}",
            user.id,
        )
    return ACTOR_USER, describe_user(user), user.id


def actor_from_context(ctx: Any) -> tuple[str, str, Optional[str]]:
    """
    (actor_type, who, actor_user_id) for a `deps.OrgContext`.

    A service caller is the voice agent — either an `X-API-Key` or a
    per-call org token from `/agent/resolve-tenant`. Both are labelled
    the same because from the clinic's point of view they are the same
    thing: its AI receptionist looked at a record. This is the per-call
    PHI access trail, and it comes for free from the dual-auth dependency.
    """
    if getattr(ctx, "actor", None) == "service":
        return ACTOR_SERVICE, "AI Voice Agent", None
    return _actor_from_user(getattr(ctx, "user", None))


def record(
    db: Session,
    org_id: str,
    action: str,
    *,
    user: Optional[models.User] = None,
    ctx: Any = None,
    who: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Optional[str] = None,
    status: str = STATUS_SUCCESS,
    request: Optional[Request] = None,
    commit: bool = False,
) -> None:
    """
    Writes one audit entry. Never raises.

    Pass **one** of `ctx` (a dual-auth `OrgContext`), `user` (a staff
    request), or an explicit `who`/`actor_type` for the cases that have
    neither — a failed login, or a super admin acting from the platform
    console.

    `commit=False` (the default) leaves the row in the caller's
    transaction so it commits with the change it describes. Pass
    `commit=True` only when there is nothing else pending, which in
    practice means a read.
    """
    try:
        if action not in ACTIONS:
            # Loud, but still recorded: losing the entry would be worse
            # than recording one with an off-vocabulary label.
            logger.error("Audit action %r is not in the closed vocabulary", action)

        if ctx is not None:
            resolved_type, resolved_who, resolved_id = actor_from_context(ctx)
        elif user is not None:
            resolved_type, resolved_who, resolved_id = _actor_from_user(user)
        else:
            resolved_type, resolved_who, resolved_id = (
                actor_type or ACTOR_ANONYMOUS,
                who or "Unknown",
                actor_user_id,
            )

        db.add(
            models.AuditLogEntry(
                org_id=org_id,
                action=action,
                who=who or resolved_who,
                status=status,
                actor_type=actor_type or resolved_type,
                actor_user_id=actor_user_id or resolved_id,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
                ip_address=client_ip(request),
                user_agent=user_agent(request),
            )
        )
        if commit:
            db.commit()
    except Exception:
        # See decision 2 in the module docstring. Roll back only what we
        # added; a caller mid-transaction would otherwise lose its work
        # to a failed audit write, which inverts the priority.
        try:
            if commit:
                db.rollback()
        except Exception:
            logger.exception("Audit rollback failed for org=%s action=%s", org_id, action)
        logger.exception("Failed to write audit entry org=%s action=%s", org_id, action)


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> Optional[str]:
    """
    A `detail` string naming which fields changed, without their values.

    Field names only, deliberately: an audit log that records the old and
    new value of every edited field becomes a second copy of the patient
    record, with a longer retention period and a page that more people
    can read. Naming the fields is enough to reconstruct what happened
    from the record's own history.
    """
    names = sorted(k for k in after if before.get(k) != after.get(k))
    return "changed: " + ", ".join(names) if names else None


# ---------------------------------------------------------------------------
# CSV export
#
# Lives here rather than in the router so the column set is defined next
# to the columns being exported. An export is itself a PHI-adjacent
# action, so `routers/security_compliance.py` audits the export request.

EXPORT_COLUMNS = (
    "occurred_at",
    "action",
    "status",
    "who",
    "actor_type",
    "target_type",
    "target_id",
    "detail",
    "ip_address",
    "user_agent",
)


def to_csv(entries: list[models.AuditLogEntry]) -> str:
    """
    Renders entries as CSV.

    `lineterminator="\\r\\n"` and a UTF-8 BOM are added by the caller's
    response headers rather than here; what matters in this function is
    that every value goes through the csv module rather than being
    joined by hand, so a `detail` containing a comma or a newline cannot
    break the file apart.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for e in entries:
        writer.writerow([
            e.occurred_at.isoformat(sep=" ", timespec="seconds") if e.occurred_at else "",
            e.action or "",
            e.status or "",
            e.who or "",
            e.actor_type or "",
            e.target_type or "",
            e.target_id or "",
            e.detail or "",
            e.ip_address or "",
            e.user_agent or "",
        ])
    return buffer.getvalue()
