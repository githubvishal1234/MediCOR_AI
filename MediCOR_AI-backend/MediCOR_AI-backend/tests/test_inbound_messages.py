"""
Inbound patient messages (PF-07) and the SMS channel (PF-04).

The inbound webhook is unauthenticated by necessity -- Twilio calls it,
not a user -- and it cancels appointments. Most of what follows is about
that, not about the happy path.

Pinned here:

  * **Signature verification precedes everything.** An unsigned or
    mis-signed request must change nothing. Without it, anyone who
    learned the URL could cancel any appointment belonging to a phone
    number they could guess.

  * **One matching patient, or nothing happens.** A number matching two
    patients is a shared handset, and acting on the wrong person's
    appointment is worse than doing nothing. This matters more here than
    elsewhere because an inbound message carries no tenant -- the phone
    number is the only identifier there is.

  * **Only the nearest upcoming appointment is reachable.** There is no
    way to address any other.

  * **Free text is never guessed at.** Anything that is not one of the
    three keywords is acknowledged and left for a person, for the same
    reason a refill request only links on an exact medication match.

  * **STOP is always honoured**, even though nothing else is acted on
    without care. It is the one instruction a patient is always entitled
    to give.

    pytest tests/test_inbound_messages.py -v
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

from app import models, notification_service, rate_limit  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import inbound_messages  # noqa: E402

AUTH_TOKEN = "test-twilio-auth-token"
URL = "http://testserver/messages/inbound"


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")

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
    app.include_router(inbound_messages.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]
        yield client, Testing, org_id


def _sign(form: dict, token: str = AUTH_TOKEN, url: str = URL) -> str:
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    return b64encode(hmac.new(token.encode(), payload.encode(), sha1).digest()).decode()


def _post(client, form, signature=None, sign=True):
    headers = {}
    if sign:
        headers["X-Twilio-Signature"] = signature or _sign(form)
    elif signature is not None:
        headers["X-Twilio-Signature"] = signature
    return client.post("/messages/inbound", data=form, headers=headers)


def _seed(Session, org_id, *, phone="+919000000001", when_days=3, second_patient=False):
    db = Session()
    try:
        p = models.Patient(org_id=org_id, mrn="MRN1", name="Anitha Rao",
                           phone=phone, contact_consent="granted")
        db.add(p)
        if second_patient:
            db.add(models.Patient(org_id=org_id, mrn="MRN2", name="Bala Rao", phone=phone))
        db.flush()
        start = datetime.now() + timedelta(days=when_days)
        a = models.Appointment(
            org_id=org_id, patient_id=p.id, title="Consultation",
            patient_name=p.name, status="upcoming", start_at=start,
            day_label="Fri, Sep 11", time_label="10:00 AM",
            link_token_hash="a" * 64,
            link_expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(a)
        db.commit()
        return p.id, a.id
    finally:
        db.close()


def _appt(Session, appt_id):
    db = Session()
    try:
        return db.query(models.Appointment).filter(models.Appointment.id == appt_id).first()
    finally:
        db.close()


def _patient(Session, patient_id):
    db = Session()
    try:
        return db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    finally:
        db.close()


# ------------------------------------------------------- the signature

def test_an_unsigned_request_changes_nothing(ctx):
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    r = _post(client, {"From": "+919000000001", "Body": "CANCEL"}, sign=False)
    assert r.status_code == 200, "must not 4xx: Twilio retries anything else"
    assert _appt(S, appt_id).status == "upcoming", "an unsigned request cancelled an appointment"


def test_a_forged_signature_changes_nothing(ctx):
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    form = {"From": "+919000000001", "Body": "CANCEL"}
    r = _post(client, form, signature=_sign(form, token="attacker-guessed-token"))
    assert r.status_code == 200
    assert _appt(S, appt_id).status == "upcoming"


def test_a_tampered_body_invalidates_the_signature(ctx):
    """
    The signature covers every parameter, so swapping CONFIRM for CANCEL
    after signing must not verify.
    """
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    signed_for = {"From": "+919000000001", "Body": "CONFIRM"}
    sig = _sign(signed_for)
    r = _post(client, {"From": "+919000000001", "Body": "CANCEL"}, signature=sig)
    assert r.status_code == 200
    assert _appt(S, appt_id).status == "upcoming"


def test_the_signature_helper_matches_twilios_scheme(monkeypatch):
    """
    URL, then every parameter in alphabetical order, HMAC-SHA1 under the
    auth token, base64. Pinned directly so a refactor cannot quietly
    change what is being verified.

    Takes `monkeypatch` rather than the `ctx` fixture because it exercises
    the helper alone -- without the token set it correctly refuses
    everything, which is what the next test asserts on purpose.
    """
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    form = {"Body": "CONFIRM", "From": "+15551234567"}
    expected = b64encode(
        hmac.new(
            AUTH_TOKEN.encode(),
            (URL + "BodyCONFIRMFrom+15551234567").encode(),
            sha1,
        ).digest()
    ).decode()
    assert inbound_messages.verify_twilio_signature(URL, form, expected)
    assert not inbound_messages.verify_twilio_signature(URL, form, "not-the-signature")
    assert not inbound_messages.verify_twilio_signature(URL, form, None)


def test_no_auth_token_configured_verifies_nothing(ctx, monkeypatch):
    """An unset token must fail closed, never open."""
    monkeypatch.setattr(settings, "twilio_auth_token", None)
    assert not inbound_messages.verify_twilio_signature(URL, {"a": "b"}, "anything")


# --------------------------------------------------------- the actions

def test_confirm_marks_attendance(ctx):
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    r = _post(client, {"From": "+919000000001", "Body": "CONFIRM"})
    assert r.status_code == 200
    a = _appt(S, appt_id)
    assert a.confirmation_status == "confirmed"
    assert a.confirmation_source == "reply"


def test_cancel_cancels_and_retires_the_link(ctx):
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    r = _post(client, {"From": "+919000000001", "Body": "CANCEL"})
    assert r.status_code == 200
    a = _appt(S, appt_id)
    assert a.status == "cancelled"
    # A cancelled appointment's self-service link has no purpose left, so
    # it stops being a live credential -- same rule as the link itself.
    assert a.link_token_hash is None


def test_stop_records_a_refusal_and_is_honoured(ctx):
    client, S, org = ctx
    patient_id, _a = _seed(S, org)
    r = _post(client, {"From": "+919000000001", "Body": "STOP"})
    assert r.status_code == 200
    p = _patient(S, patient_id)
    assert p.contact_consent == "declined"
    assert p.contact_consent_source == "reply"
    assert notification_service.may_contact(p) is False


def test_free_text_is_acknowledged_never_guessed_at(ctx):
    """
    "I can't make it on Friday" is a cancellation to a human and an
    ambiguity to a parser. Acting on it would cancel real appointments.
    """
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    r = _post(client, {"From": "+919000000001", "Body": "I can't make it on Friday sorry"})
    assert r.status_code == 200
    assert "call the clinic" in r.text.lower()
    assert _appt(S, appt_id).status == "upcoming"


def test_the_keyword_is_read_from_the_start_of_the_reply(ctx):
    """Patients type "Yes please", not "yes"."""
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    _post(client, {"From": "+919000000001", "Body": "Yes please!"})
    assert _appt(S, appt_id).confirmation_status == "confirmed"


# ------------------------------------------------------- the boundaries

def test_a_shared_handset_resolves_to_nobody(ctx):
    """
    Two patients on one number is a family phone. An inbound message
    carries no tenant, so the number is the only identifier there is --
    which makes the single-match rule load-bearing, not merely careful.
    """
    client, S, org = ctx
    _p, appt_id = _seed(S, org, second_patient=True)
    r = _post(client, {"From": "+919000000001", "Body": "CANCEL"})
    assert r.status_code == 200
    assert _appt(S, appt_id).status == "upcoming"


def test_an_unknown_number_is_told_nothing_about_membership(ctx):
    """
    Confirming whether a number belongs to a patient would make this a
    membership oracle for anyone able to forge... or simply guess.
    """
    client, S, org = ctx
    _seed(S, org)
    r = _post(client, {"From": "+919999999999", "Body": "CONFIRM"})
    assert r.status_code == 200
    body = r.text.lower()
    assert "not found" not in body and "no patient" not in body
    assert "call the clinic" in body


def test_only_the_nearest_upcoming_appointment_is_reachable(ctx):
    client, S, org = ctx
    patient_id, near_id = _seed(S, org, when_days=2)
    db = S()
    try:
        far = models.Appointment(
            org_id=org, patient_id=patient_id, title="Follow-up", status="upcoming",
            start_at=datetime.now() + timedelta(days=30),
            day_label="Later", time_label="09:00 AM",
        )
        db.add(far)
        db.commit()
        far_id = far.id
    finally:
        db.close()

    _post(client, {"From": "+919000000001", "Body": "CANCEL"})
    assert _appt(S, near_id).status == "cancelled"
    assert _appt(S, far_id).status == "upcoming", "a reply reached a second appointment"


def test_a_cancelled_appointment_is_not_reachable_again(ctx):
    client, S, org = ctx
    _p, appt_id = _seed(S, org)
    _post(client, {"From": "+919000000001", "Body": "CANCEL"})
    r = _post(client, {"From": "+919000000001", "Body": "CONFIRM"})
    assert r.status_code == 200
    assert _appt(S, appt_id).confirmation_status is None


# --------------------------------------------------------- PF-04 channel

def test_the_patients_own_channel_wins(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(settings, "whatsapp_from_number", "whatsapp:+1")
    monkeypatch.setattr(settings, "sms_from_number", "+2")
    monkeypatch.setattr(settings, "default_message_channel", "whatsapp")

    p = models.Patient(org_id="o", mrn="m", name="A", phone="+91900")
    p.message_channel = "sms"
    assert notification_service._channel_for(p) == "sms"
    p.message_channel = None
    assert notification_service._channel_for(p) == "whatsapp"


def test_the_default_falls_back_to_whatever_is_configured(monkeypatch):
    """
    A clinic that set up only SMS must not silently send nothing because
    the deployment default still says WhatsApp.
    """
    monkeypatch.setattr(settings, "twilio_account_sid", "sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(settings, "whatsapp_from_number", None)
    monkeypatch.setattr(settings, "sms_from_number", "+2")
    monkeypatch.setattr(settings, "default_message_channel", "whatsapp")

    p = models.Patient(org_id="o", mrn="m", name="A", phone="+91900")
    assert notification_service._channel_for(p) == "sms"


def test_an_sms_recipient_never_keeps_the_whatsapp_prefix(monkeypatch):
    """
    A "whatsapp:" prefix on an SMS recipient is a silent delivery failure
    at Twilio -- the message is accepted and never arrives.
    """
    sent = {}
    monkeypatch.setattr(settings, "twilio_account_sid", "sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(settings, "sms_from_number", "+2")

    class _Resp:
        status_code = 200
        text = ""

    def fake_post(url, **kw):
        sent.update(kw["data"])
        return _Resp()

    monkeypatch.setattr(notification_service.httpx, "post", fake_post)
    notification_service._send_twilio_message("whatsapp:+919000000001", "hi", channel="sms")
    assert sent["To"] == "+919000000001"
    assert sent["From"] == "+2"
