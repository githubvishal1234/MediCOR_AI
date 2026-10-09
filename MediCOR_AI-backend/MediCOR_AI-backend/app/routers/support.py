from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, require_admin

router = APIRouter(prefix="/support", tags=["support"])

DOCS_LINKS = [
    {"title": "Getting Started with MediCOR AI", "detail": "Set up your first agent in under 10 minutes."},
    {"title": "Configuring EHR Sync", "detail": "Connect your EHR so bookings sync to it."},
    {"title": "Understanding Call Outcomes & Sentiment", "detail": "How MediCOR AI classifies and scores every call."},
    {"title": "HIPAA & Data Retention", "detail": "Compliance details for security and legal review."},
]


@router.get("/docs")
def list_docs():
    return DOCS_LINKS


@router.post("/tickets", response_model=schemas.SupportTicketOut)
def create_ticket(
    payload: schemas.SupportTicketCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    ticket = models.SupportTicket(org_id=user.org_id, user_id=user.id, **payload.model_dump())
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("/tickets", response_model=list[schemas.SupportTicketOut])
def list_tickets(db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    return (
        db.query(models.SupportTicket)
        .filter(models.SupportTicket.org_id == user.org_id)
        .order_by(models.SupportTicket.created_at.desc())
        .all()
    )