"""
Persistent notification centre — HV-6.

`realtime.notify()` used to push over WebSocket only. `RealtimeProvider`
held the list in React state, so a page refresh wiped it and anything
raised while nobody had the dashboard open was lost for good — a
transfer-to-nurse request at 3am did not exist by morning. There was no
replay, no history, and no way to tell a handled alert from an unseen one.

Persistence happens in `realtime.notify()` itself (the single choke point
every notification already flowed through), so no router that raises a
notification had to change. This module is only the read/acknowledge side.

Everything here is org-scoped via `get_current_user` and reachable by any
signed-in staff member: notifications are shared clinic work, not
per-user mail. Marking one read marks it read for the whole clinic, which
is the intended behaviour for a shared front-desk queue — if one person
deals with a missed call, it should stop nagging everyone else.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

logger = logging.getLogger("notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Hard ceiling on one page, regardless of what the client asks for.
_MAX_LIMIT = 100


def _to_out(row: models.Notification) -> schemas.NotificationOut:
    """
    Decodes `data_json` back into an object for the client.

    A malformed value is dropped rather than raised on: it is written only
    by this backend, so bad JSON means a bug elsewhere, and the
    notification is still worth showing without its payload.
    """
    data = {}
    if row.data_json:
        try:
            data = json.loads(row.data_json)
        except (TypeError, ValueError):
            logger.warning("Notification %s has unreadable data_json", row.id)
    return schemas.NotificationOut(
        id=row.id,
        level=row.level or "info",
        title=row.title,
        message=row.message,
        data=data,
        read_at=row.read_at,
        created_at=row.created_at,
    )


@router.get("", response_model=schemas.NotificationPageOut)
def list_notifications(
    limit: int = 30,
    offset: int = 0,
    unread_only: bool = False,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Newest-first page of this clinic's notifications.

    `unread_count` is returned alongside the page rather than requiring a
    second request, because the bell badge needs it on every load and it
    is a cheap count over the same index.
    """
    base = db.query(models.Notification).filter(
        models.Notification.org_id == user.org_id
    )

    unread_count = base.filter(models.Notification.read_at.is_(None)).count()

    q = base
    if unread_only:
        q = q.filter(models.Notification.read_at.is_(None))

    rows = (
        q.order_by(models.Notification.created_at.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), _MAX_LIMIT))
        .all()
    )
    return schemas.NotificationPageOut(
        items=[_to_out(r) for r in rows], unread_count=unread_count
    )


@router.patch("/{notification_id}/read", response_model=schemas.NotificationOut)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    row = (
        db.query(models.Notification)
        .filter(
            models.Notification.id == notification_id,
            models.Notification.org_id == user.org_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    # Idempotent: re-reading an already-read notification keeps the
    # original timestamp rather than moving it forward.
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
    return _to_out(row)


@router.post("/read-all", response_model=schemas.NotificationReadAllOut)
def mark_all_read(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Clears the badge. Returns how many were actually affected."""
    updated = (
        db.query(models.Notification)
        .filter(
            models.Notification.org_id == user.org_id,
            models.Notification.read_at.is_(None),
        )
        .update({models.Notification.read_at: datetime.utcnow()}, synchronize_session=False)
    )
    db.commit()
    return schemas.NotificationReadAllOut(marked_read=updated)
