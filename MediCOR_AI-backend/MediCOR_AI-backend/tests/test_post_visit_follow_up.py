"""
Post-visit follow-up (PF-14).

Nothing happened after a visit. Aftercare depended on what the patient
remembered, and a follow-up booking on them thinking of it.

The test that matters most is the first one:

**A clinic that has not written its own wording gets nothing sent.**

Aftercare text is a clinical statement -- what to expect, when to worry,
when to come back. A software vendor composing it on a clinic's behalf,
to go out over that clinic's name, is not a defensible position. So the
mechanism ships and the words do not, exactly as `EMERGENCY_INSTRUCTION`
does for the voice prompt.

    pytest tests/test_post_visit_follow_up.py -v
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
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import jobs, models, notification_service  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

WORDING = "Hi {name}, hope you're feeling better after your visit. Call us if anything changes."


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "post_visit_follow_up_enabled", True)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


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


def _seed(db, *, wording=WORDING, delay=3, days_ago=5, status="done", consent="granted"):
    org = models.Organization(name="Northside Clinic", suspended=False, timezone="UTC")
    db.add(org)
    db.flush()
    if wording is not None:
        db.add(models.ClinicInfo(
            org_id=org.id, follow_up_message=wording, follow_up_delay_days=delay,
        ))
    p = models.Patient(
        org_id=org.id, mrn="M1", name="Anitha Rao", phone="+919000000001",
        email="a@example.com", contact_consent=consent,
    )
    db.add(p)
    db.flush()
    start = datetime.now() - timedelta(days=days_ago)
    a = models.Appointment(
        org_id=org.id, patient_id=p.id, title="Consultation", status=status,
        start_at=start, day_label="Mon", time_label="10:00 AM",
    )
    db.add(a)
    db.commit()
    return org, p, a


# ==================================================== the central rule

def test_a_clinic_that_wrote_nothing_sends_nothing(db, transports):
    """
    The whole safety design. Aftercare wording is a clinical statement and
    belongs to the clinic; the mechanism ships and the words do not.
    """
    _org, _p, appt = _seed(db, wording=None)
    result = jobs.send_post_visit_follow_ups(db)
    assert "clinics_without_wording=1" in result
    assert transports["whatsapp"] == [] and transports["email"] == []
    assert appt.follow_up_sent_at is None


def test_a_blank_wording_counts_as_nothing_written(db, transports):
    _org, _p, appt = _seed(db, wording="   ")
    jobs.send_post_visit_follow_ups(db)
    assert transports["whatsapp"] == []
    assert appt.follow_up_sent_at is None


def test_no_default_wording_exists_anywhere_in_the_code():
    """
    Guards against someone helpfully adding a "sensible default" later.
    The column's default must stay empty, and the sender must refuse to
    invent content.
    """
    col = models.ClinicInfo.__table__.c.follow_up_message
    assert col.default is None, "a default follow-up wording was added"
    assert col.server_default is None

    patient = models.Patient(org_id="o", mrn="m", name="A", phone="+1")
    # No message, nothing sent -- the sender does not fall back to prose
    # of its own.
    assert notification_service.send_post_visit_follow_up(patient, "", "Clinic") is False
    assert notification_service.send_post_visit_follow_up(patient, None, "Clinic") is False


# =========================================================== the sending

def test_the_clinics_own_words_are_what_go_out(db, transports):
    _org, _p, appt = _seed(db)
    jobs.send_post_visit_follow_ups(db)
    body = transports["whatsapp"][0][1]
    assert "hope you're feeling better" in body
    assert appt.follow_up_sent_at is not None


def test_the_only_substitution_is_the_first_name(db, transports):
    """
    A template language here would be a way for a settings screen to raise
    exceptions inside a scheduled job.
    """
    _org, _p, _a = _seed(db, wording="Hello {name}. {unknown} stays literal.")
    jobs.send_post_visit_follow_ups(db)
    body = transports["whatsapp"][0][1]
    assert body.startswith("Hello Anitha.")
    assert "{unknown}" in body, "an unknown placeholder was interpolated or errored"


def test_nobody_is_followed_up_twice(db, transports):
    _seed(db)
    jobs.send_post_visit_follow_ups(db)
    first = len(transports["whatsapp"])
    jobs.send_post_visit_follow_ups(db)
    assert len(transports["whatsapp"]) == first


def test_the_clinics_delay_is_respected(db, transports):
    """Counted from the appointment, not from when the job happens to run."""
    _org, _p, appt = _seed(db, delay=7, days_ago=3)
    jobs.send_post_visit_follow_ups(db)
    assert appt.follow_up_sent_at is None, "followed up before the clinic's delay elapsed"


def test_only_completed_appointments_are_followed_up(db, transports):
    for status in ("upcoming", "cancelled", "no_show"):
        s = db
        _org, _p, appt = _seed(s, status=status)
        jobs.send_post_visit_follow_ups(s)
        assert appt.follow_up_sent_at is None, f"followed up a {status} appointment"


def test_an_old_appointment_is_not_chased_when_the_job_is_switched_on(db, transports, monkeypatch):
    monkeypatch.setattr(settings, "post_visit_follow_up_max_age_days", 30)
    _org, _p, appt = _seed(db, days_ago=400)
    jobs.send_post_visit_follow_ups(db)
    assert appt.follow_up_sent_at is None


def test_consent_still_gates_it(db, transports):
    _org, _p, appt = _seed(db, consent="declined")
    jobs.send_post_visit_follow_ups(db)
    assert transports["whatsapp"] == [] and transports["email"] == []
    assert appt.follow_up_sent_at is None


def test_a_suspended_clinic_sends_nothing(db, transports):
    org, _p, appt = _seed(db)
    org.suspended = True
    db.commit()
    jobs.send_post_visit_follow_ups(db)
    assert appt.follow_up_sent_at is None


def test_the_flag_switches_it_off(db, transports, monkeypatch):
    monkeypatch.setattr(settings, "post_visit_follow_up_enabled", False)
    _seed(db)
    assert jobs.send_post_visit_follow_ups(db) == "disabled"


def test_it_is_registered_with_the_scheduler():
    from app import scheduler

    scheduler._REGISTRY.clear()
    jobs.register_all()
    assert "send_post_visit_follow_ups" in {j.name for j in scheduler.registered_jobs()}
    scheduler._REGISTRY.clear()
