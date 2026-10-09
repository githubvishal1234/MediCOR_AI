from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit_service, ehr_service, models, schemas, usage_service, webhook_service
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context, require_admin
from ..security import generate_api_key, hash_api_key

router = APIRouter(prefix="/ehr", tags=["ehr"])


@router.get("/integrations", response_model=list[schemas.EHRIntegrationOut])
def list_integrations(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return db.query(models.EHRIntegration).filter(models.EHRIntegration.org_id == user.org_id).all()


@router.patch("/integrations/{integration_id}", response_model=schemas.EHRIntegrationOut)
def update_integration(
    integration_id: str,
    payload: schemas.EHRIntegrationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    # HV-4: connecting an EHR is the act of using the gated feature, so
    # this is where the plan is checked. The read above is left open —
    # showing a clinic what it could connect is not the feature.
    plan_error = usage_service.feature_error(db, user.org_id, "ehr_access")
    if plan_error:
        raise HTTPException(status_code=403, detail=plan_error)
    integration = (
        db.query(models.EHRIntegration)
        .filter(models.EHRIntegration.id == integration_id, models.EHRIntegration.org_id == user.org_id)
        .first()
    )
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    if payload.connected is not None:
        integration.connected = payload.connected
        integration.status = "Connected — Real-time" if payload.connected else "Not Connected"
    if payload.status is not None:
        integration.status = payload.status
    audit_service.record(
        db, user.org_id, "ehr.integration_changed", user=user,
        target_type="ehr_integration", target_id=integration.id,
        detail=f"{integration.name}: {integration.status}", request=request,
    )
    db.commit()
    db.refresh(integration)
    return integration


@router.get("/api-keys", response_model=list[schemas.APIKeyOut])
def list_api_keys(db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    return (
        db.query(models.APIKey)
        .filter(models.APIKey.org_id == user.org_id, models.APIKey.revoked == False)  # noqa: E712 - SQL Server BIT compatibility
        .all()
    )


@router.post("/api-keys", response_model=schemas.APIKeyCreatedOut)
def create_api_key(
    payload: schemas.APIKeyCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    plaintext, prefix = generate_api_key()
    key_row = models.APIKey(
        org_id=user.org_id,
        label=payload.label,
        environment=payload.environment,
        key_prefix=prefix,
        hashed_key=hash_api_key(plaintext),
    )
    db.add(key_row)
    db.flush()
    # The prefix, never the key. A service credential that grants
    # dual-auth access to every patient endpoint must not be recoverable
    # from a log that more people can read than can mint one.
    audit_service.record(
        db, user.org_id, "api_key.created", user=user,
        target_type="api_key", target_id=key_row.id,
        detail=f"{key_row.label} ({key_row.environment}) prefix {key_row.key_prefix}",
        request=request,
    )
    db.commit()
    db.refresh(key_row)
    out = schemas.APIKeyCreatedOut(
        id=key_row.id,
        label=key_row.label,
        key_prefix=key_row.key_prefix,
        environment=key_row.environment,
        created_at=key_row.created_at,
        revoked=key_row.revoked,
        plaintext_key=plaintext,
    )
    return out


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    key_row = (
        db.query(models.APIKey)
        .filter(models.APIKey.id == key_id, models.APIKey.org_id == user.org_id)
        .first()
    )
    if key_row:
        key_row.revoked = True
        audit_service.record(
            db, user.org_id, "api_key.revoked", user=user,
            target_type="api_key", target_id=key_row.id,
            detail=f"{key_row.label} prefix {key_row.key_prefix}", request=request,
        )
        db.commit()
    return None


def _webhook_out(db: Session, hook: models.Webhook) -> schemas.WebhookOut:
    """
    Render a webhook, minting its signing secret on first read.

    Lazily rather than as a migration backfill: every existing row
    predates signing, and a clinic that never uses webhooks has no reason
    to carry a secret in the database.
    """
    if not hook.secret:
        hook.secret = webhook_service.generate_secret()
        db.commit()
        db.refresh(hook)
    return schemas.WebhookOut(
        endpoint_url=hook.endpoint_url,
        events=[e for e in (hook.events_csv or "").split(",") if e],
        # Served, not hardcoded in the UI — hardcoding is how the config
        # screen came to offer three events that did not exist.
        available_events=list(schemas.WEBHOOK_EVENTS),
        secret=hook.secret,
        last_delivery_at=hook.last_delivery_at,
        last_delivery_status=hook.last_delivery_status,
    )


@router.get("/webhook", response_model=schemas.WebhookOut)
def get_webhook(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    hook = db.query(models.Webhook).filter(models.Webhook.org_id == user.org_id).first()
    if not hook:
        hook = models.Webhook(org_id=user.org_id)
        db.add(hook)
        db.commit()
        db.refresh(hook)
    return _webhook_out(db, hook)


@router.put("/webhook", response_model=schemas.WebhookOut)
def update_webhook(
    payload: schemas.WebhookUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    hook = db.query(models.Webhook).filter(models.Webhook.org_id == user.org_id).first()
    if not hook:
        hook = models.Webhook(org_id=user.org_id)
        db.add(hook)
    if payload.endpoint_url is not None:
        # Empty clears it; anything else must be a URL we would actually
        # call (see webhook_service.blocked_target -- SSRF guard).
        reason = payload.endpoint_url and webhook_service.blocked_target(payload.endpoint_url)
        if reason:
            raise HTTPException(status_code=422, detail=f"The endpoint URL {reason}.")
        hook.endpoint_url = payload.endpoint_url
    if payload.events is not None:
        hook.events_csv = ",".join(payload.events)
    # Repointing a webhook redirects clinic event data to a new host, so
    # the endpoint URL is the material fact and does belong in the entry.
    audit_service.record(
        db, user.org_id, "ehr.integration_changed", user=user,
        target_type="webhook", target_id=hook.id,
        detail=f"Webhook endpoint set to {hook.endpoint_url or '(none)'}",
        request=request,
    )
    db.commit()
    db.refresh(hook)
    return _webhook_out(db, hook)

# ---------- EHR sync & clinical history (Phase 7) ----------
# These endpoints use OrgContext (JWT *or* service X-API-Key) rather than
# get_current_user, so both dashboard staff and the voice agent can read
# EHR status/history and trigger a sync — same pattern already used by
# appointments.py for booking/availability.

@router.get("/status", response_model=schemas.EHRStatusOut)
def ehr_status(db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)):
    """Whether an EHR is connected for this org, and whether it's actually wired to a live endpoint."""
    return ehr_service.get_status(db, ctx.org_id)


@router.post("/patients/{patient_id}/sync", response_model=schemas.EHRSyncResultOut)
async def sync_patient_ehr(
    patient_id: str, db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)
):
    """
    Manually (re)syncs a patient's demographics with the org's connected
    EHR. Never raises for a normal "EHR not connected/unreachable" state —
    it reports that in the response `status` field instead, so callers
    (including the voice agent) can handle it gracefully.
    """
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id, models.Patient.org_id == ctx.org_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    result = await ehr_service.sync_patient(db, ctx.org_id, patient, actor=ctx.actor)
    return result.to_dict()


@router.get("/patients/{patient_id}/history", response_model=schemas.PatientHistoryOut)
def patient_ehr_history(
    patient_id: str,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Returns the patient's clinical history (prescriptions, past visits,
    appointments) from the system of record, plus whether it's mirrored
    to an external EHR. Always available — falls back to local-only data
    when no EHR is connected, rather than failing.

    HV-1: logged. This returns prescriptions and visit notes in one
    response, which is a deeper read than the patient record itself, so
    it gets its own action rather than being folded into
    `patient.viewed`.
    """
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id, models.Patient.org_id == ctx.org_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    history = ehr_service.get_patient_history(db, patient)
    audit_service.record(
        db, ctx.org_id, "patient.history_viewed", ctx=ctx,
        target_type="patient", target_id=patient.id,
        detail=f"MRN {patient.mrn}",
        status=audit_service.STATUS_LOGGED,
        request=request, commit=True,
    )
    return history


@router.post("/appointments/{appointment_id}/sync", response_model=schemas.EHRSyncResultOut)
async def sync_appointment_ehr(
    appointment_id: str, db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)
):
    """Manually (re)syncs a single appointment/visit with the org's connected EHR."""
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == appointment_id, models.Appointment.org_id == ctx.org_id)
        .first()
    )
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    result = await ehr_service.sync_appointment(db, ctx.org_id, appt, actor=ctx.actor)
    return result.to_dict()