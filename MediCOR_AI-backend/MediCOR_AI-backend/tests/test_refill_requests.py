"""
Repeat-prescription requests taken by phone (F9).

THE TESTS THAT MATTER ARE THE ONES ABOUT WHAT THE AGENT CANNOT DO.

The feature's whole safety argument is that the AI records a request and a
person decides it. That argument is only true if the decision route is
unreachable with an agent credential, so:

  * `test_the_voice_agent_cannot_approve_a_refill`
  * `test_the_voice_agent_cannot_read_the_refill_queue`
  * `test_the_agent_cannot_create_a_refill_for_an_unidentified_caller`

are the point of this file. The storage tests are the easy part.

    pytest tests/test_refill_requests.py -v
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
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models, rate_limit, schemas  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import patients as patients_router  # noqa: E402
from app.routers import refills as refills_router  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Testing = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = Testing()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    for r in (auth_router.router, patients_router.router, refills_router.router):
        app.include_router(r)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, Testing


def _register(client, org_name, email):
    r = client.post("/auth/register", json={
        "org_name": org_name, "full_name": "Owner",
        "email": email, "password": "Passw0rd!",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _api_key(client, Testing, headers):
    org_id = client.get("/auth/me", headers=headers).json()["org_id"]
    raw, prefix = generate_api_key()
    db = Testing()
    try:
        db.add(models.APIKey(
            org_id=org_id, label="Voice Agent",
            key_prefix=prefix, hashed_key=hash_api_key(raw),
        ))
        db.commit()
    finally:
        db.close()
    return {"X-API-Key": raw}, org_id


@pytest.fixture()
def clinic(ctx):
    client, Testing = ctx
    admin = _register(client, "Northside Clinic", "admin@north.example.com")
    key, org_id = _api_key(client, Testing, admin)
    return client, Testing, admin, key, org_id


def _patient(client, headers, name="Anitha Rao"):
    r = client.post("/patients", json={"name": name, "phone": "+919000000001"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _request_refill(client, headers, patient_id, medication="Metformin", **extra):
    body = {"patient_id": patient_id, "medication": medication}
    body.update(extra)
    return client.post("/refills", json=body, headers=headers)


# ============================================================
# THE SAFETY BOUNDARY
# ============================================================

def test_the_voice_agent_cannot_approve_a_refill(clinic):
    """
    The single most important assertion in this feature.

    Approving a repeat prescription is a clinical decision. The route is
    `get_current_user`, so an agent credential cannot reach it at all —
    and that is a rule, not a prompt instruction the model might ignore.
    """
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)
    refill_id = _request_refill(client, key, pid).json()["id"]

    r = client.patch(f"/refills/{refill_id}", json={"status": "approved"}, headers=key)
    assert r.status_code in (401, 403), r.text

    still = client.get("/refills", headers=admin).json()
    assert [x["status"] for x in still] == ["pending"]


def test_the_voice_agent_cannot_read_the_refill_queue(clinic):
    """A list of other patients' medication requests is not the agent's business."""
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)
    _request_refill(client, key, pid)

    r = client.get("/refills", headers=key)
    assert r.status_code in (401, 403), r.text


def test_the_agent_cannot_create_a_refill_for_an_unidentified_caller(clinic):
    """
    An anonymous medication request is not actionable by staff, so it must
    not become a row nobody can resolve.
    """
    client, _T, admin, key, _org = clinic
    r = client.post(
        "/refills",
        json={"patient_id": "does-not-exist", "medication": "Metformin"},
        headers=key,
    )
    assert r.status_code == 404, r.text
    assert client.get("/refills", headers=admin).json() == []


def test_a_refill_can_never_be_created_already_approved(clinic):
    """
    There is no field on the create route that sets a status. Even if a
    caller sends one, it is ignored and the row starts pending.
    """
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)

    r = _request_refill(client, key, pid, status="approved")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"


def test_one_clinic_cannot_request_a_refill_for_anothers_patient(ctx):
    client, Testing = ctx
    a = _register(client, "Clinic A", "a@a.example.com")
    b = _register(client, "Clinic B", "b@b.example.com")
    key_b, _org_b = _api_key(client, Testing, b)

    theirs = _patient(client, a, name="Their Patient")

    r = _request_refill(client, key_b, theirs)
    assert r.status_code == 404, r.text
    assert client.get("/refills", headers=a).json() == []


def test_one_clinic_cannot_approve_anothers_refill(ctx):
    client, Testing = ctx
    a = _register(client, "Clinic A", "a2@a.example.com")
    b = _register(client, "Clinic B", "b2@b.example.com")
    key_a, _ = _api_key(client, Testing, a)

    pid = _patient(client, a)
    refill_id = _request_refill(client, key_a, pid).json()["id"]

    assert client.patch(
        f"/refills/{refill_id}", json={"status": "approved"}, headers=b
    ).status_code == 404


# ============================================================
# BEHAVIOUR
# ============================================================

def test_a_request_is_recorded_pending_and_audited(clinic):
    client, Testing, admin, key, org_id = clinic
    pid = _patient(client, admin)

    r = _request_refill(client, key, pid, medication="Metformin 500mg")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["medication"] == "Metformin 500mg"
    assert body["handled_at"] is None

    db = Testing()
    try:
        actions = [
            e.action for e in db.query(models.AuditLogEntry)
            .filter(models.AuditLogEntry.org_id == org_id).all()
        ]
    finally:
        db.close()
    assert "refill.requested" in actions


def test_it_links_to_an_existing_prescription_when_the_name_matches(clinic):
    """
    A link when we have one — case-insensitively, since the caller said it
    aloud and the agent transcribed it.
    """
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)
    rx = client.post(
        f"/patients/{pid}/prescriptions",
        json={"name": "Metformin", "detail": "500mg twice daily"},
        headers=admin,
    )
    assert rx.status_code in (200, 201), rx.text
    rx_id = rx.json()["id"]

    body = _request_refill(client, key, pid, medication="metformin").json()
    assert body["prescription_id"] == rx_id


def test_an_unknown_medicine_is_recorded_with_no_link_rather_than_guessed(clinic):
    """
    Null says honestly "they named something we have no record of", which
    is worth seeing at review time. A fuzzy match would be a clinical
    guess presented as a fact.
    """
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)
    client.post(
        f"/patients/{pid}/prescriptions",
        json={"name": "Metformin"}, headers=admin,
    )

    body = _request_refill(client, key, pid, medication="Amoxicillin").json()
    assert body["prescription_id"] is None
    assert body["medication"] == "Amoxicillin"


def test_staff_can_approve_and_it_records_who_and_when(clinic):
    client, Testing, admin, key, org_id = clinic
    pid = _patient(client, admin)
    refill_id = _request_refill(client, key, pid).json()["id"]

    r = client.patch(
        f"/refills/{refill_id}",
        json={"status": "approved", "note": "Issued for 3 months"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["handled_at"] is not None

    db = Testing()
    try:
        row = db.get(models.RefillRequest, refill_id)
        actions = [
            e.action for e in db.query(models.AuditLogEntry)
            .filter(models.AuditLogEntry.org_id == org_id).all()
        ]
    finally:
        db.close()
    assert row.handled_by_user_id is not None
    assert "refill.handled" in actions


def test_a_handled_request_cannot_be_decided_twice(clinic):
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)
    refill_id = _request_refill(client, key, pid).json()["id"]

    assert client.patch(
        f"/refills/{refill_id}", json={"status": "approved"}, headers=admin
    ).status_code == 200

    second = client.patch(
        f"/refills/{refill_id}", json={"status": "declined"}, headers=admin
    )
    assert second.status_code == 400
    assert "already been approved" in second.json()["detail"]


def test_the_queue_shows_outstanding_requests_oldest_first(clinic):
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)
    first = _request_refill(client, key, pid, medication="Aspirin").json()["id"]
    _request_refill(client, key, pid, medication="Metformin")

    queue = client.get("/refills", headers=admin).json()
    assert [x["medication"] for x in queue] == ["Aspirin", "Metformin"]

    client.patch(f"/refills/{first}", json={"status": "approved"}, headers=admin)

    # Handled requests leave the to-do list but remain retrievable.
    assert [x["medication"] for x in client.get("/refills", headers=admin).json()] == ["Metformin"]
    approved = client.get("/refills", params={"status": "approved"}, headers=admin).json()
    assert [x["medication"] for x in approved] == ["Aspirin"]


def test_the_queue_carries_the_patient_name_for_review(clinic):
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin, name="Anitha Rao")
    _request_refill(client, key, pid)

    assert client.get("/refills", headers=admin).json()[0]["patient_name"] == "Anitha Rao"


# ============================================================
# VALIDATION
# ============================================================

def test_a_blank_medication_is_rejected():
    with pytest.raises(ValidationError):
        schemas.RefillRequestCreate(patient_id="p1", medication="   ")
    with pytest.raises(ValidationError):
        schemas.RefillRequestCreate(patient_id="p1", medication="")


def test_only_known_statuses_are_accepted():
    for good in schemas.REFILL_STATUSES:
        assert schemas.RefillRequestUpdate(status=good).status == good
    for bad in ("pending ", "APPROVED", "cancelled", "done"):
        with pytest.raises(ValidationError):
            schemas.RefillRequestUpdate(status=bad)


def test_an_unverifiable_call_id_is_dropped_not_stored(clinic):
    """A dangling reference on a medication record is worse than none."""
    client, _T, admin, key, _org = clinic
    pid = _patient(client, admin)

    body = _request_refill(client, key, pid, call_id="not-a-real-call").json()
    assert body["call_id"] is None
