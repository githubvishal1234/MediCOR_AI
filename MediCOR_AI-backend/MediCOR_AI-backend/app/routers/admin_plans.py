"""
Super Admin router — Plans & Subscriptions (Phase 5).

Deliberately a separate file from routers/admin.py (rather than appended
to it) so the existing, already-shipped admin endpoints there are not
touched by this phase. Same isolation rules as admin.py:
  - Auth is via SuperAdmin, verified by deps.get_current_super_admin.
  - Every write is logged to SuperAdminAuditLog.
  - Never touches LiveKit, SIP, the voice agent, or org-scoped business
    logic — only the new `plans` / `subscriptions` tables and, to keep
    the clinic-facing Billing page correct, the same `Organization.plan`
    / `voice_minutes_limit` fields the existing /admin/organizations
    PATCH endpoint already writes to.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_super_admin
from .admin import _client_ip, _user_agent

logger = logging.getLogger("admin_plans")

router = APIRouter(prefix="/admin", tags=["admin-plans"])


def _log(
    db: Session,
    admin: models.SuperAdmin,
    action: str,
    target_org_id: str | None = None,
    detail: str | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        models.SuperAdminAuditLog(
            super_admin_id=admin.id,
            action=action,
            target_org_id=target_org_id,
            detail=detail,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    )


def _features_to_csv(features: list[str]) -> str:
    # Dedup while preserving order; drop blanks from stray commas/whitespace.
    seen = []
    for f in features:
        f = f.strip()
        if f and f not in seen:
            seen.append(f)
    return ",".join(seen)


def _csv_to_features(csv: str | None) -> list[str]:
    if not csv:
        return []
    return [f for f in (part.strip() for part in csv.split(",")) if f]


def _plan_out(plan: models.Plan, subscribed_counts: dict[str, int]) -> schemas.AdminPlanOut:
    return schemas.AdminPlanOut(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        monthly_price_cents=plan.monthly_price_cents,
        voice_minutes_limit=plan.voice_minutes_limit,
        user_limit=plan.user_limit,
        patient_limit=plan.patient_limit,
        ehr_access=plan.ehr_access,
        knowledge_base_access=plan.knowledge_base_access,
        features=_csv_to_features(plan.features_csv),
        is_active=plan.is_active,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        subscribed_org_count=subscribed_counts.get(plan.id, 0),
    )


def _get_plan_or_404(db: Session, plan_id: str) -> models.Plan:
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


def _subscribed_counts(db: Session) -> dict[str, int]:
    rows = (
        db.query(models.Subscription.plan_id, func.count(models.Subscription.id))
        .filter(models.Subscription.status == "active")
        .group_by(models.Subscription.plan_id)
        .all()
    )
    return {plan_id: count for plan_id, count in rows}


# ---------- Plans ----------
@router.get("/plans", response_model=list[schemas.AdminPlanOut])
def list_plans(
    db: Session = Depends(get_db), admin: models.SuperAdmin = Depends(get_current_super_admin)
):
    plans = db.query(models.Plan).order_by(models.Plan.created_at.asc()).all()
    counts = _subscribed_counts(db)
    return [_plan_out(p, counts) for p in plans]


@router.get("/plans/{plan_id}", response_model=schemas.AdminPlanOut)
def get_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    plan = _get_plan_or_404(db, plan_id)
    counts = _subscribed_counts(db)
    return _plan_out(plan, counts)


@router.post("/plans", response_model=schemas.AdminPlanOut)
def create_plan(
    payload: schemas.PlanIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    existing = db.query(models.Plan).filter(models.Plan.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="A plan with this name already exists")

    plan = models.Plan(
        name=payload.name,
        description=payload.description,
        monthly_price_cents=payload.monthly_price_cents,
        voice_minutes_limit=payload.voice_minutes_limit,
        user_limit=payload.user_limit,
        patient_limit=payload.patient_limit,
        ehr_access=payload.ehr_access,
        knowledge_base_access=payload.knowledge_base_access,
        features_csv=_features_to_csv(payload.features),
        is_active=payload.is_active,
    )
    db.add(plan)
    db.flush()
    _log(db, admin, "plan.create", detail=f"name={plan.name!r}", request=request)
    db.commit()
    db.refresh(plan)
    return _plan_out(plan, _subscribed_counts(db))


@router.patch("/plans/{plan_id}", response_model=schemas.AdminPlanOut)
def update_plan(
    plan_id: str,
    payload: schemas.PlanUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    plan = _get_plan_or_404(db, plan_id)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] != plan.name:
        clash = db.query(models.Plan).filter(models.Plan.name == data["name"], models.Plan.id != plan_id).first()
        if clash:
            raise HTTPException(status_code=409, detail="A plan with this name already exists")

    changed = []
    if "features" in data:
        features = data.pop("features")
        new_csv = _features_to_csv(features) if features is not None else plan.features_csv
        if new_csv != plan.features_csv:
            changed.append("features")
        plan.features_csv = new_csv

    for k, v in data.items():
        if getattr(plan, k) != v:
            changed.append(k)
        setattr(plan, k, v)

    _log(db, admin, "plan.update", detail=f"plan={plan.name!r}, fields={changed}" if changed else None, request=request)
    db.commit()
    db.refresh(plan)
    return _plan_out(plan, _subscribed_counts(db))


@router.post("/plans/{plan_id}/activate", response_model=schemas.AdminPlanOut)
def activate_plan(
    plan_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    plan = _get_plan_or_404(db, plan_id)
    plan.is_active = True
    _log(db, admin, "plan.activate", detail=f"plan={plan.name!r}", request=request)
    db.commit()
    db.refresh(plan)
    return _plan_out(plan, _subscribed_counts(db))


@router.post("/plans/{plan_id}/deactivate", response_model=schemas.AdminPlanOut)
def deactivate_plan(
    plan_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    plan = _get_plan_or_404(db, plan_id)
    plan.is_active = False
    _log(db, admin, "plan.deactivate", detail=f"plan={plan.name!r}", request=request)
    db.commit()
    db.refresh(plan)
    return _plan_out(plan, _subscribed_counts(db))


# ---------- Subscriptions ----------
def _subscription_out(sub: models.Subscription, org: models.Organization, plan: models.Plan) -> schemas.AdminSubscriptionOut:
    return schemas.AdminSubscriptionOut(
        id=sub.id,
        org_id=org.id,
        org_name=org.name,
        plan_id=plan.id,
        plan_name=plan.name,
        status=sub.status,
        started_at=sub.started_at,
        updated_at=sub.updated_at,
        voice_minutes_used=org.voice_minutes_used,
        voice_minutes_limit=org.voice_minutes_limit,
    )


@router.get("/subscriptions", response_model=list[schemas.AdminSubscriptionOut])
def list_subscriptions(
    db: Session = Depends(get_db), admin: models.SuperAdmin = Depends(get_current_super_admin)
):
    rows = (
        db.query(models.Subscription, models.Organization, models.Plan)
        .join(models.Organization, models.Organization.id == models.Subscription.org_id)
        .join(models.Plan, models.Plan.id == models.Subscription.plan_id)
        .order_by(models.Subscription.updated_at.desc())
        .all()
    )
    return [_subscription_out(sub, org, plan) for sub, org, plan in rows]


@router.get("/subscriptions/{org_id}", response_model=schemas.AdminSubscriptionOut | None)
def get_subscription(
    org_id: str,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    # Not having a Subscription row yet is a normal, expected state (e.g.
    # any organization created before the Phase 5 Subscriptions table
    # existed, like every pre-existing clinic) — not an error condition.
    # This previously raised a 404 here, which meant simply opening the
    # Organization detail page for such a clinic always logged a failed
    # request. Returning `null` on success lets the caller (the org
    # detail page) render its existing "no subscription yet — assign one
    # below" empty state without an HTTP error ever being involved.
    row = (
        db.query(models.Subscription, models.Organization, models.Plan)
        .join(models.Organization, models.Organization.id == models.Subscription.org_id)
        .join(models.Plan, models.Plan.id == models.Subscription.plan_id)
        .filter(models.Subscription.org_id == org_id)
        .first()
    )
    if not row:
        return None
    sub, org, plan = row
    return _subscription_out(sub, org, plan)


@router.post("/subscriptions", response_model=schemas.AdminSubscriptionOut)
def assign_subscription(
    payload: schemas.SubscriptionAssignIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    """
    Assigns a plan to a clinic — creates the org's Subscription row if it
    doesn't have one yet, otherwise changes its plan (this doubles as
    "change clinic subscription"). Also mirrors the plan's name and voice
    minute limit onto Organization.plan / Organization.voice_minutes_limit,
    the same two fields the existing /admin/organizations PATCH endpoint
    already writes to, so the clinic's own Billing page (routers/billing.py,
    left untouched) immediately reflects the new plan without any change
    to that route.
    """
    org = db.query(models.Organization).filter(models.Organization.id == payload.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    plan = db.query(models.Plan).filter(models.Plan.id == payload.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not plan.is_active:
        raise HTTPException(status_code=400, detail="Cannot assign an inactive plan")

    sub = db.query(models.Subscription).filter(models.Subscription.org_id == payload.org_id).first()
    action = "subscription.change_plan" if sub else "subscription.assign"
    if sub:
        sub.plan_id = plan.id
        sub.status = payload.status
    else:
        sub = models.Subscription(org_id=org.id, plan_id=plan.id, status=payload.status)
        db.add(sub)

    org.plan = plan.name
    org.voice_minutes_limit = plan.voice_minutes_limit

    _log(db, admin, action, target_org_id=org.id, detail=f"plan={plan.name!r}, status={payload.status!r}", request=request)
    db.commit()
    db.refresh(sub)
    return _subscription_out(sub, org, plan)


@router.patch("/subscriptions/{org_id}", response_model=schemas.AdminSubscriptionOut)
def update_subscription_status(
    org_id: str,
    payload: schemas.SubscriptionStatusUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.SuperAdmin = Depends(get_current_super_admin),
):
    sub = db.query(models.Subscription).filter(models.Subscription.org_id == org_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="This organization has no subscription yet")
    sub.status = payload.status
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    plan = db.query(models.Plan).filter(models.Plan.id == sub.plan_id).first()
    _log(db, admin, "subscription.status_change", target_org_id=org_id, detail=f"status={payload.status!r}", request=request)
    db.commit()
    db.refresh(sub)
    return _subscription_out(sub, org, plan)