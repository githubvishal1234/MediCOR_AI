"""
Patient self-service link (PF-06) and attendance confirmation (PF-08).

This is the only unauthenticated router in the product that acts on
clinical records, so most of these tests are about the boundary rather
than the happy path.

What is pinned here:

  * **The link exposes one appointment and nothing else.** The response
    shape is the security boundary of the whole feature. If someone
    switches it to `from_attributes` for convenience, adding a column to
    `Appointment` would silently start leaking it to an anonymous caller —
    so the absence of every sensitive field is asserted explicitly.

  * **Every failure looks identical.** Unknown, expired, cancelled and
    suspended all return the same 404. Distinguishing them would make this
    an oracle for guessing tokens.

  * **A link cannot reach a second appointment.** Not the patient's other
    bookings, not another patient's, not another clinic's.

  * **The clinic's scheduling rules still apply.** A patient rescheduling
    through a link gets the same past/working-hours/conflict validation as
    staff, because it calls the same validator.

    pytest tests/test_appointment_self_service_link.py -v
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
from app.routers import auth as auth_router  # noqa: E402
from app.security import generate_appointment_link_token  # noqa: E402


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
    app.include_router(auth_router.router)
    app.include_router(link_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]
        yield client, Testing, org_id


def _seed(Session, org_id, *, when=None, status="upcoming", expired=False, doctor=True):
    """One patient, one doctor, one appointment with a live link."""
    db = Session()
    try:
        p = models.Patient(
            org_id=org_id, mrn="MRN1", name="Anitha Rao",
            phone="+919000000001", email="a@example.com", dob="1985-04-02",
        )
        db.add(p)
        d = None
        if doctor:
            d = models.Doctor(
                org_id=org_id, name="Dr Meera Iyer", specialty="GP",
                slot_minutes=30, work_days_csv="0,1,2,3,4",
                work_start_hour=9, work_end_hour=17, active=True,
            )
            db.add(d)
        db.flush()

        start = when or (datetime.now() + timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        token, token_hash = generate_appointment_link_token()
        a = models.Appointment(
            org_id=org_id, patient_id=p.id, doctor_id=(d.id if d else None),
            title="Consultation", patient_name=p.name, status=status,
            start_at=start, end_at=start + timedelta(minutes=30),
            day_label="Fri, Sep 11", time_label="10:00 AM", location="Room 2",
            link_token_hash=token_hash,
            link_expires_at=(
                datetime.utcnow() - timedelta(hours=1) if expired
                else datetime.utcnow() + timedelta(days=7)
            ),
        )
        db.add(a)
        db.commit()
        return token, a.id, p.id, (d.id if d else None)
    finally:
        db.close()


# ------------------------------------------------------------ the view

def test_the_link_shows_the_appointment(ctx):
    client, S, org = ctx
    token, _a, _p, _d = _seed(S, org)
    r = client.get("/appointment-link", params={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clinic_name"] == "Northside Clinic"
    assert body["patient_first_name"] == "Anitha"
    assert body["doctor_name"] == "Dr Meera Iyer"
    assert body["day_label"] == "Fri, Sep 11"
    assert body["can_reschedule"] is True


def test_the_link_exposes_nothing_beyond_that_one_appointment(ctx):
    """
    The response shape is the security boundary. A leaked or forwarded
    link must not become a window onto the patient's record.
    """
    client, S, org = ctx
    token, _a, patient_id, _d = _seed(S, org)
    body = client.get("/appointment-link", params={"token": token}).json()

    # Nothing identifying beyond a first name.
    for forbidden in (
        "patient_id", "mrn", "phone", "email", "dob", "age",
        "prescriptions",
        "interactions", "appointments", "id", "org_id",
    ):
        assert forbidden not in body, f"{forbidden} leaked through the patient link"

    # And no value anywhere in the payload betrays the record.
    blob = str(body)
    assert patient_id not in blob
    assert "SH-99887766-X" not in blob
    assert "1985-04-02" not in blob
    assert "+919000000001" not in blob
    assert "Rao" not in blob, "surname leaked; only the first name may be shown"


def test_the_response_shape_is_explicit_not_derived():
    """
    If this is ever switched to `from_attributes`, a new column on
    Appointment starts flowing to anonymous callers the day it is added.
    """
    cfg = getattr(schemas.LinkAppointmentOut, "model_config", {})
    assert not cfg.get("from_attributes"), (
        "LinkAppointmentOut must stay an explicit field list"
    )


# --------------------------------------------------------- the failures

@pytest.mark.parametrize("case", ["unknown", "expired", "cancelled", "suspended"])
def test_every_failure_looks_the_same(ctx, case):
    client, S, org = ctx
    if case == "unknown":
        token = "not-a-real-token-value"
    elif case == "expired":
        token, _a, _p, _d = _seed(S, org, expired=True)
    elif case == "cancelled":
        token, _a, _p, _d = _seed(S, org, status="cancelled")
    else:
        token, _a, _p, _d = _seed(S, org)
        db = S()
        try:
            o = db.query(models.Organization).filter(models.Organization.id == org).first()
            o.suspended = True
            db.commit()
        finally:
            db.close()

    r = client.get("/appointment-link", params={"token": token})
    assert r.status_code == 404, f"{case}: {r.status_code}"
    # Identical wording, so the endpoint cannot be used to tell a real
    # token from a fake one.
    assert r.json()["detail"] == "This link is no longer valid. Please call the clinic."


def test_a_missing_or_blank_token_is_refused(ctx):
    client, _S, _org = ctx
    assert client.get("/appointment-link").status_code == 422
    assert client.get("/appointment-link", params={"token": "   "}).status_code == 404


# ------------------------------------------------------- confirmation

def test_confirming_records_who_and_how(ctx):
    client, S, org = ctx
    token, appt_id, _p, _d = _seed(S, org)

    r = client.post("/appointment-link/confirm", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["confirmation_status"] == "confirmed"

    db = S()
    try:
        a = db.query(models.Appointment).filter(models.Appointment.id == appt_id).first()
        assert a.confirmation_status == "confirmed"
        assert a.confirmed_at is not None
        assert a.confirmation_source == "link"

        entry = (
            db.query(models.AuditLogEntry)
            .filter(models.AuditLogEntry.action == "appointment.confirmed")
            .first()
        )
        assert entry is not None, "confirmation was not audited"
        # Attributed to the patient, not to staff and not to the AI.
        assert entry.actor_type == "patient"
    finally:
        db.close()


def test_confirming_twice_is_not_an_error(ctx):
    client, S, org = ctx
    token, _a, _p, _d = _seed(S, org)
    assert client.post("/appointment-link/confirm", json={"token": token}).status_code == 200
    assert client.post("/appointment-link/confirm", json={"token": token}).status_code == 200


# ---------------------------------------------------------- cancelling

def test_cancelling_releases_the_slot_and_retires_the_link(ctx):
    client, S, org = ctx
    token, appt_id, _p, _d = _seed(S, org)

    r = client.post("/appointment-link/cancel", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    db = S()
    try:
        a = db.query(models.Appointment).filter(models.Appointment.id == appt_id).first()
        assert a.status == "cancelled"
        # The link has no remaining purpose, so it stops being a credential.
        assert a.link_token_hash is None
        assert a.link_expires_at is None
    finally:
        db.close()

    # And the retired link is indistinguishable from any other dead one.
    again = client.get("/appointment-link", params={"token": token})
    assert again.status_code == 404


# -------------------------------------------------------- rescheduling

def test_rescheduling_through_a_link_obeys_the_clinics_rules(ctx):
    """
    The link is not a shortcut past scheduling validation. It calls the
    same `_validate_slot` the staff and agent paths call.
    """
    client, S, org = ctx
    token, _a, _p, _d = _seed(S, org)

    # In the past.
    past = (datetime.now() - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = client.post(
        "/appointment-link/reschedule",
        json={"token": token, "start_at": past.isoformat()},
    )
    assert r.status_code == 400, r.text
    assert "past" in r.json()["detail"].lower()

    # Outside the doctor's hours.
    late = (datetime.now() + timedelta(days=3)).replace(hour=22, minute=0, second=0, microsecond=0)
    r = client.post(
        "/appointment-link/reschedule",
        json={"token": token, "start_at": late.isoformat()},
    )
    assert r.status_code == 400, r.text


def test_rescheduling_retracts_an_earlier_confirmation(ctx):
    """
    They confirmed the old time, not the new one. Carrying the
    confirmation across would tell the clinic something the patient never
    said.
    """
    client, S, org = ctx
    token, appt_id, _p, _d = _seed(S, org)
    client.post("/appointment-link/confirm", json={"token": token})

    # Next Wednesday at 11:00, inside working hours.
    new_time = datetime.now() + timedelta(days=4)
    while new_time.weekday() > 4:
        new_time += timedelta(days=1)
    new_time = new_time.replace(hour=11, minute=0, second=0, microsecond=0)

    r = client.post(
        "/appointment-link/reschedule",
        json={"token": token, "start_at": new_time.isoformat()},
    )
    assert r.status_code == 200, r.text
    assert r.json()["confirmation_status"] is None

    db = S()
    try:
        a = db.query(models.Appointment).filter(models.Appointment.id == appt_id).first()
        assert a.confirmation_status is None
        assert a.confirmed_at is None
    finally:
        db.close()


def test_a_link_cannot_reach_another_appointment(ctx):
    """
    One token, one appointment. Not the patient's other bookings, and not
    anybody else's.
    """
    client, S, org = ctx
    token_a, appt_a, _p, _d = _seed(S, org)
    token_b, appt_b, _p2, _d2 = _seed(S, org)

    body_a = client.get("/appointment-link", params={"token": token_a}).json()
    body_b = client.get("/appointment-link", params={"token": token_b}).json()
    assert body_a["status"] == body_b["status"]  # both live

    # Cancelling through one must not touch the other.
    client.post("/appointment-link/cancel", json={"token": token_a})
    db = S()
    try:
        other = db.query(models.Appointment).filter(models.Appointment.id == appt_b).first()
        assert other.status == "upcoming"
        assert other.link_token_hash is not None
    finally:
        db.close()


def test_availability_is_for_this_appointments_doctor_only(ctx):
    """
    The doctor comes from the appointment, never from the request, so a
    link holder cannot enumerate the clinic's other clinicians.
    """
    client, S, org = ctx
    token, _a, _p, _d = _seed(S, org)
    day = datetime.now() + timedelta(days=3)
    while day.weekday() > 4:
        day += timedelta(days=1)

    r = client.get(
        "/appointment-link/availability",
        params={"token": token, "date": day.strftime("%Y-%m-%d")},
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    # No doctor_id parameter is accepted at all.
    r2 = client.get(
        "/appointment-link/availability",
        params={"token": token, "date": day.strftime("%Y-%m-%d"), "doctor_id": "someone-else"},
    )
    assert r2.status_code == 200
    assert r2.json() == r.json()


def test_an_appointment_without_a_doctor_cannot_be_moved_online(ctx):
    client, S, org = ctx
    token, _a, _p, _d = _seed(S, org, doctor=False)
    body = client.get("/appointment-link", params={"token": token}).json()
    assert body["can_reschedule"] is False

    when = (datetime.now() + timedelta(days=3)).isoformat()
    r = client.post("/appointment-link/reschedule", json={"token": token, "start_at": when})
    assert r.status_code == 400


# --------------------------------------------------------- rate limiting

def test_the_public_endpoint_is_rate_limited(ctx, monkeypatch):
    """
    Public, unauthenticated, and it takes a secret as a parameter. Without
    a limit it is a token-guessing oracle with unlimited attempts.
    """
    from app.config import settings

    client, _S, _org = ctx
    monkeypatch.setattr(settings, "appointment_link_rate_limit_attempts", 3)
    codes = [
        client.get("/appointment-link", params={"token": f"guess-{i}"}).status_code
        for i in range(5)
    ]
    assert 429 in codes, codes
