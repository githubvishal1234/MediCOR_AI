"""
Voice-agent tenant resolution — Phase 2.

The one endpoint that turns a single voice-agent deployment into a
multi-tenant one. The agent worker asks "which clinic owns the number
that was just dialled?" and gets back that clinic's identity plus a
short-lived, org-scoped token it can use for the rest of the call.

Everything else the agent does is unchanged: it still talks to the same
patient/appointment/call endpoints, still via `get_org_context`, and
still holds no business logic of its own.

Auth here is a PLATFORM credential (`X-Platform-Key`), deliberately not
an org one — the caller is infrastructure, and at the moment it asks the
question it does not yet know which tenant it belongs to. That makes this
the most powerful endpoint in the API: it is rate-limited, compares the
key in constant time, and refuses to run at all when the key is unset.

This router does not touch LiveKit, SIP, dialling, or audio. It answers a
lookup and mints a token.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .. import escalation_service, models, schemas, timezones
from ..config import settings
from ..database import get_db
from ..deps import OrgContext, get_org_context
from ..rate_limit import enforce_rate_limit
from ..security import create_agent_token, verify_platform_key

logger = logging.getLogger("agent-runtime")

router = APIRouter(prefix="/agent", tags=["agent-runtime"])

# These rules are rendered straight into the agent's system prompt, which
# is kept deliberately small — Groq counts prompt tokens against the same
# TPM budget as output — so cap both how many travel and how long each is.
MAX_ESCALATION_RULES = 8
MAX_RULE_CHARS = 160


def escalation_rules_for(db: Session, org_id: str) -> list[str]:
    """
    The clinic's `model` rules, one rendered line each, for the prompt.

    The docstring that used to be here said a deterministic pre-check
    "needs a real `keywords` column first, so it is deliberately not
    faked out of this prose". GAP-T9 added those columns, and
    `app/escalation_service.py` is that pre-check.

    **Only `model` rules are rendered now.** A rule the server evaluates
    must not also be in the prompt, for two reasons: it would be paid for
    on every turn of every call in Groq's TPM budget while contributing
    nothing the deterministic path has not already decided, and — worse —
    a model that half-matches it can escalate on a call the rule
    explicitly did not fire for, which puts the behaviour straight back
    to being unrepeatable.

    That is a narrowing of the prompt, not of escalation. A clinic's
    keyword list is *narrower* than a sentence a model can interpret
    across languages, so `model` remains the right choice for anything
    needing judgement, and `escalation_service` says at length why the
    two layers coexist.

    A rule with `enabled` NULL counts as enabled: the model defaults it to
    True, and for an escalation rule failing open (transferring to a human)
    is the safe direction to fail.
    """
    rows = [
        rule for rule in escalation_service.active_rules(db, org_id)
        if escalation_service.condition_of(rule) == "model"
    ][:MAX_ESCALATION_RULES]

    rendered = []
    for row in rows:
        line = f"{row.title} - {row.detail}" if row.detail else row.title
        line = " ".join(line.split())[:MAX_RULE_CHARS].strip()
        if line:
            rendered.append(line)
    return rendered


def normalize_e164(raw: str) -> str:
    """
    Reduce a dialled number to a comparable form.

    SIP hands the same number over in several shapes depending on trunk
    and carrier — "+919876543210", "919876543210", "tel:+91 98765 43210",
    "sip:+919876543210@host". Strip to digits and re-apply a leading "+"
    so a clinic can enter its number naturally and still be found.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    for prefix in ("tel:", "sip:", "sips:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    value = value.split("@", 1)[0]
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+{digits}" if digits else ""


def require_platform_key(
    request: Request,
    x_platform_key: str | None = Header(None, alias="X-Platform-Key"),
) -> None:
    """
    Guards tenant resolution.

    Rate-limited before the comparison so this cannot be used as a fast
    oracle to brute-force the key, and 404-free: a bad key is always 401,
    never a hint about whether the endpoint is enabled.
    """
    enforce_rate_limit(
        request,
        bucket="agent_resolve",
        max_attempts=settings.agent_resolve_rate_limit_attempts,
        window_seconds=settings.agent_resolve_rate_limit_window_seconds,
    )
    if not (settings.agent_platform_key or "").strip():
        # Multi-tenant resolution is off. Say so plainly — the agent falls
        # back to its single-tenant key and the call still happens.
        raise HTTPException(
            status_code=503,
            detail="Tenant resolution is not enabled on this deployment.",
        )
    if not verify_platform_key(x_platform_key):
        logger.warning("Rejected tenant-resolution attempt with an invalid platform key")
        raise HTTPException(status_code=401, detail="Invalid platform key")


@router.get("/escalation-rules", response_model=list[str])
def agent_escalation_rules(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    The calling clinic's own escalation rules.

    `/resolve-tenant` already returns these, so a multi-tenant call never
    needs this endpoint. It exists for the **single-tenant fallback**: with
    no `BACKEND_PLATFORM_KEY` the agent never resolves a tenant at all, and
    without this route the rules would silently never load — the feature
    would be inert in exactly the deployment mode most installs run in.

    Note the auth: this route is `get_org_context` (dual auth), unlike the
    platform-key resolution below. The voice agent reaches it with its
    ordinary org credential. Never put `require_admin` on it — that would
    break live calls.
    """
    return escalation_rules_for(db, ctx.org_id)


@router.post("/escalation-check", response_model=schemas.EscalationCheckOut)
def agent_escalation_check(
    payload: schemas.EscalationCheckIn,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Does anything the clinic configured say this call should reach a
    person? Evaluated here, not guessed in a prompt (GAP-T9).

    The agent calls this once per completed caller turn, and only when
    `resolve_tenant` said the clinic has rules worth asking about — a
    clinic with none pays nothing.

    `get_org_context`, like every other route the agent reaches. Never
    put `require_admin` on it: this one refusing mid-call would mean a
    caller who should have been transferred was not.

    It does not transfer anything. It returns a verdict and the agent
    performs the transfer through the machinery it already has, which
    keeps SIP entirely on the agent's side of the line — the same split
    every other action here follows.
    """
    org = db.query(models.Organization).filter(
        models.Organization.id == ctx.org_id
    ).first()

    has_rules = escalation_service.has_deterministic_rules(db, ctx.org_id)

    match = escalation_service.evaluate(
        db,
        ctx.org_id,
        utterance=payload.utterance,
        caller_is_known=payload.caller_is_known,
        timezone_name=timezones.org_timezone_name(org) if org else None,
    ) if has_rules else None

    if match is None:
        return schemas.EscalationCheckOut(escalate=False, has_rules=has_rules)

    logger.info(
        "Escalation rule %r (%s) fired for org=%s",
        match.title, match.condition_type, ctx.org_id,
    )
    return schemas.EscalationCheckOut(
        escalate=True,
        has_rules=True,
        rule_id=match.rule_id,
        rule_title=match.title,
        condition_type=match.condition_type,
        destination=match.destination,
        matched_on=match.matched_on,
    )


@router.post("/resolve-tenant", response_model=schemas.AgentTenantOut)
def resolve_tenant(
    payload: schemas.AgentResolveIn,
    db: Session = Depends(get_db),
    _: None = Depends(require_platform_key),
):
    """
    Maps a dialled number to its clinic and mints an org-scoped token.

    Returns 404 for an unknown or deactivated number rather than
    describing what went wrong — this endpoint must not become a way to
    enumerate which numbers a deployment serves.
    """
    normalized = normalize_e164(payload.dialed_number)
    if not normalized:
        raise HTTPException(status_code=404, detail="No clinic is configured for that number")

    row = (
        db.query(models.PhoneNumber)
        .filter(
            models.PhoneNumber.e164 == normalized,
            models.PhoneNumber.active == True,  # noqa: E712 - SQL Server BIT compatibility
        )
        .first()
    )
    if not row:
        logger.info(f"Tenant resolution miss for {normalized}")
        raise HTTPException(status_code=404, detail="No clinic is configured for that number")

    org = db.query(models.Organization).filter(models.Organization.id == row.org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="No clinic is configured for that number")
    if org.suspended:
        # Same posture as get_org_context: a suspended clinic's agent
        # stops working rather than quietly serving calls.
        raise HTTPException(status_code=403, detail="This organization has been suspended")

    agent_settings = (
        db.query(models.AgentSettings)
        .filter(models.AgentSettings.org_id == org.id)
        .first()
    )

    token = create_agent_token(org.id, settings.agent_token_expire_minutes)
    logger.info(f"Resolved {normalized} -> org={org.id} ({org.name})")

    return schemas.AgentTenantOut(
        org_id=org.id,
        org_name=org.name,
        timezone=timezones.org_timezone_name(org),
        phone_number=row.e164,
        access_token=token,
        expires_in_seconds=settings.agent_token_expire_minutes * 60,
        voice_profile=agent_settings.voice_profile if agent_settings else None,
        greeting_script=agent_settings.greeting_script if agent_settings else None,
        escalation_rules=escalation_rules_for(db, org.id),
        escalation_deterministic=escalation_service.has_deterministic_rules(
            db, org.id
        ),
    )
