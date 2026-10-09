"""
Closing two loops that left patients waiting: PF-11 and PF-12.

**PF-11 — the refill outcome.** The agent tells a caller, truthfully, that
their repeat-prescription request has been passed to the clinic. Then
nothing happened, so they telephoned to find out -- which is precisely the
call the feature was built to remove. The decision was already recorded
with who made it; nobody was telling the patient.

**PF-12 — the missed call.** A callback queue has existed since HV-7 and
the stale-call reaper fills it, but nothing ever acted on it. A patient
was contacted only if a staff member noticed the queue.

The rule doing the most work here is the one about `callback_status`: an
automatic message must not mark the human queue as contacted, or it
quietly empties the list staff are meant to work.

    pytest tests/test_missed_call_and_refill_outreach.py -v
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


@pytest.fixture()
def db(monkeypatch):
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
        lambda to, subject, body: sent["email"].append((to, subject, body)),
    )
    monkeypatch.setattr(
        notification_service, "_send_twilio_message",
        lambda to, body, channel="whatsapp": sent[channel].append((to, body)),
    )
    return sent


def _org(db, name="Northside Clinic"):
    o = models.Organization(name=name, suspended=False)
    db.add(o)
    db.flush()
    return o


def _patient(db, org, **kw):
    p = models.Patient(
        org_id=org.id, mrn="MRN1", name="Anitha Rao",
        phone="+919000000001", email="a@example.com",
        contact_consent=kw.pop("contact_consent", "granted"),
        **kw,
    )
    db.add(p)
    db.flush()
    return p


def _missed_call(db, org, patient, *, age_minutes=90, status="pending"):
    c = models.CallLog(
        org_id=org.id, patient_id=patient.id if patient else None,
        direction="inbound", status="failed",
        occurred_at=datetime.utcnow() - timedelta(minutes=age_minutes),
        callback_status=status,
    )
    db.add(c)
    db.flush()
    return c


# ============================================================ PF-12

def test_a_missed_call_gets_an_apology(db, transports, monkeypatch):
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org))
    db.commit()

    result = jobs.contact_missed_callers(db)
    assert "contacted=1" in result
    assert call.callback_message_sent_at is not None
    assert transports["whatsapp"], "nothing was sent"


def test_it_does_not_mark_the_human_queue_as_contacted(db, transports, monkeypatch):
    """
    The load-bearing rule. `callback_status` is the queue staff work; a
    machine apologising is not a person having called back. Writing
    "contacted" there would silently empty that queue.
    """
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org))
    db.commit()

    jobs.contact_missed_callers(db)
    assert call.callback_status == "pending", (
        "the automatic message marked the human callback queue as contacted"
    )
    assert call.callback_message_sent_at is not None


def test_nobody_is_contacted_twice(db, transports, monkeypatch):
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    _missed_call(db, org, _patient(db, org))
    db.commit()

    jobs.contact_missed_callers(db)
    first = len(transports["whatsapp"])
    jobs.contact_missed_callers(db)
    assert len(transports["whatsapp"]) == first, "the same patient was messaged twice"


def test_a_human_gets_the_first_chance(db, transports, monkeypatch):
    """
    This exists to catch what staff did not get to, not to race them to
    the patient.
    """
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    monkeypatch.setattr(settings, "missed_call_outreach_delay_minutes", 30)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org), age_minutes=5)
    db.commit()

    jobs.contact_missed_callers(db)
    assert call.callback_message_sent_at is None
    assert transports["whatsapp"] == []


def test_stale_misses_are_left_alone(db, transports, monkeypatch):
    """
    A patient who rang four days ago and gave up has moved on. An apology
    then is noise, not service.
    """
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    monkeypatch.setattr(settings, "missed_call_outreach_max_age_hours", 24)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org), age_minutes=60 * 96)
    db.commit()

    jobs.contact_missed_callers(db)
    assert call.callback_message_sent_at is None


def test_a_resolved_callback_is_not_chased(db, transports, monkeypatch):
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org), status="resolved")
    db.commit()

    jobs.contact_missed_callers(db)
    assert call.callback_message_sent_at is None


def test_a_patient_who_declined_contact_is_not_messaged(db, transports, monkeypatch):
    """
    The PF-05 gate applies here as everywhere -- and because nothing was
    dispatched, the call is left unmarked rather than being recorded as
    handled.
    """
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org, contact_consent="declined"))
    db.commit()

    jobs.contact_missed_callers(db)
    assert transports["whatsapp"] == [] and transports["email"] == []
    assert call.callback_message_sent_at is None


def test_a_suspended_clinic_cannot_spend_on_the_platform(db, transports, monkeypatch):
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    org.suspended = True
    call = _missed_call(db, org, _patient(db, org))
    db.commit()

    jobs.contact_missed_callers(db)
    assert call.callback_message_sent_at is None


def test_the_flag_switches_it_off_without_removing_it(db, transports, monkeypatch):
    """
    Registered either way, so `scheduled_job_runs` shows it ticking and
    doing nothing -- the difference between "off" and "broken".
    """
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", False)
    org = _org(db)
    call = _missed_call(db, org, _patient(db, org))
    db.commit()

    assert jobs.contact_missed_callers(db) == "disabled"
    assert call.callback_message_sent_at is None


def test_the_message_promises_nothing_it_cannot_keep(db, transports, monkeypatch):
    """
    It apologises and invites them to try again. It does NOT say anyone
    will call them back: the queue is worked by people on their own
    schedule, and an unkept promise costs more than no promise.
    """
    monkeypatch.setattr(settings, "missed_call_outreach_enabled", True)
    org = _org(db)
    _missed_call(db, org, _patient(db, org))
    db.commit()

    jobs.contact_missed_callers(db)
    body = transports["whatsapp"][0][1].lower()
    assert "call us again" in body
    for promise in ("we will call you", "we'll call you", "call you back"):
        assert promise not in body, f"the message promises {promise!r}"


def test_it_is_registered_with_the_scheduler():
    from app import scheduler

    scheduler._REGISTRY.clear()
    jobs.register_all()
    assert "contact_missed_callers" in {j.name for j in scheduler.registered_jobs()}
    # Leave the registry as it was found: the scheduler is module-level
    # state and a later test registering fakes would inherit these.
    scheduler._REGISTRY.clear()


# ============================================================ PF-11

def _refill(db, org, patient, status="pending", medication="Metformin"):
    r = models.RefillRequest(
        org_id=org.id, patient_id=patient.id, medication=medication, status=status,
    )
    db.add(r)
    db.flush()
    return r


def test_an_approval_tells_the_patient_where_to_collect(db, transports):
    org = _org(db)
    p = _patient(db, org)
    notification_service.notify_refill_decision(p, "Metformin", "approved", org.name)
    body = transports["whatsapp"][0][1]
    assert "Metformin" in body
    assert "approved" in body.lower()
    assert "collect" in body.lower()


def test_a_decline_carries_the_outcome_and_nothing_clinical(db, transports):
    """
    It says the clinic will be in touch and stops there. It does not say
    why, name a reason, or advise on the medicine -- the same boundary the
    voice agent works under. Whoever declined it knows the reason and can
    give it properly.
    """
    org = _org(db)
    p = _patient(db, org)
    notification_service.notify_refill_decision(p, "Metformin", "declined", org.name)
    body = transports["whatsapp"][0][1].lower()
    assert "could not be approved" in body
    assert "in touch" in body
    for leak in ("because", "reason", "dose", "instead", "stop taking"):
        assert leak not in body, f"the decline message leaked {leak!r}"


def test_the_receipt_states_what_was_recorded_and_nothing_more(db, transports):
    """
    PF-09. The receipt exists because a spoken conversation leaves nothing
    behind and a decision may be days away.
    """
    org = _org(db)
    p = _patient(db, org)
    notification_service.notify_refill_recorded(p, "Metformin", org.name)
    body = transports["whatsapp"][0][1]
    assert "Metformin" in body
    assert "recorded" in body.lower()
    assert "review" in body.lower()


def test_the_receipt_cannot_be_read_as_an_approval(db, transports):
    """
    This is F9's whole safety argument restated in another medium.

    The agent records a refill request and can never approve one; the tool
    result it sees is deliberately narrow so it has no material with which
    to imply otherwise. A written receipt saying "your prescription is
    being prepared" would undo that from the other end -- so it says what
    was recorded, says a person will review it, and tells the patient to
    keep following their current prescription meanwhile.
    """
    org = _org(db)
    p = _patient(db, org)
    notification_service.notify_refill_recorded(p, "Metformin", org.name)
    body = transports["whatsapp"][0][1].lower()

    assert "not an approval" in body
    assert "keep following your current" in body
    for implies_approval in (
        "approved", "ready", "prepared", "collect", "dispensed", "on its way",
    ):
        assert implies_approval not in body, (
            f"the receipt implies approval with {implies_approval!r}"
        )


def test_the_receipt_carries_no_clinical_advice(db, transports):
    org = _org(db)
    p = _patient(db, org)
    notification_service.notify_refill_recorded(p, "Metformin", org.name)
    body = transports["whatsapp"][0][1].lower()
    for clinical in ("dose", "mg", "twice a day", "stop taking", "increase"):
        assert clinical not in body, f"the receipt leaked clinical guidance: {clinical!r}"


def test_the_receipt_respects_consent(db, transports):
    org = _org(db)
    p = _patient(db, org, contact_consent="declined")
    notification_service.notify_refill_recorded(p, "Metformin", org.name)
    assert transports["whatsapp"] == [] and transports["email"] == []


def test_the_refill_notification_respects_consent(db, transports):
    org = _org(db)
    p = _patient(db, org, contact_consent="declined")
    notification_service.notify_refill_decision(p, "Metformin", "approved", org.name)
    assert transports["whatsapp"] == [] and transports["email"] == []
