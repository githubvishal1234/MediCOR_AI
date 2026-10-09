"""
Two security fixes from the 2026-09-21 audit, pinned.

1. Tokens in URLs never reach the log.
   The dashboard WebSocket (`/ws/dashboard?token=<staff JWT>`) and the
   patient appointment link (`/appointment-link?token=<link token>`) carry
   a bearer credential in the query string. Uvicorn logs every request
   line, so both were written to the log in plaintext -- replayable by
   anyone who can read it. `RedactTokensFilter` scrubs them.

2. A suspended clinic's WebSocket is refused.
   Every HTTP auth path in deps.py rejects a suspended organization; the
   WebSocket resolved the user and connected them anyway, so a suspended
   clinic kept receiving live call and appointment events.
"""

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base
from app.logging_config import RedactTokensFilter
from app.routers import ws as ws_router
from app.security import create_access_token, hash_password


def _record(msg, *args):
    return logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, msg, args, None)


def test_ws_and_link_tokens_are_redacted():
    f = RedactTokensFilter()
    rec = _record('%s - "WebSocket /ws/dashboard?token=%s" [accepted]', "127.0.0.1", "eyJhbGciOi.secret.sig")
    f.filter(rec)
    out = rec.getMessage()
    assert "eyJhbGciOi" not in out and "token=[redacted]" in out

    rec = _record('"GET /appointment-link/availability?token=abc123XYZ&date=2026-09-22 HTTP/1.1" 200')
    f.filter(rec)
    out = rec.getMessage()
    assert "abc123XYZ" not in out
    # Only the token is removed; the rest of the query survives for debugging.
    assert "token=[redacted]&date=2026-09-22" in out


def test_messages_without_tokens_are_untouched():
    f = RedactTokensFilter()
    rec = _record("Booked appointment %s for %s", "a1", "tomorrow")
    f.filter(rec)
    assert rec.getMessage() == "Booked appointment a1 for tomorrow"


def test_suspended_clinic_cannot_open_the_dashboard_socket(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(ws_router, "SessionLocal", Session)

    db = Session()
    active = models.Organization(name="Active Clinic")
    suspended = models.Organization(name="Suspended Clinic", suspended=True)
    db.add_all([active, suspended])
    db.flush()
    u_active = models.User(org_id=active.id, email="a@x.test", hashed_password=hash_password("Passw0rd!"), full_name="A")
    u_susp = models.User(org_id=suspended.id, email="s@x.test", hashed_password=hash_password("Passw0rd!"), full_name="S")
    db.add_all([u_active, u_susp])
    db.commit()

    assert ws_router._resolve_org_id(create_access_token({"sub": u_active.id})) == active.id
    assert ws_router._resolve_org_id(create_access_token({"sub": u_susp.id})) is None
