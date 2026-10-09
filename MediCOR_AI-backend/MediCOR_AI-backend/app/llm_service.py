"""
LLM call analysis — AI-2.

Replaces the word-list heuristics in `call_intelligence.py` with a real
model pass over the transcript, producing a clinical-grade summary, an
accurate outcome, calibrated sentiment, and structured extraction
(intent, symptoms, medications, follow-ups, unresolved questions).

Why this seam: `update_call` in routers/calls.py already calls
`infer_sentiment` / `infer_outcome` / `generate_summary` when a call
reaches a terminal status, and the transcript is already saved by then.
So this changes nothing about the voice agent — the agent has always
delegated summarisation to the backend by design.

What the rule-based version got wrong, concretely: `_NEGATIVE_WORDS`
contains "cancel", so **every appointment cancellation scored the patient
as "Concerned"**, and `generate_summary` produced
`"{name} called regarding {reason}. Outcome: {outcome}."` — a template,
labelled "AI Summary" in the UI.

Three properties this module holds to:

  1. **Never breaks a call.** Analysis runs in a background task after the
     response is sent, and every failure path falls back to the existing
     rule-based result. A model outage degrades summary quality; it never
     fails a call update or loses a transcript.

  2. **Never invents.** The prompt forbids inferring facts not present in
     the transcript, and `outcome` is constrained to the exact vocabulary
     the dashboard already filters on, so existing Call Logs filters keep
     working.

  3. **Bounded cost.** The transcript is truncated before sending, the
     completion is capped, and a call with no transcript is skipped
     without a request.
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

import httpx

from . import call_intelligence, models
from .config import settings

logger = logging.getLogger("llm-service")

# The exact outcome vocabulary the dashboard filters on. Constraining the
# model to this set is what keeps `/calls?outcome=...` and the Call Logs
# filter working — a free-text outcome would silently break both.
ALLOWED_OUTCOMES = [
    "Booked",
    "Rescheduled",
    "Cancelled",
    "FAQ Answered",
    "Transferred to Nurse",
    "Completed",
]

ALLOWED_SENTIMENTS = ["Positive", "Neutral", "Concerned"]

_SYSTEM_PROMPT = """\
You analyse transcripts of phone calls to a medical clinic, handled by an \
AI receptionist. You produce a factual record for clinic staff to review.

Rules:
- Use ONLY what the transcript states. Never infer a diagnosis, never \
invent a name, time, medication or outcome that was not said.
- If something was not discussed, use an empty list or null. Do not guess.
- The summary is for clinical staff: concise, specific, third person, 2-4 \
sentences. Lead with why they called and what happened.
- Never give medical advice or interpretation of symptoms. Report only \
what the caller reported.
- sentiment reflects THE CALLER's state, not the AI's tone. A caller who \
calmly cancels an appointment is Neutral, not Concerned. Reserve \
"Concerned" for distress, pain, frustration or urgency the caller \
actually expressed.

Respond with a single JSON object and nothing else."""

_RESPONSE_SHAPE = """\
{
  "summary": "2-4 sentence factual summary for staff",
  "outcome": one of %s,
  "sentiment": one of %s,
  "intent": "short phrase for why they called, e.g. 'book appointment'",
  "symptoms_mentioned": ["only symptoms the caller stated"],
  "medications_mentioned": ["only medications the caller stated"],
  "follow_ups": ["actions clinic staff still need to take"],
  "unresolved_questions": ["questions the AI could not answer"]
}""" % (json.dumps(ALLOWED_OUTCOMES), json.dumps(ALLOWED_SENTIMENTS))


def is_enabled() -> bool:
    """Whether LLM analysis is configured. Unset key = rule-based path."""
    return bool((settings.groq_api_key or "").strip())


def _transcript_text(call: models.CallLog) -> str:
    """
    The conversation as plain text, truncated to the configured cap.

    Truncation keeps the START of the call, not the end: the reason for
    calling and the caller's identity are established early, and those
    matter more to a summary than the closing pleasantries.
    """
    lines = []
    for m in call.transcript_messages:
        if not m.text:
            continue
        who = "Patient" if m.who == "patient" else "AI"
        lines.append(f"{who}: {m.text}")
    text = "\n".join(lines)
    limit = settings.llm_max_transcript_chars
    if len(text) > limit:
        text = text[:limit] + "\n[transcript truncated]"
    return text


def _build_user_prompt(call: models.CallLog, transcript: str) -> str:
    context = [
        f"Direction: {call.direction or 'inbound'}",
        f"Call status: {call.status or 'completed'}",
    ]
    if call.patient_name:
        context.append(f"Caller on record as: {call.patient_name}")
    if call.reason:
        context.append(f"Stated reason: {call.reason}")
    if call.actions_taken:
        # What the agent's tools actually did — the most reliable signal
        # for `outcome`, since it is a record of action rather than talk.
        context.append(f"Actions the AI performed: {call.actions_taken}")

    return (
        "\n".join(context)
        + "\n\nTranscript:\n"
        + transcript
        + "\n\nReturn JSON in exactly this shape:\n"
        + _RESPONSE_SHAPE
    )


# Exotic Unicode whitespace the model emits in formatted times — a real
# observed case is "2 PM" written with U+202F (narrow no-break space).
#
# Why this matters here specifically: `ai_summary` is `Column(Text)`,
# which SQLAlchemy's mssql dialect compiles to `TEXT` — a *non-Unicode*
# type (and one Microsoft deprecated in 2005). Characters outside the
# database's collation codepage are written as "?".
#
# This normalises the handful of space variants a model actually
# produces. It does NOT solve the wider problem: `transcript_messages.text`
# is the same type and holds patient speech, and the voice agent
# advertises Hindi and Telugu — so non-Latin transcripts are already
# affected today. That needs a schema change (Text -> UnicodeText across
# ~16 columns, plus a data migration), not a filter here. Flagged in
# CLAUDE.md.
_UNICODE_SPACES = {
    " ": " ",  # no-break space
    " ": " ",  # narrow no-break space
    " ": " ",  # thin space
    " ": " ",  # hair space
    " ": " ",  # figure space
    "⁠": "",   # word joiner
    "﻿": "",   # zero-width no-break space
}


def _normalize_text(value: str) -> str:
    for bad, good in _UNICODE_SPACES.items():
        value = value.replace(bad, good)
    return value.strip()


def _coerce_str_list(value: Any, limit: int = 10) -> list[str]:
    """Anything not a clean list of short strings becomes an empty list."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        if isinstance(item, str) and item.strip():
            out.append(_normalize_text(item)[:200])
    return out


def _validate(payload: dict, call: models.CallLog) -> Optional[dict]:
    """
    Validates and normalises the model's JSON.

    Anything outside the allowed vocabulary is dropped rather than
    coerced: a hallucinated outcome like "Appointment Made" would break
    the dashboard's filters, and silently mapping it to a real value
    would be a guess about what the model meant.
    """
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        logger.warning("LLM returned no usable summary for call=%s", call.id)
        return None

    outcome = payload.get("outcome")
    if outcome not in ALLOWED_OUTCOMES:
        if outcome is not None:
            logger.info(
                "LLM returned out-of-vocabulary outcome %r for call=%s — "
                "falling back to the rule-based outcome",
                outcome, call.id,
            )
        outcome = None

    # In vocabulary is not the same as true. "Booked", "Rescheduled" and
    # "Cancelled" are assertions about the appointment book, and this
    # model is reading a transcript in which somebody *asked* for one --
    # which is exactly the material needed to conclude, wrongly, that it
    # happened. The same rule the rule-based path uses, applied here so
    # there is one definition of what may claim a booking.
    if outcome and call_intelligence.verified_outcome(outcome, call) is None:
        logger.info(
            "LLM claimed outcome %r for call=%s with no appointment on the "
            "record — dropping it",
            outcome, call.id,
        )
        outcome = None

    sentiment = payload.get("sentiment")
    if sentiment not in ALLOWED_SENTIMENTS:
        sentiment = None

    intent = payload.get("intent")
    intent = _normalize_text(intent)[:120] if isinstance(intent, str) and intent.strip() else None

    return {
        "summary": _normalize_text(summary)[:4000],
        "outcome": outcome,
        "sentiment": sentiment,
        "intent": intent,
        "extracted": {
            "symptoms_mentioned": _coerce_str_list(payload.get("symptoms_mentioned")),
            "medications_mentioned": _coerce_str_list(payload.get("medications_mentioned")),
            "follow_ups": _coerce_str_list(payload.get("follow_ups")),
            "unresolved_questions": _coerce_str_list(payload.get("unresolved_questions")),
        },
    }


def _call_groq(system: str, user: str) -> Optional[dict]:
    """
    One chat completion, returning the parsed JSON object or None.

    Uses `response_format: json_object`, which Groq supports on its
    OpenAI-compatible endpoint — that makes the model emit valid JSON
    rather than JSON wrapped in prose, which is the single most common
    failure mode when parsing model output.
    """
    url = f"{settings.groq_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.groq_model,
        # Deliberately NOT `groq_temperature`, which belongs to the RAG
        # answer path. This request classifies a fixed transcript into
        # closed vocabularies, so sampling variance is noise -- and it
        # showed: one identical call analysed twice recorded `sentiment`
        # as "Positive" and then "Neutral". See config.py.
        "temperature": settings.groq_analysis_temperature,
        "seed": settings.groq_analysis_seed,
        "max_tokens": settings.llm_max_completion_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.groq_api_key.strip()}"}

    attempts = max(settings.llm_max_retries, 0) + 1
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                resp = client.post(url, json=body, headers=headers)
            if resp.status_code >= 500:
                logger.warning(
                    "Groq %s on attempt %s/%s", resp.status_code, attempt, attempts
                )
                if attempt < attempts:
                    continue
                return None
            if resp.status_code >= 400:
                # Never retried: a 4xx means the request is wrong (bad key,
                # bad model name, too many tokens). Retrying burns quota
                # and cannot succeed.
                logger.error("Groq rejected the request: %s %s", resp.status_code, resp.text[:300])
                return None

            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning("Groq unreachable on attempt %s/%s: %s", attempt, attempts, e)
        except (KeyError, IndexError, ValueError) as e:
            # Malformed response or unparseable JSON — not retryable in
            # any useful way, and the fallback is already correct.
            logger.warning("Groq returned an unusable response: %s", e)
            return None
        except Exception:  # noqa: BLE001 - this must never escape
            logger.exception("Unexpected error calling Groq")
            return None

    return None


def _call_groq_text(system: str, user: str, max_tokens: Optional[int] = None) -> Optional[str]:
    """
    One chat completion returning plain text rather than JSON.

    Used by rag_service to turn retrieved passages into a spoken answer,
    where a JSON envelope would be pure overhead. Shares this module's
    error posture exactly: 5xx and transport errors retried, 4xx never,
    and None on any failure so the caller can fall back.
    """
    url = f"{settings.groq_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.groq_model,
        "temperature": settings.groq_temperature,
        "max_tokens": max_tokens or 300,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.groq_api_key.strip()}"}

    attempts = max(settings.llm_max_retries, 0) + 1
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                resp = client.post(url, json=body, headers=headers)
            if resp.status_code >= 500:
                logger.warning("Groq %s on attempt %s/%s", resp.status_code, attempt, attempts)
                if attempt < attempts:
                    continue
                return None
            if resp.status_code >= 400:
                logger.error("Groq rejected the request: %s %s", resp.status_code, resp.text[:300])
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            return _normalize_text(content) if content else None
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning("Groq unreachable on attempt %s/%s: %s", attempt, attempts, e)
        except (KeyError, IndexError, ValueError) as e:
            logger.warning("Groq returned an unusable response: %s", e)
            return None
        except Exception:  # noqa: BLE001 - must never escape
            logger.exception("Unexpected error calling Groq")
            return None

    return None


def analyze_call(call: models.CallLog) -> Optional[dict]:
    """
    Runs LLM analysis for one finished call.

    Returns the validated result, or None if analysis is disabled,
    impossible (no transcript), or failed for any reason — in every one of
    those cases the caller keeps whatever the rule-based path produced.
    """
    if not is_enabled():
        return None

    transcript = _transcript_text(call)
    if not transcript.strip():
        # No transcript means nothing to summarise. Skipping here avoids
        # paying for a request whose only possible answer is "the caller
        # said nothing".
        logger.info("Call %s has no transcript — skipping LLM analysis", call.id)
        return None

    result = _call_groq(_SYSTEM_PROMPT, _build_user_prompt(call, transcript))
    if result is None:
        return None
    return _validate(result, call)


def apply_analysis(db, call: models.CallLog) -> bool:
    """
    Analyses `call` and writes the result, keeping the rule-based values
    wherever the model gave nothing usable.

    Returns whether anything was written. Commits its own transaction:
    this runs in a background task, after the request that triggered it
    has already responded.
    """
    try:
        analysis = analyze_call(call)
        if analysis is None:
            return False

        call.ai_summary = analysis["summary"]
        # Only overwrite when the model produced a valid value — a
        # rejected outcome leaves call_intelligence's inference in place
        # rather than nulling a field the dashboard filters on.
        if analysis["outcome"]:
            call.outcome = analysis["outcome"]
        if analysis["sentiment"]:
            call.sentiment = analysis["sentiment"]
        call.intent = analysis["intent"]
        call.extracted_json = json.dumps(analysis["extracted"])
        call.summary_model = settings.groq_model
        call.summary_generated_at = datetime.utcnow()

        db.commit()
        logger.info("LLM analysis applied to call=%s", call.id)
        return True
    except Exception:
        # Same posture as ehr_service: a summarisation failure must never
        # surface anywhere near the call it describes.
        logger.exception("LLM analysis failed for call=%s", call.id)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def run_analysis_task(call_id: str, org_id: str) -> None:
    """
    BackgroundTasks entry point.

    Takes ids and re-queries rather than accepting an ORM instance: by the
    time this runs the request's session is closed and any instance passed
    in would be detached. (That exact mistake is what silently broke
    patient notifications before QW-9 was repaired.)
    """
    from .database import SessionLocal

    db = SessionLocal()
    try:
        call = (
            db.query(models.CallLog)
            .filter(models.CallLog.id == call_id, models.CallLog.org_id == org_id)
            .first()
        )
        if call is None:
            return
        apply_analysis(db, call)
    except Exception:
        logger.exception("LLM analysis task failed for call=%s", call_id)
    finally:
        db.close()


def schedule_analysis(background_tasks, call_id: str, org_id: str) -> bool:
    """
    Queues analysis for after the response is sent.

    Returns whether it was queued. Disabled (no API key) is a normal
    state, not an error — the rule-based summary already written by
    `update_call` simply stands.
    """
    if not is_enabled():
        return False
    background_tasks.add_task(run_analysis_task, call_id, org_id)
    return True


# Re-exported so callers have one import for both paths, and so the
# rule-based functions remain the documented fallback rather than an
# implementation detail of this module.
infer_sentiment = call_intelligence.infer_sentiment
infer_outcome = call_intelligence.infer_outcome
generate_summary = call_intelligence.generate_summary
