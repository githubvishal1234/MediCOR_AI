"""
A call may not claim a booking it cannot show.

    pytest tests/test_outcome_needs_evidence.py -v

`CallLog.outcome` is not a description of a conversation. "Booked",
"Rescheduled" and "Cancelled" are assertions about rows in
`appointments`, and the dashboard filters on the exact string -- so a
call wrongly marked `Booked` appears in the booked list, and the
receptionist reviewing the day has no reason to look at it again. The
patient arrives for an appointment nobody made.

Both ways this codebase derives an outcome were willing to make that
claim from talk alone:

  * `call_intelligence.infer_outcome` scanned the transcript for the
    word "book", so a caller *asking* to book produced `Booked`;
  * `llm_service` handed a model the same transcript and a vocabulary
    containing the word, which is precisely the material needed to reach
    the same wrong conclusion.

Observed on a real call in this repository: a conversation in which
nothing was booked, `actions_taken` empty and `appointment_id` null, was
stored as `outcome="Booked"`.

`appointment_id` is the evidence. The agent sets it only after the
backend has confirmed the booking, reschedule or cancellation, so its
absence means the claim cannot be supported.
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app import call_intelligence, llm_service, models  # noqa: E402


def make_call(**kwargs):
    """A finished call, with whatever evidence the test wants on it."""
    said = kwargs.pop("said", [])
    call = models.CallLog(
        id="call-1",
        org_id="org-1",
        status=kwargs.pop("status", "completed"),
        **kwargs,
    )
    call.transcript_messages = [
        models.TranscriptMessage(id=f"m{i}", call_id="call-1", who=who, text=text)
        for i, (who, text) in enumerate(said)
    ]
    return call


BOOKING_TALK = [
    ("patient", "Hello, I would like to book an appointment please."),
    ("ai", "Certainly. I can offer tomorrow morning."),
    ("patient", "Yes, ten o'clock works for me."),
    ("ai", "Thank you for calling. Goodbye."),
]


# =====================================================================
# The rule
# =====================================================================


class TestVerifiedOutcome:
    def test_an_appointment_claim_without_an_appointment_is_dropped(self):
        call = make_call(appointment_id=None)
        for claim in ("Booked", "Rescheduled", "Cancelled"):
            assert call_intelligence.verified_outcome(claim, call) is None, claim

    def test_the_same_claim_stands_once_there_is_an_appointment(self):
        call = make_call(appointment_id="appt-1")
        for claim in ("Booked", "Rescheduled", "Cancelled"):
            assert call_intelligence.verified_outcome(claim, call) == claim

    def test_outcomes_that_describe_the_call_are_untouched(self):
        """
        "FAQ Answered" and "Transferred to Nurse" describe what happened
        in the conversation rather than to the appointment book, so they
        need no appointment to be true.
        """
        call = make_call(appointment_id=None)
        for outcome in ("FAQ Answered", "Transferred to Nurse", "Completed",
                        "Call Failed", "No Answer"):
            assert call_intelligence.verified_outcome(outcome, call) == outcome

    def test_the_guarded_set_is_exactly_the_appointment_claims(self):
        assert call_intelligence.APPOINTMENT_OUTCOMES == {
            "Booked", "Rescheduled", "Cancelled"
        }

    def test_every_guarded_outcome_is_one_the_dashboard_knows(self):
        """
        The guard drops values; it must not drop a value the vocabulary
        does not contain, which would mean the two lists had drifted.
        """
        assert call_intelligence.APPOINTMENT_OUTCOMES <= set(
            llm_service.ALLOWED_OUTCOMES
        )


# =====================================================================
# The rule-based path
# =====================================================================


class TestRuleBasedInference:
    def test_asking_to_book_is_not_booking(self):
        """
        THE regression test. This exact conversation was stored as
        `Booked` with no appointment behind it.
        """
        call = make_call(said=BOOKING_TALK, appointment_id=None)
        assert call_intelligence.infer_outcome(call) == "Completed"

    def test_a_real_booking_is_still_reported(self):
        call = make_call(said=BOOKING_TALK, appointment_id="appt-1")
        assert call_intelligence.infer_outcome(call) == "Booked"

    def test_asking_to_cancel_is_not_cancelling(self):
        """
        Worse than the booking case: a call wrongly marked `Cancelled`
        suggests an appointment was released that is still in the book.
        """
        call = make_call(
            said=[("patient", "I need to cancel my appointment on Friday.")],
            appointment_id=None,
        )
        assert call_intelligence.infer_outcome(call) == "Completed"

    def test_a_real_cancellation_is_still_reported(self):
        call = make_call(
            said=[("patient", "I need to cancel my appointment on Friday.")],
            appointment_id="appt-1",
        )
        assert call_intelligence.infer_outcome(call) == "Cancelled"

    def test_the_agents_own_action_list_is_believed_when_it_can_be_checked(self):
        call = make_call(actions_taken="Booked appointment", appointment_id="appt-1")
        assert call_intelligence.infer_outcome(call) == "Booked"

    def test_an_action_list_claiming_more_than_the_record_shows_is_not_repeated(self):
        """
        `actions_taken` is the agent's own account. It is strong
        evidence, but when the appointment it names never reached the
        call record something went wrong between the two, and the claim
        is not ours to repeat.
        """
        call = make_call(actions_taken="Booked appointment", appointment_id=None)
        assert call_intelligence.infer_outcome(call) == "Completed"

    def test_a_transfer_survives_without_an_appointment(self):
        """
        Saying it is the closest this path gets to observing it, and a
        transfer makes no claim about the appointment book.
        """
        call = make_call(actions_taken="Transferred to staff", appointment_id=None)
        assert call_intelligence.infer_outcome(call) == "Transferred to Nurse"

    def test_a_failed_call_is_still_a_failed_call(self):
        call = make_call(said=BOOKING_TALK, status="failed", appointment_id=None)
        assert call_intelligence.infer_outcome(call) == "Call Failed"

    def test_a_silent_call_is_reported_as_no_interaction(self):
        call = make_call(appointment_id=None)
        assert call_intelligence.infer_outcome(call) == "No Interaction"


# =====================================================================
# The LLM path
# =====================================================================


class TestModelClaims:
    def _coerce(self, payload, call):
        return llm_service._validate(payload, call)

    def test_a_model_claiming_a_booking_with_no_appointment_is_overruled(self):
        """
        In vocabulary is not the same as true. The model is reading a
        transcript in which somebody asked to book, which is exactly the
        material needed to conclude wrongly that it happened.
        """
        call = make_call(said=BOOKING_TALK, appointment_id=None)
        result = self._coerce(
            {"summary": "Caller asked to book.", "outcome": "Booked",
             "sentiment": "Neutral"},
            call,
        )
        assert result is not None
        assert result["outcome"] is None  # falls back to the rule-based value

    def test_a_model_claiming_a_booking_that_happened_is_kept(self):
        call = make_call(said=BOOKING_TALK, appointment_id="appt-1")
        result = self._coerce(
            {"summary": "Booked for ten o'clock.", "outcome": "Booked",
             "sentiment": "Positive"},
            call,
        )
        assert result["outcome"] == "Booked"

    def test_a_model_describing_the_call_is_left_alone(self):
        call = make_call(said=BOOKING_TALK, appointment_id=None)
        result = self._coerce(
            {"summary": "Answered a question about opening hours.",
             "outcome": "FAQ Answered", "sentiment": "Neutral"},
            call,
        )
        assert result["outcome"] == "FAQ Answered"

    def test_an_out_of_vocabulary_outcome_is_still_dropped(self):
        """The pre-existing guard, which this must not have displaced."""
        call = make_call(said=BOOKING_TALK, appointment_id="appt-1")
        result = self._coerce(
            {"summary": "s", "outcome": "Appointment Made", "sentiment": "Neutral"},
            call,
        )
        assert result["outcome"] is None


# =====================================================================
# End to end, through the endpoint that actually sets it
# =====================================================================


class TestThroughTheCallRecord:
    def test_a_conversation_about_booking_ends_up_honest(self):
        """
        What the dashboard will show for the mock call in this
        repository: a conversation, not a booking.
        """
        call = make_call(said=BOOKING_TALK, appointment_id=None, actions_taken=None)
        outcome = call_intelligence.infer_outcome(call)
        assert outcome == "Completed"
        assert outcome in llm_service.ALLOWED_OUTCOMES, (
            "the dashboard filters on this exact string"
        )

    @pytest.mark.parametrize("outcome", ["Booked", "Rescheduled", "Cancelled"])
    def test_no_appointment_claim_can_be_produced_without_an_appointment(self, outcome):
        """
        Whatever the transcript says and whatever the agent reported,
        neither derivation path can produce an appointment claim on a
        call with no appointment.
        """
        call = make_call(
            said=[("patient", "book cancel reschedule my appointment")],
            actions_taken=f"{outcome} appointment",
            appointment_id=None,
        )
        assert call_intelligence.infer_outcome(call) not in (
            call_intelligence.APPOINTMENT_OUTCOMES
        )
        coerced = llm_service._validate(
            {"summary": "s", "outcome": outcome, "sentiment": "Neutral"}, call
        )
        assert coerced["outcome"] is None
