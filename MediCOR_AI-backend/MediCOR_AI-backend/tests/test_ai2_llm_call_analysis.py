"""
AI-2 — LLM call summarisation and structured extraction.

`call_intelligence.py` was word lists and f-strings: `_NEGATIVE_WORDS`
contains "cancel", so every appointment cancellation scored the caller as
"Concerned", and `generate_summary` produced
`"{name} called regarding {reason}. Outcome: {outcome}."` — a template the
UI labelled "AI Summary".

These tests never call Groq. The HTTP layer (`_call_groq`) is stubbed, so
the suite stays fast, free and offline; what is under test is everything
around it, which is where the risk actually lives:

  * **Validation.** A model can return anything. An out-of-vocabulary
    `outcome` must be dropped rather than written, because the dashboard
    filters on that exact string — a hallucinated "Appointment Made" would
    make the call invisible in the Call Logs filter.

  * **Fallback.** Every failure path must leave the rule-based values
    intact rather than blanking a summary. A model outage is a quality
    regression, never data loss.

  * **Not blocking the call.** Analysis is queued, not awaited. A live
    phone call must never wait on a model.

The one live-API check (that a real Groq round-trip works) was run
manually during development and deliberately not automated — a test that
needs a paid API key and network is not one this suite should depend on.

    pytest tests/test_ai2_llm_call_analysis.py -v
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

from app import call_intelligence, llm_service, models
from app.config import settings


def _call(**kw):
    msgs = kw.pop("messages", [("patient", "Hello there")])
    call = models.CallLog(
        id=kw.pop("id", "call1"),
        org_id="org1",
        direction="inbound",
        status="completed",
        **kw,
    )
    call.transcript_messages = [
        models.TranscriptMessage(id=i, call_id=call.id, who=w, text=t)
        for i, (w, t) in enumerate(msgs)
    ]
    return call


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "test-key-not-used")
    return settings


def _stub(monkeypatch, payload):
    """Replace the HTTP call with a canned model response."""
    monkeypatch.setattr(llm_service, "_call_groq", lambda *a, **k: payload)


_GOOD = {
    "summary": "Maria called to cancel her Thursday appointment with Dr. Patel.",
    "outcome": "Cancelled",
    "sentiment": "Neutral",
    "intent": "cancel appointment",
    "symptoms_mentioned": [],
    "medications_mentioned": [],
    "follow_ups": ["Offer a rebooking next month"],
    "unresolved_questions": [],
}


# ---------- the bug this feature exists to fix ----------

def test_rule_based_sentiment_is_crude_in_both_directions():
    """
    Pins the defect AI-2 exists to fix, as it actually behaves.

    `infer_sentiment` counts substring hits and returns "Concerned" when
    negatives outnumber positives. `_NEGATIVE_WORDS` contains "cancel",
    so a neutral cancellation scores Concerned — but the failure is
    sloppier than that in both directions, and the second case matters
    more clinically: one polite "thank you" is enough to flip a caller in
    real distress to "Positive".
    """
    plain_cancellation = _call(
        messages=[("patient", "I need to cancel my Thursday appointment.")]
    )
    assert call_intelligence.infer_sentiment(plain_cancellation) == "Concerned"

    distressed_but_polite = _call(
        messages=[("patient", "I've had bad chest pain since this morning. Thank you.")]
    )
    assert call_intelligence.infer_sentiment(distressed_but_polite) == "Positive"


def test_llm_reads_a_calm_cancellation_correctly(enabled, monkeypatch):
    # `appointment_id` because a call where a cancellation actually
    # happened carries one, and an outcome that claims the appointment
    # book changed is now dropped without it (see
    # call_intelligence.verified_outcome). The fixture simply never set
    # it before, which made this call a cancellation of nothing.
    _stub(monkeypatch, _GOOD)
    result = llm_service.analyze_call(_call(appointment_id="appt-1"))
    assert result["sentiment"] == "Neutral"
    assert result["outcome"] == "Cancelled"
    assert result["intent"] == "cancel appointment"
    assert result["extracted"]["follow_ups"] == ["Offer a rebooking next month"]


# ---------- classification must not disagree with itself ----------

def test_analysis_is_sampled_deterministically(enabled, monkeypatch):
    """
    `outcome`, `sentiment` and `intent` are picks from closed
    vocabularies -- classifications of a fixed transcript, not writing.
    Sampling variance there is noise, and it was real: two runs over one
    identical mock call recorded `sentiment` as "Positive" and then
    "Neutral". A dashboard column that disagrees with itself about a call
    nobody touched is one a clinic learns to ignore.

    Asserted on the request body rather than by calling the model twice,
    because a test that needs a paid API key and a network is not one
    this suite should depend on -- the same reasoning that keeps the live
    round-trip check manual. Verified against the real API during
    development: five analyses of one transcript, one distinct result.

    Note what this does not claim. LLM inference is not bit-reproducible
    even at temperature 0, and Groq documents `seed` as best-effort. This
    removes the sampling variance, which is the part that was causing the
    disagreement.
    """
    sent = {}

    def capture(system, user):
        return None

    def fake_post(url, json=None, headers=None):
        sent.update(json or {})
        raise RuntimeError("stop here - the body is what is under test")

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            return fake_post(url, json=json, headers=headers)

    monkeypatch.setattr(llm_service.httpx, "Client", _Client)
    llm_service._call_groq("system", "user")

    assert sent["temperature"] == 0.0, "analysis must not sample randomly"
    assert "seed" in sent, "a fixed seed is what makes repeats comparable"
    assert sent["response_format"] == {"type": "json_object"}


def test_the_spoken_answer_path_keeps_its_natural_phrasing(enabled, monkeypatch):
    """
    The other user of Groq in this module writes text a caller HEARS
    (rag_service's answers). Flattening that to temperature 0 alongside
    the classification would have made every spoken reply robotic to fix
    a dashboard column -- so the two settings are separate on purpose.
    """
    from app.config import settings as live

    assert live.groq_temperature > 0, "spoken answers should not be flattened"
    assert live.groq_analysis_temperature == 0.0


# ---------- the transfer-outcome mismatch found while building this ----------

def test_transfer_outcome_matches_the_vocabulary_everything_else_uses():
    """
    `infer_outcome` used to return "Transferred to Staff", which nothing
    else recognised: routers/calls.py escalates only on "Transferred to
    Nurse", and the dashboard filters and colours on the same string. So
    the most urgent call type was notified as a routine success and was
    invisible when staff filtered for transfers — while the seeded demo
    rows used the correct value, hiding it in every demo.
    """
    call = _call(
        reason="speak to a nurse",
        actions_taken="transfer_call",
        messages=[("patient", "Can I speak to a nurse please")],
    )
    assert call_intelligence.infer_outcome(call) == "Transferred to Nurse"
    assert "Transferred to Nurse" in llm_service.ALLOWED_OUTCOMES


# ---------- validation ----------

def test_an_out_of_vocabulary_outcome_is_dropped_not_written(enabled, monkeypatch):
    """A hallucinated outcome must not reach a column the UI filters on."""
    _stub(monkeypatch, {**_GOOD, "outcome": "Appointment Made"})
    result = llm_service.analyze_call(_call())
    assert result["outcome"] is None  # caller keeps the rule-based value
    assert result["summary"]  # ...but the good summary is still used


def test_an_invalid_sentiment_is_dropped(enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD, "sentiment": "Furious"})
    assert llm_service.analyze_call(_call())["sentiment"] is None


def test_a_response_with_no_summary_is_rejected_entirely(enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD, "summary": "   "})
    assert llm_service.analyze_call(_call()) is None


def test_malformed_extraction_fields_become_empty_lists(enabled, monkeypatch):
    _stub(monkeypatch, {**_GOOD, "symptoms_mentioned": "a string", "follow_ups": [1, 2, None]})
    extracted = llm_service.analyze_call(_call())["extracted"]
    assert extracted["symptoms_mentioned"] == []
    assert extracted["follow_ups"] == []


def test_exotic_unicode_whitespace_is_normalised(enabled, monkeypatch):
    """
    Observed with the real model: it writes "2 PM" with U+202F. The
    summary column is VARCHAR(MAX) on SQL Server (non-Unicode), so those
    characters would be written as "?".
    """
    _stub(monkeypatch, {**_GOOD, "summary": "Called at 2 PM today."})
    summary = llm_service.analyze_call(_call())["summary"]
    assert " " not in summary and " " not in summary
    assert summary == "Called at 2 PM today."


# ---------- fallback ----------

def test_disabled_without_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert llm_service.is_enabled() is False
    assert llm_service.analyze_call(_call()) is None


def test_a_transport_failure_falls_back_silently(enabled, monkeypatch):
    _stub(monkeypatch, None)
    assert llm_service.analyze_call(_call()) is None


def test_a_call_with_no_transcript_is_skipped_without_a_request(enabled, monkeypatch):
    """No transcript means nothing to summarise — don't pay for the call."""
    called = []
    monkeypatch.setattr(llm_service, "_call_groq", lambda *a, **k: called.append(1) or _GOOD)
    call = _call()
    call.transcript_messages = []
    assert llm_service.analyze_call(call) is None
    assert called == []


def test_apply_analysis_leaves_rule_based_values_when_the_model_fails(enabled, monkeypatch):
    """The critical property: a model outage degrades quality, never data."""
    _stub(monkeypatch, None)
    call = _call()
    call.ai_summary = "Rule-based summary"
    call.outcome = "Cancelled"
    call.sentiment = "Concerned"

    class _Db:
        def commit(self): raise AssertionError("must not write on failure")
        def rollback(self): pass

    assert llm_service.apply_analysis(_Db(), call) is False
    assert call.ai_summary == "Rule-based summary"
    assert call.outcome == "Cancelled"
    assert call.sentiment == "Concerned"


def test_apply_analysis_keeps_the_rule_based_outcome_when_the_model_gives_a_bad_one(
    enabled, monkeypatch
):
    _stub(monkeypatch, {**_GOOD, "outcome": "Nonsense"})
    call = _call()
    call.outcome = "Booked"

    class _Db:
        committed = False
        def commit(self): self.committed = True
        def rollback(self): pass

    db = _Db()
    assert llm_service.apply_analysis(db, call) is True
    assert db.committed
    assert call.outcome == "Booked"  # untouched
    assert call.ai_summary == _GOOD["summary"]  # summary still upgraded
    assert call.summary_model == settings.groq_model


def test_apply_analysis_writes_provenance_and_extraction(enabled, monkeypatch):
    _stub(monkeypatch, _GOOD)
    call = _call()

    class _Db:
        def commit(self): pass
        def rollback(self): pass

    assert llm_service.apply_analysis(_Db(), call) is True
    assert call.summary_model == settings.groq_model
    assert call.summary_generated_at is not None
    assert json.loads(call.extracted_json)["follow_ups"] == ["Offer a rebooking next month"]
    # The model property the API response reads.
    assert call.extracted["follow_ups"] == ["Offer a rebooking next month"]


def test_unreadable_extracted_json_never_breaks_the_response():
    call = _call()
    call.extracted_json = "{not valid json"
    assert call.extracted == {}


# ---------- scheduling ----------

def test_analysis_is_queued_not_awaited(enabled):
    """A live call must never wait on a model round-trip."""
    class _Tasks:
        def __init__(self): self.added = []
        def add_task(self, fn, *args): self.added.append((fn, args))

    tasks = _Tasks()
    assert llm_service.schedule_analysis(tasks, "call1", "org1") is True
    assert len(tasks.added) == 1
    assert tasks.added[0][1] == ("call1", "org1")


def test_nothing_is_queued_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")

    class _Tasks:
        def __init__(self): self.added = []
        def add_task(self, fn, *args): self.added.append(fn)

    tasks = _Tasks()
    assert llm_service.schedule_analysis(tasks, "call1", "org1") is False
    assert tasks.added == []
