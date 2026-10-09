"""
Real-time dashboard infrastructure (Phase 6).

A single in-process, org-scoped WebSocket connection manager. All FastAPI
routers stay synchronous (as the rest of this codebase already is) and
call `realtime.broadcast(org_id, event, data)` — a plain, non-async
function that is safe to call from inside a normal `def` request handler.
Internally it hands the send off to the event loop that's actually
running the WebSocket connections via `asyncio.run_coroutine_threadsafe`,
so no router needs to become async and no business logic moves out of
FastAPI.

This module has no knowledge of calls, appointments, or any other
domain concept — routers build the event payload, this module just
fans it out to connected dashboard clients for that org.

Broadcasts are ephemeral: a client that isn't connected misses live
`call.*` / `appointment.*` events and picks up current state on its next
normal API call. **Staff notifications are the exception** — since HV-6,
`notify()` also writes a `notifications` row before broadcasting, so an
alert raised while nobody had the dashboard open is still there in the
morning. See app/routers/notifications.py.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from fastapi import WebSocket

logger = logging.getLogger("realtime")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once on app startup so sync code can schedule sends onto it."""
        self._loop = loop

    async def connect(self, org_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[org_id].add(ws)
        logger.info(f"dashboard ws connected org={org_id} total={len(self._connections[org_id])}")

    def disconnect(self, org_id: str, ws: WebSocket) -> None:
        self._connections[org_id].discard(ws)
        if not self._connections[org_id]:
            self._connections.pop(org_id, None)

    async def _broadcast_async(self, org_id: str, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(org_id, ())):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(org_id, ws)

    def broadcast(self, org_id: str, event: str, data: Any) -> None:
        """
        Sync-safe fire-and-forget broadcast. Safe to call from a regular
        (threadpool-executed) request handler, a background task, or
        anywhere else — including before any client has connected (it's
        simply a no-op if nobody is listening yet).
        """
        if self._loop is None or not self._connections.get(org_id):
            return
        message = {"type": event, "data": data, "ts": datetime.utcnow().isoformat() + "Z"}
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast_async(org_id, message), self._loop)
        except RuntimeError:
            logger.warning("realtime broadcast dropped — event loop not running")


manager = ConnectionManager()


# ---------- Event payload helpers ----------
# Keep payloads small and UI-ready (label strings, not raw enums) so the
# dashboard can render a notification/toast directly off the WS message
# without a follow-up API call.

def call_summary(call) -> dict[str, Any]:
    """Compact, JSON-serializable snapshot of a CallLog for WS events."""
    return {
        "id": call.id,
        "patient_id": call.patient_id,
        "appointment_id": call.appointment_id,
        "patient_name": call.patient_name,
        "caller_phone": call.caller_phone,
        "direction": call.direction,
        "status": call.status,
        "reason": call.reason,
        "outcome": call.outcome,
        "sentiment": call.sentiment,
        "duration": call.duration,
        "duration_seconds": call.duration_seconds,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
    }


def appointment_summary(appt) -> dict[str, Any]:
    return {
        "id": appt.id,
        "patient_id": appt.patient_id,
        "patient_name": appt.patient_name,
        "doctor_id": appt.doctor_id,
        "title": appt.title,
        "day_label": appt.day_label,
        "time_label": appt.time_label,
        "start_at": appt.start_at.isoformat() if appt.start_at else None,
        "status": appt.status,
        "ai_generated": appt.ai_generated,
        # PF-08: lets a confirmation arriving through the patient's link
        # light up the calendar without a refresh.
        "confirmation_status": appt.confirmation_status,
    }


def _persist_notification(
    org_id: str, level: str, title: str, message: str, data: Optional[dict]
) -> Optional[str]:
    """
    Stores the notification and returns its id (HV-6).

    Deliberately opens its own short Session rather than taking one as an
    argument. `notify()` is called from ~10 places, always *after* the
    caller has committed its own work, and threading a session through
    every call site would mean touching all of them — the point of doing
    this here is that no router has to change.

    Never raises: a failure to record a notification must not turn a
    successful booking or call update into a 500. Same posture as
    ehr_service and notification_service.
    """
    # Imported here, not at module scope: this module is imported by
    # database-layer code paths, and a top-level import would create a
    # cycle (database -> models -> ... -> realtime -> database).
    from .database import SessionLocal
    from .models import Notification

    db = None
    try:
        db = SessionLocal()
        row = Notification(
            org_id=org_id,
            level=level,
            title=title,
            message=message,
            data_json=json.dumps(data) if data else None,
        )
        db.add(row)
        db.commit()
        return row.id
    except Exception:
        if db is not None:
            db.rollback()
        logger.exception("Failed to persist notification for org=%s", org_id)
        return None
    finally:
        if db is not None:
            db.close()


def notify(org_id: str, level: str, title: str, message: str, data: Optional[dict] = None) -> None:
    """
    Records a staff-facing notification and pushes it to connected clients.

    `level` is one of info | success | warning — purely cosmetic, mirrors
    the Chip tones already used across the dashboard.

    Since HV-6 this both **persists** and broadcasts. That ordering
    matters: the stored row's id is included in the WebSocket payload, so
    a client that is connected can mark the very notification it just
    received as read, and a client that was offline sees the identical
    item when it fetches history on next load. Without the id, the live
    copy and the stored copy would be indistinguishable duplicates.
    """
    # Defensive at this level too, not only inside _persist_notification.
    #
    # Every caller invokes notify() *after* committing its own work — a
    # booking is already saved, a call is already finalised — so anything
    # raised from here would turn a succeeded operation into a 500 and,
    # worse, tell the voice agent its call update failed when it didn't.
    # A notification is a side effect of the action, never part of it.
    try:
        notification_id = _persist_notification(org_id, level, title, message, data)
        manager.broadcast(
            org_id,
            "notification",
            {
                "id": notification_id,
                "level": level,
                "title": title,
                "message": message,
                "data": data or {},
            },
        )
    except Exception:
        logger.exception("Failed to raise notification for org=%s", org_id)