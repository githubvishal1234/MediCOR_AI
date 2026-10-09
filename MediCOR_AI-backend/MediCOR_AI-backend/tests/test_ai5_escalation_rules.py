"""
AI-5 — escalation rules actually reach the voice agent.

Before this, `routing_rules` was a table with a full CRUD API and a page
in the dashboard that nothing ever read. A clinic could write "transfer
immediately on chest pain", see it saved, see it listed, and it changed
nothing about a live call. For a medical product that is worse than an
absent feature: it is a safety-shaped promise the system does not keep.

The rules now travel on the tenant-resolution payload the agent already
fetches at the start of every call, which is why these tests live against
`/agent/resolve-tenant` rather than the staff CRUD endpoints. The backend
decides which rules apply and how they read; the agent only renders them
into its prompt.

    pytest tests/test_ai5_escalation_rules.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI

from conftest import make_test_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, rate_limit
from app.config import settings
from app.database import Base, get_db
from app.routers import agent_runtime as agent_runtime_router

PLATFORM_KEY = "test-platform-key-do-not-use-in-production"


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()
    original_key = settings.agent_platform_key
    settings.agent_platform_key = PLATFORM_KEY

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(agent_runtime_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with make_test_client(app, client=("10.1.2.3", 50000)) as c:
        yield c, TestingSessionLocal

    settings.agent_platform_key = original_key
    rate_limit._attempts.clear()


def _make_clinic(session_factory, name, number, rules=()):
    """`rules` is a sequence of (title, detail, enabled) triples."""
    db = session_factory()
    try:
        org = models.Organization(name=name, timezone="Asia/Kolkata")
        db.add(org)
        db.flush()
        db.add(models.PhoneNumber(org_id=org.id, e164=number, active=True))
        for title, detail, enabled in rules:
            db.add(
                models.RoutingRule(
                    org_id=org.id, title=title, detail=detail, enabled=enabled
                )
            )
        db.commit()
        return org.id
    finally:
        db.close()


def _resolve(client, number):
    return client.post(
        "/agent/resolve-tenant",
        json={"dialed_number": number},
        headers={"X-Platform-Key": PLATFORM_KEY},
    )


def test_enabled_rules_reach_the_agent(ctx):
    client, session_factory = ctx
    _make_clinic(
        session_factory,
        "Bengaluru Clinic",
        "+919876543210",
        rules=[("Medical Emergencies", "Keywords: pain, bleeding, urgent", True)],
    )

    body = _resolve(client, "+919876543210").json()
    assert body["escalation_rules"] == [
        "Medical Emergencies - Keywords: pain, bleeding, urgent"
    ]


def test_disabled_rules_are_withheld(ctx):
    """A rule switched off in the dashboard must stop affecting calls."""
    client, session_factory = ctx
    _make_clinic(
        session_factory,
        "Clinic",
        "+919876543210",
        rules=[
            ("Active Rule", "still on", True),
            ("Retired Rule", "switched off", False),
        ],
    )

    rules = _resolve(client, "+919876543210").json()["escalation_rules"]
    assert rules == ["Active Rule - still on"]


def test_a_rule_with_null_enabled_still_counts(ctx):
    """
    `sync_missing_columns` only ever adds nullable columns and never
    backfills, so a pre-existing row can hold NULL where the model says
    default=True. For an escalation rule, failing open (still transferring
    to a human) is the safe direction.
    """
    client, session_factory = ctx
    _make_clinic(session_factory, "Clinic", "+919876543210", rules=[])
    db = session_factory()
    try:
        org = db.query(models.Organization).first()
        db.add(models.RoutingRule(org_id=org.id, title="Legacy", detail=None, enabled=None))
        db.commit()
    finally:
        db.close()

    assert _resolve(client, "+919876543210").json()["escalation_rules"] == ["Legacy"]


def test_rules_never_cross_between_clinics(ctx):
    client, session_factory = ctx
    _make_clinic(
        session_factory, "Clinic A", "+911111111111",
        rules=[("A only", "clinic A policy", True)],
    )
    _make_clinic(
        session_factory, "Clinic B", "+922222222222",
        rules=[("B only", "clinic B policy", True)],
    )

    assert _resolve(client, "+911111111111").json()["escalation_rules"] == [
        "A only - clinic A policy"
    ]
    assert _resolve(client, "+922222222222").json()["escalation_rules"] == [
        "B only - clinic B policy"
    ]


def test_a_clinic_with_no_rules_sends_an_empty_list(ctx):
    """The agent adds no ESCALATION section at all in this case."""
    client, session_factory = ctx
    _make_clinic(session_factory, "Clinic", "+919876543210", rules=[])

    assert _resolve(client, "+919876543210").json()["escalation_rules"] == []


def _service_api_key(session_factory, org_id):
    """A service API key row, as the voice agent authenticates with."""
    from app.security import generate_api_key, hash_api_key

    plaintext, prefix = generate_api_key()
    db = session_factory()
    try:
        db.add(
            models.APIKey(
                org_id=org_id,
                label="Voice Agent (test)",
                key_prefix=prefix,
                hashed_key=hash_api_key(plaintext),
                revoked=False,
            )
        )
        db.commit()
    finally:
        db.close()
    return {"X-API-Key": plaintext}


# ---------------------------------------------------------------------
# The single-tenant fallback path
#
# With no BACKEND_PLATFORM_KEY the agent never calls resolve-tenant at
# all, so rules cannot arrive that way. Without /agent/escalation-rules
# the whole feature would be inert in that mode — which is the mode most
# deployments actually run in.
# ---------------------------------------------------------------------

def test_the_agent_can_fetch_rules_with_its_own_api_key(ctx):
    client, session_factory = ctx
    org_id = _make_clinic(
        session_factory, "Clinic", "+919876543210",
        rules=[("Medical Emergencies", "Keywords: pain, bleeding", True)],
    )
    headers = _service_api_key(session_factory, org_id)

    r = client.get("/agent/escalation-rules", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == ["Medical Emergencies - Keywords: pain, bleeding"]


def test_the_fallback_route_honours_the_enabled_flag(ctx):
    client, session_factory = ctx
    org_id = _make_clinic(
        session_factory, "Clinic", "+919876543210",
        rules=[("On", "live", True), ("Off", "retired", False)],
    )
    headers = _service_api_key(session_factory, org_id)

    assert client.get("/agent/escalation-rules", headers=headers).json() == ["On - live"]


def test_the_fallback_route_is_org_scoped(ctx):
    """A key belonging to clinic A must never see clinic B's rules."""
    client, session_factory = ctx
    org_a = _make_clinic(
        session_factory, "Clinic A", "+911111111111", rules=[("A only", "a", True)]
    )
    _make_clinic(
        session_factory, "Clinic B", "+922222222222", rules=[("B only", "b", True)]
    )
    headers = _service_api_key(session_factory, org_a)

    assert client.get("/agent/escalation-rules", headers=headers).json() == ["A only - a"]


def test_the_fallback_route_refuses_an_unauthenticated_caller(ctx):
    client, _ = ctx
    assert client.get("/agent/escalation-rules").status_code in (401, 403)


def test_the_prompt_cannot_be_grown_without_limit(ctx):
    """
    These lines go into the system prompt of every call, and Groq counts
    prompt tokens against the same TPM budget as output. A clinic with
    fifty rules must not quietly make every call more expensive.
    """
    client, session_factory = ctx
    _make_clinic(
        session_factory,
        "Wordy Clinic",
        "+919876543210",
        rules=[(f"Rule {i:02d}", "x" * 400, True) for i in range(20)],
    )

    rules = _resolve(client, "+919876543210").json()["escalation_rules"]
    assert len(rules) == agent_runtime_router.MAX_ESCALATION_RULES
    assert all(len(r) <= agent_runtime_router.MAX_RULE_CHARS for r in rules)
