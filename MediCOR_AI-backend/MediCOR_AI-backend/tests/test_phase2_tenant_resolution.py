"""
Phase 2 — tenant resolution and the org-scoped agent token.

The change that lets ONE voice-agent deployment serve many clinics.
Previously the worker held a single BACKEND_API_KEY, so every caller on
every number wrote into one organization.

What matters most here is the auth boundary. The agent token is a third
token family alongside staff and super-admin tokens, and it must be
usable for exactly one thing: acting as one clinic through
`get_org_context`. It must never authenticate a staff endpoint, and the
platform key that mints it must never be usable as a tenant credential.

    pytest tests/test_phase2_tenant_resolution.py -v
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

from conftest import make_test_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, rate_limit
from app.config import settings
from app.database import Base, get_db
from app.routers import agent_runtime as agent_runtime_router
from app.routers import appointments as appointments_router
from app.routers import auth as auth_router
from app.routers import doctors as doctors_router
from app.routers import knowledge_base as kb_router
from app.routers import patients as patients_router
from app.security import create_access_token, create_agent_token

PLATFORM_KEY = "test-platform-key-do-not-use-in-production"


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()
    original_key = settings.agent_platform_key
    settings.agent_platform_key = PLATFORM_KEY

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
        agent_runtime_router.router,
        patients_router.router,
        doctors_router.router,
        appointments_router.router,
        kb_router.router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    with make_test_client(app, client=("10.1.2.3", 50000)) as c:
        yield c, TestingSessionLocal

    settings.agent_platform_key = original_key
    rate_limit._attempts.clear()


def _make_clinic(session_factory, name, number=None, timezone="Asia/Kolkata", greeting=None):
    db = session_factory()
    try:
        org = models.Organization(name=name, timezone=timezone)
        db.add(org)
        db.flush()
        db.add(
            models.User(
                org_id=org.id,
                email=f"admin-{org.id}@clinic.example",
                hashed_password="unused",
                full_name="Admin",
                role=models.UserRole.admin,
            )
        )
        if greeting is not None:
            db.add(models.AgentSettings(org_id=org.id, greeting_script=greeting))
        if number:
            db.add(models.PhoneNumber(org_id=org.id, e164=number, active=True))
        db.commit()
        return org.id
    finally:
        db.close()


def _resolve(client, number, key=PLATFORM_KEY):
    headers = {"X-Platform-Key": key} if key else {}
    return client.post("/agent/resolve-tenant", json={"dialed_number": number}, headers=headers)


# ---------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------

def test_dialled_number_resolves_to_its_clinic(ctx):
    client, session_factory = ctx
    org_id = _make_clinic(
        session_factory, "Bengaluru Clinic", "+919876543210", greeting="Namaste, Bengaluru Clinic."
    )

    r = _resolve(client, "+919876543210")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org_id"] == org_id
    assert body["org_name"] == "Bengaluru Clinic"
    assert body["timezone"] == "Asia/Kolkata"
    assert body["greeting_script"] == "Namaste, Bengaluru Clinic."
    assert body["access_token"] and body["expires_in_seconds"] > 0


def test_two_clinics_on_two_numbers_stay_separate(ctx):
    """The whole point of the phase: one deployment, two tenants, no bleed."""
    client, session_factory = ctx
    org_a = _make_clinic(session_factory, "Clinic A", "+911111111111")
    org_b = _make_clinic(session_factory, "Clinic B", "+922222222222")

    assert _resolve(client, "+911111111111").json()["org_id"] == org_a
    assert _resolve(client, "+922222222222").json()["org_id"] == org_b
    assert org_a != org_b


@pytest.mark.parametrize(
    "dialled",
    ["+919876543210", "919876543210", "tel:+919876543210", "sip:+919876543210@trunk.example", "+91 98765 43210"],
)
def test_number_shapes_sip_actually_sends_all_resolve(ctx, dialled):
    """
    SIP hands the same number over in several shapes depending on trunk
    and carrier. All of them must find the same clinic, or a call lands
    on the fallback tenant depending on carrier formatting.
    """
    client, session_factory = ctx
    org_id = _make_clinic(session_factory, "Shape Clinic", "+919876543210")
    r = _resolve(client, dialled)
    assert r.status_code == 200, f"{dialled!r} did not resolve: {r.text}"
    assert r.json()["org_id"] == org_id


def test_unknown_number_is_a_flat_404(ctx):
    client, session_factory = ctx
    _make_clinic(session_factory, "Known Clinic", "+911111111111")
    r = _resolve(client, "+915555555555")
    assert r.status_code == 404
    # No hint about whether the number exists but is inactive, belongs to
    # a suspended org, etc. — this endpoint must not enumerate tenants.
    # (Asserted against the raw body: the error envelope is added by
    # register_exception_handlers, which this minimal test app omits.)
    assert "clinic is configured" in r.text


def test_deactivated_number_stops_resolving(ctx):
    client, session_factory = ctx
    _make_clinic(session_factory, "Old Clinic", "+913333333333")
    db = session_factory()
    try:
        row = db.query(models.PhoneNumber).filter(models.PhoneNumber.e164 == "+913333333333").first()
        row.active = False
        db.commit()
    finally:
        db.close()
    assert _resolve(client, "+913333333333").status_code == 404


def test_suspended_org_is_refused(ctx):
    client, session_factory = ctx
    org_id = _make_clinic(session_factory, "Suspended Clinic", "+914444444444")
    db = session_factory()
    try:
        org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
        org.suspended = True
        db.commit()
    finally:
        db.close()
    assert _resolve(client, "+914444444444").status_code == 403


# ---------------------------------------------------------------------
# Platform key
# ---------------------------------------------------------------------

def test_resolution_requires_the_platform_key(ctx):
    client, session_factory = ctx
    _make_clinic(session_factory, "Guarded Clinic", "+916666666666")

    assert _resolve(client, "+916666666666", key=None).status_code == 401
    assert _resolve(client, "+916666666666", key="wrong-key").status_code == 401
    assert _resolve(client, "+916666666666").status_code == 200


def test_resolution_is_disabled_when_no_platform_key_is_configured(ctx):
    """
    With the key unset the endpoint refuses to run at all — that is the
    rollback switch. The agent treats 503 as "use the fallback key".
    """
    client, session_factory = ctx
    _make_clinic(session_factory, "Disabled Clinic", "+917777777777")
    settings.agent_platform_key = ""
    r = _resolve(client, "+917777777777", key="anything")
    assert r.status_code == 503


def test_platform_key_is_not_a_tenant_credential(ctx):
    """
    The platform key resolves tenants; it must not be usable to read a
    clinic's data directly.
    """
    client, session_factory = ctx
    _make_clinic(session_factory, "Data Clinic", "+918888888888")
    for headers in ({"X-Platform-Key": PLATFORM_KEY}, {"X-API-Key": PLATFORM_KEY}):
        assert client.get("/patients", headers=headers).status_code == 401


# ---------------------------------------------------------------------
# The agent token's auth boundary
# ---------------------------------------------------------------------

def _agent_headers(client, number):
    token = _resolve(client, number).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_agent_token_acts_as_its_own_clinic(ctx):
    client, session_factory = ctx
    org_a = _make_clinic(session_factory, "Clinic A", "+911111111111")
    _make_clinic(session_factory, "Clinic B", "+922222222222")

    headers_a = _agent_headers(client, "+911111111111")
    r = client.post("/patients", json={"name": "Agent Registered"}, headers=headers_a)
    assert r.status_code == 200, r.text
    patient_id = r.json()["id"]

    # It lands in clinic A, and clinic B's agent cannot see it.
    headers_b = _agent_headers(client, "+922222222222")
    assert client.get(f"/patients/{patient_id}", headers=headers_b).status_code == 404
    assert client.get(f"/patients/{patient_id}", headers=headers_a).status_code == 200

    db = session_factory()
    try:
        assert db.query(models.Patient).filter(models.Patient.id == patient_id).first().org_id == org_a
    finally:
        db.close()


def test_agent_token_cannot_authenticate_staff_endpoints(ctx):
    """
    The critical boundary. `decode_access_token` rejects any token
    carrying a `typ` claim, which is what stops an agent token reaching
    get_current_user / require_admin — the same barrier that keeps
    super-admin tokens out of clinic routes.
    """
    client, session_factory = ctx
    _make_clinic(session_factory, "Boundary Clinic", "+919999999999")
    headers = _agent_headers(client, "+919999999999")

    assert client.get("/auth/me", headers=headers).status_code == 401
    # Doctor writes are require_admin; a phone call must never create one.
    assert client.post("/doctors", json={"name": "Dr. Ghost"}, headers=headers).status_code == 401
    assert client.get("/doctors/all", headers=headers).status_code == 401


def test_staff_token_is_still_accepted_where_it_was(ctx):
    """Adding the agent path must not have disturbed staff auth."""
    client, session_factory = ctx
    org_id = _make_clinic(session_factory, "Staff Clinic", "+910000000001")
    db = session_factory()
    try:
        user = db.query(models.User).filter(models.User.org_id == org_id).first()
        staff_headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
    finally:
        db.close()

    assert client.get("/auth/me", headers=staff_headers).status_code == 200
    assert client.get("/patients", headers=staff_headers).status_code == 200
    assert client.get("/doctors/all", headers=staff_headers).status_code == 200


def test_agent_token_for_a_suspended_org_stops_working(ctx):
    """Suspending a clinic must cut off calls already in flight, not just new ones."""
    client, session_factory = ctx
    org_id = _make_clinic(session_factory, "Cutoff Clinic", "+910000000002")
    headers = _agent_headers(client, "+910000000002")
    assert client.get("/patients", headers=headers).status_code == 200

    db = session_factory()
    try:
        org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
        org.suspended = True
        db.commit()
    finally:
        db.close()

    assert client.get("/patients", headers=headers).status_code == 403


def test_forged_and_expired_agent_tokens_are_rejected(ctx):
    client, session_factory = ctx
    org_id = _make_clinic(session_factory, "Forgery Clinic", "+910000000003")

    # Expired.
    expired = create_agent_token(org_id, expires_minutes=-1)
    assert client.get("/patients", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    # Right shape, nonexistent org.
    bogus = create_agent_token("no-such-org", expires_minutes=60)
    assert client.get("/patients", headers={"Authorization": f"Bearer {bogus}"}).status_code == 401

    # Signed with the wrong secret.
    from jose import jwt
    forged = jwt.encode(
        {"sub": org_id, "org_id": org_id, "typ": "agent"}, "not-the-real-secret", algorithm="HS256"
    )
    assert client.get("/patients", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_agent_can_run_the_full_booking_flow_with_its_token(ctx):
    """End to end over the token: register, list doctors, availability, book."""
    client, session_factory = ctx
    org_id = _make_clinic(session_factory, "Booking Clinic", "+910000000004", timezone="UTC")

    db = session_factory()
    try:
        doctor = models.Doctor(
            org_id=org_id, name="Dr. Tenant", slot_minutes=30,
            work_start_hour=0, work_end_hour=23, work_days_csv="0,1,2,3,4,5,6", active=True,
        )
        db.add(doctor)
        db.commit()
        doctor_id = doctor.id
    finally:
        db.close()

    headers = _agent_headers(client, "+910000000004")
    patient_id = client.post(
        "/patients", json={"name": "Caller", "phone": "+915550001111"}, headers=headers
    ).json()["id"]

    from datetime import datetime, timedelta
    target = (datetime.utcnow() + timedelta(days=2)).date().isoformat()
    slots = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": target},
        headers=headers,
    ).json()["slots"]
    assert slots

    r = client.post(
        "/appointments/book",
        json={"patient_id": patient_id, "doctor_id": doctor_id, "start_at": slots[0]["start_at"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ai_generated"] is True, "agent-token bookings must be flagged ai_generated"
