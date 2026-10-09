"""
The single-worker constraint announces itself (GAP-T1).

This app holds two things in the process rather than the database:
`rate_limit._attempts` (a module-level dict) and
`realtime.ConnectionManager` (WebSockets in memory). A second worker
gets its own copy of both, so a login limit of 5 becomes 10 and a
dashboard connected to one worker never sees events broadcast by the
other.

The defect was never the limitation -- Redis is the real fix and it is
a genuine piece of work. The defect was that it was **silent**. Nothing
raised, nothing logged, and the dashboard did not look broken, it looked
quiet. An operator's own eyes confirmed the wrong answer.

Two detectors, and the tests below are organised around why neither is
sufficient alone:

  * the command line catches `--workers 4` at startup, before a single
    request has been served under a halved rate limit -- but a process
    is honest when it says it runs one worker, even if three others are
    running beside it;

  * the scheduler claim catches exactly those, because losing a claim to
    a *different* instance id is an observation rather than an inference.

    pytest tests/test_single_worker_guard.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import instance, models, scheduler  # noqa: E402
from app.database import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_peers():
    """Module-level, so one test's peer would otherwise be another's."""
    instance._PEERS.clear()
    yield
    instance._PEERS.clear()


@pytest.fixture()
def db():
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


# ================================================ reading the command line

@pytest.mark.parametrize("argv", [
    ["--workers", "4"],
    ["--workers=4"],
    ["-w", "4"],
    ["app.main:app", "--host", "0.0.0.0", "--workers", "4"],
])
def test_the_worker_flag_is_found_however_it_was_written(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["uvicorn"] + argv)
    assert instance.configured_worker_count() == 4


@pytest.mark.parametrize("var", [
    "WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS",
])
def test_the_environment_variables_are_read_too(monkeypatch, var):
    """
    The flag is not the only way in. `WEB_CONCURRENCY` in particular is
    honoured by both uvicorn and gunicorn and is what a PaaS sets for
    you, without anyone typing it.
    """
    monkeypatch.setattr(sys, "argv", ["uvicorn"])
    monkeypatch.setenv(var, "3")
    assert instance.configured_worker_count() == 3


def test_gunicorn_flags_hidden_in_an_env_var_are_found(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gunicorn"])
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--bind=0.0.0.0:8000 --workers=8")
    assert instance.configured_worker_count() == 8


def test_an_ordinary_single_worker_command_is_silent(monkeypatch):
    """
    A false positive here fails a correct deployment, so the negative
    case matters as much as the positive one.
    """
    monkeypatch.setattr(
        sys, "argv", ["uvicorn", "app.main:app", "--reload", "--port", "8000"]
    )
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS",
                "GUNICORN_CMD_ARGS"):
        monkeypatch.delenv(var, raising=False)
    assert instance.configured_worker_count() == 1


def test_a_worker_flag_with_no_number_does_not_crash(monkeypatch):
    """`--workers` as the final token has nothing after it to read."""
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--workers"])
    assert instance.configured_worker_count() == 1


# ============================================== what the startup gate does

def test_production_refuses_to_start(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "4"])
    with pytest.raises(RuntimeError) as exc:
        instance.check_worker_configuration(is_production=True, allow_override=False)

    message = str(exc.value)
    # It must say what is actually wrong, not just "unsupported" -- the
    # whole point is that the consequence is invisible otherwise.
    assert "rate" in message.lower()
    assert "websocket" in message.lower()
    assert "ALLOW_MULTIPLE_WORKERS" in message


def test_development_warns_loudly_and_keeps_running(monkeypatch, caplog):
    """
    Killing a developer's server over this teaches them to stop reading
    warnings. In production the consequence is a weakened auth control on
    a system holding patient data, and there is no reading under which
    continuing is better.
    """
    monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "2"])
    with caplog.at_level(logging.ERROR, logger="instance"):
        n = instance.check_worker_configuration(
            is_production=False, allow_override=False
        )
    assert n == 2
    assert caplog.records, "a second worker in development said nothing at all"
    assert caplog.records[0].levelno >= logging.ERROR


def test_the_override_downgrades_but_still_complains(monkeypatch, caplog):
    """
    It suppresses the check; it does not make the deployment safe, and it
    must not go quiet about that.
    """
    monkeypatch.setattr(sys, "argv", ["uvicorn", "--workers", "4"])
    with caplog.at_level(logging.ERROR, logger="instance"):
        n = instance.check_worker_configuration(
            is_production=True, allow_override=True
        )
    assert n == 4
    assert caplog.records


def test_one_worker_passes_in_production(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS",
                "GUNICORN_CMD_ARGS"):
        monkeypatch.delenv(var, raising=False)
    assert instance.check_worker_configuration(
        is_production=True, allow_override=False
    ) == 1


# ========================================= the detector the flag cannot be

def test_the_claim_records_which_process_won_it(db):
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    row = scheduler._claim(db, job, "bucket-1")
    assert row is not None
    assert row.instance_id == instance.INSTANCE_ID


def test_a_second_process_is_detected_from_a_lost_claim(db, caplog):
    """
    The case `--workers` can never see: two processes started
    separately, each honestly reporting one worker.
    """
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    db.add(models.ScheduledJobRun(
        job_name="j", run_key="bucket-1", status="running",
        instance_id="someothermachine",
        started_at=datetime.utcnow() + timedelta(seconds=1),
    ))
    db.commit()

    with caplog.at_level(logging.ERROR, logger="instance"):
        assert scheduler._claim(db, job, "bucket-1") is None

    assert instance.peer_count() == 1
    assert "someothermachine" in caplog.text


def test_restarting_inside_a_bucket_is_not_a_second_process(db, caplog):
    """
    The false positive that would make this useless. A process that
    restarts inside the same time bucket races *itself* -- which is
    precisely what `run_key` bucketing exists to absorb -- and must not
    be reported as a peer.
    """
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    assert scheduler._claim(db, job, "bucket-1") is not None

    with caplog.at_level(logging.ERROR, logger="instance"):
        assert scheduler._claim(db, job, "bucket-1") is None

    assert instance.peer_count() == 0, "a process reported itself as a peer"
    assert not caplog.records


def test_a_row_from_before_the_migration_reports_nothing(db):
    """
    `instance_id` is nullable and rows predating 0031 hold NULL. An
    unknown claimant is not evidence of anything.
    """
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    db.add(models.ScheduledJobRun(
        job_name="j", run_key="bucket-1", status="running", instance_id=None,
        started_at=datetime.utcnow() + timedelta(seconds=1),
    ))
    db.commit()

    assert scheduler._claim(db, job, "bucket-1") is None
    assert instance.peer_count() == 0


def test_the_same_peer_is_only_shouted_about_once(db, caplog):
    """
    Every tick loses to the same peer. One line per peer, or the signal
    is buried in its own repetition.
    """
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    for bucket in ("b1", "b2", "b3"):
        db.add(models.ScheduledJobRun(
            job_name="j", run_key=bucket, status="running", instance_id="peer",
            started_at=datetime.utcnow() + timedelta(seconds=1),
        ))
    db.commit()

    with caplog.at_level(logging.ERROR, logger="instance"):
        for bucket in ("b1", "b2", "b3"):
            scheduler._claim(db, job, bucket)

    assert len(caplog.records) == 1
    assert instance.peer_count() == 1


def test_the_claim_is_still_an_insert_race(db):
    """
    The multi-process safety story is that the claim is a bare INSERT and
    the UNIQUE constraint decides. The peer lookup added for this feature
    runs *after* the insert has already failed and changes no outcome --
    but it is a SELECT in the same function, so this pins that the
    ordering did not drift into SELECT-then-INSERT.
    """
    import inspect

    src = inspect.getsource(scheduler._claim)
    assert src.index("db.add(row)") < src.index("except IntegrityError")
    assert "_note_whoever_won" in src
    assert src.index("except IntegrityError") < src.index("_note_whoever_won(")
    # And nothing looked before writing.
    assert "db.query" not in src


def test_diagnostics_never_break_a_tick(db, monkeypatch):
    """
    This runs on every lost claim, which is the normal steady state of a
    process that is *not* the leader. A failure here must not stop the
    scheduler -- same posture as audit_service.
    """
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    db.add(models.ScheduledJobRun(
        job_name="j", run_key="bucket-1", status="running", instance_id="peer",
        started_at=datetime.utcnow() + timedelta(seconds=1),
    ))
    db.commit()

    def boom(*a, **kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(db, "query", boom)
    assert scheduler._claim(db, job, "bucket-1") is None


# ================================================================ surface

def test_health_reports_the_numbers_without_declaring_itself_unhealthy():
    """
    A load balancer reading "degraded" would pull the only process out of
    rotation, turning a misconfiguration into an outage. The numbers are
    there to be alerted on; the verdict is not.
    """
    workers = instance.status()
    assert workers["supported"] == 1
    assert workers["configured"] >= 1
    assert workers["peers"] == 0

    # And `/health` is a thin caller of exactly that, with a status field
    # the numbers never change.
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "main.py"),
        encoding="utf-8",
    ).read()
    assert 'return {"status": "ok", "workers": instance.status()}' in src


def test_the_in_process_state_this_is_about_still_exists():
    """
    If either of these ever moves to Redis, this whole guard is a lie
    that will outlive the constraint it describes. Then delete it.
    """
    from app import rate_limit, realtime

    assert isinstance(rate_limit._attempts, dict), (
        "rate limiting is no longer an in-process dict -- re-check GAP-T1"
    )
    assert hasattr(realtime.manager, "_connections"), (
        "realtime no longer holds sockets in-process -- re-check GAP-T1"
    )


def test_a_restart_is_not_mistaken_for_a_second_process(db, caplog):
    """
    The false positive that shipped and was caught in use.

    `INSTANCE_ID` is per *process*, so a restart gets a new one -- and a
    restart inside a live time bucket loses the claim to its own
    predecessor, whose id is genuinely different. A `uvicorn --reload`
    server reported two "peers" against an empty machine that way.

    What separates them is *when*: a claim made before this process
    booted belongs to a previous incarnation. A concurrent process keeps
    claiming buckets after we started.
    """
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    db.add(models.ScheduledJobRun(
        job_name="j", run_key="bucket-1", status="running",
        instance_id="the-process-that-just-died",
        started_at=instance.PROCESS_STARTED_AT - timedelta(minutes=1),
    ))
    db.commit()

    with caplog.at_level(logging.ERROR, logger="instance"):
        assert scheduler._claim(db, job, "bucket-1") is None

    assert instance.peer_count() == 0, (
        "a restart was reported as a second worker; an alarm that cries "
        "wolf is worse than no alarm"
    )
    assert not caplog.records


def test_a_claim_with_no_time_is_not_judged(db):
    """Rows predating 0031 hold no timestamp. Ambiguity errs to silence."""
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    assert instance.report_peer("someone", "j", claimed_at=None) is False


def test_a_genuinely_concurrent_process_is_still_caught(db, caplog):
    """The fix must not have silenced the thing this exists to find."""
    job = scheduler.Job(name="j", interval_seconds=60, fn=lambda s: None)
    db.add(models.ScheduledJobRun(
        job_name="j", run_key="bucket-1", status="running",
        instance_id="a-live-second-worker",
        started_at=instance.PROCESS_STARTED_AT + timedelta(seconds=30),
    ))
    db.commit()

    with caplog.at_level(logging.ERROR, logger="instance"):
        assert scheduler._claim(db, job, "bucket-1") is None
    assert instance.peer_count() == 1
    assert "a-live-second-worker" in caplog.text
