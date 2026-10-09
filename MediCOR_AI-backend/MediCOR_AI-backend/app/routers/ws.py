"""
Dashboard WebSocket endpoint (Phase 6).

Browsers can't attach an Authorization header to a native WebSocket
handshake, so this reuses the same JWT issued by /auth/login, passed as
a `token` query param instead of a bearer header — same token, same
`decode_access_token`, just a different transport. Service (X-API-Key)
auth is intentionally not supported here: this socket is for dashboard
staff clients only, the voice agent never needs it.
"""

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from ..realtime import manager
from ..security import decode_access_token

logger = logging.getLogger("ws-router")

router = APIRouter(tags=["realtime"])


def _resolve_org_id(token: str) -> str | None:
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    db: Session = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user or not user.is_active:
            return None
        # Same rule as every HTTP path in deps.py: suspending a clinic cuts
        # it off. Without this a suspended clinic's open dashboards kept
        # receiving live call and appointment events.
        if user.organization is not None and user.organization.suspended:
            return None
        return user.org_id
    finally:
        db.close()


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket, token: str = Query(...)):
    org_id = _resolve_org_id(token)
    if not org_id:
        await websocket.close(code=4401)
        return

    await manager.connect(org_id, websocket)
    try:
        while True:
            # Dashboard clients don't need to send anything; we just keep
            # the connection open and drain/ignore client pings/keepalives.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("dashboard ws error")
    finally:
        manager.disconnect(org_id, websocket)