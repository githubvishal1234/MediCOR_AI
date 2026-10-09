"""
Phase 8 — Multi-tenant isolation regression test.

Covers the one confirmed cross-org IDOR found in this audit:
DELETE /patients/{patient_id}/prescriptions/{rx_id} deleted a
Prescription by rx_id alone, without checking that the prescription
actually belongs to patient_id. Because Prescription has no org_id of
its own, an authenticated user from Org B could delete Org A's
prescription simply by knowing/guessing its id and supplying any
*valid patient_id from their own org* in the URL (which passed the
existing org-ownership check for the patient, while the prescription
itself was never checked against that patient).

This test spins up a throwaway FastAPI app (patients + auth routers
only) against an in-memory SQLite database via dependency override —
it never touches the real SQL Server configured in app/config.py, so
it's safe to run anywhere.

Run with:
    pip install -r requirements.txt
    pytest tests/test_phase8_tenant_isolation.py -v
"""

import os
import sys

# Required by app/config.py's Settings() even though this test never
# opens a real DB connection (get_db is overridden below).
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
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, get_db
from app.routers import appointments as appointments_router
from app.routers import auth as auth_router
from app.routers import patients as patients_router
from app import rate_limit


@pytest.fixture()
def client():
    # rate_limit's attempt counter is module-level/in-process; clear it so
    # one test's register/login calls never trip another test's 429.
    rate_limit._attempts.clear()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # TestClient dispatches sync endpoints onto a threadpool. Without a
        # StaticPool, SQLAlchemy hands each thread its own connection - and
        # each connection to ":memory:" is a SEPARATE, empty database, so
        # every request 500s with "no such table". Pin the whole engine to
        # one shared connection for the lifetime of the test.
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
    app.include_router(auth_router.router)
    app.include_router(patients_router.router)
    app.include_router(appointments_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        # Exposed so tests can insert rows (e.g. doctors) that have no
        # write endpoint yet, without standing up a second engine.
        c.session_factory = TestingSessionLocal
        yield c


def _register(client, org_name, email):
    r = client.post(
        "/auth/register",
        json={
            "org_name": org_name,
            "email": email,
            "password": "Sup3rSecret!",
            "full_name": "Test User",
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_patient(client, headers, name="Jane Doe"):
    r = client.post("/patients", json={"name": name, "mrn": None}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_cross_org_prescription_delete_is_blocked(client):
    """
    Org A creates a patient + prescription. Org B (a completely
    different clinic, different user, different org_id) must NOT be
    able to delete Org A's prescription — even if Org B supplies a
    valid patient_id belonging to *its own* org in the URL, and even
    if it correctly guesses/obtains Org A's prescription id.
    """
    org_a_headers = _register(client, "Org A Clinic", "admin@orga.example")
    org_b_headers = _register(client, "Org B Clinic", "admin@orgb.example")

    # Org A: create a patient and a prescription for them.
    patient_a_id = _create_patient(client, org_a_headers, "Alice A")
    r = client.post(
        f"/patients/{patient_a_id}/prescriptions",
        json={"name": "Lisinopril", "detail": "10mg daily"},
        headers=org_a_headers,
    )
    assert r.status_code == 200, r.text
    rx_a_id = r.json()["id"]

    # Org B: create their own patient (needed to pass the URL's
    # patient_id ownership check with a patient_id Org B actually owns).
    patient_b_id = _create_patient(client, org_b_headers, "Bob B")

    # Attack: Org B tries to delete Org A's prescription by pairing
    # its own (valid) patient_id with Org A's prescription id.
    r = client.delete(
        f"/patients/{patient_b_id}/prescriptions/{rx_a_id}",
        headers=org_b_headers,
    )
    assert r.status_code == 204  # endpoint is intentionally idempotent/silent

    # Verify Org A's prescription still exists (the vulnerable version
    # of this endpoint would have deleted it here).
    r = client.get(f"/patients/{patient_a_id}", headers=org_a_headers)
    assert r.status_code == 200, r.text
    rx_ids = [rx["id"] for rx in r.json()["prescriptions"]]
    assert rx_a_id in rx_ids, "Org B was able to delete a prescription belonging to Org A (IDOR)"


def test_same_org_prescription_delete_still_works(client):
    """Sanity check: the fix must not break legitimate same-org deletes."""
    headers = _register(client, "Org C Clinic", "admin@orgc.example")
    patient_id = _create_patient(client, headers, "Carol C")
    r = client.post(
        f"/patients/{patient_id}/prescriptions",
        json={"name": "Metformin", "detail": "500mg twice daily"},
        headers=headers,
    )
    rx_id = r.json()["id"]

    r = client.delete(f"/patients/{patient_id}/prescriptions/{rx_id}", headers=headers)
    assert r.status_code == 204

    r = client.get(f"/patients/{patient_id}", headers=headers)
    rx_ids = [rx["id"] for rx in r.json()["prescriptions"]]
    assert rx_id not in rx_ids


def test_cross_org_patient_read_is_blocked(client):
    """Baseline (already-correct) isolation check: GET /patients/{id} 404s cross-org."""
    org_a_headers = _register(client, "Org D Clinic", "admin@orgd.example")
    org_b_headers = _register(client, "Org E Clinic", "admin@orge.example")

    patient_a_id = _create_patient(client, org_a_headers, "Dave D")

    r = client.get(f"/patients/{patient_a_id}", headers=org_b_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------
# Phase 0 — POST /appointments cross-tenant reference injection
# ---------------------------------------------------------------------
# create_appointment used to do `Appointment(org_id=user.org_id,
# **payload.model_dump())`. `patient_id` and `doctor_id` came straight
# from the request body and were never org-scoped, so org B could pin
# org A's patient and doctor onto a row inside its own tenant.


def _create_doctor(client, org_id, name="Dr. Test"):
    """Doctors have no write endpoint until Phase 1 — insert directly."""
    db = client.session_factory()
    try:
        doctor = models.Doctor(org_id=org_id, name=name)
        db.add(doctor)
        db.commit()
        db.refresh(doctor)
        return doctor.id
    finally:
        db.close()


def _org_id_for(client, headers):
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["org_id"]


def test_cross_org_patient_id_on_appointment_create_is_blocked(client):
    org_a_headers = _register(client, "Org F Clinic", "admin@orgf.example")
    org_b_headers = _register(client, "Org G Clinic", "admin@orgg.example")

    patient_a_id = _create_patient(client, org_a_headers, "Frank F")

    # Org B books "its own" appointment but references Org A's patient.
    r = client.post(
        "/appointments",
        json={"title": "Stolen reference", "patient_id": patient_a_id},
        headers=org_b_headers,
    )
    assert r.status_code == 404, (
        "Org B was able to attach Org A's patient_id to an appointment "
        f"(cross-tenant reference injection). Got {r.status_code}: {r.text}"
    )


def test_cross_org_doctor_id_on_appointment_create_is_blocked(client):
    org_a_headers = _register(client, "Org H Clinic", "admin@orgh.example")
    org_b_headers = _register(client, "Org I Clinic", "admin@orgi.example")

    doctor_a_id = _create_doctor(client, _org_id_for(client, org_a_headers), "Dr. Alpha")

    r = client.post(
        "/appointments",
        json={"title": "Stolen doctor", "doctor_id": doctor_a_id},
        headers=org_b_headers,
    )
    assert r.status_code == 404, (
        "Org B was able to attach Org A's doctor_id to an appointment. "
        f"Got {r.status_code}: {r.text}"
    )


def test_same_org_appointment_create_still_works(client):
    """The fix must not break ordinary same-org appointment creation."""
    headers = _register(client, "Org J Clinic", "admin@orgj.example")
    org_id = _org_id_for(client, headers)

    patient_id = _create_patient(client, headers, "Jill J")
    doctor_id = _create_doctor(client, org_id, "Dr. Juliet")

    r = client.post(
        "/appointments",
        json={
            "title": "Annual physical",
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "patient_name": "Jill J",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patient_id"] == patient_id
    assert body["doctor_id"] == doctor_id


def test_appointment_create_without_references_still_works(client):
    """Freeform label-only entries (no patient/doctor) are still allowed."""
    headers = _register(client, "Org K Clinic", "admin@orgk.example")
    r = client.post(
        "/appointments",
        json={"title": "Blocked: staff meeting"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
