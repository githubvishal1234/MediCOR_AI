"""
Pre-appointment reminders (F3).

`notification_service` could already compose and send an appointment
message, but only in response to a request — so a reminder, which by
definition fires at a time rather than on a click, was structurally
impossible until the scheduler landed (F2).

The test that matters most here is
`test_the_window_is_the_clinics_wall_clock_not_utc`. `Appointment.start_at`
is clinic wall-clock naive, so computing "within the next 24 hours"
against `datetime.utcnow()` shifts a UTC+5:30 clinic's window by five and
a half hours — reminding some patients twice and others not at all,
silently, because nothing errors.

    pytest tests/test_appointment_reminders.py -v
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

from app import jobs, models, notification_service, timezones  # noqa: E402
from app.database import Base  # noqa: E402


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def org(session_factory):
    db = session_factory()
    try:
        o = models.Organization(name="Northside Clinic", timezone="UTC")
        db.add(o)
        db.commit()
        return o.id
    finally:
        db.close()


@pytest.fixture()
def mail_on(monkeypatch):
    """Pretend SMTP is configured, and capture what would be sent."""
    sent = []
    monkeypatch.setattr(notification_service, "_email_configured", lambda: True)
    monkeypatch.setattr(notification_service, "_whatsapp_configured", lambda: False)
    monkeypatch.setattr(
        notification_service,
        "_send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    return sent


def _appt(db, org_id, hours_ahead=2, status="upcoming", tz_name="UTC",
          with_patient=True, email="p@example.com", phone=None):
    """
    An appointment `hours_ahead` from that clinic's OWN wall clock.

    Built in local time deliberately: `start_at` is clinic wall-clock
    naive, and constructing these in UTC is exactly the mistake the job
    must not make.
    """
    tz = timezones.resolve(tz_name)
    start_local = timezones.now_local(tz) + timedelta(hours=hours_ahead)

    patient_id = None
    if with_patient:
        p = models.Patient(
            org_id=org_id, mrn="MRN1", name="Anitha Rao", email=email, phone=phone,
            # PF-05: reminders are consent-gated now. These tests are about
            # the reminder WINDOW, so they state the precondition explicitly
            # rather than leaning on the global default -- which is
            # deliberately "do not contact" and would silently mute them.
            contact_consent="granted",
        )
        db.add(p)
        db.commit()
        patient_id = p.id

    a = models.Appointment(
        org_id=org_id, title="Consultation", patient_id=patient_id,
        patient_name="Anitha Rao", status=status, start_at=start_local,
    )
    db.add(a)
    db.commit()
    return a.id


# ------------------------------------------------------------- gating

def test_reminders_are_silent_when_no_channel_is_configured(
    session_factory, org, monkeypatch
):
    monkeypatch.setattr(notification_service, "_email_configured", lambda: False)
    monkeypatch.setattr(notification_service, "_whatsapp_configured", lambda: False)

    db = session_factory()
    try:
        _appt(db, org)
        detail = jobs.send_appointment_reminders(db)
    finally:
        db.close()
    assert detail == "skipped: no email or WhatsApp configured"


# ------------------------------------------------------------ the window

def test_an_upcoming_appointment_is_reminded_exactly_once(session_factory, org, mail_on):
    db = session_factory()
    try:
        appt_id = _appt(db, org, hours_ahead=2)

        assert jobs.send_appointment_reminders(db).startswith(
            "reminded 1 of 1 upcoming appointment(s)"
        )
        assert len(mail_on) == 1
        assert "Reminder" in mail_on[0][1]

        db.expire_all()
        assert db.get(models.Appointment, appt_id).reminder_sent_at is not None

        # A second sweep must not send again.
        assert jobs.send_appointment_reminders(db) is None
        assert len(mail_on) == 1
    finally:
        db.close()


def test_an_appointment_beyond_the_lead_time_waits(session_factory, org, mail_on):
    db = session_factory()
    try:
        _appt(db, org, hours_ahead=48)  # default lead is 24h
        assert jobs.send_appointment_reminders(db) is None
    finally:
        db.close()
    assert mail_on == []


def test_an_appointment_already_in_the_past_is_never_reminded(session_factory, org, mail_on):
    """
    Without a lower bound on the window, a slot from last week is still
    inside "before the cutoff" and would be reminded about.
    """
    db = session_factory()
    try:
        _appt(db, org, hours_ahead=-72)
        assert jobs.send_appointment_reminders(db) is None
    finally:
        db.close()
    assert mail_on == []


@pytest.mark.parametrize("status", ["cancelled", "no_show", "done"])
def test_only_upcoming_appointments_are_reminded(session_factory, org, mail_on, status):
    db = session_factory()
    try:
        _appt(db, org, hours_ahead=2, status=status)
        assert jobs.send_appointment_reminders(db) is None
    finally:
        db.close()
    assert mail_on == []


def test_the_window_is_the_clinics_wall_clock_not_utc(session_factory, mail_on):
    """
    THE one. `start_at` is clinic wall-clock naive; a UTC comparison
    shifts a UTC+5:30 clinic's window by 5.5 hours, which for a 24-hour
    lead reminds some patients twice and others not at all.

    An appointment 2 hours from Kolkata's own clock must be reminded.
    """
    db = session_factory()
    try:
        o = models.Organization(name="Kolkata Clinic", timezone="Asia/Kolkata")
        db.add(o)
        db.commit()
        _appt(db, o.id, hours_ahead=2, tz_name="Asia/Kolkata")

        detail = jobs.send_appointment_reminders(db)
    finally:
        db.close()

    assert detail.startswith("reminded 1 of 1 upcoming appointment(s)"), (
        "the window was computed against UTC rather than the clinic's "
        "own wall clock"
    )
    assert len(mail_on) == 1


# --------------------------------------------------- nothing to send to

def test_a_patient_with_no_contact_details_is_not_marked_reminded(
    session_factory, org, mail_on
):
    """
    Nothing was sent, so nothing is claimed — and there is nothing to
    retry either, which is why it stays unmarked rather than looping.
    """
    db = session_factory()
    try:
        appt_id = _appt(db, org, hours_ahead=2, email=None, phone=None)
        assert jobs.send_appointment_reminders(db).startswith(
            "reminded 0 of 1 upcoming appointment(s)"
        )

        db.expire_all()
        assert db.get(models.Appointment, appt_id).reminder_sent_at is None
    finally:
        db.close()
    assert mail_on == []


def test_an_appointment_with_no_patient_attached_is_skipped(session_factory, org, mail_on):
    """A staff-entered slot carrying only a name has nobody to remind."""
    db = session_factory()
    try:
        _appt(db, org, hours_ahead=2, with_patient=False)
        assert jobs.send_appointment_reminders(db).startswith(
            "reminded 0 of 1 upcoming appointment(s)"
        )
    finally:
        db.close()
    assert mail_on == []


# ------------------------------------------------------------ isolation

def test_each_clinic_reminds_its_own_patients_in_its_own_name(session_factory, org, mail_on):
    db = session_factory()
    try:
        other = models.Organization(name="Southside", timezone="UTC")
        db.add(other)
        db.commit()
        _appt(db, org, hours_ahead=2, email="north@example.com")
        _appt(db, other.id, hours_ahead=2, email="south@example.com")

        jobs.send_appointment_reminders(db)
    finally:
        db.close()

    assert sorted(to for to, _s, _b in mail_on) == ["north@example.com", "south@example.com"]
    for to, _subject, body in mail_on:
        expected = "Northside Clinic" if to.startswith("north") else "Southside"
        assert expected in body, "a clinic's reminder named the wrong practice"


# -------------------------------------------------------------- wording

def test_the_message_does_not_invite_a_reply_nothing_reads(session_factory, org, mail_on):
    """
    Two-way confirmation needs an inbound WhatsApp webhook this
    deployment does not have. Promising a channel that is not listening
    is worse than not offering one, so the wording asks them to call.
    """
    db = session_factory()
    try:
        _appt(db, org, hours_ahead=2)
        jobs.send_appointment_reminders(db)
    finally:
        db.close()

    body = mail_on[0][2].lower()
    assert "call us" in body
    assert "reply" not in body


def test_the_confirmation_column_still_has_a_writer():
    """
    Inverted deliberately, and the invariant is unchanged.

    This started life as `test_no_confirmation_column_was_shipped_without_a_writer`,
    asserting the column did NOT exist. That was right at the time: nothing
    could write it, and this codebase had already carried that mistake
    twice — `recording_url`, which had playback code and an API field
    around a column nothing writes, and the pre-HV-4 `voice_minutes_used`.

    PF-06 supplied the writer, so the column is now correct to have. What
    the guard protects is the *rule*, not the absence: a confirmation
    column may exist only while something actually sets it. If the
    self-service link router is ever removed and the column left behind,
    this fails — which is the same failure the original test was written
    to catch, approached from the other side.
    """
    from app.routers import appointment_link

    assert "confirmation_status" in models.Appointment.__table__.c
    assert "reminder_sent_at" in models.Appointment.__table__.c

    # The writer, structurally: the route that sets it must still be there.
    assert hasattr(appointment_link, "confirm_appointment")
    paths = {getattr(r, "path", "") for r in appointment_link.router.routes}
    assert "/appointment-link/confirm" in paths, (
        "confirmation_status has no writer; either restore the route or drop the column"
    )
