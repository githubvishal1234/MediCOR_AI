"""
Super Admin router — Usage Management (Phase 6).

Deliberately a separate file from routers/admin.py and
routers/admin_plans.py (rather than appended to either) so the existing,
already-shipped admin endpoints there are not touched by this phase.
Same isolation rules as the rest of the Super Admin surface:
  - Auth is via SuperAdmin, verified by deps.get_current_super_admin.
  - Read-only: this router only *reads* across orgs. It never writes to
    any table, so nothing here is logged to SuperAdminAuditLog (there is
    no action to log).
  - Never touches LiveKit, SIP, the voice agent, call lifecycle/active
    call tracking, the clinic dashboard, clinic billing, or the existing
    call APIs (routers/calls.py, routers/dashboard.py) — it only issues
    additional read queries against the same tables those already use.

Every number returned here is computed directly from existing tables:
  - Calls / voice minutes  -> CallLog (occurred_at, duration_seconds)
  - Appointments           -> Appointment (created_at, ai_generated)
  - Patients / Users       -> Patient, User (current counts)
  - Knowledge base         -> KBDocument, KBSource, FAQ (current counts)
  - EHR                    -> EHRIntegration (connected/total counts)
  - API usage              -> APIKey (active count)
  - Plan limit vs. usage   -> Subscription+Plan (Phase 5) when the org has
                               one, else Organization.plan /
                               voice_minutes_limit / voice_minutes_used
                               (the same fallback the clinic's own
                               Billing page and /admin/organizations
                               already use)

Nothing is invented: a metric the system does not actually persist
anywhere (e.g. a per-call EHR sync counter) is returned as `None` rather
than a fabricated number, so the frontend can render "Not available".
"""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_super_admin

router = APIRouter(prefix="/admin", tags=["admin-usage"])

_DEFAULT_WINDOW_DAYS = 30


def _resolve_range(start_date: date | None, end_date: date | None) -> tuple[date, date, datetime, datetime]:
    """
    Defaults to the last 30 days (inclusive) when no range is given, same
    "recent window" default used elsewhere in the admin UI. Returns both
    the plain dates (for the response) and the full-day datetime bounds
    (for filtering DateTime columns).
    """
    today = datetime.utcnow().date()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = end_date - timedelta(days=_DEFAULT_WINDOW_DAYS - 1)
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date")

    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    return start_date, end_date, start_dt, end_dt


@router.get("/usage", response_model=schemas.AdminUsageOut)
def get_usage(
    start_date: date | None = Query(None, description="Range start (inclusive). Defaults to 30 days before end_date."),
    end_date: date | None = Query(None, description="Range end (inclusive). Defaults to today."),
    org_id: str | None = Query(None, description="Limit to a single clinic."),
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    start_date, end_date, start_dt, end_dt = _resolve_range(start_date, end_date)

    org_q = db.query(models.Organization)
    if org_id:
        org_q = org_q.filter(models.Organization.id == org_id)
    orgs = org_q.order_by(models.Organization.name).all()
    if org_id and not orgs:
        raise HTTPException(status_code=404, detail="Organization not found")

    org_ids = [o.id for o in orgs]
    if not org_ids:
        # No clinics on the platform yet (or filter matched none) — real
        # empty state, not an error, and not fabricated zero-row data.
        empty_summary = schemas.AdminUsageSummaryOut(
            clinic_count=0,
            total_calls=0,
            total_voice_minutes_used_period=0.0,
            total_appointments=0,
            total_ai_appointments=0,
            total_patients=0,
            total_users=0,
            total_kb_documents=0,
            total_kb_sources=0,
            total_faqs=0,
            total_ehr_integrations_connected=0,
            total_api_keys_active=0,
        )
        return schemas.AdminUsageOut(
            start_date=start_date, end_date=end_date, org_id=org_id, summary=empty_summary, clinics=[]
        )

    # ---------- Calls: count + real duration sum, scoped to the date range ----------
    call_rows = (
        db.query(
            models.CallLog.org_id,
            func.count(models.CallLog.id),
            func.sum(models.CallLog.duration_seconds),
        )
        .filter(
            models.CallLog.org_id.in_(org_ids),
            models.CallLog.occurred_at >= start_dt,
            models.CallLog.occurred_at <= end_dt,
        )
        .group_by(models.CallLog.org_id)
        .all()
    )
    calls_by_org = {org: (count or 0, seconds or 0) for org, count, seconds in call_rows}

    # ---------- Appointments: total + AI-booked, scoped to the date range ----------
    appt_rows = (
        db.query(
            models.Appointment.org_id,
            func.count(models.Appointment.id),
            func.sum(case((models.Appointment.ai_generated == True, 1), else_=0)),  # noqa: E712
        )
        .filter(
            models.Appointment.org_id.in_(org_ids),
            models.Appointment.created_at >= start_dt,
            models.Appointment.created_at <= end_dt,
        )
        .group_by(models.Appointment.org_id)
        .all()
    )
    appts_by_org = {org: (total or 0, ai or 0) for org, total, ai in appt_rows}

    # ---------- Patients / Users: current roster size, not date-scoped ----------
    patient_counts = dict(
        db.query(models.Patient.org_id, func.count(models.Patient.id))
        .filter(models.Patient.org_id.in_(org_ids))
        .group_by(models.Patient.org_id)
        .all()
    )
    user_counts = dict(
        db.query(models.User.org_id, func.count(models.User.id))
        .filter(models.User.org_id.in_(org_ids))
        .group_by(models.User.org_id)
        .all()
    )

    # ---------- Knowledge base: current totals ----------
    kb_doc_counts = dict(
        db.query(models.KBDocument.org_id, func.count(models.KBDocument.id))
        .filter(models.KBDocument.org_id.in_(org_ids))
        .group_by(models.KBDocument.org_id)
        .all()
    )
    kb_source_counts = dict(
        db.query(models.KBSource.org_id, func.count(models.KBSource.id))
        .filter(models.KBSource.org_id.in_(org_ids))
        .group_by(models.KBSource.org_id)
        .all()
    )
    faq_counts = dict(
        db.query(models.FAQ.org_id, func.count(models.FAQ.id))
        .filter(models.FAQ.org_id.in_(org_ids))
        .group_by(models.FAQ.org_id)
        .all()
    )

    # ---------- EHR / API keys: current totals ----------
    ehr_rows = (
        db.query(
            models.EHRIntegration.org_id,
            func.count(models.EHRIntegration.id),
            func.sum(case((models.EHRIntegration.connected == True, 1), else_=0)),  # noqa: E712
        )
        .filter(models.EHRIntegration.org_id.in_(org_ids))
        .group_by(models.EHRIntegration.org_id)
        .all()
    )
    ehr_by_org = {org: (total or 0, connected or 0) for org, total, connected in ehr_rows}

    api_key_counts = dict(
        db.query(models.APIKey.org_id, func.count(models.APIKey.id))
        .filter(models.APIKey.org_id.in_(org_ids), models.APIKey.revoked == False)  # noqa: E712
        .group_by(models.APIKey.org_id)
        .all()
    )

    # ---------- Plan (Phase 5 Subscription+Plan, else legacy Organization fields) ----------
    sub_rows = (
        db.query(models.Subscription, models.Plan)
        .join(models.Plan, models.Plan.id == models.Subscription.plan_id)
        .filter(models.Subscription.org_id.in_(org_ids))
        .all()
    )
    plan_by_org = {sub.org_id: plan for sub, plan in sub_rows}

    clinics: list[schemas.AdminUsageClinicOut] = []
    for org in orgs:
        calls_count, call_seconds = calls_by_org.get(org.id, (0, 0))
        appt_total, appt_ai = appts_by_org.get(org.id, (0, 0))
        ehr_total, ehr_connected = ehr_by_org.get(org.id, (0, 0))
        plan = plan_by_org.get(org.id)

        voice_limit = plan.voice_minutes_limit if plan else org.voice_minutes_limit
        voice_used_alltime = org.voice_minutes_used or 0
        usage_pct = round((voice_used_alltime / voice_limit) * 100, 1) if voice_limit else 0.0

        clinics.append(
            schemas.AdminUsageClinicOut(
                org_id=org.id,
                org_name=org.name,
                plan_name=plan.name if plan else org.plan,
                has_subscription=plan is not None,
                total_calls=calls_count,
                voice_minutes_used_period=round((call_seconds or 0) / 60, 1),
                appointments_total=appt_total,
                appointments_ai_booked=appt_ai,
                patient_count=patient_counts.get(org.id, 0),
                user_count=user_counts.get(org.id, 0),
                kb_document_count=kb_doc_counts.get(org.id, 0),
                kb_source_count=kb_source_counts.get(org.id, 0),
                faq_count=faq_counts.get(org.id, 0),
                ehr_integrations_connected=ehr_connected,
                ehr_integrations_total=ehr_total,
                ehr_sync_count=None,
                api_keys_active=api_key_counts.get(org.id, 0),
                plan_voice_minutes_limit=voice_limit,
                plan_voice_minutes_used_alltime=voice_used_alltime,
                plan_voice_minutes_usage_pct=usage_pct,
                plan_user_limit=plan.user_limit if plan else None,
                plan_patient_limit=plan.patient_limit if plan else None,
            )
        )

    summary = schemas.AdminUsageSummaryOut(
        clinic_count=len(clinics),
        total_calls=sum(c.total_calls for c in clinics),
        total_voice_minutes_used_period=round(sum(c.voice_minutes_used_period for c in clinics), 1),
        total_appointments=sum(c.appointments_total for c in clinics),
        total_ai_appointments=sum(c.appointments_ai_booked for c in clinics),
        total_patients=sum(c.patient_count for c in clinics),
        total_users=sum(c.user_count for c in clinics),
        total_kb_documents=sum(c.kb_document_count for c in clinics),
        total_kb_sources=sum(c.kb_source_count for c in clinics),
        total_faqs=sum(c.faq_count for c in clinics),
        total_ehr_integrations_connected=sum(c.ehr_integrations_connected for c in clinics),
        total_api_keys_active=sum(c.api_keys_active for c in clinics),
    )

    return schemas.AdminUsageOut(
        start_date=start_date,
        end_date=end_date,
        org_id=org_id,
        summary=summary,
        clinics=clinics,
    )
