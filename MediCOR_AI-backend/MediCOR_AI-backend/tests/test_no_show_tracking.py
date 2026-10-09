"""
No-show tracking (F4).

Until `no_show` existed, a patient who simply did not arrive was
indistinguishable from an appointment nobody got round to updating. The
status vocabulary was `upcoming | done | cancelled`, so the no-show rate —
the number a clinic buys this product to reduce — could not be computed
at all.

Two things these tests exist to pin:

  * **The denominator.** No-shows are counted against appointments that
    concluded (done + no_show). Cancellations are excluded: the patient
    told you, which is a different and much cheaper event. Folding them
    in dilutes the rate toward zero and hides a real problem.

  * **The timezone.** `Appointment.start_at` is clinic wall-clock naive
    while `resolve_range` hands back UTC bounds. Mixing the two is the
    documented bug class in app/timezones.py, and here it would file a
    UTC+5:30 clinic's early-morning appointments under the previous day.

    pytest tests/test_no_show_tracking.py -v
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
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import analytics_service, models, rate_limit, schemas, timezones  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import appointments as appointments_router  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402


# ---------------------------------------------------------------- service

@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def org(db):
    o = models.Organization(name="Northside Clinic", timezone="UTC")
    db.add(o)
    db.commit()
    return o


_n = [0]


def _appt(db, org, status="done", start_at=None):
    _n[0] += 1
    a = models.Appointment(
        id=f"a{_n[0]}",
        org_id=org.id,
        title="Consultation",
        patient_name=f"Patient {_n[0]}",
        status=status,
        start_at=start_at if start_at is not None else datetime.utcnow(),
    )
    db.add(a)
    db.commit()
    return a


def test_the_rate_is_none_not_zero_when_nothing_has_concluded(db, org):
    """
    "Nobody missed an appointment" and "nothing has happened yet" are
    different facts. A zero reads as the first. Same reasoning the
    resolution rate already follows.
    """
    for _ in range(3):
        _appt(db, org, status="upcoming")

    out = analytics_service.appointment_outcomes(db, org, days=30)
    assert out["appointments_scheduled"] == 3
    assert out["no_show_rate_pct"] is None


def test_cancellations_are_not_counted_as_no_shows(db, org):
    """
    The denominator is done + no_show. Cancelled is excluded — the
    patient told the clinic. Were cancellations folded in, this case
    would report 20% instead of the true 50%.
    """
    _appt(db, org, status="done")
    _appt(db, org, status="no_show")
    for _ in range(3):
        _appt(db, org, status="cancelled")

    out = analytics_service.appointment_outcomes(db, org, days=30)
    assert out["appointments_completed"] == 1
    assert out["appointments_no_show"] == 1
    assert out["appointments_cancelled"] == 3
    assert out["no_show_rate_pct"] == 50.0


def test_upcoming_appointments_do_not_dilute_the_rate(db, org):
    _appt(db, org, status="no_show")
    _appt(db, org, status="done")
    for _ in range(10):
        _appt(db, org, status="upcoming")

    out = analytics_service.appointment_outcomes(db, org, days=30)
    assert out["no_show_rate_pct"] == 50.0


def test_appointments_are_bucketed_in_the_clinics_timezone_not_utc(db):
    """
    The bug this guards: `start_at` is clinic wall-clock naive, and
    `resolve_range` returns UTC bounds. Comparing the two shifts a
    UTC+5:30 clinic's day by 5.5 hours.

    A 09:00 appointment today, in Asia/Kolkata, must land inside a
    1-day range. Against UTC bounds it would fall out of it.
    """
    o = models.Organization(name="Kolkata Clinic", timezone="Asia/Kolkata")
    db.add(o)
    db.commit()

    tz = timezones.org_timezone(o)
    today_local = timezones.now_local(tz).date()
    nine_am_local = datetime.combine(today_local, datetime.min.time()) + timedelta(hours=9)

    _appt(db, o, status="no_show", start_at=nine_am_local)

    out = analytics_service.appointment_outcomes(db, o, days=1)
    assert out["appointments_scheduled"] == 1, (
        "a 09:00 local appointment fell outside today's range — start_at "
        "is being compared against UTC bounds"
    )
    assert out["no_show_rate_pct"] == 100.0


def test_appointments_without_a_start_time_are_not_counted(db, org):
    """Unschedulable shells cannot be placed on a day, so they are excluded."""
    _appt(db, org, status="no_show", start_at=None)
    db.query(models.Appointment).update({"start_at": None})
    db.commit()

    out = analytics_service.appointment_outcomes(db, org, days=30)
    assert out["appointments_scheduled"] == 0
    assert out["no_show_rate_pct"] is None


# ------------------------------------------------------------------ route

@pytest.fixture()
def api():
    rate_limit._attempts.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        s = TestingSessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(appointments_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]

        raw_key, prefix = generate_api_key()
        s = TestingSessionLocal()
        try:
            s.add(models.APIKey(
                org_id=org_id, label="Voice Agent",
                key_prefix=prefix, hashed_key=hash_api_key(raw_key),
            ))
            s.commit()
        finally:
            s.close()

        yield client, TestingSessionLocal, admin, {"X-API-Key": raw_key}, org_id


def _create(client, headers):
    r = client.post("/appointments", json={
        "title": "Consultation", "patient_name": "Sam Patient",
        "start_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
    }, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_marking_a_no_show_records_it_and_audits_who_did_it(api):
    client, Session, admin, _key, org_id = api
    appt_id = _create(client, admin)

    r = client.post(f"/appointments/{appt_id}/no-show", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "no_show"

    s = Session()
    try:
        rows = (
            s.query(models.AuditLogEntry)
            .filter(
                models.AuditLogEntry.org_id == org_id,
                models.AuditLogEntry.action == "appointment.no_show",
            )
            .all()
        )
    finally:
        s.close()
    assert len(rows) == 1
    assert rows[0].target_id == appt_id


def test_a_cancelled_appointment_cannot_also_be_a_no_show(api):
    """
    Both would double-count one slot and inflate the rate with
    appointments nobody actually missed.
    """
    client, _Session, admin, _key, _org = api
    appt_id = _create(client, admin)
    assert client.post(f"/appointments/{appt_id}/cancel", headers=admin).status_code == 200

    r = client.post(f"/appointments/{appt_id}/no-show", headers=admin)
    assert r.status_code == 400
    assert "cancelled" in r.json()["detail"].lower()


def test_marking_the_same_appointment_twice_is_rejected(api):
    client, _Session, admin, _key, _org = api
    appt_id = _create(client, admin)
    assert client.post(f"/appointments/{appt_id}/no-show", headers=admin).status_code == 200

    r = client.post(f"/appointments/{appt_id}/no-show", headers=admin)
    assert r.status_code == 400


def test_the_voice_agent_cannot_mark_a_no_show(api):
    """
    Staff-only by design. An AI deciding from a phone call that a patient
    did not turn up is a judgement it has no evidence for, so the route
    is `get_current_user` and the agent's API key must not reach it.
    """
    client, _Session, admin, key, _org = api
    appt_id = _create(client, admin)

    r = client.post(f"/appointments/{appt_id}/no-show", headers=key)
    assert r.status_code in (401, 403), r.text

    detail = client.get("/appointments", headers=admin).json()
    assert all(a["status"] != "no_show" for a in detail)


def test_a_missing_appointment_is_404_not_a_new_row(api):
    client, _Session, admin, _key, _org = api
    assert client.post("/appointments/does-not-exist/no-show", headers=admin).status_code == 404


# ------------------------------------------------------------- validation

def test_a_mistyped_status_is_rejected_rather_than_stored():
    """
    Before this, `status` took any string. An appointment PATCHed to
    "canceled" (one l) stayed visible in the calendar and kept blocking
    its slot, because every conflict query tests `status != "cancelled"`.
    """
    for good in schemas.APPOINTMENT_STATUSES:
        assert schemas.AppointmentUpdate(status=good).status == good

    for bad in ("canceled", "noshow", "No Show", "done "):
        with pytest.raises(ValidationError):
            schemas.AppointmentUpdate(status=bad)


def test_status_stays_optional_so_other_fields_can_be_patched_alone():
    assert schemas.AppointmentUpdate(title="Follow-up").status is None
