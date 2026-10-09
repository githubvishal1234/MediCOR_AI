"""
Patient feedback (PF-10).

The product has always been thorough about its opinion of itself: every
call is scored by a language model as judge, flagged for hallucination and
policy breaches, and queued for review. None of it ever asked the patient.
This closes an asymmetry rather than filling a feature gap.

What is pinned here:

  * **A digit is unambiguous, which is why the ask uses one.** It cannot
    collide with CONFIRM / CANCEL / STOP, and it is checked first so a
    "1" is never read as something else.

  * **Nobody is surveyed after every call.** The cooldown is per
    *patient*, not per call, and counted from when we last *asked* --
    someone who ignored the question has told us something too.

  * **A failed call is not surveyed at all.** That patient is already
    getting an apology from the missed-call job; asking them to rate the
    experience on top is close to rude.

  * **An average of nothing is not zero out of five.** `average_rating`
    is None when nobody answered, the same rule `resolution_rate_pct`
    follows.

    pytest tests/test_patient_feedback.py -v
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
from app.routers import auth as auth_router  # noqa: E402
from app.routers import inbound_messages  # noqa: E402
from app.routers import quality as quality_router  # noqa: E402

AUTH_TOKEN = "test-twilio-auth-token"
URL = "http://testserver/messages/inbound"


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")
    monkeypatch.setattr(settings, "feedback_requests_enabled", True)

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
    app.include_router(inbound_messages.router)
    app.include_router(quality_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]
        yield client, Session, admin, org_id


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


def _seed(Session, org_id, *, status="completed", age_minutes=120, asked_ago=None):
    db = Session()
    try:
        p = models.Patient(org_id=org_id, mrn="M1", name="Anitha Rao",
                           phone="+919000000001", contact_consent="granted")
        db.add(p)
        db.flush()
        c = models.CallLog(
            org_id=org_id, patient_id=p.id, direction="inbound", status=status,
            occurred_at=datetime.utcnow() - timedelta(minutes=age_minutes),
            feedback_requested_at=(
                datetime.utcnow() - timedelta(minutes=asked_ago)
                if asked_ago is not None else None
            ),
        )
        db.add(c)
        db.commit()
        return p.id, c.id
    finally:
        db.close()


def _feedback(Session, org_id):
    db = Session()
    try:
        return (
            db.query(models.PatientFeedback)
            .filter(models.PatientFeedback.org_id == org_id)
            .all()
        )
    finally:
        db.close()


def _call(Session, call_id):
    db = Session()
    try:
        return db.query(models.CallLog).filter(models.CallLog.id == call_id).first()
    finally:
        db.close()


# ================================================================ asking

def test_a_completed_call_is_asked_about(ctx, transports):
    client, S, _admin, org = ctx
    _p, call_id = _seed(S, org)
    db = S()
    try:
        assert "asked=1" in jobs.request_call_feedback(db)
    finally:
        db.close()
    assert _call(S, call_id).feedback_requested_at is not None
    assert "1 (poor) to 5 (excellent)" in transports["whatsapp"][0][1]


def test_a_failed_call_is_never_surveyed(ctx, transports):
    """
    That patient is already receiving an apology from the missed-call
    job. Asking them to rate the experience on top of it is close to rude.
    """
    client, S, _admin, org = ctx
    _p, call_id = _seed(S, org, status="failed")
    db = S()
    try:
        jobs.request_call_feedback(db)
    finally:
        db.close()
    assert _call(S, call_id).feedback_requested_at is None
    assert transports["whatsapp"] == []


def test_a_frequent_caller_is_not_asked_every_time(ctx, transports):
    """
    The cooldown is per patient, not per call. Being surveyed after every
    contact is how a clinic teaches people to ignore its messages.
    """
    client, S, _admin, org = ctx
    _p, first = _seed(S, org)
    db = S()
    try:
        jobs.request_call_feedback(db)
        # A second completed call from the same patient, straight after.
        patient_id = _call(S, first).patient_id
        c2 = models.CallLog(
            org_id=org, patient_id=patient_id, direction="inbound", status="completed",
            occurred_at=datetime.utcnow() - timedelta(minutes=120),
        )
        db.add(c2)
        db.commit()
        second = c2.id
        jobs.request_call_feedback(db)
    finally:
        db.close()
    assert _call(S, second).feedback_requested_at is None
    assert len(transports["whatsapp"]) == 1


def test_two_calls_in_one_sweep_produce_one_ask(ctx, transports):
    """
    The cooldown query has to see the ask made moments earlier in the same
    run, which is why the job commits inside the loop.
    """
    client, S, _admin, org = ctx
    _p, _c = _seed(S, org)
    db = S()
    try:
        patient_id = db.query(models.Patient).filter(models.Patient.org_id == org).first().id
        db.add(models.CallLog(
            org_id=org, patient_id=patient_id, direction="inbound", status="completed",
            occurred_at=datetime.utcnow() - timedelta(minutes=130),
        ))
        db.commit()
        jobs.request_call_feedback(db)
    finally:
        db.close()
    assert len(transports["whatsapp"]) == 1


def test_it_is_off_by_default(ctx, transports, monkeypatch):
    """
    Every other message this product sends is something the patient needs.
    This is something the clinic wants, and that is a decision a clinic
    should make rather than inherit.
    """
    client, S, _admin, org = ctx
    monkeypatch.setattr(settings, "feedback_requests_enabled", False)
    _seed(S, org)
    db = S()
    try:
        assert jobs.request_call_feedback(db) == "disabled"
    finally:
        db.close()
    assert transports["whatsapp"] == []


def test_consent_still_gates_it(ctx, transports):
    client, S, _admin, org = ctx
    _p, call_id = _seed(S, org)
    db = S()
    try:
        db.query(models.Patient).filter(models.Patient.org_id == org).first().contact_consent = "declined"
        db.commit()
        jobs.request_call_feedback(db)
    finally:
        db.close()
    assert _call(S, call_id).feedback_requested_at is None


# =============================================================== receiving

def test_a_digit_reply_is_recorded_as_a_rating(ctx):
    client, S, _admin, org = ctx
    _p, call_id = _seed(S, org, asked_ago=30)
    r = _reply(client, "5")
    assert r.status_code == 200
    rows = _feedback(S, org)
    assert len(rows) == 1
    assert rows[0].rating == 5
    assert rows[0].call_id == call_id
    assert rows[0].source == "reply"


def test_anything_they_add_is_kept_verbatim_and_not_parsed(ctx):
    client, S, _admin, org = ctx
    _seed(S, org, asked_ago=30)
    _reply(client, "2 the wait was far too long")
    rows = _feedback(S, org)
    assert rows[0].rating == 2
    assert rows[0].comment == "the wait was far too long"


def test_a_rating_outside_the_scale_is_refused(ctx):
    client, S, _admin, org = ctx
    _seed(S, org, asked_ago=30)
    r = _reply(client, "9")
    assert "number from 1 to 5" in r.text
    assert _feedback(S, org) == []


def test_a_digit_with_nothing_outstanding_is_dropped(ctx):
    """Filed against an arbitrary call, it would be worse than nothing."""
    client, S, _admin, org = ctx
    _seed(S, org)  # never asked
    _reply(client, "5")
    assert _feedback(S, org) == []


def test_the_same_call_is_not_rated_twice(ctx):
    client, S, _admin, org = ctx
    _seed(S, org, asked_ago=30)
    _reply(client, "5")
    _reply(client, "1")
    rows = _feedback(S, org)
    assert len(rows) == 1
    assert rows[0].rating == 5, "the first answer was overwritten"


def test_a_rating_never_collides_with_the_appointment_keywords(ctx):
    """
    Checked before CONFIRM / CANCEL / STOP so a "1" is never read as
    anything else -- which is exactly why the ask uses a number.
    """
    client, S, _admin, org = ctx
    _p, _c = _seed(S, org, asked_ago=30)
    db = S()
    try:
        patient_id = db.query(models.Patient).filter(models.Patient.org_id == org).first().id
        a = models.Appointment(
            org_id=org, patient_id=patient_id, title="Consultation", status="upcoming",
            start_at=datetime.now() + timedelta(days=3),
            day_label="Fri", time_label="10:00 AM",
        )
        db.add(a)
        db.commit()
        appt_id = a.id
    finally:
        db.close()

    _reply(client, "1")
    db = S()
    try:
        appt = db.query(models.Appointment).filter(models.Appointment.id == appt_id).first()
        assert appt.status == "upcoming", "a rating cancelled an appointment"
        assert appt.confirmation_status is None
    finally:
        db.close()
    assert _feedback(S, org)[0].rating == 1


def test_stop_still_wins_over_everything(ctx):
    client, S, _admin, org = ctx
    _seed(S, org, asked_ago=30)
    _reply(client, "STOP")
    db = S()
    try:
        p = db.query(models.Patient).filter(models.Patient.org_id == org).first()
        assert p.contact_consent == "declined"
    finally:
        db.close()


# ================================================================ summary

def test_the_summary_reports_none_not_zero_when_nobody_answered(ctx):
    client, S, admin, org = ctx
    r = client.get("/quality/feedback", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"] == 0
    assert body["average_rating"] is None, "an average of nothing is not zero out of five"


def test_the_summary_keeps_the_distribution_not_just_the_average(ctx):
    """
    Two 1s and two 5s average to 3, which describes nobody's experience.
    The split has to stay visible.
    """
    client, S, admin, org = ctx
    db = S()
    try:
        for rating in (1, 1, 5, 5):
            db.add(models.PatientFeedback(org_id=org, rating=rating, source="reply"))
        db.commit()
    finally:
        db.close()

    body = client.get("/quality/feedback", headers=admin).json()
    assert body["responses"] == 4
    assert body["average_rating"] == 3.0
    assert body["distribution"]["1"] == 2
    assert body["distribution"]["5"] == 2


def test_the_summary_reports_how_many_were_asked(ctx):
    """
    Responses without the denominator would flatter a clinic that asked
    two hundred people and heard from three.
    """
    client, S, admin, org = ctx
    _seed(S, org, asked_ago=30)
    body = client.get("/quality/feedback", headers=admin).json()
    assert body["asked"] == 1
    assert body["responses"] == 0
