"""
In-process scheduler (F2) and the stale-call reaper.

Two separate things are pinned here.

**The lock.** A second uvicorn worker already makes `rate_limit` and
`realtime` misbehave silently; an in-memory scheduler would have added a
third symptom, each worker firing its own timer so every clinic gets two
of every reminder. The guarantee is a UNIQUE row in `scheduled_job_runs`,
so `test_a_second_process_ticking_the_same_bucket_runs_nothing` is the
most important test in this file — it is the reason the scheduler was
built around a table instead of around a scheduling library.

**The reaper.** `voice-agent/agent.py` finalises a call from a LiveKit
shutdown callback, which does not run if the worker is killed. The row
then stays `in_progress` forever: shown as live on the dashboard, and
counted as *connected* by `analytics_service` (only failed/no_answer are
missed), so it sits in the resolution rate's denominator permanently.

    pytest tests/test_scheduler_and_stale_calls.py -v
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

from app import jobs, models, scheduler  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402


@pytest.fixture()
def session_factory(monkeypatch):
    """
    A throwaway DB wired into the scheduler.

    `run_due_jobs` opens its own Session per job (so one job's rollback
    cannot poison another's), which means the factory itself has to be
    swapped rather than a session injected.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Testing = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(scheduler, "SessionLocal", Testing)
    monkeypatch.setattr(scheduler, "_REGISTRY", [], raising=False)
    yield Testing


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


# ------------------------------------------------------------ the lock

def test_a_job_runs_once_per_bucket(session_factory):
    calls = []
    scheduler.register("counter", 300, lambda db: calls.append(1) or "ran")

    now = datetime(2026, 9, 6, 17, 0, 0)
    assert scheduler.run_due_jobs(now) == ["counter"]
    assert scheduler.run_due_jobs(now) == []   # same bucket, already claimed
    assert scheduler.run_due_jobs(now) == []
    assert len(calls) == 1


def test_a_second_process_ticking_the_same_bucket_runs_nothing(session_factory):
    """
    THE guarantee. Two workers sharing one database, both ticking the
    same instant: exactly one executes. Without the UNIQUE constraint
    both would, and every clinic would get two of every reminder with
    nothing raising an error.
    """
    calls = []
    scheduler.register("reminders", 300, lambda db: calls.append(1) or "sent")

    now = datetime(2026, 9, 6, 17, 0, 0)
    worker_a = scheduler.run_due_jobs(now)
    worker_b = scheduler.run_due_jobs(now)

    assert sorted([len(worker_a), len(worker_b)]) == [0, 1]
    assert len(calls) == 1, "the job ran twice — the claim did not hold"

    db = session_factory()
    try:
        rows = db.query(models.ScheduledJobRun).filter_by(job_name="reminders").all()
    finally:
        db.close()
    assert len(rows) == 1


def test_the_next_bucket_runs_again(session_factory):
    calls = []
    scheduler.register("counter", 300, lambda db: calls.append(1) or "ran")

    base = datetime(2026, 9, 6, 17, 0, 0)
    scheduler.run_due_jobs(base)
    scheduler.run_due_jobs(base + timedelta(seconds=30))    # same 300s bucket
    scheduler.run_due_jobs(base + timedelta(seconds=600))   # a later bucket

    assert len(calls) == 2


def test_a_failing_job_is_recorded_and_does_not_stop_the_others(session_factory):
    order = []

    def boom(db):
        order.append("boom")
        raise RuntimeError("provider exploded")

    scheduler.register("aaa_explodes", 300, boom)
    scheduler.register("bbb_still_runs", 300, lambda db: order.append("ok") or "fine")

    executed = scheduler.run_due_jobs(datetime(2026, 9, 6, 17, 0, 0))

    assert "bbb_still_runs" in executed
    assert order == ["boom", "ok"]

    db = session_factory()
    try:
        failed = db.query(models.ScheduledJobRun).filter_by(job_name="aaa_explodes").one()
        ok = db.query(models.ScheduledJobRun).filter_by(job_name="bbb_still_runs").one()
    finally:
        db.close()
    assert failed.status == "failed"
    assert "provider exploded" in failed.detail
    assert failed.finished_at is not None
    assert ok.status == "succeeded"


def test_registering_the_same_name_twice_replaces_it(session_factory):
    scheduler.register("dup", 300, lambda db: "first")
    scheduler.register("dup", 300, lambda db: "second")
    assert [j.name for j in scheduler.registered_jobs()] == ["dup"]


# ----------------------------------------------------------- the reaper

def _call(db, org_id, status="in_progress", started_minutes_ago=120, transcript=False):
    call = models.CallLog(
        org_id=org_id,
        direction="inbound",
        status=status,
        patient_name="Sam Patient",
        started_at=datetime.utcnow() - timedelta(minutes=started_minutes_ago),
        occurred_at=datetime.utcnow() - timedelta(minutes=started_minutes_ago),
    )
    db.add(call)
    db.commit()
    if transcript:
        db.add(models.TranscriptMessage(call_id=call.id, who="patient", text="Hello?"))
        db.commit()
    return call.id


def test_a_stalled_call_with_a_transcript_is_completed_not_failed(session_factory, org):
    """
    A conversation happened and only the bookkeeping was lost. Marking it
    failed would push a patient who was actually served into the callback
    queue and invite a pointless second call.
    """
    db = session_factory()
    try:
        call_id = _call(db, org, transcript=True)
        assert jobs.reap_stale_calls(db) is not None
        db.expire_all()
        call = db.get(models.CallLog, call_id)
        assert call.status == "completed"
        assert call.ended_at is not None
        assert call.callback_status is None
    finally:
        db.close()


def test_a_silent_stalled_call_is_failed_and_queued_for_callback(session_factory, org):
    db = session_factory()
    try:
        call_id = _call(db, org, transcript=False)
        jobs.reap_stale_calls(db)
        db.expire_all()
        call = db.get(models.CallLog, call_id)
        assert call.status == "failed"
        assert call.callback_status == "pending"
    finally:
        db.close()


def test_a_call_still_inside_the_timeout_is_left_alone(session_factory, org):
    """A long consultation is not an abandoned one."""
    db = session_factory()
    try:
        call_id = _call(db, org, started_minutes_ago=settings.stale_call_timeout_minutes - 5)
        assert jobs.reap_stale_calls(db) is None
        db.expire_all()
        assert db.get(models.CallLog, call_id).status == "in_progress"
    finally:
        db.close()


def test_already_finished_calls_are_untouched(session_factory, org):
    db = session_factory()
    try:
        call_id = _call(db, org, status="completed")
        jobs.reap_stale_calls(db)
        db.expire_all()
        call = db.get(models.CallLog, call_id)
        assert call.status == "completed"
        assert call.ended_at is None
    finally:
        db.close()


def test_the_reaper_never_invents_a_duration(session_factory, org):
    """
    The true length is unknowable once the agent is gone, and a made-up
    one would flow straight into `ai_handled_minutes` — the figure that
    tells a clinic how much staff time the AI saved.
    """
    db = session_factory()
    try:
        call_id = _call(db, org, transcript=True)
        jobs.reap_stale_calls(db)
        db.expire_all()
        call = db.get(models.CallLog, call_id)
        assert call.duration_seconds is None
        assert call.duration is None
        assert call.ai_summary is None
    finally:
        db.close()


def test_reaping_is_idempotent(session_factory, org):
    db = session_factory()
    try:
        _call(db, org, transcript=False)
        first = jobs.reap_stale_calls(db)
        second = jobs.reap_stale_calls(db)
        assert first is not None
        assert second is None, "a second sweep re-reaped an already-closed call"
    finally:
        db.close()


def test_one_orgs_sweep_does_not_reach_another(session_factory, org):
    """
    The reaper is platform-wide by design (it is infrastructure, not a
    tenant action), but each row must keep its own org_id so the
    dashboard broadcast lands in the right clinic.
    """
    db = session_factory()
    try:
        other = models.Organization(name="Southside", timezone="UTC")
        db.add(other)
        db.commit()

        a = _call(db, org, transcript=False)
        b = _call(db, other.id, transcript=False)
        jobs.reap_stale_calls(db)
        db.expire_all()

        assert db.get(models.CallLog, a).org_id == org
        assert db.get(models.CallLog, b).org_id == other.id
        assert db.get(models.CallLog, a).status == "failed"
        assert db.get(models.CallLog, b).status == "failed"
    finally:
        db.close()


# ------------------------------------------- automatic quality evaluation

def test_auto_evaluation_is_off_by_default_and_spends_nothing(session_factory, org, monkeypatch):
    """
    The cost guard, and the most important test here.

    Each judged call is a paid model request. The AI-7 design note says
    outright that judging every call doubles per-call LLM cost for a
    signal nobody reads live — a decision already made and written down.
    A scheduled job must not quietly reverse it.
    """
    from app import evaluation_service, jobs as jobs_mod

    called = []
    monkeypatch.setattr(
        evaluation_service, "run_batch_task",
        lambda org_id, limit: called.append(org_id) or 1,
    )

    db = session_factory()
    try:
        assert jobs_mod.evaluate_recent_calls(db) is None
    finally:
        db.close()
    assert called == [], "the judge was invoked while auto-evaluation was disabled"


def test_when_enabled_without_an_llm_it_says_so_instead_of_looping(session_factory, org, monkeypatch):
    from app import evaluation_service, jobs as jobs_mod, llm_service

    monkeypatch.setattr(settings, "quality_auto_eval_enabled", True)
    monkeypatch.setattr(llm_service, "is_enabled", lambda: False)
    called = []
    monkeypatch.setattr(
        evaluation_service, "run_batch_task",
        lambda org_id, limit: called.append(org_id) or 1,
    )

    db = session_factory()
    try:
        detail = jobs_mod.evaluate_recent_calls(db)
    finally:
        db.close()

    assert detail is not None and "no LLM configured" in detail
    assert called == []


def test_when_enabled_it_judges_each_clinic_within_the_per_org_cap(session_factory, org, monkeypatch):
    from app import evaluation_service, jobs as jobs_mod, llm_service

    db = session_factory()
    try:
        db.add(models.Organization(name="Southside", timezone="UTC"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "quality_auto_eval_enabled", True)
    monkeypatch.setattr(settings, "quality_auto_eval_per_org_limit", 5)
    monkeypatch.setattr(llm_service, "is_enabled", lambda: True)

    seen = []
    monkeypatch.setattr(
        evaluation_service, "run_batch_task",
        lambda org_id, limit: seen.append((org_id, limit)) or 2,
    )

    db = session_factory()
    try:
        detail = jobs_mod.evaluate_recent_calls(db)
    finally:
        db.close()

    assert len(seen) == 2, "not every clinic was swept"
    assert all(limit == 5 for _org, limit in seen), "the per-org cap was not applied"
    assert "judged 4 call(s) across 2 clinic(s)" == detail


def test_a_suspended_clinic_is_not_judged(session_factory, org, monkeypatch):
    """A suspended org should not be consuming paid model calls."""
    from app import evaluation_service, jobs as jobs_mod, llm_service

    db = session_factory()
    try:
        db.add(models.Organization(name="Suspended Clinic", timezone="UTC", suspended=True))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "quality_auto_eval_enabled", True)
    monkeypatch.setattr(llm_service, "is_enabled", lambda: True)
    seen = []
    monkeypatch.setattr(
        evaluation_service, "run_batch_task",
        lambda org_id, limit: seen.append(org_id) or 1,
    )

    db = session_factory()
    try:
        jobs_mod.evaluate_recent_calls(db)
    finally:
        db.close()

    assert seen == [org], "a suspended clinic was swept"


def test_an_org_with_suspended_null_is_still_swept(session_factory, org, monkeypatch):
    """
    `sync_missing_columns` only ever adds nullable columns and never
    backfills, so rows predating the `suspended` column hold NULL. NULL
    means "not suspended" here, not "unknown, skip".
    """
    from app import evaluation_service, jobs as jobs_mod, llm_service

    db = session_factory()
    try:
        db.query(models.Organization).update({"suspended": None})
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "quality_auto_eval_enabled", True)
    monkeypatch.setattr(llm_service, "is_enabled", lambda: True)
    seen = []
    monkeypatch.setattr(
        evaluation_service, "run_batch_task",
        lambda org_id, limit: seen.append(org_id) or 1,
    )

    db = session_factory()
    try:
        jobs_mod.evaluate_recent_calls(db)
    finally:
        db.close()

    assert seen == [org]


def test_the_evaluation_job_is_registered_even_while_disabled(session_factory):
    """
    Registered either way, so `scheduled_job_runs` shows it ticking and
    doing nothing rather than being absent — which is the difference
    between "switched off" and "broken".
    """
    from app import jobs as jobs_mod

    jobs_mod.register_all()
    assert "evaluate_recent_calls" in [j.name for j in scheduler.registered_jobs()]


# -------------------------------------------------- weekly clinic digest

def _admin(db, org_id, email="admin@north.example.com", **kw):
    from app.security import hash_password

    u = models.User(
        org_id=org_id, email=email, full_name="Nora North",
        hashed_password=hash_password("Passw0rd!"),
        role=models.UserRole.admin, is_active=True,
        **kw,
    )
    db.add(u)
    db.commit()
    return u


def test_the_digest_is_silent_when_no_mail_is_configured(session_factory, org, monkeypatch):
    """
    SMTP is unset in this deployment, so the job must report that rather
    than pretend it sent something.
    """
    from app import jobs as jobs_mod, notification_service as ns

    monkeypatch.setattr(settings, "weekly_digest_enabled", True)
    monkeypatch.setattr(ns, "_email_configured", lambda: False)

    db = session_factory()
    try:
        detail = jobs_mod.send_weekly_digests(db)
    finally:
        db.close()
    assert detail == "skipped: no SMTP configured"


def test_the_digest_goes_to_admins_only(session_factory, org, monkeypatch):
    """
    An operational performance report is for whoever runs the practice.
    A receptionist getting a weekly scorecard is noise, not signal.
    """
    from app import notification_service as ns
    from app.security import hash_password

    db = session_factory()
    try:
        _admin(db, org)
        db.add(models.User(
            org_id=org, email="nurse@north.example.com", full_name="Nurse Joy",
            hashed_password=hash_password("Passw0rd!"),
            role=models.UserRole.medical_staff, is_active=True,
        ))
        db.commit()
        recipients = ns._digest_recipients(db, org)
    finally:
        db.close()

    assert [u.email for u in recipients] == ["admin@north.example.com"]


def test_an_unaccepted_invite_is_not_mailed_a_performance_report(session_factory, org):
    """
    Mailing a practice's numbers to an address that has never logged in
    is a small disclosure to a possibly-wrong recipient.
    """
    from datetime import datetime as _dt

    from app import notification_service as ns

    db = session_factory()
    try:
        _admin(db, org, email="pending@north.example.com",
               invite_token_hash="x" * 64, invited_at=_dt.utcnow())
        recipients = ns._digest_recipients(db, org)
    finally:
        db.close()
    assert recipients == []


def test_a_deactivated_admin_is_not_mailed(session_factory, org):
    from app import notification_service as ns
    from app.security import hash_password

    db = session_factory()
    try:
        db.add(models.User(
            org_id=org, email="gone@north.example.com", full_name="Former Admin",
            hashed_password=hash_password("Passw0rd!"),
            role=models.UserRole.admin, is_active=False,
        ))
        db.commit()
        assert ns._digest_recipients(db, org) == []
    finally:
        db.close()


def test_the_digest_reports_an_em_dash_not_zero_when_nothing_concluded(session_factory, org):
    """
    Same rule the Analytics page follows. "0%" reads as "nobody missed an
    appointment", which is a claim; the truth is there was nothing to
    measure.
    """
    from app import models as m, notification_service as ns

    db = session_factory()
    try:
        o = db.get(m.Organization, org)
        subject, body = ns.build_weekly_digest(db, o, days=7)
    finally:
        db.close()

    assert "Northside Clinic" in subject
    assert "No-show rate       —" in body
    assert "Resolved by AI     —" in body
    assert "0%" not in body


def test_the_digest_counts_real_appointments(session_factory, org):
    from datetime import datetime as _dt

    from app import models as m, notification_service as ns

    db = session_factory()
    try:
        for status in ("done", "done", "no_show"):
            db.add(m.Appointment(
                org_id=org, title="Consultation", status=status,
                start_at=_dt.utcnow(),
            ))
        db.commit()
        o = db.get(m.Organization, org)
        _subject, body = ns.build_weekly_digest(db, o, days=7)
    finally:
        db.close()

    assert "No-shows           1" in body
    assert "Completed          2" in body
    assert "No-show rate       33.3%" in body
    assert "1 of 3 concluded" in body


def test_a_failing_clinic_does_not_stop_the_others(session_factory, org, monkeypatch):
    from app import jobs as jobs_mod, notification_service as ns

    db = session_factory()
    try:
        db.add(models.Organization(name="Southside", timezone="UTC"))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "weekly_digest_enabled", True)
    monkeypatch.setattr(ns, "_email_configured", lambda: True)

    seen = []

    def flaky(db_, org_, days):
        seen.append(org_.name)
        if org_.name == "Northside Clinic":
            raise RuntimeError("bad data")
        return 2

    monkeypatch.setattr(ns, "send_weekly_digest", flaky)

    db = session_factory()
    try:
        detail = jobs_mod.send_weekly_digests(db)
    finally:
        db.close()

    assert len(seen) == 2, "the sweep stopped at the failing clinic"
    assert detail == "sent 2 digest email(s) to 1 clinic(s)"
