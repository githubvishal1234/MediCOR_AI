"""
Security & compliance — the clinic's own view.

The audit-log endpoints here read `audit_log`, which as of HV-1 is
written on every request that touches PHI (see `app/audit_service.py`).
Before that it held five rows planted by `seed.py`, so this page rendered
a compliance posture with nothing behind it.

`GET /audit-log` returns a **page**, not a flat list. The old signature
took `limit=50` and returned an array, which meant a clinic with any real
activity silently saw only its most recent fifty events and had no way to
reach the rest — an audit log you cannot page through is a recent-events
widget.
"""

import logging
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import audit_service, models, schemas
from ..database import get_db
from ..deps import get_current_user, require_admin

logger = logging.getLogger("security")

router = APIRouter(prefix="/security", tags=["security"])

# Upper bound on one CSV export. An export is a bulk PHI-adjacent
# extraction, so it is capped and admin-only rather than being an
# unbounded "download everything" any staff account can trigger.
_MAX_EXPORT_ROWS = 5000

ROLE_DETAIL = {
    models.UserRole.admin: "Full access to settings, billing, and system configuration.",
    models.UserRole.medical_staff: "Access to patient transcripts, voice logs, and EHR sync.",
    models.UserRole.ai_agent: "Restricted read/write access to designated endpoints.",
}
ROLE_LABEL = {
    models.UserRole.admin: "Administrators",
    models.UserRole.medical_staff: "Medical Staff",
    models.UserRole.ai_agent: "AI Agents",
}


@router.get("/compliance", response_model=schemas.ComplianceOut)
def compliance(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    org = db.query(models.Organization).filter(models.Organization.id == user.org_id).first()
    entries = (
        db.query(func.count(models.AuditLogEntry.id))
        .filter(models.AuditLogEntry.org_id == user.org_id)
        .scalar()
        or 0
    )
    return schemas.ComplianceOut(
        # Derived, not asserted. The schema used to default this to True
        # and nothing ever set it, so the API told every clinic it was
        # verified. A recorded audit date is the only evidence there is.
        hipaa_verified=bool((org.hipaa_last_audit or "").strip()),
        last_security_audit=org.hipaa_last_audit or "Not yet audited",
        data_retention_years=org.data_retention_years,
        baa_status=org.baa_status,
        audit_log_entries=entries,
    )


@router.get("/roles", response_model=list[schemas.RoleSummaryOut])
def roles(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    counts = dict(
        db.query(models.User.role, func.count(models.User.id))
        .filter(models.User.org_id == user.org_id)
        .group_by(models.User.role)
        .all()
    )
    return [
        schemas.RoleSummaryOut(
            name=ROLE_LABEL[role], count=counts.get(role, 0), detail=ROLE_DETAIL[role]
        )
        for role in models.UserRole
    ]


def _filtered(
    db: Session,
    org_id: str,
    q: str | None,
    action: str | None,
    status_filter: str | None,
    start_date: date | None,
    end_date: date | None,
):
    """
    The shared query behind the paged view and the CSV export, so the two
    can never disagree about what a given filter means.

    `occurred_at` is UTC (see app/timezones.py). The date bounds are
    applied as UTC days rather than clinic-local ones — deliberately, and
    it is the one place on this page where that shows: an audit trail is
    a forensic record, and a range that shifts with a clinic's timezone
    setting is harder to reconcile against other systems' logs than one
    that does not.
    """
    query = db.query(models.AuditLogEntry).filter(models.AuditLogEntry.org_id == org_id)

    if action:
        query = query.filter(models.AuditLogEntry.action == action)
    if status_filter:
        query = query.filter(models.AuditLogEntry.status == status_filter)
    if start_date:
        query = query.filter(
            models.AuditLogEntry.occurred_at >= datetime.combine(start_date, time.min)
        )
    if end_date:
        query = query.filter(
            models.AuditLogEntry.occurred_at <= datetime.combine(end_date, time.max)
        )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                models.AuditLogEntry.action.ilike(like),
                models.AuditLogEntry.who.ilike(like),
                models.AuditLogEntry.detail.ilike(like),
                models.AuditLogEntry.ip_address.ilike(like),
                models.AuditLogEntry.target_id.ilike(like),
            )
        )
    return query


@router.get("/audit-log", response_model=schemas.AuditLogPageOut)
def audit_log(
    q: str | None = Query(None, description="Free-text search over action, who, detail, IP, target."),
    action: str | None = Query(None, description="Exact action, e.g. 'patient.viewed'."),
    status_filter: str | None = Query(None, alias="status", description="Success | Blocked | Logged."),
    start_date: date | None = Query(None, description="Range start (inclusive, UTC)."),
    end_date: date | None = Query(None, description="Range end (inclusive, UTC)."),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    This clinic's audit trail, newest first.

    Readable by all staff, not just admins: a receptionist should be able
    to see that their own account was used, and hiding the log from the
    people it records is how an access log becomes something only the
    person with the most to hide can read. The **export** is admin-only,
    because bulk extraction is a different act from looking.
    """
    query = _filtered(db, user.org_id, q, action, status_filter, start_date, end_date)

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    rows = (
        query.order_by(models.AuditLogEntry.occurred_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return schemas.AuditLogPageOut(
        items=rows, total=total, page=page, page_size=page_size, total_pages=total_pages
    )


@router.get("/audit-log/actions", response_model=list[str])
def audit_log_actions(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    """
    Distinct actions this clinic has actually recorded, for the filter.

    Deliberately not `audit_service.ACTIONS`: offering a clinic a filter
    for every action the product can emit means most choices return
    nothing, which reads as a broken filter rather than a quiet clinic.
    """
    rows = (
        db.query(models.AuditLogEntry.action)
        .filter(models.AuditLogEntry.org_id == user.org_id)
        .distinct()
        .order_by(models.AuditLogEntry.action)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/audit-log/export")
def export_audit_log(
    request: Request,
    q: str | None = Query(None),
    action: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """
    The current filter as CSV, for a BAA's "produce your access log" ask.

    Admin-only, capped at `_MAX_EXPORT_ROWS`, and — the part that matters
    — **the export is itself audited**. A compliance log where pulling a
    copy of the log leaves no trace has a hole exactly where an
    investigator would look.
    """
    rows = (
        _filtered(db, user.org_id, q, action, status_filter, start_date, end_date)
        .order_by(models.AuditLogEntry.occurred_at.desc())
        .limit(_MAX_EXPORT_ROWS)
        .all()
    )
    body = audit_service.to_csv(rows)

    audit_service.record(
        db,
        user.org_id,
        "audit_log.exported",
        user=user,
        target_type="audit_log",
        detail=f"{len(rows)} row{'' if len(rows) == 1 else 's'} exported as CSV",
        status=audit_service.STATUS_LOGGED,
        request=request,
        commit=True,
    )

    filename = f"audit-log-{datetime.utcnow().date().isoformat()}.csv"
    return Response(
        # A UTF-8 BOM so Excel on Windows opens the file in UTF-8 rather
        # than the system codepage — without it a patient name with an
        # accent arrives mangled, on the one machine most likely to open
        # this file.
        content="﻿" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
