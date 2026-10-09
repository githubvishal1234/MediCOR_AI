"""
Phase 0 — regression tests for the hardening/correctness fixes.

Covers, in order:

  1. X-Forwarded-For is ignored unless the direct peer is a configured
     trusted proxy (previously the header was trusted unconditionally,
     so rotating it bypassed login rate limiting entirely).
  2. Per-account failed-login throttling, which sees a distributed
     attack against one email that per-IP limiting cannot.
  3. GET /appointments/availability scans the whole working day
     (previously the result cap terminated the scan, hiding afternoons).
  4. Verifying a pending booking produces a real, schedulable
     appointment — or is refused, instead of silently creating one with
     no start_at that renders nowhere.

Like the other tests here, this runs against a throwaway in-memory
SQLite database via dependency override and never touches the SQL Server
configured in app/config.py.

Run with:
    pytest tests/test_phase0_hardening.py -v
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
from app.routers import appointments as appointments_router
from app.routers import auth as auth_router
from app.routers import doctors as doctors_router
from app.routers import patients as patients_router
from app.security import create_access_token

PEER_IP = "10.1.2.3"


@pytest.fixture()
def ctx():
    """Yields (client, session_factory) with a clean limiter and no trusted proxies."""
    rate_limit._attempts.clear()
    original_proxies = settings.trusted_proxy_ips
    settings.trusted_proxy_ips = ""

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
    app.include_router(auth_router.router)
    app.include_router(appointments_router.router)
    app.include_router(doctors_router.router)
    app.include_router(patients_router.router)
    app.dependency_overrides[get_db] = override_get_db

    # A real IP for the socket peer — "testclient" (the default) is not a
    # parseable address and so could never match a trusted-proxy network.
    with make_test_client(app, client=(PEER_IP, 50000)) as c:
        yield c, TestingSessionLocal

    settings.trusted_proxy_ips = original_proxies
    rate_limit._attempts.clear()


def _login(client, email, forwarded_for=None):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return client.post(
        "/auth/login",
        data={"username": email, "password": "wrong-password"},
        headers=headers,
    )


# ---------------------------------------------------------------------
# 1. X-Forwarded-For handling
# ---------------------------------------------------------------------

def test_rotating_forwarded_for_cannot_bypass_ip_rate_limit(ctx):
    """
    No trusted proxy configured, so X-Forwarded-For must be ignored
    entirely and every request must land in the same per-IP bucket.

    A distinct email per attempt keeps the per-account limiter out of the
    way, so a 429 here can only have come from the per-IP limiter.
    """
    client, _ = ctx
    limit = settings.login_rate_limit_attempts

    for i in range(limit):
        r = _login(client, f"nobody{i}@example.com", forwarded_for=f"203.0.113.{i}")
        assert r.status_code == 401, f"attempt {i} -> {r.status_code}: {r.text}"

    r = _login(client, "nobody-final@example.com", forwarded_for="203.0.113.250")
    assert r.status_code == 429, (
        "Rotating X-Forwarded-For bypassed the login rate limiter — the "
        "header is being trusted without a configured proxy."
    )


def test_forwarded_for_is_honoured_from_a_trusted_proxy(ctx):
    """With the peer configured as a trusted proxy, XFF gives real per-client buckets."""
    client, _ = ctx
    settings.trusted_proxy_ips = PEER_IP

    # Comfortably more attempts than the per-IP limit, each from a
    # different forwarded client address.
    for i in range(settings.login_rate_limit_attempts + 5):
        r = _login(client, f"nobody{i}@example.com", forwarded_for=f"198.51.100.{i}")
        assert r.status_code == 401, f"attempt {i} -> {r.status_code}: {r.text}"


def test_trusted_proxy_uses_rightmost_untrusted_hop(ctx):
    """
    A client that forges a chain must not be able to pick its own bucket:
    only the right-most entry that isn't one of our proxies counts.
    """
    client, _ = ctx
    settings.trusted_proxy_ips = PEER_IP
    limit = settings.login_rate_limit_attempts

    # The forged left-hand entries vary; the real (right-most) hop is
    # constant, so every request must share one bucket.
    for i in range(limit):
        r = _login(client, f"nobody{i}@example.com", forwarded_for=f"1.2.3.{i}, 198.51.100.9")
        assert r.status_code == 401, f"attempt {i} -> {r.status_code}: {r.text}"

    r = _login(client, "nobody-final@example.com", forwarded_for="9.9.9.9, 198.51.100.9")
    assert r.status_code == 429, "Forged left-hand X-Forwarded-For entries were trusted."


# ---------------------------------------------------------------------
# 2. Per-account throttling
# ---------------------------------------------------------------------

def test_per_account_throttle_stops_distributed_credential_stuffing(ctx):
    """
    Same target email, a different source IP every time. Per-IP limiting
    is blind to this; the per-account limiter is what stops it.
    """
    client, _ = ctx
    settings.trusted_proxy_ips = PEER_IP
    limit = settings.login_account_rate_limit_attempts

    for i in range(limit):
        r = _login(client, "victim@clinic.example", forwarded_for=f"198.51.100.{i}")
        assert r.status_code == 401, f"attempt {i} -> {r.status_code}: {r.text}"

    r = _login(client, "victim@clinic.example", forwarded_for="198.51.100.200")
    assert r.status_code == 429, (
        "A distributed attack against one account was not throttled."
    )


def test_per_account_throttle_is_case_insensitive(ctx):
    client, _ = ctx
    settings.trusted_proxy_ips = PEER_IP
    limit = settings.login_account_rate_limit_attempts

    for i in range(limit):
        email = "Victim@Clinic.Example" if i % 2 else "victim@clinic.example"
        r = _login(client, email, forwarded_for=f"198.51.100.{i}")
        assert r.status_code == 401

    r = _login(client, "VICTIM@CLINIC.EXAMPLE", forwarded_for="198.51.100.201")
    assert r.status_code == 429, "Case variation split the per-account counter."


def test_successful_login_clears_the_account_failure_counter(ctx):
    """A user who mistypes a few times and then succeeds must start clean."""
    client, session_factory = ctx
    settings.trusted_proxy_ips = PEER_IP

    r = client.post(
        "/auth/register",
        json={
            "org_name": "Clear Counter Clinic",
            "email": "staff@clinic.example",
            "password": "Sup3rSecret!",
            "full_name": "Staff User",
        },
    )
    assert r.status_code == 200, r.text

    # A few failures, but short of the limit.
    for i in range(settings.login_account_rate_limit_attempts - 1):
        assert _login(client, "staff@clinic.example", forwarded_for=f"198.51.100.{i}").status_code == 401

    # Correct password succeeds and resets the counter.
    r = client.post(
        "/auth/login",
        data={"username": "staff@clinic.example", "password": "Sup3rSecret!"},
        headers={"X-Forwarded-For": "198.51.100.150"},
    )
    assert r.status_code == 200, r.text

    # The budget is available again rather than one attempt from lockout.
    r = _login(client, "staff@clinic.example", forwarded_for="198.51.100.151")
    assert r.status_code == 401, "Successful login did not clear the failure counter."


# ---------------------------------------------------------------------
# Fixtures for the scheduling tests
# ---------------------------------------------------------------------

def _next_weekday_at(hour, days_ahead=1):
    target = (datetime.utcnow() + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    while target.weekday() > 4:
        target += timedelta(days=1)
    return target


def _make_org_with_doctor(session_factory, slot_minutes=30, start_hour=9, end_hour=17):
    """Creates an org + admin user + doctor directly, and returns auth headers."""
    db = session_factory()
    try:
        org = models.Organization(name="Scheduling Clinic")
        db.add(org)
        db.flush()

        user = models.User(
            org_id=org.id,
            email=f"admin-{org.id}@clinic.example",
            hashed_password="unused-token-is-minted-directly",
            full_name="Scheduling Admin",
            role=models.UserRole.admin,
        )
        db.add(user)

        doctor = models.Doctor(
            org_id=org.id,
            name="Dr. Fullday",
            slot_minutes=slot_minutes,
            work_start_hour=start_hour,
            work_end_hour=end_hour,
            work_days_csv="0,1,2,3,4",
            active=True,
        )
        db.add(doctor)
        db.commit()
        db.refresh(user)
        db.refresh(doctor)

        headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
        return headers, org.id, doctor.id
    finally:
        db.close()


# ---------------------------------------------------------------------
# 3. Availability covers the whole working day
# ---------------------------------------------------------------------

def test_availability_returns_the_full_working_day(ctx):
    """
    A 9-5 doctor on 30-minute slots has 16 openings on an empty day. The
    pre-Phase-0 loop stopped after 10, so nothing after ~1:30 PM was ever
    offered to a caller or to staff.
    """
    client, session_factory = ctx
    headers, _, doctor_id = _make_org_with_doctor(session_factory)

    target = _next_weekday_at(9, days_ahead=3)
    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": target.date().isoformat()},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]

    assert len(slots) == 16, f"expected a full 9-5 day of 30-minute slots, got {len(slots)}"

    last_hour = datetime.fromisoformat(slots[-1]["start_at"]).hour
    assert last_hour >= 16, (
        f"availability stops at {last_hour}:00 — the afternoon is still being "
        "truncated by the result cap."
    )


def test_availability_respects_the_configured_cap(ctx):
    """The cap still bounds the response; it just no longer truncates the scan."""
    client, session_factory = ctx
    headers, _, doctor_id = _make_org_with_doctor(session_factory)

    original = settings.availability_max_slots
    appointments_router.MAX_SLOTS_RETURNED = 4
    try:
        target = _next_weekday_at(9, days_ahead=3)
        r = client.get(
            "/appointments/availability",
            params={"doctor_id": doctor_id, "date": target.date().isoformat()},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["slots"]) == 4
    finally:
        appointments_router.MAX_SLOTS_RETURNED = original


# ---------------------------------------------------------------------
# 4. Pending bookings verify into real appointments
# ---------------------------------------------------------------------

def test_verify_pending_booking_without_a_slot_is_refused(ctx):
    """
    A label-only pending booking (the pre-Phase-0 shape) must NOT silently
    become an appointment with no start_at — such a row never renders on
    the calendar and never blocks its slot.
    """
    client, session_factory = ctx
    headers, org_id, _ = _make_org_with_doctor(session_factory)

    db = session_factory()
    try:
        booking = models.PendingBooking(
            org_id=org_id,
            patient_name="Legacy Row",
            type_label="Follow-up",
            when_label="Tomorrow, 9:30 AM",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id
    finally:
        db.close()

    r = client.post(f"/appointments/pending/bookings/{booking_id}/verify", headers=headers)
    assert r.status_code == 400, f"expected refusal, got {r.status_code}: {r.text}"

    # And crucially: no phantom appointment was created.
    r = client.get("/appointments", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == [], "a label-only pending booking still created an appointment"


def test_verify_pending_booking_creates_a_schedulable_appointment(ctx):
    """With a doctor and a concrete slot, verify produces a real appointment."""
    client, session_factory = ctx
    headers, org_id, doctor_id = _make_org_with_doctor(session_factory)
    start_at = _next_weekday_at(10, days_ahead=2)

    db = session_factory()
    try:
        booking = models.PendingBooking(
            org_id=org_id,
            patient_name="Real Booking",
            type_label="Follow-up",
            doctor_id=doctor_id,
            start_at=start_at,
            reason="Follow-up",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id
    finally:
        db.close()

    r = client.post(f"/appointments/pending/bookings/{booking_id}/verify", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "verified"

    r = client.get("/appointments", headers=headers)
    assert r.status_code == 200, r.text
    appts = r.json()
    assert len(appts) == 1
    appt = appts[0]

    # The three fields whose absence made the old row a phantom.
    assert appt["start_at"] is not None, "appointment has no start_at — invisible on the calendar"
    assert appt["end_at"] is not None, "appointment has no end_at — invisible to conflict checking"
    assert appt["doctor_id"] == doctor_id
    assert datetime.fromisoformat(appt["start_at"]) == start_at


def test_verified_pending_booking_actually_blocks_its_slot(ctx):
    """
    The point of the fix: the resulting appointment must be visible to
    conflict detection, so the slot stops being offered.
    """
    client, session_factory = ctx
    headers, org_id, doctor_id = _make_org_with_doctor(session_factory)
    start_at = _next_weekday_at(10, days_ahead=2)
    date_str = start_at.date().isoformat()

    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": date_str},
        headers=headers,
    )
    before = {s["start_at"] for s in r.json()["slots"]}
    assert start_at.isoformat() in before

    db = session_factory()
    try:
        booking = models.PendingBooking(
            org_id=org_id,
            patient_name="Blocks The Slot",
            doctor_id=doctor_id,
            start_at=start_at,
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id
    finally:
        db.close()

    assert client.post(
        f"/appointments/pending/bookings/{booking_id}/verify", headers=headers
    ).status_code == 200

    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": date_str},
        headers=headers,
    )
    after = {s["start_at"] for s in r.json()["slots"]}
    assert start_at.isoformat() not in after, (
        "the verified booking did not block its own slot — it is still invisible "
        "to conflict checking"
    )


def test_verify_pending_booking_rejects_a_cross_org_doctor(ctx):
    """Tenant scoping applies to the structured fields too."""
    client, session_factory = ctx
    headers, org_id, _ = _make_org_with_doctor(session_factory)

    db = session_factory()
    try:
        other_org = models.Organization(name="Other Clinic")
        db.add(other_org)
        db.flush()
        foreign_doctor = models.Doctor(org_id=other_org.id, name="Dr. Foreign", active=True)
        db.add(foreign_doctor)
        db.flush()

        booking = models.PendingBooking(
            org_id=org_id,
            patient_name="Cross Tenant",
            doctor_id=foreign_doctor.id,
            start_at=_next_weekday_at(10, days_ahead=2),
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id
    finally:
        db.close()

    r = client.post(f"/appointments/pending/bookings/{booking_id}/verify", headers=headers)
    assert r.status_code == 404, f"expected 404 for a foreign doctor, got {r.status_code}"


# ---------------------------------------------------------------------
# 5. The voice agent's own path must be unchanged
# ---------------------------------------------------------------------
# Phase 0 changed _validate_slot's signature (ctx -> org_id) and the
# availability scan. Both are on the exact path the LiveKit agent uses
# via backend_client: X-API-Key auth -> availability -> book. This
# exercises that path end to end so a regression there is loud.


@pytest.fixture()
def no_side_effects(monkeypatch):
    """
    Booking fires EHR sync + patient notification as background tasks, and
    the EHR task opens the REAL SessionLocal (SQL Server). Stub both — this
    test is about the scheduling contract, not about EHR or SMTP.
    """
    from app import ehr_service, notification_service

    monkeypatch.setattr(ehr_service, "schedule_appointment_sync", lambda *a, **k: None)
    monkeypatch.setattr(
        notification_service, "notify_appointment_booked", lambda *a, **k: None
    )
    yield


def _service_api_key(session_factory, org_id):
    """Creates a service API key row and returns the X-API-Key header."""
    from app.security import generate_api_key, hash_api_key

    plaintext, prefix = generate_api_key()
    db = session_factory()
    try:
        db.add(
            models.APIKey(
                org_id=org_id,
                label="Voice Agent (test)",
                key_prefix=prefix,
                hashed_key=hash_api_key(plaintext),
                revoked=False,
            )
        )
        db.commit()
    finally:
        db.close()
    return {"X-API-Key": plaintext}


def test_voice_agent_availability_then_book_still_works(ctx, no_side_effects):
    client, session_factory = ctx
    staff_headers, org_id, doctor_id = _make_org_with_doctor(session_factory)
    agent_headers = _service_api_key(session_factory, org_id)

    # The agent registers a caller as a new patient.
    r = client.post(
        "/patients",
        json={"name": "Phone Caller", "phone": "+15550001111"},
        headers=agent_headers,
    )
    assert r.status_code == 200, r.text
    patient_id = r.json()["id"]

    # It lists doctors, then checks availability — service auth, same as backend_client.
    r = client.get("/doctors", headers=agent_headers)
    assert r.status_code == 200, r.text
    assert any(d["id"] == doctor_id for d in r.json())

    date_str = _next_weekday_at(9, days_ahead=4).date().isoformat()
    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": date_str},
        headers=agent_headers,
    )
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert slots, "no slots offered to the voice agent"

    # It books the exact slot the caller picked.
    chosen = slots[2]["start_at"]
    r = client.post(
        "/appointments/book",
        json={"patient_id": patient_id, "doctor_id": doctor_id, "start_at": chosen, "reason": "Cough"},
        headers=agent_headers,
    )
    assert r.status_code == 200, r.text
    appt = r.json()
    assert appt["ai_generated"] is True, "service-actor bookings must be flagged ai_generated"
    assert appt["start_at"] is not None and appt["end_at"] is not None

    # Double-booking the same slot is still refused.
    r = client.post(
        "/appointments/book",
        json={"patient_id": patient_id, "doctor_id": doctor_id, "start_at": chosen},
        headers=agent_headers,
    )
    assert r.status_code == 409, f"conflict detection broke: {r.status_code} {r.text}"

    # The agent can find, reschedule and cancel it.
    r = client.get(f"/appointments/patient/{patient_id}", headers=agent_headers)
    assert r.status_code == 200 and len(r.json()) == 1

    r = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"start_at": slots[5]["start_at"]},
        headers=agent_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["start_at"] == slots[5]["start_at"]

    r = client.post(f"/appointments/{appt['id']}/cancel", headers=agent_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


def test_revoked_service_key_is_still_rejected(ctx):
    """Sanity: the API-key auth path itself is untouched."""
    client, session_factory = ctx
    _, org_id, _ = _make_org_with_doctor(session_factory)
    headers = _service_api_key(session_factory, org_id)

    db = session_factory()
    try:
        key = db.query(models.APIKey).filter(models.APIKey.org_id == org_id).first()
        key.revoked = True
        db.commit()
    finally:
        db.close()

    assert client.get("/doctors", headers=headers).status_code == 401
