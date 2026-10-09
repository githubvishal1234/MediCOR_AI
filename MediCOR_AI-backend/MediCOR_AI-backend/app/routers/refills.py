"""
Repeat-prescription requests taken by phone (F9).

THE CLINICAL LINE, AND WHERE IT IS DRAWN

The voice agent may **record that a patient asked** for a refill. It may
not approve one, say one is ready, or advise on dose or timing. Nothing in
this module lets it: `POST /refills` only ever creates a row with
`status="pending"`, and the transition to `approved` / `declined` is
`get_current_user` — staff only, unreachable with an agent token or the
service API key.

That is the whole safety design. It is enforced by the dependency on each
route rather than by prompt wording, because a prompt is a request and a
dependency is a rule.

WHY THE AGENT MAY CREATE AT ALL

Refill calls are high volume and low clinical risk *as requests*. Before
this the agent's only option was to transfer, so a receptionist took the
same message by hand. Recording it is strictly better than transcribing
it twice, provided the decision stays with a person — which it does.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit_service, models, notification_service, schemas, webhook_service
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context

logger = logging.getLogger("refills")

router = APIRouter(prefix="/refills", tags=["refills"])

# The queue is a to-do list; a handled request has no reason to sit at the
# top of it. Same reasoning as the HV-7 callback queue default.
_OPEN_STATUSES = ("pending",)

_MAX_LIMIT = 200


def _get_or_404(db: Session, refill_id: str, org_id: str) -> models.RefillRequest:
    row = (
        db.query(models.RefillRequest)
        .filter(
            models.RefillRequest.id == refill_id,
            models.RefillRequest.org_id == org_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Refill request not found")
    return row


def _match_prescription(
    db: Session, patient_id: str, medication: str
) -> Optional[models.Prescription]:
    """
    Link the request to something already on this patient's record, when
    the name plainly matches.

    Case-insensitive exact match only. A fuzzy match here would be a
    clinical guess presented as a fact — "you asked for X, we think you
    meant Y" is a decision for the person reviewing, and a null link says
    honestly that the caller named something we have no record of.
    """
    wanted = medication.strip().lower()
    if not wanted:
        return None
    return (
        db.query(models.Prescription)
        .filter(
            models.Prescription.patient_id == patient_id,
            func.lower(models.Prescription.name) == wanted,
        )
        .first()
    )


def _out(row: models.RefillRequest, patient_name: Optional[str]) -> schemas.RefillRequestOut:
    return schemas.RefillRequestOut(
        id=row.id,
        patient_id=row.patient_id,
        patient_name=patient_name,
        prescription_id=row.prescription_id,
        call_id=row.call_id,
        medication=row.medication,
        note=row.note,
        status=row.status,
        requested_at=row.requested_at,
        handled_at=row.handled_at,
    )


@router.post("", response_model=schemas.RefillRequestOut, status_code=201)
def create_refill_request(
    payload: schemas.RefillRequestCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Records that a patient asked for a repeat prescription.

    Dual auth, because the voice agent is the main caller — but it can
    only ever create a `pending` row. There is no field on this route that
    can approve anything.

    The patient must already be identified. An anonymous medication
    request is not actionable by staff and not safe to queue, so a
    caller the agent could not match is refused here rather than becoming
    a row nobody can resolve.
    """
    patient = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == payload.patient_id,
            # Org-scoped: the parent being in this org does not prove the
            # child is. That is the exact shape of the one cross-org IDOR
            # this codebase has had.
            models.Patient.org_id == ctx.org_id,
        )
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    call_id = None
    if payload.call_id:
        call = (
            db.query(models.CallLog)
            .filter(
                models.CallLog.id == payload.call_id,
                models.CallLog.org_id == ctx.org_id,
            )
            .first()
        )
        # A call id we cannot verify is dropped rather than stored: a
        # dangling reference on a medication record is worse than none.
        call_id = call.id if call else None

    matched = _match_prescription(db, patient.id, payload.medication)

    row = models.RefillRequest(
        org_id=ctx.org_id,
        patient_id=patient.id,
        prescription_id=matched.id if matched else None,
        call_id=call_id,
        medication=payload.medication.strip(),
        note=payload.note,
        status="pending",
        requested_at=datetime.utcnow(),
    )
    db.add(row)

    audit_service.record(
        db, ctx.org_id, "refill.requested", ctx=ctx,
        target_type="refill_request", target_id=row.id,
        # The medication name is the point of the record and is already on
        # the row this entry points at; the detail line names the patient
        # so the Security page reads usefully without re-joining.
        detail=patient.name,
        status=audit_service.STATUS_LOGGED,
        request=request,
    )
    db.commit()
    db.refresh(row)

    # F14. Deliberately carries no medication-free-text beyond the name
    # the caller gave: this leaves the system for an endpoint the clinic
    # controls, and the row itself remains the record of truth.
    webhook_service.schedule(
        background_tasks, ctx.org_id, "refill.requested",
        {
            "refill_request_id": row.id,
            "patient_id": patient.id,
            "medication": row.medication,
            "prescription_id": row.prescription_id,
            "status": row.status,
        },
    )

    # PF-09: the written receipt. The caller is told on the phone that the
    # request has been passed on, and then holds nothing until a decision
    # arrives -- which may be days away.
    #
    # After the response and non-fatal, like the webhook above: a request
    # taken during a live call must never fail because a message could
    # not be sent.
    notification_service.schedule_refill_recorded_notification(
        background_tasks, row.id, ctx.org_id
    )

    logger.info("Refill requested for patient=%s org=%s", patient.id, ctx.org_id)
    return _out(row, patient.name)


@router.get("", response_model=list[schemas.RefillRequestOut])
def list_refill_requests(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    The clinic's refill queue, oldest request first.

    Staff-only: this is a list of medication requests, which the voice
    agent has no reason to read back. Defaults to what is still
    outstanding rather than all history.
    """
    q = (
        db.query(models.RefillRequest, models.Patient.name)
        .outerjoin(models.Patient, models.Patient.id == models.RefillRequest.patient_id)
        .filter(models.RefillRequest.org_id == user.org_id)
    )
    if status:
        if status not in schemas.REFILL_STATUSES:
            raise HTTPException(
                status_code=422,
                detail="Invalid status. Expected one of: "
                + ", ".join(schemas.REFILL_STATUSES),
            )
        q = q.filter(models.RefillRequest.status == status)
    else:
        q = q.filter(models.RefillRequest.status.in_(_OPEN_STATUSES))

    rows = q.order_by(models.RefillRequest.requested_at.asc()).limit(limit).all()
    return [_out(row, name) for row, name in rows]


@router.patch("/{refill_id}", response_model=schemas.RefillRequestOut)
def update_refill_request(
    refill_id: str,
    payload: schemas.RefillRequestUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Approve or decline a request.

    **`get_current_user`, never `get_org_context`.** This is the decision
    the AI is not allowed to make, and the dependency is what stops it —
    an agent token or service key cannot reach this route at all. Do not
    widen it.

    Not `require_admin` either: in most practices the person who works the
    prescription queue is a nurse or receptionist acting on a prescriber's
    standing instruction, and requiring an admin would push the work back
    to paper. Who did it is recorded on the row and in the audit log.
    """
    row = _get_or_404(db, refill_id, user.org_id)

    if row.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"This request has already been {row.status}.",
        )

    row.status = payload.status
    if payload.note is not None:
        row.note = payload.note
    row.handled_at = datetime.utcnow()
    row.handled_by_user_id = user.id

    audit_service.record(
        db, user.org_id, "refill.handled", user=user,
        target_type="refill_request", target_id=row.id,
        detail=payload.status,
        status=audit_service.STATUS_LOGGED,
        request=request,
    )
    db.commit()
    db.refresh(row)

    # PF-11: close the loop with the patient. The agent told them the
    # request had been passed to the clinic, which was true and then went
    # nowhere -- so they telephoned to ask, which is the call this whole
    # feature exists to remove.
    #
    # After the response and non-fatal, like every other side effect here:
    # a clinician working the queue must never see a decision fail because
    # a message could not be sent.
    notification_service.schedule_refill_decision_notification(
        background_tasks, row.id, user.org_id
    )

    patient = (
        db.query(models.Patient).filter(models.Patient.id == row.patient_id).first()
        if row.patient_id
        else None
    )
    return _out(row, patient.name if patient else None)
