"""
The consent gate explains itself in the job run row (PF-05 follow-up).

THE FAILURE THIS PREVENTS

`Patient.contact_consent` is tri-state, and NULL -- meaning nobody has
ever asked -- is what every row written before migration 0019 holds, and
what the voice agent leaves behind whenever it registers a caller
without asking. `CONTACT_UNASKED_IS_CONSENT` decides how NULL reads and
defaults to the cautious answer.

Put those together and a clinic that has just configured SMTP or Twilio
sends **nothing at all**, correctly, to a roster nobody has been asked
about. Every outreach job reported a bare `sent=0`, which is exactly
what a broken transport reports. The clinic's next move is to go looking
for a fault that does not exist.

That is the same reason `evaluate_recent_calls` is registered with the
scheduler even while switched off: a run row has to distinguish
"correctly doing nothing" from "failing".

WHAT IS DELIBERATELY *NOT* DONE HERE

The gate is not moved. It stays inside `_send_patient_email` /
`_send_patient_whatsapp` -- a check each new caller has to remember is
one that eventually gets forgotten, and the failure mode (messaging
somebody who asked not to be) does not show up in testing. These jobs
call the same `may_contact` predicate purely to count, so the tally and
the decision cannot drift apart.

    pytest tests/test_consent_is_a_reported_reason.py -v
"""

import itertools
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
    # A configured transport, so "nothing was sent" can only be the
    # consent gate and never a missing channel.
    monkeypatch.setattr(notification_service, "_email_configured", lambda: True)
    monkeypatch.setattr(notification_service, "_whatsapp_configured", lambda: False)
    monkeypatch.setattr(notification_service, "_send_email", lambda *a, **k: True)
    monkeypatch.setattr(settings, "contact_unasked_is_consent", False)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def org(db):
    o = models.Organization(name="Northside Clinic", suspended=False)
    db.add(o)
    db.commit()
    return o


_mrn = itertools.count(1)


def patient(db, org, consent=None):
    p = models.Patient(
        org_id=org.id, mrn=f"MRN{next(_mrn):04d}", name="Anitha Rao",
        phone="+919876543210", email="anitha@example.test",
        contact_consent=consent, status="Active",
    )
    db.add(p)
    db.commit()
    return p


def upcoming_appointment(db, org, pat):
    doc = models.Doctor(org_id=org.id, name="Dr Rao", specialty="GP",
                        work_start_hour=9, work_end_hour=17)
    db.add(doc)
    db.flush()
    start = datetime.now() + timedelta(hours=2)
    appt = models.Appointment(
        org_id=org.id, patient_id=pat.id, doctor_id=doc.id,
        title="Consultation", patient_name=pat.name,
        start_at=start, end_at=start + timedelta(minutes=30),
        status="upcoming",
    )
    db.add(appt)
    db.commit()
    return appt


# ============================================ the number actually appears

def test_an_unasked_patient_is_counted_not_silently_skipped(db, org):
    pat = patient(db, org, consent=None)
    upcoming_appointment(db, org, pat)

    detail = jobs.send_appointment_reminders(db)
    assert "no_consent=1" in detail, (
        "the reminder job reported zero sends with no reason; that is "
        "indistinguishable from a broken transport"
    )
    assert "reminded 0 of 1" in detail


def test_a_consenting_patient_is_not_counted(db, org):
    pat = patient(db, org, consent="granted")
    upcoming_appointment(db, org, pat)

    detail = jobs.send_appointment_reminders(db)
    assert "no_consent=0" in detail
    assert "reminded 1 of 1" in detail


def test_a_declining_patient_is_counted(db, org):
    pat = patient(db, org, consent="declined")
    upcoming_appointment(db, org, pat)
    assert "no_consent=1" in jobs.send_appointment_reminders(db)


def test_the_permissive_reading_clears_the_count(db, org, monkeypatch):
    """
    The figure tracks the *decision*, not just the column. Flipping
    `CONTACT_UNASKED_IS_CONSENT` must move it, or it is reporting
    something other than what actually gates the send.
    """
    monkeypatch.setattr(settings, "contact_unasked_is_consent", True)
    pat = patient(db, org, consent=None)
    upcoming_appointment(db, org, pat)

    detail = jobs.send_appointment_reminders(db)
    assert "no_consent=0" in detail
    assert "reminded 1 of 1" in detail


# ================================== every outreach job, not just one

@pytest.mark.parametrize("job_name", [
    "send_appointment_reminders",
    "contact_missed_callers",
    "request_call_feedback",
    "offer_waiting_list_slots",
])
def test_every_patient_facing_job_reports_the_reason(job_name):
    """
    One job explaining itself while three stay silent is worse than
    none -- the clinic learns to trust a diagnostic that is only
    sometimes there.
    """
    import inspect

    src = inspect.getsource(getattr(jobs, job_name))
    assert "no_consent" in src, f"{job_name} does not report the consent gate"
    assert "may_contact" in src, (
        f"{job_name} counts something other than the predicate that "
        "actually gates the send"
    )


def test_the_count_uses_the_same_predicate_the_gate_uses(db, org):
    """
    Not a reimplementation of the rule. If the job decided for itself
    what consent means, the reported figure and the actual behaviour
    would drift the first time either changed.
    """
    pat = patient(db, org, consent=None)
    assert notification_service.may_contact(pat) is False

    import inspect
    src = inspect.getsource(jobs.send_appointment_reminders)
    assert "notification_service.may_contact(" in src
    # And it is not deciding for itself.
    assert '"granted"' not in src
    assert '"declined"' not in src


# ======================================== the gate itself did not move

def test_the_gate_is_still_inside_the_send_wrappers():
    """
    The load-bearing part of PF-05. A check a new caller has to remember
    to add is one that eventually gets forgotten, and the failure mode --
    messaging somebody who asked not to be -- does not show up in
    testing. Counting in the jobs must not have become gating in the
    jobs.
    """
    import inspect

    for fn in (notification_service._send_patient_email,
               notification_service._send_patient_whatsapp):
        assert "may_contact" in inspect.getsource(fn), (
            f"{fn.__name__} no longer checks consent; the gate has moved "
            "out of the chokepoint"
        )


def test_a_patient_with_no_contact_details_is_not_a_consent_problem(db, org):
    """
    Two different reasons for sending nothing, and conflating them would
    send the clinic to fix the wrong thing.
    """
    pat = models.Patient(org_id=org.id, mrn=f"MRN{next(_mrn):04d}",
                         name="No Contact", phone=None, email=None,
                         contact_consent="granted", status="Active")
    db.add(pat)
    db.commit()
    upcoming_appointment(db, org, pat)

    detail = jobs.send_appointment_reminders(db)
    assert "no_consent=0" in detail, "no contact details was reported as no consent"
    assert "reminded 0 of 1" in detail
