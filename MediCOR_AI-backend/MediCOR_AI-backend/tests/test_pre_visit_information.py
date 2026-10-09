"""
Pre-visit information (PF-13).

Everything reception would otherwise collect at the desk while other
patients wait. The whole design turns on one decision, and most of these
tests are about it:

**IT IS A SUBMISSION, NOT AN EDIT.** Nothing a patient types here reaches
the patient record. Two independent reasons, either sufficient:

  * **Security.** It is reached with the appointment-link token, which is
    unauthenticated by design. If that token could change a phone number,
    a forwarded or leaked link becomes an account-takeover path: redirect
    the messages, then use inbound replies (PF-07) to cancel or book.

  * **Clinical correctness.** A patient-reported medication list is not a
    verified one, and writing it into a medical record would put
    unverified clinical text where verified text is expected.

    pytest tests/test_pre_visit_information.py -v
"""

import os
import sys
from datetime import datetime, timedelta

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
from app.routers import appointment_link as link_router  # noqa: E402
from app.routers import appointments as appointments_router  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.security import generate_appointment_link_token  # noqa: E402

REAL_PHONE = "+919000000001"
REAL_EMAIL = "real@example.com"


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(link_router.router)
    app.include_router(appointments_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]
        yield client, Session, admin, org_id


def _seed(Session, org_id):
    db = Session()
    try:
        p = models.Patient(
            org_id=org_id, mrn="M1", name="Anitha Rao",
            phone=REAL_PHONE, email=REAL_EMAIL,
        )
        db.add(p)
        db.flush()
        token, token_hash = generate_appointment_link_token()
        start = (datetime.now() + timedelta(days=3)).replace(hour=10, minute=0,
                                                             second=0, microsecond=0)
        a = models.Appointment(
            org_id=org_id, patient_id=p.id, title="Consultation", status="upcoming",
            start_at=start, end_at=start + timedelta(minutes=30),
            day_label="Fri, Sep 11", time_label="10:00 AM",
            link_token_hash=token_hash,
            link_expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(a)
        db.commit()
        return token, a.id, p.id
    finally:
        db.close()


def _patient(Session, pid):
    db = Session()
    try:
        return db.query(models.Patient).filter(models.Patient.id == pid).first()
    finally:
        db.close()


FULL = {
    "visit_reason": "Persistent cough for two weeks",
    "medications": "Metformin 500mg, Lisinopril",
    "allergies": "Penicillin",
    "notes": "Prefer a morning appointment",
    "phone": "+919999999999",
    "email": "attacker@example.com",
}


# ================================================== the central boundary

def test_a_submission_never_touches_the_patient_record(ctx):
    """
    The load-bearing test. If this ever fails, a forwarded appointment
    link has become a way to redirect somebody's messages.
    """
    client, S, _admin, org = ctx
    token, _a, pid = _seed(S, org)

    r = client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)
    assert r.status_code == 200, r.text

    p = _patient(S, pid)
    assert p.phone == REAL_PHONE, "the link changed the patient's phone number"
    assert p.email == REAL_EMAIL, "the link changed the patient's email"


def test_contact_changes_are_stored_as_proposals_only(ctx):
    client, S, admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)

    staff = client.get(f"/appointments/{appt_id}/pre-visit", headers=admin).json()
    # Named `proposed_` so no future reader mistakes them for the record.
    assert staff["proposed_phone"] == "+919999999999"
    assert staff["proposed_email"] == "attacker@example.com"


def test_the_patient_view_does_not_echo_back_contact_details(ctx):
    """
    A link that may have been forwarded has no reason to read a phone
    number back out. The patient sees what they said about the visit.
    """
    client, S, _admin, org = ctx
    token, _a, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)

    body = client.get("/appointment-link/pre-visit", params={"token": token}).json()
    blob = str(body)
    assert "attacker@example.com" not in blob
    assert "+919999999999" not in blob
    assert "ATTACKER-CONTROLLED" not in blob
    assert body["visit_reason"] == FULL["visit_reason"]


def test_the_summary_view_still_leaks_nothing(ctx):
    """
    PF-06's boundary must survive the new field. `pre_visit_submitted` is
    a boolean, not the content.
    """
    client, S, _admin, org = ctx
    token, _a, pid = _seed(S, org)
    before = client.get("/appointment-link", params={"token": token}).json()
    assert before["pre_visit_submitted"] is False

    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)
    after = client.get("/appointment-link", params={"token": token}).json()
    assert after["pre_visit_submitted"] is True

    blob = str(after)
    assert pid not in blob
    assert FULL["phone"] not in blob, "the summary view leaked a contact detail"
    assert "Metformin" not in blob, "the summary view leaked clinical text"


# =========================================================== the workflow

def test_a_correction_replaces_rather_than_duplicating(ctx):
    """
    A patient correcting themselves must not leave two versions for a
    nurse to reconcile at the desk.
    """
    client, S, admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token},
                json={"medications": "Metformin"})
    client.post("/appointment-link/pre-visit", params={"token": token},
                json={"medications": "Metformin and Lisinopril"})

    db = S()
    try:
        rows = db.query(models.PreVisitSubmission).filter(
            models.PreVisitSubmission.appointment_id == appt_id).all()
        assert len(rows) == 1
        assert rows[0].medications == "Metformin and Lisinopril"
    finally:
        db.close()


def test_a_correction_marks_it_unread_again(ctx):
    """New information means staff should look again."""
    client, S, admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token},
                json={"medications": "Metformin"})
    client.get(f"/appointments/{appt_id}/pre-visit", headers=admin)  # read it

    client.post("/appointment-link/pre-visit", params={"token": token},
                json={"medications": "Metformin, Lisinopril"})
    staff = client.get(f"/appointments/{appt_id}/pre-visit", headers=admin).json()
    assert staff["submitted_at"] is not None


def test_reading_it_is_audited_as_a_clinical_record_access(ctx):
    client, S, admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)
    client.get(f"/appointments/{appt_id}/pre-visit", headers=admin)

    db = S()
    try:
        entry = (
            db.query(models.AuditLogEntry)
            .filter(models.AuditLogEntry.action == "patient.history_viewed")
            .first()
        )
        assert entry is not None, "reading a patient's medication list was not audited"
    finally:
        db.close()


def test_the_audit_records_field_names_never_values(ctx):
    """
    A member id or a medication list in an audit detail would put clinical
    and financial text in a table with longer retention and a wider
    audience than the record it describes.
    """
    client, S, admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)

    db = S()
    try:
        details = " ".join(
            e.detail or "" for e in db.query(models.AuditLogEntry).all()
        )
    finally:
        db.close()
    for secret in ("Metformin", "Penicillin", "ATTACKER-CONTROLLED",
                   "attacker@example.com", "+919999999999"):
        assert secret not in details, f"the audit log leaked {secret!r}"


def test_the_submission_is_attributed_to_the_patient(ctx):
    client, S, admin, org = ctx
    token, _a, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)
    db = S()
    try:
        entry = (
            db.query(models.AuditLogEntry)
            .filter(models.AuditLogEntry.action == "patient.record_changed")
            .first()
        )
        assert entry is not None
        assert entry.actor_type == "patient"
    finally:
        db.close()


# ============================================================ boundaries

def test_a_dead_link_cannot_submit(ctx):
    client, S, _admin, org = ctx
    _t, _a, _p = _seed(S, org)
    r = client.post("/appointment-link/pre-visit",
                    params={"token": "not-a-real-token"}, json=FULL)
    assert r.status_code == 404


def test_the_staff_view_needs_a_staff_session(ctx):
    client, S, _admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    client.post("/appointment-link/pre-visit", params={"token": token}, json=FULL)
    assert client.get(f"/appointments/{appt_id}/pre-visit").status_code in (401, 403)


def test_nothing_submitted_reads_as_not_found_for_staff(ctx):
    client, S, admin, org = ctx
    _t, appt_id, _p = _seed(S, org)
    assert client.get(f"/appointments/{appt_id}/pre-visit", headers=admin).status_code == 404


def test_overlong_input_is_refused_rather_than_stored(ctx):
    """
    This arrives from an unauthenticated endpoint; "a patient typed too
    much" should be a validation error, not a very large row.
    """
    client, S, _admin, org = ctx
    token, _a, _p = _seed(S, org)
    r = client.post("/appointment-link/pre-visit", params={"token": token},
                    json={"medications": "x" * 5000})
    assert r.status_code == 422


def test_unicode_survives(ctx):
    """
    The free-text columns are UNICODE_TEXT. This schema has already lost
    one set of transcripts to a non-Unicode column -- see 0012 and 0021.
    """
    client, S, admin, org = ctx
    token, appt_id, _p = _seed(S, org)
    devanagari = "खाँसी दो हफ्ते"
    client.post("/appointment-link/pre-visit", params={"token": token},
                json={"visit_reason": devanagari})
    staff = client.get(f"/appointments/{appt_id}/pre-visit", headers=admin).json()
    assert staff["visit_reason"] == devanagari
    assert "?" not in staff["visit_reason"]
