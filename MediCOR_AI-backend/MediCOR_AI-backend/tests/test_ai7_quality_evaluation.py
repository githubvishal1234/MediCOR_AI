"""
AI-7 — call quality evaluation.

`agent.py` carries a visible history of behavioural fixes that were
presumably found in production, because nothing evaluates the agent
offline. This scores calls after the fact with an LLM as judge.

These tests stub the judge, so they are offline and free. What they pin
is the decision logic around it, which is where the value and the risk
are:

  * **`needs_review` is not a score threshold.** Validated against the
    real judge: a call where the AI gave medical advice scored **70** with
    `goal_achieved=True` — a naive "flag below 60" rule passes it. It is
    flagged because a policy violation flags a call whatever the score.
    `test_a_high_scoring_call_with_a_violation_is_still_flagged` is the
    test that matters most here.

  * **The closed vocabulary is enforced.** An unrecognised policy code is
    dropped, because the whole point of a fixed list is that counts stay
    comparable across runs and rubric changes.

    pytest tests/test_ai7_quality_evaluation.py -v
"""

import json
import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import evaluation_service, llm_service, models
from app.config import settings
from app.database import Base

_GOOD_VERDICT = {
    "goal_achieved": True,
    "hallucination_detected": False,
    "policy_violations": [],
    "conciseness": 5,
    "score": 92,
    "reasoning": "Booked the appointment the caller asked for. Brief and accurate.",
}


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
    o = models.Organization(name="Northside Clinic")
    db.add(o)
    db.commit()
    return o


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-used")


def _call(db, org, call_id="call1", actions="book_appointment", messages=None):
    call = models.CallLog(
        id=call_id, org_id=org.id, direction="inbound", status="completed",
        patient_name="Test Caller", outcome="Booked", actions_taken=actions,
    )
    db.add(call)
    db.flush()
    for who, text in (messages or [("patient", "I'd like to book an appointment.")]):
        db.add(models.TranscriptMessage(call_id=call_id, who=who, text=text))
    db.commit()
    db.refresh(call)
    return call


def _stub(monkeypatch, verdict):
    monkeypatch.setattr(llm_service, "_call_groq", lambda *a, **k: verdict)


# ---------- happy path ----------

def test_a_clean_call_is_scored_and_not_flagged(db, org, enabled, monkeypatch):
    _stub(monkeypatch, _GOOD_VERDICT)
    ev = evaluation_service.evaluate_call(db, _call(db, org))
    assert ev.score == 92
    assert ev.goal_achieved is True
    assert ev.hallucination_detected is False
    assert ev.policy_violations == []
    assert ev.needs_review is False
    assert ev.judge_model == settings.groq_model


# ---------- what makes a call need review ----------

def test_a_high_scoring_call_with_a_violation_is_still_flagged(db, org, enabled, monkeypatch):
    """
    **The most important test here.**

    Verified against the real judge: a call where the AI told a patient to
    take 400mg ibuprofen scored **70** with `goal_achieved=True` — the
    caller did get an answer. A score threshold alone passes that call.
    A clinic must still see it.
    """
    _stub(monkeypatch, {**_GOOD_VERDICT, "score": 70, "policy_violations": ["medical_advice"]})
    ev = evaluation_service.evaluate_call(db, _call(db, org))
    assert ev.score == 70          # comfortably above any "bad call" cutoff
    assert ev.goal_achieved is True
    assert ev.needs_review is True  # ...and flagged anyway


def test_a_hallucination_flags_the_call_whatever_the_score(db, org, enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD_VERDICT, "score": 88, "hallucination_detected": True})
    assert evaluation_service.evaluate_call(db, _call(db, org)).needs_review is True


def test_a_low_score_alone_flags_the_call(db, org, enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD_VERDICT, "score": 40})
    assert evaluation_service.evaluate_call(db, _call(db, org)).needs_review is True


def test_signing_off_removes_a_call_from_the_queue(db, org, enabled, monkeypatch):
    from datetime import datetime

    _stub(monkeypatch, {**_GOOD_VERDICT, "score": 30})
    ev = evaluation_service.evaluate_call(db, _call(db, org))
    assert ev.needs_review is True
    ev.reviewed_at = datetime.utcnow()
    assert ev.needs_review is False


# ---------- validation ----------

def test_an_unrecognised_policy_code_is_dropped(db, org, enabled, monkeypatch):
    """
    A closed vocabulary only means something if it is enforced — a
    free-text code would quietly break aggregation across runs.
    """
    _stub(monkeypatch, {
        **_GOOD_VERDICT,
        "policy_violations": ["medical_advice", "was_a_bit_rude", "invented_information"],
    })
    ev = evaluation_service.evaluate_call(db, _call(db, org))
    assert ev.policy_violations == ["medical_advice", "invented_information"]


def test_out_of_range_values_are_clamped(db, org, enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD_VERDICT, "score": 150, "conciseness": 9})
    ev = evaluation_service.evaluate_call(db, _call(db, org))
    assert ev.score == 100
    assert ev.conciseness == 5


def test_a_verdict_with_no_usable_score_is_rejected(db, org, enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD_VERDICT, "score": "not a number"})
    assert evaluation_service.evaluate_call(db, _call(db, org)) is None


def test_policy_violations_that_are_not_a_list_become_empty(db, org, enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD_VERDICT, "policy_violations": "medical_advice"})
    assert evaluation_service.evaluate_call(db, _call(db, org)).policy_violations == []


def test_unreadable_violations_json_never_breaks_the_response(db, org):
    ev = models.CallEvaluation(org_id=org.id, call_id="x", policy_violations_json="{bad")
    assert ev.policy_violations == []


# ---------- when it must not run ----------

def test_nothing_is_evaluated_without_a_judge(db, org, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert evaluation_service.evaluate_call(db, _call(db, org)) is None


def test_a_call_with_no_transcript_is_skipped_without_a_request(db, org, enabled, monkeypatch):
    """Nothing to audit, so don't pay to be told the caller said nothing."""
    called = []
    monkeypatch.setattr(
        llm_service, "_call_groq", lambda *a, **k: called.append(1) or _GOOD_VERDICT
    )
    call = models.CallLog(
        id="empty", org_id=org.id, direction="inbound", status="completed"
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    assert evaluation_service.evaluate_call(db, call) is None
    assert called == []


def test_a_judge_failure_returns_none_rather_than_a_bogus_score(db, org, enabled, monkeypatch):
    _stub(monkeypatch, None)
    assert evaluation_service.evaluate_call(db, _call(db, org)) is None


def test_a_batch_is_not_queued_without_a_judge(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")

    class _Tasks:
        def __init__(self): self.added = []
        def add_task(self, fn, *a): self.added.append(fn)

    tasks = _Tasks()
    assert evaluation_service.schedule_batch(tasks, "org1", 10) is False
    assert tasks.added == []


# ---------- aggregation ----------

def test_summary_of_an_org_with_no_evaluations_is_empty_not_an_error(db, org):
    s = evaluation_service.summarize(db, org.id)
    assert s["evaluated_calls"] == 0
    assert s["average_score"] is None
    assert s["violations_by_code"] == {}


def test_summary_counts_violations_separately_from_the_average(db, org, enabled, monkeypatch):
    """
    A run of concise, efficient calls that each stated one wrong fact
    shows a healthy average and is still the most urgent thing on the
    page — so the two numbers are reported independently.
    """
    verdicts = [
        {**_GOOD_VERDICT, "score": 90},
        {**_GOOD_VERDICT, "score": 88, "policy_violations": ["medical_advice"]},
        {**_GOOD_VERDICT, "score": 86, "hallucination_detected": True},
    ]
    for i, verdict in enumerate(verdicts):
        _stub(monkeypatch, verdict)
        evaluation_service.evaluate_call(db, _call(db, org, call_id=f"c{i}"))

    s = evaluation_service.summarize(db, org.id)
    assert s["evaluated_calls"] == 3
    assert s["average_score"] == 88.0        # looks healthy...
    assert s["hallucination_count"] == 1     # ...but two calls need eyes
    assert s["policy_violation_count"] == 1
    assert s["needs_review_count"] == 2
    assert s["violations_by_code"] == {"medical_advice": 1}


def test_summary_is_org_scoped(db, enabled, monkeypatch):
    a = models.Organization(name="Clinic A")
    b = models.Organization(name="Clinic B")
    db.add_all([a, b])
    db.commit()
    _stub(monkeypatch, _GOOD_VERDICT)
    evaluation_service.evaluate_call(db, _call(db, a, call_id="a1"))

    assert evaluation_service.summarize(db, a.id)["evaluated_calls"] == 1
    assert evaluation_service.summarize(db, b.id)["evaluated_calls"] == 0


def test_the_judge_is_told_which_tools_actually_ran(db, org):
    """
    The signal that makes hallucination detectable: the agent claiming to
    have booked something when no booking tool ran is invisible from the
    transcript alone.
    """
    call = _call(db, org, actions="")
    prompt = evaluation_service._build_prompt(call, "AI: I've booked you in.")
    assert "NONE" in prompt

    call2 = _call(db, org, call_id="c2", actions="book_appointment")
    assert "book_appointment" in evaluation_service._build_prompt(call2, "x")
