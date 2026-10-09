"""
HV-4 — usage metering and plan limits.

`Organization.voice_minutes_used` was **never incremented anywhere**: set
once by seed.py to 3450 and read by the clinic's Billing page, which
therefore showed "3,450 / 5,000 minutes" forever. Super admin meanwhile
computed the real figure from `sum(CallLog.duration_seconds)/60`, so the
two views of the same number disagreed permanently (finding C8).

The fix is to compute rather than count, and
`test_the_clinic_and_super_admin_now_agree` is the test that pins it —
that contradiction cannot come back while usage has one source.

Separately, `Plan.user_limit` / `patient_limit` / `ehr_access` /
`knowledge_base_access` were stored, shown in the admin UI, and never
checked at request time. They are checked now, with one deliberate
exception documented in
`test_the_voice_agent_can_still_register_a_caller_at_the_patient_limit`.

    pytest tests/test_hv4_usage_metering.py -v
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

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, usage_service
from app.database import Base


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def org(db):
    o = models.Organization(
        name="Northside Clinic",
        timezone="UTC",
        voice_minutes_limit=100,
        # The stale value the Billing page used to display forever.
        voice_minutes_used=3450,
    )
    db.add(o)
    db.commit()
    return o


def _call(db, org, seconds, when=None, call_id=None):
    call = models.CallLog(
        id=call_id or f"call{seconds}{id(when)}",
        org_id=org.id,
        direction="inbound",
        status="completed",
        duration_seconds=seconds,
        occurred_at=when or datetime.utcnow(),
    )
    db.add(call)
    db.commit()
    return call


# ---------- the C8 contradiction ----------

def test_usage_is_computed_from_calls_not_read_from_the_stale_column(db, org):
    """
    The column says 3450. Reality is two calls totalling 5 minutes.
    Usage must report reality.
    """
    _call(db, org, 180)
    _call(db, org, 120)
    assert usage_service.voice_minutes_used(db, org) == 5.0
    assert org.voice_minutes_used == 3450  # column untouched, and unread


def test_the_clinic_and_super_admin_now_agree(db, org):
    """
    Pins finding C8 closed.

    Super admin computes `sum(duration_seconds)/60` over the period.
    The clinic-facing figure must be derived the same way, or the two
    pages disagree about the same number — which is exactly what happened
    when one was a stored counter nothing incremented.
    """
    _call(db, org, 90)
    _call(db, org, 150)

    start, end = usage_service.current_period_bounds(org)
    admin_view = round(
        (
            db.query(func.sum(models.CallLog.duration_seconds))
            .filter(
                models.CallLog.org_id == org.id,
                models.CallLog.occurred_at >= start,
                models.CallLog.occurred_at < end,
            )
            .scalar()
            or 0
        )
        / 60,
        1,
    )
    assert usage_service.voice_minutes_used(db, org) == admin_view


def test_no_calls_means_zero_not_the_seeded_number(db, org):
    assert usage_service.voice_minutes_used(db, org) == 0.0


# ---------- billing period ----------

def test_only_this_billing_month_counts(db, org):
    _call(db, org, 600, when=datetime.utcnow(), call_id="thismonth")
    _call(db, org, 6000, when=datetime.utcnow() - timedelta(days=70), call_id="old")
    assert usage_service.voice_minutes_used(db, org) == 10.0


def test_the_period_is_the_clinics_calendar_month_not_utcs(db):
    """
    A clinic at UTC+5:30 would otherwise see its month roll over at
    5:30 AM on the 1st, and a call just after local midnight on the 1st
    would be billed to the previous month.
    """
    utc_org = models.Organization(name="UTC Clinic", timezone="UTC")
    ist_org = models.Organization(name="IST Clinic", timezone="Asia/Kolkata")
    db.add_all([utc_org, ist_org])
    db.commit()

    utc_start, _ = usage_service.current_period_bounds(utc_org)
    ist_start, _ = usage_service.current_period_bounds(ist_org)
    # IST is ahead, so its local month starts earlier in UTC terms.
    assert ist_start != utc_start
    assert (utc_start - ist_start) == timedelta(hours=5, minutes=30)


def test_an_unknown_timezone_falls_back_rather_than_raising(db):
    o = models.Organization(name="Bad TZ", timezone="Not/AZone")
    db.add(o)
    db.commit()
    start, end = usage_service.current_period_bounds(o)
    assert start < end


# ---------- snapshot & limits ----------

def test_snapshot_reports_percentage_and_over_limit(db, org):
    _call(db, org, 60 * 80)  # 80 of 100 minutes
    snap = usage_service.usage_snapshot(db, org)
    assert snap["minutes_used"] == 80.0
    assert snap["minutes_limit"] == 100
    assert snap["usage_pct"] == 80.0
    assert snap["over_limit"] is False

    _call(db, org, 60 * 30, call_id="over")
    assert usage_service.usage_snapshot(db, org)["over_limit"] is True


def test_a_zero_limit_never_divides_by_zero(db):
    o = models.Organization(name="No Limit", timezone="UTC", voice_minutes_limit=0)
    db.add(o)
    db.commit()
    snap = usage_service.usage_snapshot(db, o)
    assert snap["usage_pct"] == 0.0
    assert snap["over_limit"] is False


def test_an_assigned_plan_overrides_the_org_column(db, org):
    plan = models.Plan(name="Enterprise", voice_minutes_limit=9000)
    db.add(plan)
    db.flush()
    db.add(models.Subscription(org_id=org.id, plan_id=plan.id))
    db.commit()
    assert usage_service.effective_limit(db, org) == 9000


# ---------- threshold warnings ----------

def test_a_warning_fires_on_the_crossing_only(db, org, monkeypatch):
    """
    Warning on "currently over" would fire on every call for the rest of
    the month and train staff to ignore it. It fires on the transition.
    """
    sent = []
    import app.realtime as realtime

    monkeypatch.setattr(realtime, "notify", lambda *a, **kw: sent.append(kw or a))

    _call(db, org, 60 * 70, call_id="a")  # 70% — no warning
    crossing = _call(db, org, 60 * 15, call_id="b")  # -> 85%, crosses 80
    usage_service.notify_if_threshold_crossed(db, org, crossing.id)
    assert len(sent) == 1

    later = _call(db, org, 60 * 2, call_id="c")  # 87%, still over 80
    usage_service.notify_if_threshold_crossed(db, org, later.id)
    assert len(sent) == 1  # not warned again


def test_crossing_the_limit_warns_that_minutes_are_exhausted(db, org, monkeypatch):
    sent = []
    import app.realtime as realtime

    monkeypatch.setattr(realtime, "notify", lambda *a, **kw: sent.append(kw))

    _call(db, org, 60 * 95, call_id="a")
    crossing = _call(db, org, 60 * 10, call_id="b")  # -> 105%
    usage_service.notify_if_threshold_crossed(db, org, crossing.id)
    assert sent and "exhausted" in sent[-1]["title"].lower()


def test_a_threshold_failure_never_breaks_the_call_update(db, org, monkeypatch):
    import app.realtime as realtime

    def boom(*a, **kw):
        raise RuntimeError("notification backend down")

    monkeypatch.setattr(realtime, "notify", boom)
    call = _call(db, org, 60 * 200, call_id="huge")
    usage_service.notify_if_threshold_crossed(db, org, call.id)  # must not raise


def test_no_warning_when_the_org_has_no_limit(db, monkeypatch):
    sent = []
    import app.realtime as realtime

    monkeypatch.setattr(realtime, "notify", lambda *a, **kw: sent.append(1))
    o = models.Organization(name="Unlimited", timezone="UTC", voice_minutes_limit=0)
    db.add(o)
    db.commit()
    call = _call(db, o, 60 * 500, call_id="x")
    usage_service.notify_if_threshold_crossed(db, o, call.id)
    assert sent == []


# ---------- plan limits that used to be decoration ----------

def test_seat_and_patient_limits_are_unenforced_without_a_plan(db, org):
    """
    Most orgs have no Subscription row. Denying them would break every
    existing clinic the moment this shipped.
    """
    assert usage_service.check_user_limit(db, org.id) is None
    assert usage_service.check_patient_limit(db, org.id) is None
    assert usage_service.has_feature(db, org.id, "ehr_access") is True


def _assign_plan(db, org, status="active", **kw):
    plan = models.Plan(name="Starter", **kw)
    db.add(plan)
    db.flush()
    db.add(models.Subscription(org_id=org.id, plan_id=plan.id, status=status))
    db.commit()
    return plan


def test_the_seat_limit_is_enforced_and_counts_active_users_only(db, org):
    _assign_plan(db, org, user_limit=2)
    for i in range(2):
        db.add(models.User(
            org_id=org.id, email=f"u{i}@clinic.example.com",
            hashed_password="x", full_name=f"U{i}", is_active=True,
        ))
    db.commit()
    assert "2 team members" in usage_service.check_user_limit(db, org.id)

    # Deactivating one frees a seat — a departed colleague should not
    # consume one, or a clinic must delete history to hire.
    db.query(models.User).filter(
        models.User.email == "u0@clinic.example.com"
    ).first().is_active = False
    db.commit()
    assert usage_service.check_user_limit(db, org.id) is None


def test_the_patient_limit_is_enforced(db, org):
    _assign_plan(db, org, patient_limit=1)
    db.add(models.Patient(org_id=org.id, mrn="MRN1", name="A"))
    db.commit()
    assert "patient records" in usage_service.check_patient_limit(db, org.id)


def test_feature_gates_are_read_from_the_plan(db, org):
    _assign_plan(db, org, ehr_access=False, knowledge_base_access=True)
    assert usage_service.has_feature(db, org.id, "ehr_access") is False
    assert usage_service.has_feature(db, org.id, "knowledge_base_access") is True


def test_the_voice_agent_can_still_register_a_caller_at_the_patient_limit(db, org):
    """
    The one deliberate exception, documented rather than implied.

    `POST /patients` is dual-auth — the agent calls it to register a
    caller mid-conversation. A clinic over its patient cap has a billing
    problem, and failing a patient on a live phone call is the wrong way
    to raise it. The limit is checked only when `ctx.actor == "user"`, so
    staff get the upgrade prompt at a moment someone can act on it.

    This asserts the intent at the routing layer rather than the service
    layer, since `check_patient_limit` itself is actor-agnostic.
    """
    import inspect

    from app.routers import patients as patients_router

    source = inspect.getsource(patients_router.create_patient)
    assert 'ctx.actor == "user"' in source
    assert "check_patient_limit" in source


# ---------- Subscription.status, which nothing used to consult ----------

def test_a_cancelled_subscription_revokes_gated_features(db, org):
    """
    The case the grandfather rule gets wrong if it is applied naively.

    `has_feature` returns True for an org with **no** subscription, so
    that enforcement shipping did not lock out every existing clinic. But
    "no plan" and "plan cancelled" are different facts: falling through
    to the grandfather rule for a cancellation would leave a cancelled
    clinic with more access than a paying Starter one.
    """
    _assign_plan(db, org, status="canceled", ehr_access=True, knowledge_base_access=True)

    assert usage_service.has_feature(db, org.id, "ehr_access") is False
    assert usage_service.has_feature(db, org.id, "knowledge_base_access") is False

    message = usage_service.feature_error(db, org.id, "ehr_access")
    assert "canceled" in message
    # It must say what to do about it, not just refuse.
    assert "reactivate" in message.lower()


def test_a_past_due_subscription_keeps_access(db, org):
    """
    Deliberately not revoked. Cutting a clinic's phone AI off over a late
    invoice is a dunning decision, and dunning belongs with HV-5 where a
    payment provider defines "late". Until then a past-due clinic keeps
    exactly what its plan grants.
    """
    _assign_plan(db, org, status="past_due", ehr_access=True, knowledge_base_access=False)

    assert usage_service.has_feature(db, org.id, "ehr_access") is True
    assert usage_service.has_feature(db, org.id, "knowledge_base_access") is False


def test_feature_error_names_the_plan_and_says_what_is_missing(db, org):
    _assign_plan(db, org, ehr_access=False, knowledge_base_access=True)

    message = usage_service.feature_error(db, org.id, "ehr_access")
    assert "Starter" in message
    assert "EHR integration" in message
    # An entitled feature produces no error at all.
    assert usage_service.feature_error(db, org.id, "knowledge_base_access") is None


def test_entitlements_reports_what_the_billing_page_should_say(db, org):
    _assign_plan(
        db, org, user_limit=3, patient_limit=500,
        ehr_access=False, knowledge_base_access=True,
    )
    grants = usage_service.entitlements(db, org.id)
    assert grants == {
        "subscription_status": "active",
        "plan_name": "Starter",
        "user_limit": 3,
        "patient_limit": 500,
        "ehr_access": False,
        "knowledge_base_access": True,
    }


# ---------- the gates degrade on agent-reachable paths, 403 on staff ones ----------

def test_the_ehr_paths_see_no_integration_when_the_plan_excludes_it(db, org):
    """
    The gate lives in `ehr_service.get_active_integration`, which every
    EHR path goes through — so sync, history and status all report the
    same graceful `local_only` state an unconfigured org already had,
    rather than raising. Two of those paths are dual-auth and the voice
    agent reaches them mid-call.
    """
    from app import ehr_service

    db.add(models.EHRIntegration(
        org_id=org.id, name="Epic", connected=True,
        api_credentials_json='{"base_url": "https://ehr.example.com"}',
    ))
    db.commit()

    # With no plan at all the integration is live (grandfather rule).
    assert ehr_service.get_active_integration(db, org.id) is not None

    _assign_plan(db, org, ehr_access=False, knowledge_base_access=True)

    assert ehr_service.get_active_integration(db, org.id) is None
    status = ehr_service.get_status(db, org.id)
    assert status["connected"] is False
    # Distinguished from "nobody connected one", so the dashboard can
    # offer an upgrade instead of a Connect button that 403s.
    assert status["plan_restricted"] is True
    assert "Starter" in status["detail"]


def test_a_gated_knowledge_base_declines_in_words_a_caller_may_hear(db, org):
    """
    The property that matters on a live call: the plan-gated reply is
    **byte-identical** to the nothing-matched reply. A patient must never
    be told the clinic has a billing problem, and the agent already
    treats `source: "none"` as "offer a transfer".

    This also runs before any embedding work, so a gated org costs no
    model load.
    """
    from app import rag_service

    _assign_plan(db, org, ehr_access=True, knowledge_base_access=False)

    result = rag_service.answer_question(db, org.id, "do you treat asthma?")
    assert result["source"] == "none"
    assert result["answer"] == rag_service.NO_ANSWER_TEXT
    assert result["confidence"] == 0.0
    for value in result.values():
        assert "plan" not in str(value).lower()
        assert "upgrade" not in str(value).lower()


# ---------- the same gates, at the HTTP boundary ----------
#
# The service-level tests above prove the rules. These prove the wiring:
# which status code a clinic gets, and — the one that would break live
# calls if it regressed — that the voice agent gets none.

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import rate_limit  # noqa: E402
from app.database import get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import billing as billing_router  # noqa: E402
from app.routers import ehr as ehr_router  # noqa: E402
from app.routers import knowledge_base as kb_router  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402


@pytest.fixture()
def gated(db):
    """
    A clinic on a plan with no knowledge base and no EHR, an admin token,
    and a service key of the kind the voice agent presents.

    Shares the `db` fixture's session factory so the plan assigned here is
    the plan the request sees.
    """
    rate_limit._attempts.clear()

    app = FastAPI()
    for r in (auth_router.router, kb_router.router, ehr_router.router, billing_router.router):
        app.include_router(r)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post(
            "/auth/register",
            json={
                "org_name": "Gated Clinic",
                "full_name": "Admin One",
                "email": "admin@gated.example.com",
                "password": "Passw0rd!",
            },
        )
        assert r.status_code == 200, r.text
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=admin).json()["org_id"]

        raw_key, prefix = generate_api_key()
        db.add(models.APIKey(
            org_id=org_id, label="Voice Agent",
            key_prefix=prefix, hashed_key=hash_api_key(raw_key),
        ))
        plan = models.Plan(name="Starter", ehr_access=False, knowledge_base_access=False)
        db.add(plan)
        db.flush()
        db.add(models.Subscription(org_id=org_id, plan_id=plan.id, status="active"))
        db.commit()

        yield client, admin, {"X-API-Key": raw_key}, org_id


def test_staff_knowledge_base_writes_are_403_when_the_plan_excludes_it(gated):
    client, admin, _key, _org = gated

    faq = client.post(
        "/knowledge-base/faqs",
        json={"question": "Do you bulk bill?", "answer": "Yes."},
        headers=admin,
    )
    assert faq.status_code == 403
    assert "Starter" in faq.json()["detail"]

    source = client.post(
        "/knowledge-base/sources",
        json={"url": "https://clinic.example.com/handbook"},
        headers=admin,
    )
    assert source.status_code == 403

    doc = client.post(
        "/knowledge-base/documents",
        files={"file": ("handbook.txt", b"opening hours are 9 to 5", "text/plain")},
        headers=admin,
    )
    # 403 rather than 415: the plan check runs ahead of the extension and
    # content-type allowlist, so the clinic is told the real blocker
    # instead of being sent to fix a file that would be refused anyway.
    assert doc.status_code == 403


def test_the_voice_agent_is_never_403d_by_a_plan_gate(gated):
    """
    The regression guard for this whole item.

    A 403 on `/knowledge-base/ask` would surface mid-call as a failed tool
    call. The gated org must get a normal 200 whose `source` is "none",
    which the agent already handles by offering a transfer.
    """
    client, _admin, key, _org = gated

    r = client.get("/knowledge-base/ask", params={"q": "do you take Aetna?"}, headers=key)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "none"
    assert "plan" not in body["answer"].lower()

    # Same for the dual-auth EHR reads: a status, not an error.
    status = client.get("/ehr/status", headers=key)
    assert status.status_code == 200, status.text
    assert status.json()["connected"] is False
    assert status.json()["plan_restricted"] is True


def test_clinic_facts_are_never_gated(gated):
    """
    `clinic-info` and `services` are excluded from the knowledge-base gate
    on purpose. An agent that cannot say what time the clinic opens is
    broken, not downgraded — and those are the most common inbound
    questions there are.
    """
    client, admin, key, _org = gated

    saved = client.put(
        "/knowledge-base/clinic-info",
        json={"timings": "Mon-Fri 9am-5pm"},
        headers=admin,
    )
    assert saved.status_code == 200, saved.text

    service = client.post(
        "/knowledge-base/services",
        json={"name": "General consult", "fee_label": "$80"},
        headers=admin,
    )
    assert service.status_code == 200, service.text

    # And the agent can still read them.
    assert client.get("/knowledge-base/clinic-info", headers=key).status_code == 200
    assert client.get("/knowledge-base/services", headers=key).status_code == 200


def test_the_billing_page_reports_what_the_plan_actually_grants(gated):
    """
    The page used to describe every plan as including "premium EHR
    integrations" in hardcoded copy. It now has the real flags to render.
    """
    client, admin, _key, _org = gated

    body = client.get("/billing/plan", headers=admin).json()
    assert body["plan"] == "Starter"
    assert body["ehr_access"] is False
    assert body["knowledge_base_access"] is False
    assert body["status"] == "Active"
    assert body["over_limit"] is False
    # The billing month in the clinic's timezone, not a stored column.
    assert body["period_label"]


def test_a_cancelled_subscription_shows_as_cancelled_on_the_billing_page(gated, db):
    """
    `status` was hardcoded to the schema default, so this chip read a
    green "Active" forever — including for a clinic whose subscription a
    super admin had cancelled.

    `db` is the same session the fixture and its requests share, so the
    change below is visible to the next request without a commit dance.
    """
    client, admin, _key, org_id = gated

    db.query(models.Subscription).filter(
        models.Subscription.org_id == org_id
    ).first().status = "canceled"
    db.commit()

    body = client.get("/billing/plan", headers=admin).json()
    assert body["status"] == "Canceled"
    assert body["subscription_status"] == "canceled"
    # And the features it used to grant are gone with it.
    assert body["knowledge_base_access"] is False
    # The plan is still named — a clinic needs to know *what* was
    # cancelled — but its caps are not advertised, because a cancelled
    # subscription's limits are not enforced either.
    assert body["plan"] == "Starter"
    assert body["user_limit"] is None
