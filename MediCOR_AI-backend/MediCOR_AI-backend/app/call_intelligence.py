"""
Rule-based call-intelligence helpers: AI call summary and sentiment
inference, generated purely from data already stored on a CallLog
(transcript messages, reason, outcome, actions taken).

Consistent with the knowledge_base router's keyword-matching approach,
this stays dependency-free (no external NLP/LLM call) so it works
out-of-the-box in any environment. All of it runs server-side, on the
backend, so the voice agent never needs to know how a summary is built.
"""

import re
from typing import Optional

from . import models

_NEGATIVE_WORDS = {
    "angry", "upset", "frustrated", "annoyed", "worried", "urgent",
    "pain", "hurts", "hurting", "emergency", "terrible", "worse",
    "complain", "complaint", "unhappy", "disappointed", "cancel",
    "cancelled", "wrong", "mistake", "confused", "help me", "scared",
}
_POSITIVE_WORDS = {
    "thanks", "thank you", "great", "perfect", "appreciate", "wonderful",
    "awesome", "helpful", "good", "excellent", "happy", "sounds good",
}

_BOOKING_HINTS = {"book", "booked", "appointment", "schedule", "scheduled"}
_RESCHEDULE_HINTS = {"reschedule", "rescheduled", "move", "moved", "change"}
_CANCEL_HINTS = {"cancel", "cancelled", "canceled"}
_TRANSFER_HINTS = {"transfer", "transferred", "staff", "nurse", "human"}

# The canonical transfer outcome is "Transferred to Nurse".
#
# This used to return "Transferred to Staff", which nothing else in the
# system recognised: routers/calls.py raises a *warning*-level
# notification only for `_URGENT_OUTCOMES = {"Transferred to Nurse"}`, the
# dashboard's outcome filter and colour map key on the same string, and
# seed.py writes it too. So every real transfer — the single most urgent
# call type there is — was notified to staff as a routine "success" and
# was invisible when they filtered for transfers. Only the seeded demo
# rows had the right value, which is why it looked correct in the demo.
_TRANSFER_OUTCOME = "Transferred to Nurse"


def _patient_texts(call: models.CallLog) -> list[str]:
    return [m.text for m in call.transcript_messages if m.who == "patient" and m.text]


def infer_sentiment(call: models.CallLog) -> str:
    """Best-effort sentiment label (Positive | Neutral | Concerned) from
    the patient's side of the transcript."""
    text = " ".join(_patient_texts(call)).lower()
    if not text:
        return "Neutral"

    neg_hits = sum(1 for w in _NEGATIVE_WORDS if w in text)
    pos_hits = sum(1 for w in _POSITIVE_WORDS if w in text)

    if neg_hits > pos_hits:
        return "Concerned"
    if pos_hits > 0:
        return "Positive"
    return "Neutral"


# Outcomes that assert an appointment was created, moved or cancelled.
# Each is a claim about a row in `appointments`, so each needs one.
APPOINTMENT_OUTCOMES = frozenset({"Booked", "Rescheduled", "Cancelled"})


def verified_outcome(outcome: Optional[str], call: models.CallLog) -> Optional[str]:
    """
    Drop an outcome that claims something the call cannot show happened.

    WHY THIS EXISTS

    "Booked", "Rescheduled" and "Cancelled" are not descriptions of a
    conversation; they are assertions about the appointment book. Both
    ways this codebase derives an outcome were willing to make them from
    talk alone -- the rule-based path by finding "book" in the transcript,
    the LLM path by reading the same transcript and picking from a
    vocabulary that offers the word.

    So a caller who rang, asked to book, and got nothing booked produced
    a call marked `Booked`. That is not a cosmetic error: the dashboard
    filters on this exact string, so the call appears in the booked list,
    and a receptionist reviewing the day's calls has no reason to look at
    it again. The patient turns up to an appointment nobody made.

    `appointment_id` is the evidence, and it is set by the agent only
    after the backend has confirmed the booking, reschedule or
    cancellation. No id, no claim.

    Returns None when the outcome cannot be supported, leaving the caller
    to fall through to something it can.
    """
    if outcome in APPOINTMENT_OUTCOMES and not call.appointment_id:
        return None
    return outcome


def infer_outcome(call: models.CallLog) -> str:
    """
    Best-effort outcome label from actions_taken (set by the voice agent
    as its tools succeed) or, failing that, the transcript.

    Every appointment-claiming answer goes through `verified_outcome`, so
    a transcript full of the word "book" cannot on its own mark a call
    `Booked`. What is left when a claim is dropped is deliberately the
    weaker, true statement -- a conversation happened -- rather than the
    next-best guess.
    """
    if call.actions_taken:
        actions = [a.strip().lower() for a in call.actions_taken.splitlines() if a.strip()]
        if actions:
            last = actions[-1]
            candidate = None
            if any(h in last for h in _CANCEL_HINTS):
                candidate = "Cancelled"
            elif any(h in last for h in _RESCHEDULE_HINTS):
                candidate = "Rescheduled"
            elif any(h in last for h in _BOOKING_HINTS):
                candidate = "Booked"
            elif any(h in last for h in _TRANSFER_HINTS):
                candidate = "Transferred to Nurse"

            if candidate:
                # The agent reporting "Booked appointment" is strong
                # evidence, but it is still the agent's own account. When
                # the appointment it names did not reach the call record,
                # something went wrong between the two and the claim is
                # not ours to repeat.
                verified = verified_outcome(candidate, call)
                return verified if verified else "Completed"
            return "FAQ Answered"

    if call.status == "failed":
        return "Call Failed"
    if call.status == "no_answer":
        return "No Answer"

    all_text = " ".join(m.text.lower() for m in call.transcript_messages if m.text)
    candidate = None
    if any(h in all_text for h in _CANCEL_HINTS):
        candidate = "Cancelled"
    elif any(h in all_text for h in _RESCHEDULE_HINTS):
        candidate = "Rescheduled"
    elif any(h in all_text for h in _BOOKING_HINTS):
        candidate = "Booked"
    elif any(h in all_text for h in _TRANSFER_HINTS):
        candidate = "Transferred to Nurse"

    # Here the hints matched what somebody *said*, which is the weakest
    # evidence there is -- "I'd like to book an appointment" is a request,
    # not a booking. Only a transfer survives unverified, because saying
    # it is the closest this path gets to observing it.
    verified = verified_outcome(candidate, call)
    if verified:
        return verified
    return "Completed" if call.transcript_messages else "No Interaction"


def format_duration_label(duration_seconds: int) -> str:
    minutes, seconds = divmod(max(0, int(duration_seconds)), 60)
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def generate_summary(call: models.CallLog) -> str:
    """
    Builds a short, human-readable call summary from what's already on
    the CallLog record: caller, reason, key actions, and outcome. Falls
    back gracefully when a field wasn't captured.
    """
    who = call.patient_name or "The caller"
    parts: list[str] = []

    opener = f"{who} called"
    if call.reason:
        opener += f" regarding {call.reason.strip().rstrip('.')}"
    parts.append(opener + ".")

    if call.actions_taken:
        actions = [a.strip() for a in call.actions_taken.splitlines() if a.strip()]
        if actions:
            parts.append("Actions taken: " + "; ".join(actions) + ".")

    outcome = call.outcome or infer_outcome(call)
    parts.append(f"Outcome: {outcome}.")

    if call.status == "failed":
        parts.append("The call could not be completed.")
    elif call.status == "no_answer":
        parts.append("The call was not answered.")

    patient_msgs = _patient_texts(call)
    if patient_msgs and not call.reason:
        first = re.sub(r"\s+", " ", patient_msgs[0]).strip()
        if first:
            snippet = first if len(first) <= 140 else first[:137] + "..."
            parts.append(f'Caller opened with: "{snippet}"')

    return " ".join(parts)