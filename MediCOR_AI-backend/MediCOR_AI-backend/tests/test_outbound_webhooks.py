"""
Outbound webhooks (F14).

The `webhooks` table, `GET`/`PUT /ehr/webhook`, the config screen and a
landing-page bullet reading "Webhooks for custom infrastructure" all
existed before anything ever delivered. `endpoint_url` was written by the
clinic and then only ever read back out in the same endpoint's response,
and the three events on offer — `transcript.completed`,
`intent.requires_action`, `agent.error` — were emitted by nothing.

So the tests that matter are the ones proving it is no longer a promise:
something is posted, it is signed, and a clinic's dead endpoint cannot
take down the booking that triggered it.

    pytest tests/test_outbound_webhooks.py -v
"""

import hashlib
import hmac
import json
import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models, schemas, webhook_service  # noqa: E402
from app.database import Base  # noqa: E402

URL = "https://clinic.example.com/hooks/medvoice"


@pytest.fixture()
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Testing = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # _deliver opens its own session (the request's is closed by then),
    # so the module-global has to be the one it reaches.
    import app.database as app_database

    monkeypatch.setattr(app_database, "SessionLocal", Testing)
    return Testing


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


def _hook(session_factory, org_id, url=URL, events=None, secret="s3cret-value"):
    db = session_factory()
    try:
        h = models.Webhook(
            org_id=org_id,
            endpoint_url=url,
            events_csv=",".join(events or ["call.completed", "appointment.booked"]),
            secret=secret,
        )
        db.add(h)
        db.commit()
        return h.id
    finally:
        db.close()


@pytest.fixture()
def posts(monkeypatch):
    """Capture what would go over the wire."""
    captured = []

    def fake_post(url, content=None, headers=None, timeout=None):
        captured.append({"url": url, "body": content, "headers": headers})

        class _Resp:
            status_code = 204

        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    return captured


# ------------------------------------------------- it actually delivers

def test_a_subscribed_event_is_delivered(session_factory, org, posts):
    _hook(session_factory, org)
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})

    assert len(posts) == 1
    assert posts[0]["url"] == URL

    payload = json.loads(posts[0]["body"])
    assert payload["event"] == "call.completed"
    assert payload["org_id"] == org
    assert payload["data"] == {"call_id": "c1"}
    assert payload["occurred_at"].endswith("Z"), "timestamp must be unambiguously UTC"
    assert payload["delivery_id"]


def test_an_unsubscribed_event_is_not_delivered(session_factory, org, posts):
    _hook(session_factory, org, events=["appointment.booked"])
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})
    assert posts == []


def test_nothing_is_sent_when_no_endpoint_is_configured(session_factory, org, posts):
    _hook(session_factory, org, url=None)
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})
    assert posts == []


def test_a_clinic_with_no_webhook_row_is_a_no_op(session_factory, org, posts):
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})
    assert posts == []


# ------------------------------------------------------------- signing

def test_every_delivery_is_signed_over_the_exact_bytes_sent(session_factory, org, posts):
    """
    The endpoint URL sits on the public internet and is chosen by the
    clinic. Without a signature, anyone who learns it could post
    fabricated appointments into their systems.

    Signed over the raw body, not a re-serialised dict: the receiver
    verifies what arrived, and any difference in key order or spacing
    between two serialisations would fail every check.
    """
    _hook(session_factory, org, secret="top-secret")
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})

    sent = posts[0]
    header = sent["headers"][webhook_service.SIGNATURE_HEADER]
    expected = hmac.new(b"top-secret", sent["body"], hashlib.sha256).hexdigest()

    assert header == f"sha256={expected}"
    assert sent["headers"][webhook_service.EVENT_HEADER] == "call.completed"
    assert sent["headers"][webhook_service.DELIVERY_HEADER]


def test_a_webhook_without_a_secret_gets_one_rather_than_sending_unsigned(
    session_factory, org, posts
):
    """An unsigned delivery is the thing the signature exists to prevent."""
    hook_id = _hook(session_factory, org, secret=None)
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})

    db = session_factory()
    try:
        assert db.get(models.Webhook, hook_id).secret
    finally:
        db.close()
    assert posts[0]["headers"][webhook_service.SIGNATURE_HEADER].startswith("sha256=")


# ----------------------------------------- a clinic's endpoint is not us

def test_a_dead_endpoint_never_raises(session_factory, org, monkeypatch):
    """
    The booking has already been committed by the time we try to tell
    anyone. A clinic's endpoint being down must not turn a succeeded
    booking into a 500, nor tell the voice agent its call update failed.
    """
    _hook(session_factory, org)

    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", boom)
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})  # must not raise


def test_a_timeout_is_recorded_not_raised(session_factory, org, monkeypatch):
    _hook(session_factory, org)

    def slow(*_a, **_k):
        raise httpx.TimeoutException("too slow")

    monkeypatch.setattr(httpx, "post", slow)
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})

    db = session_factory()
    try:
        hook = db.query(models.Webhook).filter_by(org_id=org).one()
    finally:
        db.close()
    assert hook.last_delivery_status == "timeout"
    assert hook.last_delivery_at is not None


def test_the_outcome_is_recorded_so_the_screen_can_say_something_true(
    session_factory, org, posts
):
    _hook(session_factory, org)
    webhook_service._deliver(org, "call.completed", {"call_id": "c1"})

    db = session_factory()
    try:
        hook = db.query(models.Webhook).filter_by(org_id=org).one()
    finally:
        db.close()
    assert hook.last_delivery_status == "204"
    assert hook.last_delivery_at is not None


# ---------------------------------------------------- the vocabulary

def test_the_offered_events_are_ones_that_actually_happen():
    """
    The three this replaced were emitted by nothing, for as long as the
    feature had existed.
    """
    assert set(schemas.WEBHOOK_EVENTS) == {
        "call.completed",
        "appointment.booked",
        "appointment.cancelled",
        "appointment.rescheduled",
        "refill.requested",
    }
    for dead in ("transcript.completed", "intent.requires_action", "agent.error"):
        assert dead not in schemas.WEBHOOK_EVENTS


def test_subscribing_to_an_unknown_event_is_rejected():
    """
    A typo here would fail silently and permanently: the event simply
    never matches a subscription and nothing is ever delivered.
    """
    ok = schemas.WebhookUpdate(events=["call.completed"])
    assert ok.events == ["call.completed"]

    with pytest.raises(ValidationError):
        schemas.WebhookUpdate(events=["transcript.completed"])
    with pytest.raises(ValidationError):
        schemas.WebhookUpdate(events=["call.complete"])


def test_sending_an_unknown_event_is_refused_rather_than_delivered(session_factory, org, posts):
    _hook(session_factory, org, events=["call.completed"])

    class _BT:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, *args):
            self.tasks.append((fn, args))

    bt = _BT()
    assert webhook_service.schedule(bt, org, "not.a.real.event", {}) is False
    assert bt.tasks == []


def test_schedule_is_a_no_op_without_background_tasks(org):
    assert webhook_service.schedule(None, org, "call.completed", {}) is False
