"""
The waiting list (PF-15).

A cancellation is pure lost capacity: the slot reopens and whoever wanted
it has already been told there was nothing and gone away. Nothing recorded
unmet demand, so nothing could match the two.

**Adding is dual-auth, deciding is not** -- the same split as
repeat-prescription requests, and for the same reason:

| Route | Dependency | The agent? |
|---|---|---|
| `POST /waiting-list` | `get_org_context` | Yes. Offering to note down what a caller wanted is the natural end of a call where nothing was free |
| `GET /waiting-list` | `get_current_user` | **No.** A list of who else is waiting, and what for, is not the agent's business |
| `DELETE /waiting-list/{id}` | `get_current_user` | **No.** Removing someone from a queue is a decision about fairness |

Nothing here books anything. Matching a freed slot to a waiting patient
is `jobs.offer_waiting_list_slots`, and accepting is the patient's own
reply -- see routers/inbound_messages.py.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from .. import audit_service, models, schemas
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context

logger = logging.getLogger("waiting-list")

router = APIRouter(prefix="/waiting-list", tags=["waiting-list"])


def _get_or_404(db: Session, entry_id: str, org_id: str) -> models.WaitingListEntry:
    row = (
        db.query(models.WaitingListEntry)
        .filter(
            models.WaitingListEntry.id == entry_id,
            models.WaitingListEntry.org_id == org_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Waiting list entry not found")
    return row


def _out(db: Session, row: models.WaitingListEntry) -> schemas.WaitingListOut:
    patient = (
        db.query(models.Patient).filter(models.Patient.id == row.patient_id).first()
    )
    doctor = (
        db.query(models.Doctor).filter(models.Doctor.id == row.doctor_id).first()
        if row.doctor_id
        else None
    )
    return schemas.WaitingListOut(
        id=row.id,
        patient_id=row.patient_id,
        patient_name=patient.name if patient else None,
        doctor_id=row.doctor_id,
        doctor_name=doctor.name if doctor else None,
        wanted_from=row.wanted_from,
        wanted_until=row.wanted_until,
        reason=row.reason,
        status=row.status,
        offered_start_at=row.offered_start_at,
        offer_expires_at=row.offer_expires_at,
        offers_made=row.offers_made or 0,
        created_at=row.created_at,
    )


@router.post("", response_model=schemas.WaitingListOut)
def add_to_waiting_list(
    payload: schemas.WaitingListCreate,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Note that this patient wanted an appointment we could not offer.

    Dual-auth on purpose: the natural moment to ask "shall I put you on
    the list?" is the end of a call where nothing was free, and that call
    is being handled by the agent.

    Nothing here books, holds, or promises anything -- it records unmet
    demand, which is the one thing that was missing.
    """
    patient = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == payload.patient_id,
            models.Patient.org_id == ctx.org_id,
        )
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    if payload.doctor_id:
        doctor = (
            db.query(models.Doctor)
            .filter(
                models.Doctor.id == payload.doctor_id,
                models.Doctor.org_id == ctx.org_id,
            )
            .first()
        )
        if doctor is None:
            raise HTTPException(status_code=404, detail="Doctor not found")

    # One live entry per patient per clinician. A caller who rings three
    # times about the same thing should move up the queue by seniority,
    # not by having three places in it.
    existing = (
        db.query(models.WaitingListEntry)
        .filter(
            models.WaitingListEntry.org_id == ctx.org_id,
            models.WaitingListEntry.patient_id == patient.id,
            models.WaitingListEntry.doctor_id == payload.doctor_id,
            models.WaitingListEntry.status.in_(("waiting", "offered")),
        )
        .first()
    )
    if existing is not None:
        return _out(db, existing)

    row = models.WaitingListEntry(
        org_id=ctx.org_id,
        patient_id=patient.id,
        doctor_id=payload.doctor_id,
        wanted_from=payload.wanted_from,
        wanted_until=payload.wanted_until,
        reason=payload.reason,
        status="waiting",
    )
    db.add(row)
    audit_service.record(
        db, ctx.org_id, "appointment.updated", ctx=ctx,
        target_type="patient", target_id=patient.id,
        detail="Added to the waiting list",
        status=audit_service.STATUS_LOGGED,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.get("", response_model=list[schemas.WaitingListOut])
def list_waiting_list(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Who is waiting, oldest first -- which is also the order they will be
    offered slots in, so the screen shows the real queue rather than an
    arbitrary one.

    Staff only. A list of who else is waiting, and what for, is not
    something the voice agent has any business reading out.
    """
    q = db.query(models.WaitingListEntry).filter(
        models.WaitingListEntry.org_id == user.org_id
    )
    if status:
        if status not in schemas.WAITING_LIST_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of {sorted(schemas.WAITING_LIST_STATUSES)}",
            )
        q = q.filter(models.WaitingListEntry.status == status)
    else:
        q = q.filter(models.WaitingListEntry.status.in_(("waiting", "offered")))

    rows = q.order_by(models.WaitingListEntry.created_at.asc()).all()
    return [_out(db, r) for r in rows]


@router.delete("/{entry_id}", response_model=schemas.WaitingListOut)
def remove_from_waiting_list(
    entry_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Take someone off the list.

    Staff only, and not `require_admin`: whoever works the queue is the
    person who knows a patient has been seen elsewhere or no longer needs
    it, and that is the same reasoning as the refill queue.

    Cancelled rather than deleted, so "why did we stop offering this
    person anything" remains answerable.
    """
    row = _get_or_404(db, entry_id, user.org_id)
    row.status = "cancelled"
    row.offered_start_at = None
    row.offer_expires_at = None
    audit_service.record(
        db, user.org_id, "appointment.updated", user=user,
        target_type="patient", target_id=row.patient_id,
        detail="Removed from the waiting list",
        status=audit_service.STATUS_LOGGED,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return _out(db, row)
