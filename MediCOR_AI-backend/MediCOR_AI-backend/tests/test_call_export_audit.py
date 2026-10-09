"""
Bulk call-log export writes its own audit entry.

The dashboard's Export button used to build its CSV in the browser. The
rows were never invisible — every `GET /calls/{id}` it fetched wrote a
`call.transcript_viewed` entry — but nothing recorded that 200 of them
were one bulk pull rather than a day of ordinary lookups, and that is the
distinction a breach investigation turns on.

The export now happens in `POST /calls/export` and audits itself once,
the same way `/security/audit-log/export` does.

    pytest tests/test_call_export_audit.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models, rate_limit, schemas  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import calls as calls_router  # noqa: E402
from app.routers import users as users_router  # noqa: E402


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    for r in (auth_router.router, calls_router.router, users_router.router):
        app.include_router(r)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal


def _register(client, org_name, email):
    r = client.post("/auth/register", json={
        "org_name": org_name, "full_name": "Owner",
        "email": email, "password": "Passw0rd!",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def clinic(ctx):
    client, Session = ctx
    admin = _register(client, "Northside Clinic", "admin@north.example.com")
    org_id = client.get("/auth/me", headers=admin).json()["org_id"]
    return client, Session, admin, org_id


def _make_call(client, headers, name, transcript=True):
    r = client.post("/calls", json={
        "direction": "inbound", "status": "in_progress",
        "patient_name": name, "caller_phone": "+15551110000",
    }, headers=headers)
    assert r.status_code == 200, r.text
    call_id = r.json()["id"]
    if transcript:
        client.put(f"/calls/{call_id}/transcript/bulk", json={"messages": [
            {"who": "ai", "text": "Clinic, how can I help?"},
            {"who": "patient", "text": "I need an appointment."},
        ]}, headers=headers)
    return call_id


def _exports(Session, org_id):
    db = Session()
    try:
        return (
            db.query(models.AuditLogEntry)
            .filter(
                models.AuditLogEntry.org_id == org_id,
                models.AuditLogEntry.action == "call_log.exported",
            )
            .all()
        )
    finally:
        db.close()


def test_exporting_call_logs_writes_one_audit_entry(clinic):
    client, Session, admin, org_id = clinic
    ids = [_make_call(client, admin, f"Caller {i}") for i in range(3)]

    r = client.post("/calls/export", json={"call_ids": ids}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment;" in r.headers["content-disposition"]

    body = r.text.lstrip("﻿")
    assert body.splitlines()[0].startswith("Patient,Phone,Direction,Time")
    for i in range(3):
        assert f"Caller {i}" in body
    # Transcripts travel with the export — which is why it is PHI-adjacent
    # and why one entry per bulk pull matters.
    assert "I need an appointment." in body

    entries = _exports(Session, org_id)
    assert len(entries) == 1
    assert "3 calls exported as CSV" in entries[0].detail


def test_a_call_with_no_transcript_says_so_rather_than_going_blank(clinic):
    client, _Session, admin, _org_id = clinic
    call_id = _make_call(client, admin, "Silent Caller", transcript=False)

    body = client.post(
        "/calls/export", json={"call_ids": [call_id]}, headers=admin
    ).text
    assert "No transcript available" in body


def test_export_cannot_reach_another_clinics_calls(ctx):
    """
    The ids come from the client, so this is the one that matters: asking
    for a call belonging to someone else must yield nothing for it, not
    that clinic's transcript.
    """
    client, _Session = ctx
    a = _register(client, "Clinic A", "a@a.example.com")
    b = _register(client, "Clinic B", "b@b.example.com")

    theirs = _make_call(client, a, "Clinic A Patient")
    mine = _make_call(client, b, "Clinic B Patient")

    r = client.post("/calls/export", json={"call_ids": [mine, theirs]}, headers=b)
    assert r.status_code == 200, r.text
    assert "Clinic B Patient" in r.text
    assert "Clinic A Patient" not in r.text


def test_the_audit_entry_counts_only_what_was_actually_exported(ctx):
    """A cross-org id is skipped, so the count must not include it."""
    client, Session = ctx
    a = _register(client, "Clinic A", "a2@a.example.com")
    b = _register(client, "Clinic B", "b2@b.example.com")
    org_b = client.get("/auth/me", headers=b).json()["org_id"]

    theirs = _make_call(client, a, "Theirs")
    mine = _make_call(client, b, "Mine")

    client.post("/calls/export", json={"call_ids": [mine, theirs]}, headers=b)

    entries = _exports(Session, org_b)
    assert len(entries) == 1
    assert "1 call exported as CSV" in entries[0].detail


def test_export_is_open_to_medical_staff_not_admin_only(clinic):
    """
    Medical staff could already export from this page. Quietly making the
    fixed version admin-only would be a regression dressed up as a fix.
    """
    client, _Session, admin, _org_id = clinic
    call_id = _make_call(client, admin, "Someone")

    # Invite + accept, the same route pair tests/test_qw7_rbac.py uses.
    # `POST /users` is not it — that path opens its own session and tries
    # to reach the configured SQL Server rather than the test override.
    invite = client.post("/users/invite", json={
        "email": "mia@north.example.com",
        "full_name": "Mia Medic",
        "role": "medical_staff",
    }, headers=admin)
    assert invite.status_code in (200, 201), invite.text

    accepted = client.post("/auth/accept-invite", json={
        "token": invite.json()["invite_token"], "password": "N3wPassw0rd!",
    })
    assert accepted.status_code == 200, accepted.text
    staff = {"Authorization": f"Bearer {accepted.json()['access_token']}"}

    r = client.post("/calls/export", json={"call_ids": [call_id]}, headers=staff)
    assert r.status_code == 200, r.text


def test_an_empty_id_list_is_rejected_rather_than_exporting_everything(clinic):
    client, _Session, admin, _org_id = clinic
    r = client.post("/calls/export", json={"call_ids": []}, headers=admin)
    assert r.status_code == 422, r.text


# ---------- call recording is not built, and must not be advertised ----

def test_no_response_shape_advertises_a_recording(clinic):
    """
    `CallLog.recording_url` exists but nothing has ever written it. The
    call-detail panel carried a "Play Recording" control for it and the
    API returned the field — the control was gated on the value being
    truthy so it never rendered, but the dead playback code and the
    advertised field were real.

    Both are gone. This fails if either comes back without the capability
    behind it.

    If recording IS built later: do not serve this column as a bare URL.
    A recording is a full PHI conversation, and an unauthenticated link
    to one has no org check and leaves no audit entry. Playback belongs
    behind an authenticated endpoint that records who listened, the way
    `GET /calls/{id}` already does for transcripts.
    """
    client, _Session, admin, org_id = clinic
    call_id = _make_call(client, admin, "Someone")

    detail = client.get(f"/calls/{call_id}", headers=admin).json()
    assert "recording_url" not in detail

    listed = client.get("/calls", headers=admin).json()
    assert listed and all("recording_url" not in row for row in listed)

    for shape in ("CallLogBase", "CallLogCreate", "CallLogUpdate",
                  "CallLogListOut", "CallLogDetailOut"):
        fields = getattr(schemas, shape).model_fields
        assert "recording_url" not in fields, (
            f"{shape} advertises a recording the product cannot produce"
        )
