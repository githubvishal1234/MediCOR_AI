"""
Waiting list and earlier-slot offers (PF-15).

A cancellation is pure lost capacity: the slot reopens and whoever wanted
it has already been told there was nothing and gone away. Availability was
always exact and cancelling always released the slot -- nothing recorded
who wanted one.

The tests that matter most are about the race, because the slot is
deliberately **not** held:

  * an offer is exclusive to one patient for a bounded time;
  * a walk-in can still take it first, and the acceptance must then fail
    the ordinary conflict check and put the patient back on the list
    rather than producing a double booking;
  * an expired offer must return to the pool.

Plus the boundary the router draws: the agent may add someone, and may
not read or edit the queue -- the same split as repeat prescriptions.

    pytest tests/test_waiting_list.py -v
"""

import hmac
import os
import sys
from base64 import b64encode
from datetime import datetime, timedelta
from hashlib import sha1

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

from app import jobs, models, notification_service, rate_limit  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import appointments as appointments_router  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import inbound_messages  # noqa: E402
from app.routers import waiting_list as wl_router  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402

AUTH_TOKEN = "test-twilio-auth-token"
URL = "http://testserver/messages/inbound"


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    monkeypatch.setattr(settings, "waiting_list_enabled", True)

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
    app.include_router(wl_router.router)
    app.include_router(appointments_router.router)
    app.include_router(inbound_messages.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]

        raw_key, prefix = generate_api_key()
        db = Session()
        try:
            db.add(models.APIKey(
                org_id=org_id, label="Voice Agent",
                key_prefix=prefix, hashed_key=hash_api_key(raw_key),
            ))
            db.commit()
        finally:
            db.close()

        yield client, Session, admin, {"X-API-Key": raw_key}, org_id


@pytest.fixture()
def transports(monkeypatch):
    sent = {"email": [], "whatsapp": [], "sms": []}
    monkeypatch.setattr(notification_service, "_email_configured", lambda: True)
    monkeypatch.setattr(notification_service, "_whatsapp_configured", lambda: True)
    monkeypatch.setattr(
        notification_service, "_send_email",
        lambda to, subject, body: sent["email"].append((to, body)),
    )
    monkeypatch.setattr(
        notification_service, "_send_twilio_message",
        lambda to, body, channel="whatsapp": sent[channel].append((to, body)),
    )
    return sent


def _sign(form):
    payload = URL + "".join(f"{k}{form[k]}" for k in sorted(form))
    return b64encode(hmac.new(AUTH_TOKEN.encode(), payload.encode(), sha1).digest()).decode()


def _reply(client, body, sender="+919000000001"):
    form = {"From": sender, "Body": body}
    return client.post("/messages/inbound", data=form,
                       headers={"X-Twilio-Signature": _sign(form)})


def _setup(Session, org_id, *, phone="+919000000001", name="Anitha Rao", mrn="M1"):
    """A patient and an active doctor with ordinary weekday hours."""
    db = Session()
    try:
        p = models.Patient(org_id=org_id, mrn=mrn, name=name, phone=phone,
                           contact_consent="granted")
        db.add(p)
        d = (
            db.query(models.Doctor).filter(models.Doctor.org_id == org_id).first()
        )
        if d is None:
            d = models.Doctor(
                org_id=org_id, name="Dr Meera Iyer", specialty="GP", slot_minutes=30,
                work_days_csv="0,1,2,3,4", work_start_hour=9, work_end_hour=17,
                active=True,
            )
            db.add(d)
        db.commit()
        return p.id, d.id
    finally:
        db.close()


def _entry(Session, entry_id):
    db = Session()
    try:
        return (
            db.query(models.WaitingListEntry)
            .filter(models.WaitingListEntry.id == entry_id)
            .first()
        )
    finally:
        db.close()


# ================================================================= adding

def test_the_agent_may_add_someone_to_the_list(ctx):
    """
    Dual-auth: the natural moment to ask "shall I put you on the list?" is
    the end of a call where nothing was free.
    """
    client, S, _admin, key, org = ctx
    pid, did = _setup(S, org)
    r = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=key)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "waiting"


def test_the_agent_may_not_read_the_queue(ctx):
    """
    Who else is waiting, and what for, is not something the voice agent
    has any business reading out. Same split as repeat prescriptions.
    """
    client, S, _admin, key, org = ctx
    assert client.get("/waiting-list", headers=key).status_code in (401, 403)


def test_the_agent_may_not_remove_anyone(ctx):
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid}, headers=admin).json()
    assert client.delete(f"/waiting-list/{entry['id']}", headers=key).status_code in (401, 403)


def test_asking_twice_does_not_take_two_places_in_the_queue(ctx):
    """
    A caller who rings three times about the same thing should move up by
    seniority, not hold three places.
    """
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    first = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=key).json()
    second = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=key).json()
    assert first["id"] == second["id"]


def test_removal_keeps_the_row(ctx):
    """Cancelled, not deleted: "why did we stop offering" stays answerable."""
    client, S, admin, key, org = ctx
    pid, _d = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid}, headers=admin).json()
    r = client.delete(f"/waiting-list/{entry['id']}", headers=admin)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert _entry(S, entry["id"]) is not None


def test_a_patient_from_another_clinic_is_refused(ctx):
    client, S, admin, key, org = ctx
    r = client.post("/waiting-list", json={"patient_id": "someone-elses-id"}, headers=admin)
    assert r.status_code == 404


# ================================================================ offering

def test_a_waiting_patient_is_offered_a_free_slot(ctx, transports):
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin).json()

    db = S()
    try:
        assert "offered=1" in jobs.offer_waiting_list_slots(db)
    finally:
        db.close()

    row = _entry(S, entry["id"])
    assert row.status == "offered"
    assert row.offered_start_at is not None
    assert row.offers_made == 1
    assert "Reply BOOK" in transports["whatsapp"][0][1]


def test_the_offer_does_not_claim_the_slot_is_held(ctx, transports):
    """
    It is not held -- a walk-in can still take it. Saying otherwise is the
    sort of small dishonesty discovered at the worst possible moment.
    """
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin)
    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
    finally:
        db.close()
    body = transports["whatsapp"][0][1].lower()
    assert "first to reply" in body
    for false_promise in ("held for you", "reserved", "we've saved"):
        assert false_promise not in body


def test_the_longest_waiting_patient_is_offered_first(ctx, transports):
    """Fairness is a business rule here, not an ordering convenience."""
    client, S, admin, key, org = ctx
    first_pid, did = _setup(S, org)
    second_pid, _ = _setup(S, org, phone="+919000000002", name="Bala Rao", mrn="M2")

    a = client.post("/waiting-list", json={"patient_id": first_pid, "doctor_id": did}, headers=admin).json()
    b = client.post("/waiting-list", json={"patient_id": second_pid, "doctor_id": did}, headers=admin).json()

    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
    finally:
        db.close()

    # Both can be offered, but different slots, and the first-added gets
    # the earlier one.
    ra, rb = _entry(S, a["id"]), _entry(S, b["id"])
    assert ra.status == "offered"
    if rb.status == "offered":
        assert ra.offered_start_at < rb.offered_start_at


def test_one_slot_is_never_offered_to_two_people(ctx, transports):
    client, S, admin, key, org = ctx
    first_pid, did = _setup(S, org)
    second_pid, _ = _setup(S, org, phone="+919000000002", name="Bala Rao", mrn="M2")
    a = client.post("/waiting-list", json={"patient_id": first_pid, "doctor_id": did}, headers=admin).json()
    b = client.post("/waiting-list", json={"patient_id": second_pid, "doctor_id": did}, headers=admin).json()

    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
    finally:
        db.close()

    ra, rb = _entry(S, a["id"]), _entry(S, b["id"])
    if ra.offered_start_at and rb.offered_start_at:
        assert ra.offered_start_at != rb.offered_start_at


def test_an_expired_offer_returns_to_the_pool(ctx, transports):
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin).json()

    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
        row = db.query(models.WaitingListEntry).filter(
            models.WaitingListEntry.id == entry["id"]).first()
        row.offer_expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
        assert "expired=1" in jobs.offer_waiting_list_slots(db)
    finally:
        db.close()

    row = _entry(S, entry["id"])
    assert row.status in ("waiting", "offered")


def test_a_patient_who_declined_contact_is_left_waiting(ctx, transports):
    """
    Not marked offered: a staff member can still place them by hand, and
    marking an offer nobody received would take them out of the queue.
    """
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin).json()
    db = S()
    try:
        db.query(models.Patient).filter(models.Patient.id == pid).first().contact_consent = "declined"
        db.commit()
        jobs.offer_waiting_list_slots(db)
    finally:
        db.close()
    assert _entry(S, entry["id"]).status == "waiting"


def test_the_flag_switches_it_off(ctx, transports, monkeypatch):
    client, S, admin, key, org = ctx
    monkeypatch.setattr(settings, "waiting_list_enabled", False)
    db = S()
    try:
        assert jobs.offer_waiting_list_slots(db) == "disabled"
    finally:
        db.close()


def test_it_is_registered_with_the_scheduler():
    from app import scheduler

    scheduler._REGISTRY.clear()
    jobs.register_all()
    assert "offer_waiting_list_slots" in {j.name for j in scheduler.registered_jobs()}
    scheduler._REGISTRY.clear()


# =============================================================== accepting

def test_replying_book_takes_the_slot(ctx, transports):
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin).json()
    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
    finally:
        db.close()

    r = _reply(client, "BOOK")
    assert r.status_code == 200
    assert "booked" in r.text.lower()

    row = _entry(S, entry["id"])
    assert row.status == "booked"
    assert row.resulting_appointment_id is not None

    db = S()
    try:
        appt = db.query(models.Appointment).filter(
            models.Appointment.id == row.resulting_appointment_id).first()
        assert appt.status == "upcoming"
        # They asked for this one explicitly, in writing.
        assert appt.confirmation_status == "confirmed"
    finally:
        db.close()


def test_book_with_no_offer_open_does_nothing(ctx, transports):
    client, S, admin, key, org = ctx
    _setup(S, org)
    r = _reply(client, "BOOK")
    assert r.status_code == 200
    assert "no appointment offer" in r.text.lower()


def test_losing_the_race_puts_the_patient_back_on_the_list(ctx, transports):
    """
    The slot was never held, so a walk-in can take it first. The
    acceptance must fail the ordinary conflict check and return the entry
    to waiting -- never produce a double booking.
    """
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin).json()
    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
        row = db.query(models.WaitingListEntry).filter(
            models.WaitingListEntry.id == entry["id"]).first()
        stolen = row.offered_start_at
        # Somebody walks in and takes exactly that slot.
        db.add(models.Appointment(
            org_id=org, doctor_id=did, title="Walk-in", status="upcoming",
            start_at=stolen, end_at=stolen + timedelta(minutes=30),
        ))
        db.commit()
    finally:
        db.close()

    r = _reply(client, "BOOK")
    assert r.status_code == 200
    assert "taken just before" in r.text.lower()

    row = _entry(S, entry["id"])
    assert row.status == "waiting", "the entry was lost after losing the race"

    db = S()
    try:
        at_that_time = db.query(models.Appointment).filter(
            models.Appointment.org_id == org,
            models.Appointment.start_at == stolen,
            models.Appointment.status == "upcoming",
        ).count()
        assert at_that_time == 1, "a double booking was created"
    finally:
        db.close()


def test_an_expired_offer_cannot_be_accepted(ctx, transports):
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    entry = client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin).json()
    db = S()
    try:
        jobs.offer_waiting_list_slots(db)
        row = db.query(models.WaitingListEntry).filter(
            models.WaitingListEntry.id == entry["id"]).first()
        row.offer_expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    r = _reply(client, "BOOK")
    assert "expired" in r.text.lower()
    assert _entry(S, entry["id"]).status == "waiting"


def test_book_does_not_collide_with_the_appointment_keywords(ctx, transports):
    """
    A patient may hold an offer AND an upcoming appointment at once, which
    is exactly why the offer says BOOK rather than reusing "yes".
    """
    client, S, admin, key, org = ctx
    pid, did = _setup(S, org)
    client.post("/waiting-list", json={"patient_id": pid, "doctor_id": did}, headers=admin)
    db = S()
    try:
        existing = models.Appointment(
            org_id=org, patient_id=pid, doctor_id=did, title="Existing",
            status="upcoming", start_at=datetime.now() + timedelta(days=10),
            day_label="Later", time_label="09:00 AM",
        )
        db.add(existing)
        db.commit()
        existing_id = existing.id
        jobs.offer_waiting_list_slots(db)
    finally:
        db.close()

    _reply(client, "BOOK")
    db = S()
    try:
        appt = db.query(models.Appointment).filter(
            models.Appointment.id == existing_id).first()
        assert appt.status == "upcoming", "BOOK disturbed an existing appointment"
        assert appt.confirmation_status is None
    finally:
        db.close()
