"""
Phase 2 — the voice agent's own client, over real HTTP.

Imports `voice-agent/backend_client.py` and points it at a real uvicorn
server running the real backend routers. Nothing is mocked: this is the
same code path a live call takes, minus LiveKit and the telephony.

What it is really guarding is the multi-tenancy invariant. The client
used to be a module-global with one API key baked in, so a worker
process could only ever serve one clinic. It is now constructed per call,
and two concurrent calls to different clinics must not be able to see
each other's patients.

    pytest tests/test_phase2_agent_client.py -v
"""

import asyncio
import os
import socket
import sys
import threading
import time

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)
# The agent lives beside the backend and imports its own top-level
# `config` module; put it on the path so `backend_client` resolves.
sys.path.insert(0, os.path.join(_BACKEND_DIR, "voice-agent"))

import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.config import settings
from app.database import Base, get_db
from app.exceptions import register_exception_handlers
from app.routers import agent_runtime as agent_runtime_router
from app.routers import appointments as appointments_router
from app.routers import calls as calls_router
from app.routers import doctors as doctors_router
from app.routers import knowledge_base as kb_router
from app.routers import patients as patients_router
from app.security import generate_api_key, hash_api_key

import backend_client  # noqa: E402  - from voice-agent/
import config as agent_config  # noqa: E402  - voice-agent/config.py

PLATFORM_KEY = "integration-test-platform-key"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_backend():
    """A real backend on a real port, backed by throwaway SQLite."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Overriding `get_db` covers request handlers, but NOT code that opens
    # its own session from the module-global factory. Since HV-6,
    # `realtime._persist_notification` does exactly that (deliberately —
    # see its docstring), and `POST /calls` raises a notification. Left
    # unpatched it would reach for the real SQL Server configured in
    # app/config.py, block on a pyodbc login timeout, and blow the agent
    # client's 8s HTTP timeout — so this test would fail with "Backend is
    # unreachable" for a reason that has nothing to do with the agent.
    #
    # Patching the attribute (rather than the imported name) works because
    # _persist_notification imports it inside the function body, so it
    # resolves against the current module attribute on every call.
    import app.database as app_database

    _real_session_local = app_database.SessionLocal
    app_database.SessionLocal = SessionLocal

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    for router in (
        agent_runtime_router.router,
        patients_router.router,
        doctors_router.router,
        appointments_router.router,
        calls_router.router,
        kb_router.router,
    ):
        app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = override_get_db

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "backend did not start"

    original_url = agent_config.BACKEND_API_URL
    original_platform = agent_config.BACKEND_PLATFORM_KEY
    original_key = agent_config.BACKEND_API_KEY
    original_settings_key = settings.agent_platform_key

    agent_config.BACKEND_API_URL = f"http://127.0.0.1:{port}"
    agent_config.BACKEND_PLATFORM_KEY = PLATFORM_KEY
    settings.agent_platform_key = PLATFORM_KEY

    yield SessionLocal

    app_database.SessionLocal = _real_session_local
    agent_config.BACKEND_API_URL = original_url
    agent_config.BACKEND_PLATFORM_KEY = original_platform
    agent_config.BACKEND_API_KEY = original_key
    settings.agent_platform_key = original_settings_key
    server.should_exit = True
    thread.join(timeout=10)


def _make_clinic(SessionLocal, name, number, greeting=None, voice=None):
    db = SessionLocal()
    try:
        org = models.Organization(name=name, timezone="UTC")
        db.add(org)
        db.flush()
        db.add(models.PhoneNumber(org_id=org.id, e164=number, active=True))
        if greeting or voice:
            db.add(
                models.AgentSettings(
                    org_id=org.id,
                    greeting_script=greeting or "Hello.",
                    voice_profile=voice or "Dr. Sarah (Calm, Professional)",
                )
            )
        db.commit()
        return org.id
    finally:
        db.close()


def _api_key_for(SessionLocal, org_id):
    plaintext, prefix = generate_api_key()
    db = SessionLocal()
    try:
        db.add(
            models.APIKey(
                org_id=org_id, label="Voice Agent", key_prefix=prefix,
                hashed_key=hash_api_key(plaintext), revoked=False,
            )
        )
        db.commit()
    finally:
        db.close()
    return plaintext


# ---------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------

def test_resolve_tenant_returns_the_right_clinic(live_backend):
    org_id = _make_clinic(live_backend, "Resolved Clinic", "+911111100001", greeting="Welcome.")

    async def run():
        tenant = await backend_client.resolve_tenant("+911111100001")
        try:
            assert tenant is not None, "resolution returned nothing"
            assert tenant.resolved is True
            assert tenant.org_id == org_id
            assert tenant.org_name == "Resolved Clinic"
            assert tenant.greeting_script == "Welcome."
            assert tenant.timezone == "UTC"
        finally:
            if tenant:
                await tenant.client.aclose()

    asyncio.run(run())


def test_unknown_number_falls_back_instead_of_failing(live_backend):
    """
    A call must never be lost to a routing gap. An unassigned number
    resolves to None, and agent.py then uses the single-tenant key.
    """
    async def run():
        assert await backend_client.resolve_tenant("+915555500000") is None

    asyncio.run(run())


def test_resolution_is_skipped_without_a_platform_key(live_backend):
    """The rollback switch: no platform key means no resolution attempt at all."""
    original = agent_config.BACKEND_PLATFORM_KEY
    agent_config.BACKEND_PLATFORM_KEY = None
    try:
        _make_clinic(live_backend, "Ignored Clinic", "+911111100002")

        async def run():
            assert await backend_client.resolve_tenant("+911111100002") is None

        asyncio.run(run())
    finally:
        agent_config.BACKEND_PLATFORM_KEY = original


def test_missing_dialled_number_falls_back(live_backend):
    async def run():
        assert await backend_client.resolve_tenant(None) is None
        assert await backend_client.resolve_tenant("") is None

    asyncio.run(run())


def test_a_wrong_platform_key_falls_back_rather_than_raising(live_backend):
    original = agent_config.BACKEND_PLATFORM_KEY
    agent_config.BACKEND_PLATFORM_KEY = "not-the-key"
    try:
        _make_clinic(live_backend, "Guarded Clinic", "+911111100003")

        async def run():
            assert await backend_client.resolve_tenant("+911111100003") is None

        asyncio.run(run())
    finally:
        agent_config.BACKEND_PLATFORM_KEY = original


def test_unreachable_backend_falls_back_rather_than_raising(live_backend):
    """Resolution failure must degrade to the fallback key, never kill the call."""
    original = agent_config.BACKEND_API_URL
    agent_config.BACKEND_API_URL = f"http://127.0.0.1:{_free_port()}"
    try:
        async def run():
            assert await backend_client.resolve_tenant("+911111100001") is None

        asyncio.run(run())
    finally:
        agent_config.BACKEND_API_URL = original


# ---------------------------------------------------------------------
# The multi-tenancy invariant
# ---------------------------------------------------------------------

def test_two_concurrent_calls_do_not_see_each_others_patients(live_backend):
    """
    The invariant the whole phase exists for.

    Two clinics, two numbers, two clients alive at the same time in one
    process — exactly what a worker handling concurrent calls looks like.
    With the old module-global client this was impossible to get right.
    """
    org_a = _make_clinic(live_backend, "Concurrent A", "+912222200001")
    org_b = _make_clinic(live_backend, "Concurrent B", "+912222200002")

    async def run():
        tenant_a = await backend_client.resolve_tenant("+912222200001")
        tenant_b = await backend_client.resolve_tenant("+912222200002")
        assert tenant_a and tenant_b
        try:
            # Interleave the two calls the way concurrent traffic would.
            patient_a, patient_b = await asyncio.gather(
                tenant_a.client.register_patient(name="Alice A", phone="+915550000001"),
                tenant_b.client.register_patient(name="Bob B", phone="+915550000002"),
            )

            a_names = [p["name"] for p in await tenant_a.client.search_patients("A")]
            b_names = [p["name"] for p in await tenant_b.client.search_patients("B")]
            assert "Alice A" in a_names and "Bob B" not in a_names
            assert "Bob B" in b_names and "Alice A" not in b_names

            # And each landed in the right org in the database.
            db = live_backend()
            try:
                assert db.query(models.Patient).filter(
                    models.Patient.id == patient_a["id"]).first().org_id == org_a
                assert db.query(models.Patient).filter(
                    models.Patient.id == patient_b["id"]).first().org_id == org_b
            finally:
                db.close()
        finally:
            await tenant_a.client.aclose()
            await tenant_b.client.aclose()

    asyncio.run(run())


def test_fallback_client_still_works_with_an_api_key(live_backend):
    """The pre-Phase-2 path must keep working untouched."""
    org_id = _make_clinic(live_backend, "Legacy Clinic", "+913333300001")
    agent_config.BACKEND_API_KEY = _api_key_for(live_backend, org_id)

    async def run():
        tenant = backend_client.fallback_tenant()
        assert tenant.resolved is False
        try:
            patient = await tenant.client.register_patient(name="Legacy Caller")
            assert patient["id"]
            db = live_backend()
            try:
                assert db.query(models.Patient).filter(
                    models.Patient.id == patient["id"]).first().org_id == org_id
            finally:
                db.close()
        finally:
            await tenant.client.aclose()

    asyncio.run(run())


# ---------------------------------------------------------------------
# Behaviour that must not have changed
# ---------------------------------------------------------------------

def test_full_call_lifecycle_over_a_resolved_tenant(live_backend):
    """Call log -> transcript -> finalize, the shape agent.py drives."""
    _make_clinic(live_backend, "Lifecycle Clinic", "+914444400001")

    async def run():
        tenant = await backend_client.resolve_tenant("+914444400001")
        assert tenant
        try:
            call = await tenant.client.create_call_log(
                direction="inbound", caller_phone="+915551234567"
            )
            call_id = call["id"]

            await tenant.client.update_call_caller_phone(call_id, "+915559999999")
            await tenant.client.save_transcript_bulk(
                call_id,
                [
                    {"who": "ai", "text": "How can I help?"},
                    {"who": "patient", "text": "I need an appointment. Thanks!"},
                ],
            )
            final = await tenant.client.finalize_call(
                call_id, status="completed", duration_seconds=95,
                actions_taken="Booked appointment",
            )
            assert final["status"] == "completed"
            assert final["duration"] == "1m 35s"
            # The backend, not the agent, derives these.
            #
            # "Completed" and not "Booked", even though `actions_taken`
            # says the agent booked something: no `appointment_id` was
            # sent with the finalize, so there is no appointment on the
            # record to support the claim. The agent's own account is
            # strong evidence and still not proof -- see
            # call_intelligence.verified_outcome and
            # tests/test_outcome_needs_evidence.py.
            #
            # A real call sends both together (bridge.py passes
            # `appointment_id=runner.appointment_id` alongside `actions`),
            # so this asserts the honest answer for the half-shape this
            # test drives rather than the flattering one.
            assert final["outcome"] == "Completed"
            assert final["ai_summary"]
            assert final["caller_phone"] == "+915559999999"
        finally:
            await tenant.client.aclose()

    asyncio.run(run())


def test_4xx_is_not_retried_and_keeps_the_backend_message(live_backend):
    """
    The retry policy is load-bearing on a live call: retry 5xx and
    connection blips, never a 4xx. Booking a nonexistent patient is a
    404 and must surface immediately with the backend's own wording.
    """
    _make_clinic(live_backend, "Retry Clinic", "+915555500001")

    async def run():
        tenant = await backend_client.resolve_tenant("+915555500001")
        assert tenant
        try:
            started = time.monotonic()
            with pytest.raises(backend_client.BackendAPIError) as excinfo:
                await tenant.client.book_appointment(
                    patient_id="nope", doctor_id="nope", start_at="2030-01-01T10:00:00"
                )
            elapsed = time.monotonic() - started
            assert excinfo.value.status_code == 404
            assert "not found" in str(excinfo.value).lower()
            assert elapsed < 1.0, f"a 4xx appears to have been retried ({elapsed:.2f}s)"
        finally:
            await tenant.client.aclose()

    asyncio.run(run())


def test_knowledge_base_reads_are_scoped_to_the_resolved_clinic(live_backend):
    org_id = _make_clinic(live_backend, "KB Clinic", "+916666600001")
    db = live_backend()
    try:
        db.add(models.ClinicInfo(org_id=org_id, timings_text="Open 9 to 5, Monday to Friday"))
        db.add(models.Service(org_id=org_id, name="Dental cleaning", fee_label="800", active=True))
        db.commit()
    finally:
        db.close()

    async def run():
        tenant = await backend_client.resolve_tenant("+916666600001")
        assert tenant
        try:
            info = await tenant.client.get_clinic_info()
            assert info["timings_text"] == "Open 9 to 5, Monday to Friday"
            services = await tenant.client.list_services()
            assert [s["name"] for s in services] == ["Dental cleaning"]
        finally:
            await tenant.client.aclose()

    asyncio.run(run())
