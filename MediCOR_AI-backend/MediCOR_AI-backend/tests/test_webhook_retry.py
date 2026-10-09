"""
Bounded webhook retry (GAP-T4).

F14 shipped one attempt and said plainly why: retrying needs somewhere
durable to hold the attempt, and a per-event delivery table would collect
a row for every call a busy clinic takes. It also named the fix -- "if
retries are wanted, the scheduler is the place". The scheduler now exists.

The three properties that make this safe to add:

  * **Only failures are queued.** A delivery that succeeds writes nothing,
    so the table is empty for a healthy endpoint. That is the direct
    answer to the original objection.

  * **A retry resends the identical bytes.** The payload carries its own
    `delivery_id`, so a receiver can tell the third attempt from a third
    event. Re-serialising the dict would also break the signature, which
    is an HMAC over exactly what went on the wire.

  * **It gives up.** Retrying forever against an endpoint that will never
    accept the payload is the worse failure.

    pytest tests/test_webhook_retry.py -v
"""

import json
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

from app import jobs, models, webhook_service  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

URL = "https://clinic.example.com/hooks"


class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.text = ""


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "webhook_retry_enabled", True)
    monkeypatch.setattr(settings, "webhook_max_attempts", 4)
    monkeypatch.setattr(settings, "webhook_retry_backoff_minutes", "1,5,30")
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
def posts(monkeypatch):
    """Capture every outbound POST, and let a test choose the response."""
    calls = []
    outcome = {"code": 200}

    def fake_post(url, **kw):
        calls.append({
            "url": url,
            "body": kw.get("content"),
            "headers": kw.get("headers", {}),
        })
        return _Resp(outcome["code"])

    monkeypatch.setattr(webhook_service.httpx, "post", fake_post)
    return calls, outcome


def _hook(db, events="refill.requested"):
    org = models.Organization(name="Northside Clinic", suspended=False)
    db.add(org)
    db.flush()
    hook = models.Webhook(
        org_id=org.id, endpoint_url=URL, events_csv=events,
        secret=webhook_service.generate_secret(),
    )
    db.add(hook)
    db.commit()
    # Plain ids, not ORM objects: `_deliver` commits and closes this
    # session, leaving anything loaded beforehand expired AND detached --
    # at which point even reading `hook.id` raises DetachedInstanceError.
    return org.id, hook.id


def _queued(db):
    return db.query(models.WebhookDelivery).all()


def _reload_hook(db, hook_id):
    """Fetch the webhook fresh; see `_hook` for why nothing is held."""
    return db.query(models.Webhook).filter(models.Webhook.id == hook_id).first()


def _deliver(db, org_id, monkeypatch, event="refill.requested"):
    """Run the immediate delivery against this test's session."""
    monkeypatch.setattr(webhook_service, "SessionLocal", lambda: db, raising=False)
    import app.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    webhook_service._deliver(org_id, event, {"refill_request_id": "r1"})


# ============================================== only failures are queued

def test_a_successful_delivery_queues_nothing(db, posts, monkeypatch):
    """
    The answer to F14's objection: a healthy endpoint leaves this table
    empty rather than accumulating a row per event.
    """
    calls, outcome = posts
    outcome["code"] = 200
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)

    assert len(calls) == 1
    assert _queued(db) == []


def test_a_failed_delivery_is_queued_once(db, posts, monkeypatch):
    calls, outcome = posts
    outcome["code"] = 500
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)

    rows = _queued(db)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].attempts == 1
    assert rows[0].last_status == "500"
    assert rows[0].next_attempt_at is not None


# ================================================ a retry is a retry

def test_a_retry_resends_the_identical_body(db, posts, monkeypatch):
    """
    Byte-for-byte, because the signature is an HMAC over what went on the
    wire and the payload carries the delivery id a receiver deduplicates
    on.
    """
    calls, outcome = posts
    outcome["code"] = 500
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)
    first_body = calls[0]["body"]

    jobs.retry_webhook_deliveries(db)
    assert len(calls) == 1, "retried before it was due"

    row = _queued(db)[0]
    row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    jobs.retry_webhook_deliveries(db)
    assert len(calls) == 2
    assert calls[1]["body"] == first_body, "the retry sent a different payload"

    # And the delivery id inside it is the same one.
    payload = json.loads(first_body.decode("utf-8"))
    assert calls[1]["headers"]["X-MedVoice-Delivery"] == payload["delivery_id"]


def test_the_retry_is_signed_with_the_current_secret(db, posts, monkeypatch):
    """
    Recomputed at send time rather than stored, so a clinic that rotates
    its secret between attempts gets something it can verify.
    """
    calls, outcome = posts
    outcome["code"] = 500
    org_id, hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)

    row = _queued(db)[0]
    row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    hook = _reload_hook(db, hook_id)
    hook.secret = webhook_service.generate_secret()  # rotated
    db.commit()
    rotated_secret = hook.secret

    jobs.retry_webhook_deliveries(db)
    expected = webhook_service.sign(rotated_secret, calls[1]["body"])
    assert calls[1]["headers"]["X-MedVoice-Signature"] == expected


def test_a_successful_retry_settles_the_row(db, posts, monkeypatch):
    calls, outcome = posts
    outcome["code"] = 500
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)

    row = _queued(db)[0]
    row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    outcome["code"] = 200
    result = jobs.retry_webhook_deliveries(db)
    assert "delivered=1" in result
    row = _queued(db)[0]
    assert row.status == "delivered"
    assert row.next_attempt_at is None
    assert row.completed_at is not None


# ================================================== bounded, not forever

def test_it_gives_up_after_the_configured_attempts(db, posts, monkeypatch):
    """
    Beyond roughly half an hour a clinic's endpoint is down rather than
    flapping, and retrying forever is the worse failure.
    """
    calls, outcome = posts
    outcome["code"] = 500
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)  # attempt 1

    for _ in range(5):
        row = _queued(db)[0]
        if row.status != "pending":
            break
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        jobs.retry_webhook_deliveries(db)

    row = _queued(db)[0]
    assert row.status == "failed"
    assert row.attempts == settings.webhook_max_attempts
    assert len(calls) == settings.webhook_max_attempts


def test_a_row_not_yet_due_is_left_alone(db, posts, monkeypatch):
    calls, outcome = posts
    outcome["code"] = 500
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)

    jobs.retry_webhook_deliveries(db)
    assert len(calls) == 1
    assert _queued(db)[0].status == "pending"


def test_a_removed_webhook_stops_the_retry(db, posts, monkeypatch):
    """Nowhere left to deliver, so stop rather than retry into the void."""
    calls, outcome = posts
    outcome["code"] = 500
    org_id, hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)

    row = _queued(db)[0]
    row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    hook = _reload_hook(db, hook_id)
    hook.endpoint_url = ""
    db.commit()

    jobs.retry_webhook_deliveries(db)
    assert len(calls) == 1, "retried a webhook with no endpoint"
    assert _queued(db)[0].status == "failed"


# ============================================================ hygiene

def test_settled_rows_are_pruned(db, posts, monkeypatch):
    calls, outcome = posts
    org_id, _hook_id = _hook(db)
    db.add(models.WebhookDelivery(
        org_id=org_id, event="refill.requested", body="{}", status="delivered",
        completed_at=datetime.utcnow() - timedelta(days=90),
    ))
    db.commit()
    result = jobs.retry_webhook_deliveries(db)
    assert "pruned=1" in result
    assert _queued(db) == []


def test_a_recent_settled_row_is_kept(db, posts, monkeypatch):
    calls, outcome = posts
    org_id, _hook_id = _hook(db)
    db.add(models.WebhookDelivery(
        org_id=org_id, event="refill.requested", body="{}", status="failed",
        completed_at=datetime.utcnow(),
    ))
    db.commit()
    jobs.retry_webhook_deliveries(db)
    assert len(_queued(db)) == 1


def test_the_flag_switches_it_off(db, posts, monkeypatch):
    monkeypatch.setattr(settings, "webhook_retry_enabled", False)
    assert jobs.retry_webhook_deliveries(db) == "disabled"


def test_a_failure_is_not_queued_when_retries_are_off(db, posts, monkeypatch):
    calls, outcome = posts
    outcome["code"] = 500
    monkeypatch.setattr(settings, "webhook_retry_enabled", False)
    org_id, _hook_id = _hook(db)
    _deliver(db, org_id, monkeypatch)
    assert _queued(db) == [], "queued a retry while retries were disabled"


def test_an_unparseable_backoff_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "webhook_retry_backoff_minutes", "not,a,number")
    assert webhook_service.backoff_minutes() == [1, 5, 30]


def test_it_is_registered_with_the_scheduler():
    from app import scheduler

    scheduler._REGISTRY.clear()
    jobs.register_all()
    assert "retry_webhook_deliveries" in {j.name for j in scheduler.registered_jobs()}
    scheduler._REGISTRY.clear()
