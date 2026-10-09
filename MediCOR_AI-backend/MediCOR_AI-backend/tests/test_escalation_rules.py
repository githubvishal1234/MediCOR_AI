"""
Escalation the server decides, not the prompt guesses (GAP-T9).

A `RoutingRule` was a title and a sentence, rendered into the agent's
system prompt. Whether it fired was whether a language model recognised
it on that call -- unrepeatable (two identical calls could resolve
differently), untestable (no assertion can pin a model's judgement) and
unauditable afterwards, because no rule ever "applied": a transfer
happened and the reason lived in the model's weights.

The two properties this file exists to hold:

  * **`model` rules still work, unchanged.** This is a layer added
    underneath, not a replacement. A clinic's English keyword list will
    never catch a caller saying the Telugu for "chest pain", so the prose
    rule and the EMERGENCY block are still carrying the wider case. Any
    change that deletes the prompt path to "simplify" this loses calls.

  * **Nothing changed for anybody on deploy.** Every pre-existing rule
    reads as `model`, which is exactly what it was.

    pytest tests/test_escalation_rules.py -v
"""

import os
import sys
from datetime import datetime

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

from app import escalation_service, models, schemas  # noqa: E402
from app.database import Base  # noqa: E402
from app.routers.agent_runtime import escalation_rules_for  # noqa: E402


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


@pytest.fixture()
def org(db):
    o = models.Organization(name="Northside Clinic", suspended=False)
    db.add(o)
    db.commit()
    return o


def rule(db, org, **kw):
    kw.setdefault("title", "A rule")
    kw.setdefault("enabled", True)
    r = models.RoutingRule(org_id=org.id, **kw)
    db.add(r)
    db.commit()
    return r


def doctor(db, org, start=9, end=17):
    d = models.Doctor(
        org_id=org.id, name="Dr Rao", specialty="GP",
        work_start_hour=start, work_end_hour=end,
    )
    db.add(d)
    db.commit()
    return d


# ============================================ nothing changed on deploy

def test_a_rule_from_before_the_migration_is_still_a_prompt_rule(db, org):
    """
    `condition_type` is NULL on every row written before 0032, and
    `sync_missing_columns` never backfills. That must read as `model` --
    the behaviour those rules already had -- and not as "unconfigured,
    therefore inert".
    """
    r = rule(db, org, title="Emergencies", detail="Transfer chest pain",
             condition_type=None)

    assert escalation_service.condition_of(r) == "model"
    assert escalation_rules_for(db, org.id) == ["Emergencies - Transfer chest pain"]
    assert escalation_service.evaluate(db, org.id, utterance="chest pain") is None


def test_an_unrecognised_condition_degrades_to_the_prompt(db, org):
    """
    A downgrade must not silently switch off a clinic's escalation.
    Treating an unknown type as prose keeps the rule working, just less
    sharply, which is the right direction for a safety feature.
    """
    r = rule(db, org, title="Something", condition_type="sentiment_v2")
    assert escalation_service.condition_of(r) == "model"
    assert escalation_rules_for(db, org.id) == ["Something"]


def test_a_null_enabled_still_counts_as_enabled(db, org):
    rule(db, org, title="Emergencies", enabled=None)
    assert escalation_rules_for(db, org.id) == ["Emergencies"]


# ================================================= keyword: deterministic

def test_a_keyword_rule_fires_on_the_words_it_lists(db, org):
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="chest pain, bleeding, unconscious", destination="+15550100")

    match = escalation_service.evaluate(
        db, org.id, utterance="I've got really bad chest pain since this morning"
    )
    assert match is not None
    assert match.title == "Emergencies"
    assert match.matched_on == "chest pain"
    assert match.destination == "+15550100"


def test_the_same_utterance_gives_the_same_answer_every_time(db, org):
    """
    The whole point. A model produced a different judgement per call;
    this cannot.
    """
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="chest pain")
    said = "my chest pain is worse"
    answers = {
        escalation_service.evaluate(db, org.id, utterance=said).rule_id
        for _ in range(5)
    }
    assert len(answers) == 1


def test_a_keyword_rule_does_not_fire_on_unrelated_speech(db, org):
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="chest pain, bleeding")
    assert escalation_service.evaluate(
        db, org.id, utterance="I'd like to book a checkup next Tuesday"
    ) is None


def test_matching_is_case_and_encoding_insensitive(db, org):
    """
    NFKC is not decoration here: Devanagari and Telugu both have
    characters with more than one valid encoding, and a clinic typing a
    keyword on one keyboard while Sarvam emits the other would produce a
    rule that silently never matched.
    """
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="CHEST PAIN")
    assert escalation_service.evaluate(db, org.id, utterance="Chest Pain") is not None


def test_keywords_work_in_the_languages_this_product_actually_takes(db, org):
    """
    The stated reason keyword matching was resisted for so long was that
    a caller saying the Hindi or Telugu for "chest pain" must escalate
    too. That is why `keywords` is a Unicode column, and this is the
    assertion that keeps it one.
    """
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="सीने में दर्द, ఛాతీ నొప్పి")

    assert escalation_service.evaluate(
        db, org.id, utterance="मुझे सीने में दर्द हो रहा है"
    ) is not None
    assert escalation_service.evaluate(
        db, org.id, utterance="నాకు ఛాతీ నొప్పి ఉంది"
    ) is not None


def test_a_keyword_rule_never_fires_on_a_turn_with_no_words(db, org):
    rule(db, org, title="Emergencies", condition_type="keyword", keywords="pain")
    assert escalation_service.evaluate(db, org.id, utterance=None) is None
    assert escalation_service.evaluate(db, org.id, utterance="") is None


# ============================================== unknown caller / hours

def test_unknown_caller_fires_only_when_somebody_actually_looked(db, org):
    """
    `None` means nobody checked, which is NOT "the caller is unknown".
    Conflating them would transfer every call from a deployment with no
    caller ID -- a clinic would switch this on and lose their whole phone
    line to it.
    """
    rule(db, org, title="Screen new callers", condition_type="unknown_caller")

    assert escalation_service.evaluate(db, org.id, caller_is_known=None) is None
    assert escalation_service.evaluate(db, org.id, caller_is_known=True) is None
    assert escalation_service.evaluate(db, org.id, caller_is_known=False) is not None


def test_outside_hours_uses_the_clinics_wall_clock(db, org):
    rule(db, org, title="After hours", condition_type="outside_hours")
    doctor(db, org, start=9, end=17)

    at_2pm = datetime(2026, 9, 8, 14, 0)
    at_9pm = datetime(2026, 9, 8, 21, 0)
    assert escalation_service.evaluate(db, org.id, now_local=at_2pm) is None
    assert escalation_service.evaluate(db, org.id, now_local=at_9pm) is not None


def test_any_doctor_working_means_the_clinic_is_open(db, org):
    rule(db, org, title="After hours", condition_type="outside_hours")
    doctor(db, org, start=9, end=13)
    doctor(db, org, start=16, end=20)

    assert escalation_service.evaluate(
        db, org.id, now_local=datetime(2026, 9, 8, 18, 0)
    ) is None
    assert escalation_service.evaluate(
        db, org.id, now_local=datetime(2026, 9, 8, 14, 0)
    ) is not None


def test_a_clinic_with_no_doctors_does_not_escalate_every_call(db, org):
    """
    The one condition that fails CLOSED, and the reason is in the
    consequence: a fail-open reading would escalate every call at every
    hour, to a line the rule itself presumes is unstaffed. Everything
    else here errs towards reaching a person.
    """
    rule(db, org, title="After hours", condition_type="outside_hours")
    assert escalation_service.evaluate(
        db, org.id, now_local=datetime(2026, 9, 8, 3, 0)
    ) is None


# ================================================= ordering and scoping

def test_priority_decides_which_rule_wins(db, org):
    rule(db, org, title="General", condition_type="keyword",
         keywords="pain", destination="+15550111", priority=50)
    rule(db, org, title="Emergency", condition_type="keyword",
         keywords="pain", destination="+15550999", priority=10)

    match = escalation_service.evaluate(db, org.id, utterance="pain")
    assert match.title == "Emergency"
    assert match.destination == "+15550999"


def test_a_rule_written_before_priority_existed_sorts_at_the_default(db, org):
    rule(db, org, title="Legacy", condition_type="keyword",
         keywords="pain", priority=None)
    rule(db, org, title="Newer", condition_type="keyword",
         keywords="pain", priority=10)
    assert escalation_service.evaluate(db, org.id, utterance="pain").title == "Newer"


def test_a_disabled_rule_never_fires(db, org):
    rule(db, org, title="Off", condition_type="keyword",
         keywords="pain", enabled=False)
    assert escalation_service.evaluate(db, org.id, utterance="pain") is None


def test_rules_do_not_leak_across_clinics(db, org):
    other = models.Organization(name="Other Clinic", suspended=False)
    db.add(other)
    db.commit()
    rule(db, org, title="Ours", condition_type="keyword", keywords="pain")

    assert escalation_service.evaluate(db, other.id, utterance="pain") is None
    assert escalation_service.evaluate(db, org.id, utterance="pain") is not None


# ================================== the prompt and the server do not overlap

def test_a_server_evaluated_rule_is_kept_out_of_the_prompt(db, org):
    """
    Two reasons, and the second is the important one. It would be paid
    for on every turn of every call against the same Groq TPM budget as
    the reply -- and a model that half-matches it can escalate on a call
    the deterministic rule explicitly did not fire for, which puts the
    behaviour straight back to being unrepeatable.
    """
    rule(db, org, title="Keyword rule", condition_type="keyword", keywords="pain")
    rule(db, org, title="Prose rule", detail="if they sound distressed",
         condition_type="model")

    assert escalation_rules_for(db, org.id) == ["Prose rule - if they sound distressed"]


def test_the_two_layers_coexist(db, org):
    """
    Not a migration stage. The keyword list is *narrower* than a sentence
    a model can interpret across languages, so removing the wider layer
    to keep the sharper one loses calls.
    """
    rule(db, org, title="Keyword rule", condition_type="keyword",
         keywords="chest pain")
    rule(db, org, title="Prose rule", detail="if they sound distressed",
         condition_type="model")

    assert escalation_service.has_deterministic_rules(db, org.id) is True
    assert escalation_rules_for(db, org.id)  # the model still gets its rule
    assert escalation_service.evaluate(db, org.id, utterance="chest pain") is not None


def test_a_clinic_with_only_prose_rules_is_not_asked_per_turn(db, org):
    """
    The agent consults the backend once per caller turn. A clinic with
    nothing to evaluate must not pay a round trip for an answer that is
    always None.
    """
    rule(db, org, title="Prose rule", condition_type="model")
    assert escalation_service.has_deterministic_rules(db, org.id) is False


def test_a_disabled_deterministic_rule_does_not_switch_the_check_on(db, org):
    rule(db, org, title="Keyword rule", condition_type="keyword",
         keywords="pain", enabled=False)
    assert escalation_service.has_deterministic_rules(db, org.id) is False


# ============================================================= validation

def test_a_keyword_rule_with_no_keywords_is_refused(db, org):
    """
    It could never match -- silently, and forever. That is the exact
    defect shape this codebase keeps closing (a working control over a
    capability that does nothing), so it is refused while somebody is
    still looking at the screen.
    """
    with pytest.raises(ValueError):
        schemas.RoutingRuleBase(title="X", condition_type="keyword", keywords="")
    with pytest.raises(ValueError):
        schemas.RoutingRuleBase(title="X", condition_type="keyword", keywords=" , ")


def test_an_unknown_condition_type_is_refused_at_the_api(db, org):
    with pytest.raises(ValueError):
        schemas.RoutingRuleBase(title="X", condition_type="vibes")


def test_the_vocabulary_is_served_not_hardcoded():
    """
    F14's lesson: the webhook screen offered three events nothing
    emitted, because the list lived in the frontend.
    """
    assert tuple(schemas.ESCALATION_CONDITIONS) == escalation_service.CONDITION_TYPES

    ui = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "..", "MediCOR_AI-frontend", "MediCOR_AI-frontend",
            "src", "pages", "app", "AgentSettings.jsx",
        ),
        encoding="utf-8",
    ).read()
    assert "listRoutingRuleConditions" in ui, (
        "the settings screen is deciding for itself which conditions exist"
    )


def test_keyword_lists_are_bounded(db, org):
    """A clinic could otherwise paste an essay and have every turn scan it."""
    many = ",".join(f"term{i}" for i in range(200))
    assert len(escalation_service.parse_keywords(many)) == escalation_service.MAX_KEYWORDS


def test_a_rule_with_no_destination_means_the_default(db, org):
    """
    Null is not "nowhere". It is the agent's configured default transfer
    number, which is where every escalation went before rules could name
    their own.
    """
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="pain", destination=None)
    assert escalation_service.evaluate(db, org.id, utterance="pain").destination is None


# =========================================================== the endpoint
#
# Everything above tests the evaluator directly. `POST
# /agent/escalation-check` is the actual contract the voice agent depends
# on, and a verdict that is right in `escalation_service` but never
# reaches the call is worth nothing.

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import get_db  # noqa: E402
from app.routers import agent_runtime  # noqa: E402
from app.security import create_agent_token  # noqa: E402


@pytest.fixture()
def agent_client(db, org):
    app = FastAPI()
    app.include_router(agent_runtime.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {create_agent_token(org.id, expires_minutes=30)}"
    return client


def _check(client, **payload):
    return client.post("/agent/escalation-check", json=payload)


def test_the_agent_gets_the_rule_that_fired(agent_client, db, org):
    """
    Not just "escalate: true". The rule's title and the term that matched
    are what make the decision explicable afterwards -- exactly what the
    prompt-only path could never provide.
    """
    rule(db, org, title="Emergencies", condition_type="keyword",
         keywords="chest pain", destination="+15550100")

    res = _check(agent_client, utterance="bad chest pain here")
    assert res.status_code == 200
    body = res.json()
    assert body["escalate"] is True
    assert body["rule_title"] == "Emergencies"
    assert body["condition_type"] == "keyword"
    assert body["destination"] == "+15550100"
    assert body["matched_on"] == "chest pain"


def test_an_ordinary_turn_is_a_plain_no(agent_client, db, org):
    rule(db, org, title="Emergencies", condition_type="keyword", keywords="chest pain")
    body = _check(agent_client, utterance="I'd like to book an appointment").json()
    assert body["escalate"] is False
    assert body["rule_id"] is None


def test_a_clinic_with_no_rules_is_told_to_stop_asking(agent_client, db, org):
    """
    `has_rules` is what makes the single-tenant fallback affordable: that
    path never resolves a tenant, so it cannot be told up front. Without
    this it would either ask on every turn forever, or never ask and
    silently lose every rule a clinic converted from prose to a keyword.
    """
    body = _check(agent_client, utterance="anything").json()
    assert body["has_rules"] is False
    assert body["escalate"] is False

    rule(db, org, title="Emergencies", condition_type="keyword", keywords="pain")
    assert _check(agent_client, utterance="anything").json()["has_rules"] is True


def test_a_clinic_with_only_prose_rules_reports_no_rules(agent_client, db, org):
    rule(db, org, title="Prose", condition_type="model")
    assert _check(agent_client, utterance="anything").json()["has_rules"] is False


def test_the_endpoint_needs_a_credential(db, org):
    app = FastAPI()
    app.include_router(agent_runtime.router)
    app.dependency_overrides[get_db] = lambda: db
    res = TestClient(app).post("/agent/escalation-check", json={"utterance": "x"})
    assert res.status_code in (401, 403)


def test_an_agent_token_for_another_clinic_sees_that_clinics_rules(db, org):
    """
    The org boundary is the token's, not the payload's -- there is no
    org_id in the request at all, which is what stops one clinic probing
    another's escalation rules.
    """
    other = models.Organization(name="Other Clinic", suspended=False)
    db.add(other)
    db.commit()
    rule(db, org, title="Ours", condition_type="keyword", keywords="pain")

    app = FastAPI()
    app.include_router(agent_runtime.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {create_agent_token(other.id, expires_minutes=30)}"

    assert _check(client, utterance="pain").json()["escalate"] is False


def test_it_is_reachable_by_the_agent_and_not_admin_gated():
    """
    A `require_admin` here would surface mid-call as a failed tool call
    on a caller the clinic said should have reached a person. Same rule
    as every other agent-reachable route.
    """
    import inspect

    from app.deps import get_org_context

    sig = inspect.signature(agent_runtime.agent_escalation_check)
    deps = [p.default.dependency for p in sig.parameters.values()
            if hasattr(p.default, "dependency")]
    assert get_org_context in deps
