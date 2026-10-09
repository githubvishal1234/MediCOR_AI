"""
Patient contact consent (PF-05).

Four kinds of test here, and only the first is about storage.

  * **NULL is a third state.** "Nobody asked" is not "they said no", and it
    is what every patient row created before this feature holds. Reading it
    as a refusal would mute a clinic's whole roster the day messaging is
    switched on; reading it as consent would message people who never
    agreed. So the reading comes from configuration, and both directions
    are pinned here.

  * **The gate cannot be bypassed.** It lives inside the two functions
    every patient-facing message passes through, not at the top of each
    notify_* function -- a gate a new caller must remember to add is one
    that eventually gets forgotten. The test asserts on the wrappers, so it
    keeps holding for notifications that do not exist yet.

  * **Staff mail is not consent-gated.** A receptionist is not a patient
    and their invitation is not marketing. This is the regression that a
    careless "just gate everything" refactor would cause.

  * **A reminder that was not sent must not be marked as sent.**
    `reminder_sent_at` is written on the strength of the return value, so a
    patient who has not consented has to read as False -- exactly like one
    with no contact details. Nothing was claimed, so there is nothing to
    suppress next time.

    pytest tests/test_patient_contact_consent.py -v
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

from app import ehr_service, models, notification_service, rate_limit, schemas  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import patients as patients_router  # noqa: E402


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()
    monkeypatch.setattr(ehr_service, "schedule_patient_sync", lambda *a, **k: None)

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
    app.include_router(patients_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        yield client, Testing, admin


def _create(client, headers, **extra):
    body = {"name": "Anitha Rao", "phone": "+919000000001", "email": "a@example.com"}
    body.update(extra)
    r = client.post("/patients", json=body, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _patient(**kw):
    """A detached model instance — the wrappers only read attributes."""
    p = models.Patient(id="p1", org_id="o1", mrn="M1", name="Anitha Rao")
    p.phone = kw.pop("phone", "+919000000001")
    p.email = kw.pop("email", "a@example.com")
    for k, v in kw.items():
        setattr(p, k, v)
    return p


@pytest.fixture()
def transports(monkeypatch):
    """Both transports configured and captured, so only consent decides."""
    sent = {"email": [], "whatsapp": [], "sms": []}
    monkeypatch.setattr(notification_service, "_email_configured", lambda: True)
    monkeypatch.setattr(notification_service, "_whatsapp_configured", lambda: True)
    monkeypatch.setattr(
        notification_service, "_send_email",
        lambda to, subject, body: sent["email"].append(to),
    )
    # Stubbed at `_send_twilio_message` rather than `_send_whatsapp`,
    # because PF-04 made the channel a property of the patient and this is
    # the single seam every phone message now passes through. Recording
    # the channel means these tests can assert *how* someone was reached,
    # not just that they were.
    monkeypatch.setattr(
        notification_service, "_send_twilio_message",
        lambda to, body, channel="whatsapp": sent[channel].append(to),
    )
    return sent


# ----------------------------------------------------------- storage

def test_consent_is_stored_and_stamped_with_when_and_how(ctx):
    client, _S, admin = ctx
    created = _create(client, admin, contact_consent="granted")
    assert created["contact_consent"] == "granted"
    # The client sent neither of these; the backend derives them, for the
    # same reason it decides which detected language becomes a preference.
    assert created["contact_consent_at"] is not None
    assert created["contact_consent_source"] == "staff"


def test_not_asking_leaves_it_null_rather_than_declined(ctx):
    client, _S, admin = ctx
    created = _create(client, admin)
    assert created["contact_consent"] is None
    assert created["contact_consent_at"] is None


def test_an_unknown_consent_value_is_refused_loudly(ctx):
    client, _S, admin = ctx
    r = client.post(
        "/patients",
        json={"name": "X", "contact_consent": "maybe"},
        headers=admin,
    )
    assert r.status_code == 422, r.text


def test_the_stamp_does_not_move_when_the_answer_is_unchanged(ctx):
    client, _S, admin = ctx
    created = _create(client, admin, contact_consent="granted")
    first = created["contact_consent_at"]

    # Re-saving the form with the same answer must not restate when it
    # was given: that date is evidence, and a form save is not a new
    # conversation with the patient.
    r = client.patch(
        f"/patients/{created['id']}",
        json={"contact_consent": "granted", "doctor": "Dr Rao"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["contact_consent_at"] == first


def test_changing_the_answer_does_restamp(ctx):
    client, _S, admin = ctx
    created = _create(client, admin, contact_consent="granted")
    r = client.patch(
        f"/patients/{created['id']}", json={"contact_consent": "declined"}, headers=admin
    )
    assert r.status_code == 200, r.text
    assert r.json()["contact_consent"] == "declined"
    assert r.json()["contact_consent_at"] is not None


# ------------------------------------------------------------- the gate

def test_declined_blocks_every_channel(transports):
    p = _patient(contact_consent="declined")
    assert notification_service._send_patient_email(p, "s", "b") is False
    assert notification_service._send_patient_whatsapp(p, "b") is False
    assert transports["email"] == []
    assert transports["whatsapp"] == []


def test_granted_allows_every_channel(transports):
    p = _patient(contact_consent="granted")
    assert notification_service._send_patient_email(p, "s", "b") is True
    assert notification_service._send_patient_whatsapp(p, "b") is True
    assert transports["email"] == ["a@example.com"]
    assert transports["whatsapp"] == ["+919000000001"]


def test_never_asked_follows_configuration_and_defaults_to_silence(transports, monkeypatch):
    p = _patient(contact_consent=None)

    # The shipped default: an unasked patient is not messaged.
    monkeypatch.setattr(settings, "contact_unasked_is_consent", False)
    assert notification_service.may_contact(p) is False
    assert notification_service._send_patient_email(p, "s", "b") is False
    assert transports["email"] == []

    # A clinic that collected consent on paper can flip it, and its whole
    # existing roster becomes reachable without a backfill inventing an
    # answer nobody gave.
    monkeypatch.setattr(settings, "contact_unasked_is_consent", True)
    assert notification_service.may_contact(p) is True
    assert notification_service._send_patient_email(p, "s", "b") is True
    assert transports["email"] == ["a@example.com"]


def test_the_four_patient_notifications_all_route_through_the_gate(transports, monkeypatch):
    """
    The point of this test is the *wrappers*, not the four functions.

    The gate lives inside `_send_patient_email` / `_send_patient_whatsapp`
    precisely so a notification added next year cannot forget it. If
    someone moves the check up into the individual notify_* functions,
    this keeps passing — and the one they forget to update is the one that
    messages a patient who declined.
    """
    monkeypatch.setattr(settings, "contact_unasked_is_consent", False)
    p = _patient(contact_consent="declined")
    appt = models.Appointment(id="a1", org_id="o1", title="Consultation")
    appt.day_label, appt.time_label = "Tue, Sep 9", "10:00 AM"

    notification_service.notify_appointment_booked(p, appt, None, "Northside")
    notification_service.notify_appointment_cancelled(p, appt, None, "Northside")
    notification_service.notify_appointment_rescheduled(p, appt, None, "Northside", "Mon 9 AM")

    assert transports["email"] == []
    assert transports["whatsapp"] == []


def test_staff_mail_is_not_consent_gated(transports, monkeypatch):
    """
    A receptionist is not a patient, and their invitation is not marketing.

    Staff mail deliberately calls the raw transport. The regression this
    guards against is a well-meaning refactor that routes everything
    through the patient wrappers and silently stops staff invitations and
    the weekly digest from going out.
    """
    notification_service._send_email("staff@clinic.example.com", "Invitation", "body")
    assert transports["email"] == ["staff@clinic.example.com"]


# --------------------------------------------------- the reminder marker

def test_a_reminder_that_was_not_sent_is_not_marked_as_sent(ctx, transports, monkeypatch):
    """
    `send_appointment_reminder` returning True is what causes the job to
    write `reminder_sent_at`. A patient who has not consented must read as
    False, exactly like one with no contact details: nothing was claimed
    on their behalf, so there is nothing to suppress next time.
    """
    client, Testing, admin = ctx
    monkeypatch.setattr(settings, "contact_unasked_is_consent", False)

    declined = _create(client, admin, contact_consent="declined")
    granted = _create(
        client, admin, name="Bala Rao", phone="+919000000002",
        email="b@example.com", contact_consent="granted",
    )

    db = Testing()
    try:
        for pid, expected in ((declined["id"], False), (granted["id"], True)):
            appt = models.Appointment(
                org_id=db.query(models.Patient).get(pid).org_id,
                patient_id=pid, title="Consultation",
                day_label="Tue, Sep 9", time_label="10:00 AM",
            )
            db.add(appt)
            db.commit()
            got = notification_service.send_appointment_reminder(db, appt, "Northside")
            assert got is expected, f"patient {pid} reminder returned {got}"
    finally:
        db.close()

    assert transports["email"] == ["b@example.com"]
    assert transports["whatsapp"] == ["+919000000002"]


# ------------------------------------------------------- what the LLM sees

def test_consent_is_not_handed_to_the_voice_agent(ctx):
    """
    `PatientListOut` is what patient_lookup returns straight into a live
    prompt. Whether a patient may be messaged is not the model's business,
    and every field there costs tokens on every call — the same reasoning
    that keeps the language preference out of it.
    """
    assert "contact_consent" not in schemas.PatientListOut.model_fields
    assert "contact_consent" in schemas.PatientDetailOut.model_fields


def test_the_consent_vocabulary_stays_closed():
    """
    A typo would fail open or closed silently rather than loudly. Note
    there is deliberately no "unknown" member: never having asked is NULL,
    not a value.
    """
    assert schemas.CONTACT_CONSENT_STATES == ("granted", "declined")
    with pytest.raises(ValidationError):
        schemas.PatientUpdate(contact_consent="unknown")
