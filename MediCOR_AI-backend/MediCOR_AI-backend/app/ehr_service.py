"""
EHR (Electronic Health Record) integration layer — Phase 7, hardened in
Phase 8.

Single place where the app talks to an org's configured external EHR
system. Routers should never reach into `models.EHRIntegration` or make
outbound EHR HTTP calls directly — they call the functions here and get
back a structured result, never an exception that would break the
caller's primary action (booking, patient create, etc.).

No real EHR vendor is hardcoded or invented. An org's "active"
integration is whatever row in `ehr_integrations` currently has
`connected = True` — set from the dashboard's EHR Integration page
(app/routers/ehr.py, pre-existing). If that row has no usable
`api_credentials_json` (i.e. nobody has actually pointed it at a real
EHR/FHIR gateway base URL + key), sync is reported as
`not_configured` rather than faking success. If a base URL *is*
configured but unreachable/rejects the request, sync is reported as
`unavailable`. If no integration is connected at all, the org is
`local_only` — the backend's own database remains the system of
record and every read (history, prescriptions, appointments) keeps
working normally.

Phase 8 adds: a bounded retry-with-backoff around the outbound HTTP
call for *transient* failures (connection errors, timeouts, 5xx) —
configurable via settings, not hardcoded — while a 4xx from the EHR is
treated as non-retryable (the request itself is wrong, retrying won't
help). Retries still can't turn an eventual failure into anything
other than the existing non-throwing `unavailable` status.
"""

import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import audit_service, models, usage_service
from .config import settings
from .database import SessionLocal
from .realtime import notify

logger = logging.getLogger("ehr_service")

# Phase 8: timeout + retry policy is env-configurable via settings rather
# than a hardcoded constant, so ops can tune it per deployment.
EHR_HTTP_TIMEOUT_SECONDS = settings.ehr_http_timeout_seconds
EHR_HTTP_MAX_RETRIES = settings.ehr_http_max_retries
EHR_HTTP_RETRY_BACKOFF_SECONDS = settings.ehr_http_retry_backoff_seconds


class EHRSyncStatus(str, Enum):
    SYNCED = "synced"                  # reached the external EHR and it accepted the record
    NOT_CONFIGURED = "not_configured"  # integration connected, but no usable credentials/base URL
    UNAVAILABLE = "unavailable"        # a connected+configured integration could not be reached / errored
    LOCAL_ONLY = "local_only"          # no EHR integration is connected for this org at all


class EHRSyncResult:
    def __init__(
        self,
        status: EHRSyncStatus,
        provider: Optional[str] = None,
        detail: Optional[str] = None,
        synced_at: Optional[datetime] = None,
    ):
        self.status = status
        self.provider = provider
        self.detail = detail
        self.synced_at = synced_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider,
            "detail": self.detail,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }


# ---------- Integration lookup ----------

def get_active_integration(db: Session, org_id: str) -> Optional[models.EHRIntegration]:
    """
    Returns the org's connected EHR integration row, if any. An org can
    have several EHRIntegration rows (Epic, Cerner, athenahealth, ...)
    seeded/configured from the dashboard; only the one(s) marked
    connected=True are treated as active for sync purposes.

    HV-4: an org whose plan excludes `ehr_access` has no active
    integration as far as this module is concerned, so every EHR path
    behind this one function — sync, history, status — reports
    `local_only` rather than raising. That is the same graceful state an
    unconfigured org already had, which matters because two of those
    paths are dual-auth and reachable by the voice agent mid-call. It is
    also why the gate lives here rather than in the router: one choke
    point, and no way to add an EHR endpoint that forgets it.
    """
    if not usage_service.has_feature(db, org_id, "ehr_access"):
        return None
    return (
        db.query(models.EHRIntegration)
        .filter(models.EHRIntegration.org_id == org_id, models.EHRIntegration.connected == True)  # noqa: E712 - SQL Server BIT compatibility
        .order_by(models.EHRIntegration.id)
        .first()
    )


def _credentials(integration: models.EHRIntegration) -> dict[str, Any]:
    if not integration.api_credentials_json:
        return {}
    try:
        parsed = json.loads(integration.api_credentials_json)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _actor_label(actor: str) -> str:
    return "Voice Agent" if actor == "service" else "Dashboard Staff"


def _log_audit(
    db: Session,
    org_id: str,
    detail: str,
    who: str,
    status: str,
    patient_id: Optional[str] = None,
) -> None:
    """
    HV-1: delegates to `audit_service` rather than building the row here.

    This function predates the audit service and was the only writer
    besides `seed.py`. Two things changed when it moved across: the
    action became the closed-vocabulary `ehr.sync` with the specifics in
    `detail` (so the Security page's action filter has one EHR category
    rather than a sentence per outcome), and the actor is now typed
    rather than being a bare display string.

    It still commits, unlike the default — sync runs from a background
    task on its own session, so there is no caller transaction to join.
    """
    audit_service.record(
        db,
        org_id,
        "ehr.sync",
        who=who,
        actor_type=(
            audit_service.ACTOR_SERVICE if who == "Voice Agent" else audit_service.ACTOR_USER
        ),
        target_type="patient" if patient_id else None,
        target_id=patient_id,
        detail=detail,
        status=status,
        commit=True,
    )


# ---------- Outbound sync (generic, credential-driven — no vendor assumed) ----------

async def _push_to_ehr(
    integration: models.EHRIntegration, resource_type: str, payload: dict[str, Any]
) -> EHRSyncResult:
    """
    Generic outbound sync call. Only actually reaches out over the network
    if the integration has a real `base_url` configured in
    `api_credentials_json` — this module does not assume any specific
    vendor's API shape, only a conventional
    `POST {base_url}/{resource_type}` with an optional bearer token, which
    is what most EHR middleware / FHIR gateways expect for a basic write.
    If no base_url is set, the integration is treated as display-only
    (connected in the dashboard, but not yet wired to a real endpoint).

    Transient failures (timeouts, connection errors, 5xx responses) are
    retried up to EHR_HTTP_MAX_RETRIES times with exponential backoff.
    A 4xx response is treated as non-retryable (the request itself is
    invalid/rejected) and reported as `unavailable` immediately.
    """
    creds = _credentials(integration)
    base_url = creds.get("base_url")
    if not base_url:
        return EHRSyncResult(
            EHRSyncStatus.NOT_CONFIGURED,
            provider=integration.name,
            detail=(
                f"{integration.name} is marked connected but has no API base URL configured. "
                "Add credentials under EHR Integration settings to enable live sync."
            ),
        )

    api_key = creds.get("api_key") or creds.get("token")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/") + f"/{resource_type}"

    last_error_detail = f"Could not reach {integration.name} — EHR is currently unavailable"
    attempts = EHR_HTTP_MAX_RETRIES + 1

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=EHR_HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            return EHRSyncResult(
                EHRSyncStatus.SYNCED, provider=integration.name, detail="Synced", synced_at=datetime.utcnow()
            )
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code < 500:
                # Client-side error (auth, bad payload, not found, etc.) —
                # retrying the exact same request won't change the outcome.
                logger.warning(f"EHR sync rejected by {integration.name}: HTTP {status_code}")
                return EHRSyncResult(
                    EHRSyncStatus.UNAVAILABLE,
                    provider=integration.name,
                    detail=f"{integration.name} rejected the sync (HTTP {status_code})",
                )
            last_error_detail = f"{integration.name} returned a server error (HTTP {status_code})"
            logger.warning(
                f"EHR sync attempt {attempt}/{attempts} to {integration.name} failed: HTTP {status_code}"
            )
        except httpx.RequestError as e:
            last_error_detail = f"Could not reach {integration.name} — EHR is currently unavailable"
            logger.warning(f"EHR sync attempt {attempt}/{attempts} could not reach {integration.name}: {e}")

        if attempt < attempts:
            await asyncio.sleep(EHR_HTTP_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    return EHRSyncResult(EHRSyncStatus.UNAVAILABLE, provider=integration.name, detail=last_error_detail)


async def sync_patient(db: Session, org_id: str, patient: models.Patient, actor: str = "service") -> EHRSyncResult:
    """Pushes current patient demographics to the org's active EHR, if any."""
    integration = get_active_integration(db, org_id)
    if not integration:
        _log_audit(
            db, org_id, "Patient sync skipped — no EHR integration connected",
            _actor_label(actor), "Logged", patient_id=patient.id,
        )
        return EHRSyncResult(EHRSyncStatus.LOCAL_ONLY, detail="No EHR integration is connected for this organization.")

    payload = {
        "mrn": patient.mrn,
        "name": patient.name,
        "dob": patient.dob,
        "phone": patient.phone,
        "status": patient.status,
    }
    result = await _push_to_ehr(integration, "patients", payload)
    if result.status == EHRSyncStatus.SYNCED:
        try:
            integration.meta1_label = "Last Sync"
            integration.meta1_value = "just now"
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist EHR integration sync metadata")
    _log_audit(
        db, org_id, f"Patient synced to {integration.name}: {result.status.value}",
        _actor_label(actor),
        "Success" if result.status == EHRSyncStatus.SYNCED else "Logged",
        patient_id=patient.id,
    )
    return result


async def sync_appointment(
    db: Session, org_id: str, appointment: models.Appointment, actor: str = "service"
) -> EHRSyncResult:
    """Pushes an appointment/visit's current state to the org's active EHR, if any."""
    integration = get_active_integration(db, org_id)
    if not integration:
        _log_audit(
            db, org_id, "Appointment sync skipped — no EHR integration connected",
            _actor_label(actor), "Logged",
        )
        return EHRSyncResult(EHRSyncStatus.LOCAL_ONLY, detail="No EHR integration is connected for this organization.")

    payload = {
        "patient_name": appointment.patient_name,
        "doctor_id": appointment.doctor_id,
        "start_at": appointment.start_at.isoformat() if appointment.start_at else None,
        "end_at": appointment.end_at.isoformat() if appointment.end_at else None,
        "status": appointment.status,
        "reason": appointment.reason,
    }
    result = await _push_to_ehr(integration, "appointments", payload)
    _log_audit(
        db, org_id,
        f"Appointment synced to {integration.name}: {result.status.value}",
        _actor_label(actor),
        "Success" if result.status == EHRSyncStatus.SYNCED else "Logged",
    )
    return result


# ---------- Background (fire-and-forget) sync ----------
# Used by patients.py / appointments.py so booking/registration never
# blocks on — or fails because of — an external EHR call. Each task opens
# its own DB session since the request-scoped one is closed by the time
# a background task actually runs.

def _run_patient_sync_task(patient_id: str, org_id: str, actor: str) -> None:
    db = SessionLocal()
    try:
        patient = (
            db.query(models.Patient)
            .filter(models.Patient.id == patient_id, models.Patient.org_id == org_id)
            .first()
        )
        if not patient:
            return
        result = asyncio.run(sync_patient(db, org_id, patient, actor=actor))
        if result.status == EHRSyncStatus.UNAVAILABLE:
            notify(
                org_id,
                level="warning",
                title="EHR sync failed",
                message=f"Could not sync {patient.name} with {result.provider or 'the EHR'} — will retry on next change.",
                data={"patient_id": patient.id},
            )
    except Exception:
        logger.exception("Background patient EHR sync failed")
    finally:
        db.close()


def schedule_patient_sync(background_tasks: BackgroundTasks, patient_id: str, org_id: str, actor: str) -> None:
    background_tasks.add_task(_run_patient_sync_task, patient_id, org_id, actor)


def _run_appointment_sync_task(appointment_id: str, org_id: str, actor: str) -> None:
    db = SessionLocal()
    try:
        appt = (
            db.query(models.Appointment)
            .filter(models.Appointment.id == appointment_id, models.Appointment.org_id == org_id)
            .first()
        )
        if not appt:
            return
        result = asyncio.run(sync_appointment(db, org_id, appt, actor=actor))
        if result.status == EHRSyncStatus.UNAVAILABLE:
            notify(
                org_id,
                level="warning",
                title="EHR sync failed",
                message=f"Could not sync appointment for {appt.patient_name or 'patient'} with {result.provider or 'the EHR'}.",
                data={"appointment_id": appt.id},
            )
    except Exception:
        logger.exception("Background appointment EHR sync failed")
    finally:
        db.close()


def schedule_appointment_sync(background_tasks: BackgroundTasks, appointment_id: str, org_id: str, actor: str) -> None:
    background_tasks.add_task(_run_appointment_sync_task, appointment_id, org_id, actor)


# ---------- Status & history (reads — always available, EHR or not) ----------

def get_status(db: Session, org_id: str) -> dict[str, Any]:
    """Whether an EHR is connected for this org, and whether it's actually wired to a live endpoint."""
    if not usage_service.has_feature(db, org_id, "ehr_access"):
        # Distinguished from "nobody has connected one" so the dashboard
        # can offer an upgrade rather than a Connect button that 403s.
        return {
            "connected": False,
            "provider": None,
            "configured": False,
            "plan_restricted": True,
            "detail": usage_service.feature_error(db, org_id, "ehr_access"),
        }
    integration = get_active_integration(db, org_id)
    if not integration:
        return {
            "connected": False,
            "provider": None,
            "configured": False,
            "detail": "No EHR integration is connected. Records are managed locally only.",
        }
    creds = _credentials(integration)
    return {
        "connected": True,
        "provider": integration.name,
        "configured": bool(creds.get("base_url")),
        "detail": integration.detail,
    }


def get_patient_history(db: Session, patient: models.Patient) -> dict[str, Any]:
    """
    Aggregates the patient's clinical history from the system of record
    (local DB — prescriptions, visit/interaction notes, appointments).
    This always works regardless of external EHR connectivity; when an
    EHR is connected, `ehr_source` tells the caller whether this data is
    also mirrored externally, so the dashboard/voice agent can be
    transparent about it rather than claiming an EHR sync that may not
    exist.
    """
    integration = get_active_integration(db, patient.org_id)
    return {
        "patient_id": patient.id,
        "mrn": patient.mrn,
        "name": patient.name,
        "prescriptions": [
            {"id": rx.id, "name": rx.name, "detail": rx.detail, "status": rx.status, "note": rx.note}
            for rx in patient.prescriptions
        ],
        "visits": [
            {
                "id": i.id,
                "title": i.title,
                "date_label": i.date_label,
                "occurred_at": i.occurred_at.isoformat() if i.occurred_at else None,
                "detail": i.detail,
            }
            for i in sorted(patient.interactions, key=lambda i: i.occurred_at or datetime.min, reverse=True)
        ],
        "appointments": [
            {
                "id": a.id,
                "title": a.title,
                "day_label": a.day_label,
                "time_label": a.time_label,
                "status": a.status,
                "start_at": a.start_at.isoformat() if a.start_at else None,
            }
            for a in patient.appointments
        ],
        "ehr_source": {
            "connected": integration is not None,
            "provider": integration.name if integration else None,
        },
    }