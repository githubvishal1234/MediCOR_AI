"""
HV-6 (persistent notifications) and HV-7 (missed-call callback queue).

Both close the same shape of gap: something the product already detected
but immediately forgot.

  * **HV-6** — `realtime.notify()` pushed over WebSocket and stored
    nothing, so a refresh wiped the list and anything raised overnight was
    gone by morning. The critical assertion here is that a notification
    survives *with no WebSocket client connected at all*, because that is
    exactly the case that used to lose data silently.

  * **HV-7** — `CallLog.status` already supported "failed"/"no_answer"
    and fired a warning, but nothing tracked the follow-up. The critical
    assertion is that advancing a callback is not undone by a later
    update to the same call.

    pytest tests/test_hv6_hv7_notifications_callbacks.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, rate_limit, realtime
from app.database import Base, get_db
from app.routers import auth as auth_router
from app.routers import calls as calls_router
from app.routers import notifications as notifications_router


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # realtime._persist_notification opens its OWN session from the
    # module-global SessionLocal (by design — see its docstring), which
    # the get_db override does not touch. Point it at the test engine so
    # notifications land in the same in-memory database the API reads.
    import app.database as app_database

    monkeypatch.setattr(app_database, "SessionLocal", TestingSessionLocal)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(calls_router.router)
    app.include_router(notifications_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal


@pytest.fixture()
def auth(ctx):
    client, Session = ctx
    r = client.post(
        "/auth/register",
        json={
            "org_name": "Clinic A",
            "full_name": "Admin One",
            "email": "admin@clinica.example.com",
            "password": "Passw0rd!",
        },
    )
    assert r.status_code == 200, r.text
    return client, {"Authorization": f"Bearer {r.json()['access_token']}"}, Session


def _start_call(client, headers, **kw):
    body = {"direction": "inbound", "caller_phone": "+15551230000", **kw}
    r = client.post("/calls", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- HV-6 ----------

def test_a_notification_is_stored_even_with_nobody_connected(auth):
    """
    The case that used to lose data outright.

    No WebSocket client exists in this test, so before HV-6 this
    notification would have been broadcast into the void and forgotten.
    """
    client, headers, _ = auth
    _start_call(client, headers, patient_name="Jane Caller")

    page = client.get("/notifications", headers=headers).json()
    assert page["unread_count"] == 1
    assert page["items"][0]["title"] == "Incoming call"
    assert "Jane Caller" in page["items"][0]["message"]
    # The payload survives the JSON round-trip through data_json.
    assert page["items"][0]["data"]["call_id"]


def test_notifications_are_newest_first_and_paginate(auth):
    client, headers, _ = auth
    for i in range(3):
        _start_call(client, headers, patient_name=f"Caller {i}")

    page = client.get("/notifications?limit=2", headers=headers).json()
    assert len(page["items"]) == 2
    assert page["unread_count"] == 3  # counts all unread, not just this page
    assert "Caller 2" in page["items"][0]["message"]

    second = client.get("/notifications?limit=2&offset=2", headers=headers).json()
    assert len(second["items"]) == 1
    assert "Caller 0" in second["items"][0]["message"]


def test_marking_read_is_idempotent_and_clears_the_badge(auth):
    client, headers, _ = auth
    _start_call(client, headers)
    nid = client.get("/notifications", headers=headers).json()["items"][0]["id"]

    first = client.patch(f"/notifications/{nid}/read", headers=headers).json()
    assert first["read_at"] is not None

    # Re-reading must not move the timestamp forward.
    again = client.patch(f"/notifications/{nid}/read", headers=headers).json()
    assert again["read_at"] == first["read_at"]

    assert client.get("/notifications", headers=headers).json()["unread_count"] == 0


def test_read_all_clears_everything_and_reports_the_count(auth):
    client, headers, _ = auth
    for _ in range(3):
        _start_call(client, headers)

    assert client.post("/notifications/read-all", headers=headers).json()["marked_read"] == 3
    assert client.get("/notifications", headers=headers).json()["unread_count"] == 0
    # Nothing left to mark — must not error or double-count.
    assert client.post("/notifications/read-all", headers=headers).json()["marked_read"] == 0


def test_unread_only_filters_the_page(auth):
    client, headers, _ = auth
    _start_call(client, headers, patient_name="First")
    nid = client.get("/notifications", headers=headers).json()["items"][0]["id"]
    client.patch(f"/notifications/{nid}/read", headers=headers)
    _start_call(client, headers, patient_name="Second")

    unread = client.get("/notifications?unread_only=true", headers=headers).json()
    assert len(unread["items"]) == 1
    assert "Second" in unread["items"][0]["message"]


def test_one_clinic_never_sees_another_clinics_notifications(auth):
    client, a_headers, _ = auth
    _start_call(client, a_headers, patient_name="Clinic A Caller")

    r = client.post(
        "/auth/register",
        json={
            "org_name": "Clinic B",
            "full_name": "Admin Two",
            "email": "admin@clinicb.example.com",
            "password": "Passw0rd!",
        },
    )
    b_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    page = client.get("/notifications", headers=b_headers).json()
    assert page["items"] == []
    assert page["unread_count"] == 0


def test_a_notification_failure_never_breaks_the_request(auth, monkeypatch):
    """
    Persistence is a side effect of the call, not part of it — the same
    posture as ehr_service and notification_service.
    """
    client, headers, _ = auth

    def boom(*_a, **_kw):
        raise RuntimeError("database is down")

    monkeypatch.setattr(realtime, "_persist_notification", boom)
    # Creating a call raises a notification internally; it must still 200.
    r = client.post(
        "/calls", json={"direction": "inbound", "caller_phone": "+1555"}, headers=headers
    )
    assert r.status_code == 200


# ---------- HV-7 ----------

@pytest.mark.parametrize("missed_status", ["failed", "no_answer"])
def test_a_missed_call_enters_the_callback_queue(auth, missed_status):
    client, headers, _ = auth
    call_id = _start_call(client, headers, patient_name="Missed Caller")

    client.patch(f"/calls/{call_id}", json={"status": missed_status}, headers=headers)

    queue = client.get("/calls/callbacks/queue", headers=headers).json()
    assert len(queue) == 1
    assert queue[0]["id"] == call_id
    assert queue[0]["callback_status"] == "pending"


def test_an_answered_call_never_enters_the_queue(auth):
    client, headers, _ = auth
    call_id = _start_call(client, headers)
    client.patch(f"/calls/{call_id}", json={"status": "completed"}, headers=headers)

    assert client.get("/calls/callbacks/queue", headers=headers).json() == []
    # ...and it cannot be forced in, because there is nothing to call back about.
    r = client.patch(
        f"/calls/{call_id}/callback", json={"callback_status": "pending"}, headers=headers
    )
    assert r.status_code == 400


def test_advancing_a_callback_survives_a_later_call_update(auth):
    """
    The regression that would quietly undo staff work: re-finalising a
    call (the agent retrying, say) must not reset a callback someone has
    already actioned.
    """
    client, headers, _ = auth
    call_id = _start_call(client, headers)
    client.patch(f"/calls/{call_id}", json={"status": "no_answer"}, headers=headers)

    client.patch(
        f"/calls/{call_id}/callback",
        json={"callback_status": "contacted", "callback_note": "Left voicemail"},
        headers=headers,
    )

    # A second finalisation of the same call.
    client.patch(
        f"/calls/{call_id}", json={"status": "no_answer", "duration_seconds": 12}, headers=headers
    )

    queue = client.get("/calls/callbacks/queue", headers=headers).json()
    assert queue[0]["callback_status"] == "contacted"
    assert queue[0]["callback_note"] == "Left voicemail"


def test_resolved_callbacks_leave_the_default_queue(auth):
    client, headers, _ = auth
    call_id = _start_call(client, headers)
    client.patch(f"/calls/{call_id}", json={"status": "failed"}, headers=headers)

    client.patch(
        f"/calls/{call_id}/callback", json={"callback_status": "resolved"}, headers=headers
    )

    # The queue is a to-do list, so resolved items drop out by default...
    assert client.get("/calls/callbacks/queue", headers=headers).json() == []
    # ...but remain retrievable explicitly.
    resolved = client.get("/calls/callbacks/queue?status=resolved", headers=headers).json()
    assert len(resolved) == 1


def test_the_queue_is_oldest_first(auth):
    """A to-do list: the longest-waiting caller is the most urgent."""
    client, headers, _ = auth
    ids = []
    for i in range(3):
        cid = _start_call(client, headers, patient_name=f"Caller {i}")
        client.patch(f"/calls/{cid}", json={"status": "no_answer"}, headers=headers)
        ids.append(cid)

    queue = client.get("/calls/callbacks/queue", headers=headers).json()
    assert [q["id"] for q in queue] == ids


def test_an_invalid_callback_status_is_rejected(auth):
    client, headers, _ = auth
    call_id = _start_call(client, headers)
    client.patch(f"/calls/{call_id}", json={"status": "failed"}, headers=headers)

    assert client.patch(
        f"/calls/{call_id}/callback", json={"callback_status": "done"}, headers=headers
    ).status_code == 422
    assert client.get(
        "/calls/callbacks/queue?status=nonsense", headers=headers
    ).status_code == 422


def test_the_callback_queue_is_org_scoped(auth):
    client, a_headers, _ = auth
    call_id = _start_call(client, a_headers)
    client.patch(f"/calls/{call_id}", json={"status": "failed"}, headers=a_headers)

    r = client.post(
        "/auth/register",
        json={
            "org_name": "Clinic B",
            "full_name": "Admin Two",
            "email": "admin@clinicb.example.com",
            "password": "Passw0rd!",
        },
    )
    b_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert client.get("/calls/callbacks/queue", headers=b_headers).json() == []
    assert client.patch(
        f"/calls/{call_id}/callback", json={"callback_status": "contacted"}, headers=b_headers
    ).status_code == 404
