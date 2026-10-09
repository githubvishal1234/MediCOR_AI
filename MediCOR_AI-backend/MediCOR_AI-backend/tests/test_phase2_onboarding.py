"""
Phase 2 — self-serve signup and the onboarding checklist.

Registration deliberately creates an EMPTY clinic: no placeholder
doctors, services or fees, because the voice agent reads those straight
out to patients. These tests pin both halves of that decision — that
nothing is invented, and that what's missing is reported so it doesn't
stay silently missing.

    pytest tests/test_phase2_onboarding.py -v
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

from app import models, rate_limit
from app.database import Base, get_db
from app.routers import auth as auth_router
from app.routers import doctors as doctors_router
from app.routers import knowledge_base as kb_router
from app.routers import onboarding as onboarding_router


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
    for router in (
        auth_router.router,
        onboarding_router.router,
        doctors_router.router,
        kb_router.router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c, TestingSessionLocal

    rate_limit._attempts.clear()


def _signup(client, email="founder@clinic.example", timezone=None, org="New Clinic"):
    payload = {
        "org_name": org,
        "full_name": "Founder",
        "email": email,
        "password": "Sup3rSecret!",
    }
    if timezone is not None:
        payload["timezone"] = timezone
    return client.post("/auth/register", json=payload)


def _steps(client, headers):
    r = client.get("/onboarding/status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {s["key"]: s for s in body["steps"]}


# ---------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------

def test_signup_sets_the_clinics_timezone(ctx):
    client, session_factory = ctx
    r = _signup(client, timezone="Asia/Kolkata")
    assert r.status_code == 200, r.text

    db = session_factory()
    try:
        org = db.query(models.Organization).filter(models.Organization.name == "New Clinic").first()
        assert org.timezone == "Asia/Kolkata"
    finally:
        db.close()


def test_signup_without_a_timezone_defaults_to_utc(ctx):
    """Omitting it must not fail — the profile page can set it later."""
    client, session_factory = ctx
    assert _signup(client).status_code == 200

    db = session_factory()
    try:
        org = db.query(models.Organization).filter(models.Organization.name == "New Clinic").first()
        assert org.timezone == "UTC"
    finally:
        db.close()


def test_signup_rejects_an_unusable_timezone(ctx):
    """
    Better to fail signup than to silently seat the clinic on UTC —
    every slot it offered would be wrong by its own offset.
    """
    client, _ = ctx
    r = _signup(client, timezone="Mars/Olympus_Mons")
    assert r.status_code == 422
    assert "not a recognised timezone" in r.text


def test_signup_creates_no_placeholder_clinical_data(ctx):
    """
    The agent reads doctors, services and fees out to patients. A seeded
    "Dr. Placeholder" or invented consultation fee would be spoken to a
    real caller as fact, so registration must create none.
    """
    client, session_factory = ctx
    assert _signup(client).status_code == 200

    db = session_factory()
    try:
        org = db.query(models.Organization).filter(models.Organization.name == "New Clinic").first()
        assert db.query(models.Doctor).filter(models.Doctor.org_id == org.id).count() == 0
        assert db.query(models.Service).filter(models.Service.org_id == org.id).count() == 0
        assert db.query(models.FAQ).filter(models.FAQ.org_id == org.id).count() == 0
        assert db.query(models.Patient).filter(models.Patient.org_id == org.id).count() == 0

        # Structure, though, is created so nothing has to be lazily made later.
        assert db.query(models.ClinicInfo).filter(models.ClinicInfo.org_id == org.id).count() == 1
        assert db.query(models.AgentSettings).filter(models.AgentSettings.org_id == org.id).count() == 1

        info = db.query(models.ClinicInfo).filter(models.ClinicInfo.org_id == org.id).first()
        assert not (info.timings_text or info.address or info.phone), "clinic info was pre-filled"
    finally:
        db.close()


# ---------------------------------------------------------------------
# The checklist
# ---------------------------------------------------------------------

def test_a_brand_new_clinic_is_told_what_is_missing(ctx):
    client, _ = ctx
    token = _signup(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    body, steps = _steps(client, headers)
    assert body["complete"] is False
    for key in ("timezone", "doctors", "clinic_hours", "contact", "services", "phone_number"):
        assert steps[key]["done"] is False, f"{key} should be outstanding on a new clinic"

    # The one that actually blocks booking says so.
    assert "booked" in steps["doctors"]["detail"].lower()


def test_steps_flip_as_the_clinic_fills_things_in(ctx):
    client, session_factory = ctx
    token = _signup(client, timezone="Asia/Kolkata").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Timezone was set at signup.
    _, steps = _steps(client, headers)
    assert steps["timezone"]["done"] is True
    assert steps["doctors"]["done"] is False

    assert client.post(
        "/doctors", json={"name": "Dr. Real", "work_days": [0, 1, 2, 3, 4]}, headers=headers
    ).status_code == 201

    assert client.put(
        "/knowledge-base/clinic-info",
        json={"timings_text": "9am-5pm Mon-Fri", "phone": "+919876543210"},
        headers=headers,
    ).status_code == 200

    assert client.post(
        "/knowledge-base/services", json={"name": "Consultation"}, headers=headers
    ).status_code == 200

    _, steps = _steps(client, headers)
    for key in ("timezone", "doctors", "clinic_hours", "contact", "services"):
        assert steps[key]["done"] is True, f"{key} should be complete"
    # Only a super admin can assign a number, so this one stays open.
    assert steps["phone_number"]["done"] is False


def test_checklist_completes_once_a_number_is_routed(ctx):
    client, session_factory = ctx
    token = _signup(client, timezone="Asia/Kolkata").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/doctors", json={"name": "Dr. Real"}, headers=headers)
    client.put(
        "/knowledge-base/clinic-info",
        json={"timings_text": "9-5", "address": "1 Main Street"},
        headers=headers,
    )
    client.post("/knowledge-base/services", json={"name": "Consultation"}, headers=headers)

    db = session_factory()
    try:
        org = db.query(models.Organization).first()
        db.add(models.PhoneNumber(org_id=org.id, e164="+919876543210", active=True))
        db.commit()
    finally:
        db.close()

    body, _ = _steps(client, headers)
    assert body["complete"] is True, body


def test_inactive_doctors_do_not_count_as_setup(ctx):
    """A deactivated doctor cannot take bookings, so the step stays open."""
    client, _ = ctx
    token = _signup(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    doctor_id = client.post("/doctors", json={"name": "Dr. Temp"}, headers=headers).json()["id"]
    _, steps = _steps(client, headers)
    assert steps["doctors"]["done"] is True

    assert client.delete(f"/doctors/{doctor_id}", headers=headers).status_code == 204
    _, steps = _steps(client, headers)
    assert steps["doctors"]["done"] is False


def test_checklist_is_scoped_to_the_callers_own_clinic(ctx):
    client, _ = ctx
    token_a = _signup(client, email="a@a.example", org="Clinic A").json()["access_token"]
    token_b = _signup(client, email="b@b.example", org="Clinic B").json()["access_token"]

    client.post("/doctors", json={"name": "Dr. A"}, headers={"Authorization": f"Bearer {token_a}"})

    _, steps_a = _steps(client, {"Authorization": f"Bearer {token_a}"})
    _, steps_b = _steps(client, {"Authorization": f"Bearer {token_b}"})
    assert steps_a["doctors"]["done"] is True
    assert steps_b["doctors"]["done"] is False, "clinic B saw clinic A's doctor"


def test_checklist_requires_authentication(ctx):
    client, _ = ctx
    assert client.get("/onboarding/status").status_code == 401
