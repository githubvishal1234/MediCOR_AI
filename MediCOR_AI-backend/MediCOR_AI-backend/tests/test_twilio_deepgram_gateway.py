"""
The Twilio + Deepgram voice gateway.

    pytest tests/test_twilio_deepgram_gateway.py -v

WHAT THIS FILE IS FOR

The LiveKit agent had exactly two tests worth the name -- the prompt's
emergency block and the frozen tool schema -- and both exist because the
things they guard are ones you cannot check by reading. This file is the
equivalent for the new stack, and it deliberately does not test that
modules import or that a dict has a key.

Four properties are load-bearing, and every one of them fails silently on
a live call rather than raising:

  * **Barge-in reaches Twilio.** Deepgram stops generating the moment it
    hears the caller, but Twilio has already been handed seconds of audio
    and will keep playing them. If the `clear` is not sent, interruption
    appears not to work even though everything upstream did its job --
    and no error is raised anywhere.

  * **A tool never claims something the backend did not do.** A failing
    tool must come back as a result the model can act on, never as an
    exception (which strands the caller in silence) and never as a
    success.

  * **The refill tool records and cannot approve.** F9's safety argument
    restated for a new tool layer.

  * **Signature verification fails closed.** These endpoints are public
    and they answer the phone.

Everything is exercised against fakes: no Twilio, no Deepgram, no
network, no audio device.
"""

import asyncio
import json
import os
import sys
import types

import pytest

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VOICE_AGENT = os.path.join(_BACKEND_DIR, "voice-agent")
if _VOICE_AGENT not in sys.path:
    sys.path.insert(0, _VOICE_AGENT)

import backend_client  # noqa: E402
from twilio_deepgram import prompt, settings, tools, twilio_api  # noqa: E402
from twilio_deepgram.bridge import CallBridge  # noqa: E402


# =====================================================================
# Fakes
# =====================================================================


class FakeTwilioWebSocket:
    """
    Stands in for the Twilio media-stream socket.

    Records everything sent to it, which is how the barge-in assertions
    below can be about what Twilio would actually have received rather
    than about which method was called.
    """

    def __init__(self, incoming: list[str] | None = None):
        self._incoming = list(incoming or [])
        self.sent: list[dict] = []
        self.closed = False

    async def receive_text(self) -> str:
        if not self._incoming:
            raise RuntimeError("twilio socket closed")
        return self._incoming.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True

    def events(self, name: str) -> list[dict]:
        return [m for m in self.sent if m.get("event") == name]


class FakeAPI:
    """
    A `backend_client.TenantClient` that answers from memory.

    Deliberately mirrors the real return shapes, because the tools' whole
    job is reshaping them for a model -- a fake that returned something
    convenient would test nothing.
    """

    def __init__(self, **overrides):
        self.calls: list[tuple] = []
        self.overrides = overrides
        self.raise_on: set[str] = set()

    async def _maybe_fail(self, name: str):
        if name in self.raise_on:
            raise backend_client.BackendAPIError("Slot is no longer available", 409)

    async def search_patients(self, query):
        self.calls.append(("search_patients", query))
        await self._maybe_fail("search_patients")
        return self.overrides.get(
            "patients",
            [
                {
                    "id": "pat-1",
                    "name": "Priya Sharma",
                    "phone": "+919876500001",
                    "preferred_language": "te-IN",
                    "speech_pace_preference": "slow",
                }
            ],
        )

    async def register_patient(self, **kwargs):
        self.calls.append(("register_patient", kwargs))
        return {"id": "pat-new", "mrn": "MRN-9", **kwargs}

    async def list_doctors(self):
        self.calls.append(("list_doctors", None))
        return [{"id": "doc-1", "name": "Dr Rao", "specialty": "General"}]

    async def get_availability(self, doctor_id, date):
        self.calls.append(("get_availability", doctor_id, date))
        return {
            "date": date,
            "slots": [
                {"start_at": "2026-09-15T10:00:00", "label": "10:00 AM"},
                {"start_at": "2026-09-15T14:30:00", "label": "2:30 PM"},
            ],
        }

    async def book_appointment(self, **kwargs):
        self.calls.append(("book_appointment", kwargs))
        await self._maybe_fail("book_appointment")
        return {"id": "appt-1", "status": "scheduled", **kwargs}

    async def list_patient_appointments(self, patient_id):
        self.calls.append(("list_patient_appointments", patient_id))
        return [{"id": "appt-1", "start_at": "2026-09-15T10:00:00"}]

    async def reschedule_appointment(self, **kwargs):
        self.calls.append(("reschedule_appointment", kwargs))
        return {"id": kwargs["appointment_id"], "status": "scheduled"}

    async def cancel_appointment(self, appointment_id):
        self.calls.append(("cancel_appointment", appointment_id))
        return {"id": appointment_id, "status": "cancelled"}

    async def get_patient_history(self, patient_id):
        self.calls.append(("get_patient_history", patient_id))
        return {"prescriptions": [], "visits": [], "ehr_source": "local_only"}

    async def get_clinic_info(self):
        self.calls.append(("get_clinic_info", None))
        return {"name": "HealthLink Clinic", "timings": "9-6"}

    async def list_services(self):
        self.calls.append(("list_services", None))
        return [{"name": "Consultation", "fee_label": "500"}]

    async def ask_knowledge_base(self, question):
        self.calls.append(("ask_knowledge_base", question))
        return {"answer": "We are open until six.", "source": "faq"}

    async def create_refill_request(self, **kwargs):
        self.calls.append(("create_refill_request", kwargs))
        return {"id": "refill-1", "medication": kwargs["medication"], "status": "pending"}

    async def check_escalation(self, utterance=None, caller_is_known=None):
        self.calls.append(("check_escalation", utterance))
        return self.overrides.get("escalation", {"escalate": False, "has_rules": True})


def make_runner(api=None, **kwargs):
    """A ToolRunner whose call-control callbacks record instead of acting."""
    record = {"transfer": [], "ended": False, "slowed": False}

    async def on_transfer(destination):
        record["transfer"].append(destination)
        return kwargs.get("transfer_result", "ok")

    async def on_end_call():
        record["ended"] = True

    async def on_speak_slower():
        record["slowed"] = True
        return True

    runner = tools.ToolRunner(
        api=api or FakeAPI(),
        on_transfer=on_transfer,
        on_end_call=on_end_call,
        on_speak_slower=on_speak_slower,
    )
    return runner, record


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# =====================================================================
# Barge-in
# =====================================================================


class TestInterruption:
    def test_user_started_speaking_clears_twilio_playback(self):
        """
        THE test for requirement 12.

        Twilio buffers everything we send and plays it out in real time.
        When Deepgram says the caller has started speaking, the sentence
        the agent was half-way through is still sitting in that buffer.
        Without a `clear`, the caller talks and the agent keeps talking
        over them -- and nothing anywhere reports a problem.
        """
        ws = FakeTwilioWebSocket()
        bridge = CallBridge(ws)
        bridge.stream_sid = "MZ123"

        run(bridge._handle_agent_event("UserStartedSpeaking", {}))

        cleared = ws.events("clear")
        assert cleared, "no clear sent to Twilio - the agent would talk over the caller"
        assert cleared[0]["streamSid"] == "MZ123"

    def test_agent_audio_is_forwarded_as_base64_media(self):
        ws = FakeTwilioWebSocket()
        bridge = CallBridge(ws)
        bridge.stream_sid = "MZ123"

        run(bridge._send_audio_to_twilio(b"\xff\xfe\xfd"))

        media = ws.events("media")
        assert len(media) == 1
        import base64

        assert base64.b64decode(media[0]["media"]["payload"]) == b"\xff\xfe\xfd"

    def test_nothing_is_sent_before_the_stream_is_identified(self):
        """
        Audio addressed to no stream is not merely useless -- Twilio
        rejects it, and a rejected frame on a live socket is a step
        towards the socket being closed under us.
        """
        ws = FakeTwilioWebSocket()
        bridge = CallBridge(ws)
        assert bridge.stream_sid is None

        run(bridge._send_audio_to_twilio(b"\x00"))
        run(bridge._clear_twilio_playback())

        assert ws.sent == []


# =====================================================================
# Tool execution
# =====================================================================


class TestToolsNeverBreakTheCall:
    def test_a_backend_failure_becomes_a_result_not_an_exception(self):
        """
        A tool that raises leaves the model waiting for a response that
        never arrives, which the caller experiences as the line going
        dead mid-sentence. Every failure has to come back as something
        the model can speak around.
        """
        api = FakeAPI()
        api.raise_on.add("book_appointment")
        runner, _ = make_runner(api)
        runner.patient_id = "pat-1"

        content = run(
            runner.execute(
                "book_appointment",
                {
                    "patient_id": "pat-1",
                    "doctor_id": "doc-1",
                    "start_at": "2026-09-15T10:00:00",
                },
            )
        )

        result = json.loads(content)
        assert result["ok"] is False
        assert "no longer available" in result["error"]
        # And crucially, nothing was recorded as having happened.
        assert runner.appointment_id is None
        assert runner.actions == []

    def test_an_unknown_tool_name_is_refused_rather_than_ignored(self):
        runner, _ = make_runner()
        result = json.loads(run(runner.execute("delete_everything", {})))
        assert result["ok"] is False

    def test_execute_always_returns_a_json_string(self):
        """Deepgram's FunctionCallResponse `content` is a string."""
        runner, _ = make_runner()
        content = run(runner.execute("list_doctors", {}))
        assert isinstance(content, str)
        assert json.loads(content)["doctors"][0]["id"] == "doc-1"

    def test_malformed_arguments_are_answered_not_crashed(self):
        """
        Deepgram sends `arguments` as a JSON string produced by a model.
        A model that emits broken JSON must produce a tool error it can
        recover from, not an unanswered function call.
        """
        ws = FakeTwilioWebSocket()
        bridge = CallBridge(ws)
        bridge.runner, _ = make_runner()

        sent = []

        async def capture(call_id, name, content):
            sent.append((call_id, name, json.loads(content)))

        bridge.agent.send_function_result = capture

        run(
            bridge._run_function(
                {"id": "fc_1", "name": "list_doctors", "arguments": "{not json"}
            )
        )

        assert sent, "the function call was never answered"
        assert sent[0][2]["ok"] is False


class TestBookingIsNeverClaimedWithoutTheBackend:
    def test_a_successful_booking_records_the_appointment(self):
        runner, _ = make_runner()
        result = json.loads(
            run(
                runner.execute(
                    "book_appointment",
                    {
                        "patient_id": "pat-1",
                        "doctor_id": "doc-1",
                        "start_at": "2026-09-15T10:00:00",
                    },
                )
            )
        )
        assert result["id"] == "appt-1"
        assert runner.appointment_id == "appt-1"
        assert "Booked appointment" in runner.actions

    def test_a_bare_time_is_recovered_only_from_slots_we_were_given(self):
        """
        Models echo back "10:00" instead of the timestamp they were
        handed. Recovering it from the availability response is not the
        agent inventing a slot -- every candidate came from the backend
        in this same call. A fragment matching nothing is passed through
        unchanged, so the backend rejects it, which is correct: a slot we
        cannot identify is one we must not book.
        """
        api = FakeAPI()
        runner, _ = make_runner(api)
        run(runner.execute("check_available_slots", {"doctor_id": "doc-1", "date": "2026-09-15"}))

        assert runner._resolve_start_at("10:00", "doc-1") == "2026-09-15T10:00:00"
        assert runner._resolve_start_at("14:30", "doc-1") == "2026-09-15T14:30:00"

        # Never seen. Passed through for the backend to refuse.
        recovered = runner._resolve_start_at("23:45", "doc-1")
        assert recovered != "2026-09-15T10:00:00"

    def test_availability_is_required_before_a_time_can_be_recovered(self):
        runner, _ = make_runner()
        assert runner._resolve_start_at("10:00", "doc-1") == "10:00"


class TestRefillsRecordAndCannotApprove:
    def test_the_result_says_pending_and_nothing_more(self):
        """
        F9's safety argument, restated for this tool layer. The result
        handed back to the model is deliberately narrow so it has no
        material with which to imply the refill is granted.
        """
        runner, _ = make_runner()
        runner.patient_id = "pat-1"
        result = json.loads(run(runner.execute("request_refill", {"medication": "Metformin"})))

        assert result == {
            "recorded": True,
            "medication": "Metformin",
            "status": "pending clinic review",
        }
        for forbidden in ("approved", "ready", "collect", "dispensed", "safe"):
            assert forbidden not in json.dumps(result).lower()

    def test_an_anonymous_request_is_refused(self):
        """
        A medication request with no patient is not actionable by staff,
        so it is refused rather than queued for nobody to resolve.
        """
        runner, _ = make_runner()
        result = json.loads(run(runner.execute("request_refill", {"medication": "Metformin"})))
        assert result["ok"] is False

    def test_the_schema_offers_no_way_to_set_a_status(self):
        """
        The narrow result is one half; this is the other. No parameter on
        this tool can approve anything, so a model cannot be talked into
        it.
        """
        schema = next(s for s in tools.FUNCTION_SCHEMAS if s["name"] == "request_refill")
        properties = set(schema["parameters"]["properties"])
        assert properties == {"medication", "note"}
        assert "approved" in schema["description"] or "approve" in schema["description"]


class TestPatientLookup:
    def test_speech_and_language_preferences_never_reach_the_model(self):
        """
        `PatientListOut` carries these so this process can act on them --
        set the pace, pick a language. They are stripped before the
        result becomes model context: the model has no use for either,
        and every field in a tool result is context it has to read.
        """
        runner, record = make_runner()
        result = json.loads(run(runner.execute("find_patient", {"query": "+919876500001"})))

        assert result["found"] is True
        patient = result["patients"][0]
        assert "preferred_language" not in patient
        assert "speech_pace_preference" not in patient
        # But it WAS acted on.
        assert record["slowed"] is True

    def test_one_match_is_remembered_but_does_not_authenticate(self):
        """
        Identifying who we are probably speaking to is what makes the
        rest of the call work. It is not proof of identity: several
        matches means a shared handset, and the tools still require the
        model to have looked someone up.
        """
        runner, _ = make_runner()
        run(runner.execute("find_patient", {"query": "+919876500001"}))
        assert runner.patient_id == "pat-1"

        api = FakeAPI(patients=[{"id": "a", "name": "A"}, {"id": "b", "name": "B"}])
        runner2, _ = make_runner(api)
        run(runner2.execute("find_patient", {"query": "+919876500001"}))
        assert runner2.patient_id is None


class TestEscalation:
    def test_a_clinic_with_no_rules_is_asked_once_and_then_left_alone(self):
        """
        The check runs on every completed caller turn. A clinic with no
        server-evaluated rules must not pay a round trip per turn for an
        answer that is always no.
        """
        api = FakeAPI(escalation={"escalate": False, "has_rules": False})
        runner, _ = make_runner(api)

        assert run(runner.maybe_escalate("hello")) is False
        assert runner.escalation_enabled is False
        assert run(runner.maybe_escalate("hello again")) is False

        checks = [c for c in api.calls if c[0] == "check_escalation"]
        assert len(checks) == 1

    def test_a_matching_rule_transfers_and_names_itself(self):
        """
        The rule's title goes into the action list, which is what the
        AI-7 judge reads. The model-only escalation path could never say
        *which* rule did this; that was the whole complaint.
        """
        api = FakeAPI(
            escalation={
                "escalate": True,
                "has_rules": True,
                "rule_title": "Chest pain",
                "destination": "+919999999999",
            }
        )
        runner, record = make_runner(api)

        assert run(runner.maybe_escalate("I have chest pain")) is True
        assert record["transfer"] == ["+919999999999"]
        assert any("Chest pain" in a for a in runner.actions)

    def test_a_failed_transfer_does_not_latch(self):
        """
        If the transfer did not take, a later turn must be able to try
        again -- otherwise the caller is left with the AI on a call the
        clinic said should have reached a person.
        """
        api = FakeAPI(
            escalation={"escalate": True, "has_rules": True, "rule_title": "X"}
        )
        runner, _ = make_runner(api, transfer_result="Twilio said no")

        assert run(runner.maybe_escalate("chest pain")) is False
        assert runner.transferred is False

    def test_escalation_survives_a_backend_that_is_down(self):
        """Rules are advisory; calls are not."""

        class Broken(FakeAPI):
            async def check_escalation(self, **kwargs):
                raise backend_client.BackendAPIError("unreachable")

        runner, _ = make_runner(Broken())
        assert run(runner.maybe_escalate("anything")) is False


# =====================================================================
# Twilio protocol
# =====================================================================


class TestTwilioSignature:
    KEY = "test-auth-token"

    def _sign(self, url, form, token=KEY):
        import hmac
        from base64 import b64encode
        from hashlib import sha1

        payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
        return b64encode(
            hmac.new(token.encode(), payload.encode(), sha1).digest()
        ).decode()

    def test_a_correct_signature_is_accepted(self, monkeypatch):
        monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", self.KEY)
        url = "https://gw.example.com/voice/incoming"
        form = {"CallSid": "CA1", "From": "+91999", "To": "+91888"}
        assert twilio_api.verify_signature(url, form, self._sign(url, form))

    def test_a_wrong_signature_is_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", self.KEY)
        url = "https://gw.example.com/voice/incoming"
        form = {"CallSid": "CA1"}
        assert not twilio_api.verify_signature(url, form, self._sign(url, form, "wrong"))

    def test_an_unset_token_verifies_nothing(self, monkeypatch):
        """
        Fails closed. An unset auth token must not mean "accept
        everything" on an endpoint that answers the phone and starts a
        paid conversation.
        """
        monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", None)
        url = "https://gw.example.com/voice/incoming"
        form = {"CallSid": "CA1"}
        assert not twilio_api.verify_signature(url, form, self._sign(url, form))
        assert not twilio_api.verify_signature(url, form, None)

    def test_the_signed_url_comes_from_the_configured_public_base(self, monkeypatch):
        """
        Behind a tunnel or a proxy the URL this process sees is not the
        one Twilio signed, and every signature fails for a reason that
        looks like a bad token.
        """
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://real.example.com")
        assert (
            twilio_api.public_url_for("/voice/incoming", "http://127.0.0.1:8080/voice/incoming")
            == "https://real.example.com/voice/incoming"
        )

    def test_without_a_public_base_the_request_url_is_used(self, monkeypatch):
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
        assert (
            twilio_api.public_url_for("/voice/incoming", "http://127.0.0.1:8080/voice/incoming")
            == "http://127.0.0.1:8080/voice/incoming"
        )


class TestTransferCannotLoop:
    """
    The transfer number must never be the number the caller dialled.

    On this stack the clinic's published number reaches the AI, so
    transferring to it puts the caller back through to the agent, which
    offers to transfer them again -- unbounded, billed by the minute on
    both legs, and the caller never reaches a person.

    It is a likely misconfiguration, not an exotic one: a clinic's "main
    number" is exactly what somebody would paste into a transfer field.
    """

    def test_the_same_line_is_recognised_through_formatting(self):
        assert twilio_api.same_number("+918071583192", "918071583192")
        assert twilio_api.same_number("+91 80 7158 3192", "+918071583192")
        # An international-prefix spelling of the same line.
        assert twilio_api.same_number("008918071583192", "+918071583192")
        # A clinic that stores its number without the country code still
        # matches, because the trailing digits identify the line.
        assert twilio_api.same_number("8071583192", "+918071583192")

    def test_different_lines_are_not_confused(self):
        assert not twilio_api.same_number("+918071583192", "+919000000000")
        assert not twilio_api.same_number("+918071583192", "")
        assert not twilio_api.same_number(None, "+918071583192")

    def test_a_short_extension_does_not_match_everything(self):
        """
        A suffix comparison on too few digits would make an extension
        match every number ending in those digits, which would refuse
        legitimate transfers.
        """
        assert not twilio_api.same_number("3192", "+918071583192")

    def test_a_transfer_to_the_dialled_number_is_refused(self):
        ws = FakeTwilioWebSocket()
        bridge = CallBridge(ws)
        bridge.stream_sid = "MZ1"

        from twilio_deepgram.call_session import CallSession

        bridge.session = CallSession(
            call_sid="CA1", dialed_number="+919000000000", caller_phone="+919111111111"
        )

        result = run(bridge._perform_transfer("+91 90 0000 0000"))
        assert result != "ok"
        assert "misconfigured" in result

    def test_a_transfer_to_a_real_staff_line_is_attempted(self, monkeypatch):
        ws = FakeTwilioWebSocket()
        bridge = CallBridge(ws)
        bridge.stream_sid = "MZ1"

        from twilio_deepgram.call_session import CallSession

        bridge.session = CallSession(
            call_sid="CA1", dialed_number="+919000000000", caller_phone="+919111111111"
        )

        attempted = {}

        async def fake_redirect(call_sid, destination, caller_id=None):
            attempted["to"] = destination
            return True

        monkeypatch.setattr(twilio_api, "redirect_to_dial", fake_redirect)
        assert run(bridge._perform_transfer("+918071583192")) == "ok"
        assert attempted["to"] == "+918071583192"


class TestTwiML:
    def test_a_bidirectional_stream_is_requested(self):
        """
        <Connect>, not <Start>. Start forks audio one way and lets the
        call continue; Connect is two-way and blocks, which is what makes
        closing the socket a hang-up.
        """
        xml = twilio_api.connect_stream("wss://gw.example.com/voice/stream")
        assert "<Connect>" in xml and "<Stream" in xml
        assert "<Start>" not in xml

    def test_the_dialled_number_is_passed_through_as_a_parameter(self):
        xml = twilio_api.connect_stream(
            "wss://gw.example.com/voice/stream",
            parameters={"dialed_number": "+919000000000", "caller_phone": "+919111111111"},
        )
        assert 'name="dialed_number"' in xml
        assert "+919000000000" in xml

    def test_no_credential_is_ever_put_in_the_twiml(self):
        """
        The TwiML travels to Twilio and back. Nothing that authenticates
        against the clinic's records goes through it -- the gateway
        resolves the tenant itself, from the number.
        """
        xml = twilio_api.connect_stream(
            "wss://gw.example.com/voice/stream",
            parameters={"dialed_number": "+91900", "caller_phone": "+91911"},
        )
        for secret in ("Bearer", "access_token", "X-API-Key", "sk_live"):
            assert secret not in xml

    def test_parameter_values_are_attribute_escaped(self):
        """
        Twilio's `To` is not attacker-controlled in practice, but this
        string is XML that a third party parses, and quoting it correctly
        costs nothing.
        """
        xml = twilio_api.connect_stream(
            "wss://x/y", parameters={"dialed_number": '"><Hangup/><!--'}
        )
        assert "<Hangup/>" not in xml

    def test_a_stream_url_is_derived_from_the_public_base(self, monkeypatch):
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://gw.example.com")
        monkeypatch.setattr(settings, "ROUTE_PREFIX", "/voice")
        assert settings.stream_websocket_url() == "wss://gw.example.com/voice/stream"

    def test_a_local_url_is_reported_as_unreachable_by_twilio(self, monkeypatch):
        """
        A loopback or LAN address works perfectly for `simulate_call.py`
        and not at all for a real call, and the failure is silent from
        this side: the gateway serves valid TwiML, Twilio cannot open the
        stream, and the caller hears nothing.

        Setting it locally is the right thing to do before the accounts
        exist -- so the check exists to stop that becoming the thing
        nobody remembered to change.
        """
        for local in (
            "http://127.0.0.1:8080",
            "http://localhost:8080",
            "https://localhost:8080",
            "https://192.168.1.20:8080",
            "https://10.0.0.5",
            "https://172.16.4.1",
            "http://gw.example.com",  # public host, but not TLS
        ):
            monkeypatch.setattr(settings, "PUBLIC_BASE_URL", local)
            assert not settings.reachable_by_twilio(), local
            assert any(
                "LOCAL address" in p for p in settings.startup_problems()
            ), local

    def test_a_real_public_https_url_is_accepted(self, monkeypatch):
        for public in (
            "https://gw.example.com",
            "https://abcd-1-2-3-4.ngrok-free.app",
            "https://voice.clinic.co.in:8443",
            "https://172.9.0.1",  # 172.9 is public; only 172.16-31 is private
        ):
            monkeypatch.setattr(settings, "PUBLIC_BASE_URL", public)
            assert settings.reachable_by_twilio(), public
            assert not any("LOCAL address" in p for p in settings.startup_problems())

    def test_the_local_url_still_produces_a_usable_stream_url(self, monkeypatch):
        """
        The point of setting it locally: the simulator needs a websocket
        URL, and ws:// against loopback is the correct one.
        """
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8080")
        monkeypatch.setattr(settings, "ROUTE_PREFIX", "/voice")
        assert settings.stream_websocket_url() == "ws://127.0.0.1:8080/voice/stream"

    def test_no_public_base_means_no_stream_url(self, monkeypatch):
        """
        Reported as empty so the webhook can say a sentence and hang up,
        rather than handing Twilio a URL that resolves to nothing and
        leaving the caller on a silent open line.
        """
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
        assert settings.stream_websocket_url() == ""


# =====================================================================
# Deepgram Settings message
# =====================================================================


class TestDeepgramSettings:
    def test_audio_is_twilios_format_in_both_directions(self):
        """
        8 kHz mulaw, uncontainerised. This is not a preference: it is
        what Twilio sends and what Twilio accepts, and anything else is a
        call that connects to noise.
        """
        audio = settings.audio_block()
        assert audio["input"] == {"encoding": "mulaw", "sample_rate": 8000}
        assert audio["output"]["encoding"] == "mulaw"
        assert audio["output"]["sample_rate"] == 8000
        assert audio["output"]["container"] == "none"

    def test_unset_options_are_omitted_rather_than_sent_as_null(self, monkeypatch):
        monkeypatch.setattr(settings, "DEEPGRAM_STT_VERSION", None)
        monkeypatch.setattr(settings, "DEEPGRAM_STT_EOT_THRESHOLD", None)
        provider = settings.listen_block()["provider"]
        assert "version" not in provider
        assert "eot_threshold" not in provider
        assert None not in provider.values()

    def test_language_hints_replace_language_rather_than_joining_it(self, monkeypatch):
        """
        Flux takes hints; Nova takes a language. Sending both is a
        contradiction, not belt and braces.
        """
        monkeypatch.setattr(settings, "DEEPGRAM_STT_LANGUAGE_HINTS", ["en", "hi"])
        provider = settings.listen_block()["provider"]
        assert provider["language_hints"] == ["en", "hi"]
        assert "language" not in provider

    def test_tools_are_client_side_so_no_credential_leaves_this_process(self):
        """
        A schema carrying an `endpoint` makes Deepgram call the backend
        itself -- which would mean handing them the org token that reads
        and writes one clinic's patient records.
        """
        for schema in tools.FUNCTION_SCHEMAS:
            assert "endpoint" not in schema, f"{schema['name']} would be server-side"

    def test_every_schema_is_well_formed(self):
        for schema in tools.FUNCTION_SCHEMAS:
            assert schema["name"] and schema["description"]
            params = schema["parameters"]
            assert params["type"] == "object"
            for required in params.get("required", []):
                assert required in params["properties"], (
                    f"{schema['name']} requires {required}, which it does not declare"
                )

    def test_the_declared_names_and_the_implemented_ones_are_the_same_set(self):
        """
        A schema with no handler is a tool the model will call and get
        nothing back from -- silence on a live call. A handler with no
        schema is dead code.
        """
        runner, _ = make_runner()
        for name in tools.TOOL_NAMES:
            assert hasattr(runner, f"_tool_{name}"), f"{name} has no implementation"

        implemented = {
            attr[len("_tool_"):] for attr in dir(runner) if attr.startswith("_tool_")
        }
        assert implemented == tools.TOOL_NAMES


# =====================================================================
# The prompt
# =====================================================================


class TestPromptSafety:
    """
    The same properties `tests/test_agent_prompt_safety.py` pins for the
    LiveKit prompt. A second stack does not get a second, laxer standard.
    """

    def test_the_emergency_block_exists_and_outranks_everything(self):
        text = prompt.build_instructions()
        assert "EMERGENCY" in text
        assert text.index("EMERGENCY") < text.index("APPOINTMENT")
        assert "outranks every other instruction" in text

    def test_the_emergency_wording_comes_from_configuration(self):
        import config as agent_config

        text = prompt.build_instructions()
        assert agent_config.EMERGENCY_NUMBER in text
        # The placeholder must always be substituted, or the agent reads
        # the literal word to a caller describing chest pain.
        assert "EMERGENCY_LINE" not in text
        assert "{number}" not in text

    def test_the_emergency_number_is_spoken_digit_by_digit(self):
        """
        Text-to-speech number normalisation turns "112" into "one hundred
        and twelve" as readily as into "one one two" -- Sarvam's
        `enable_preprocessing` exists precisely to make numbers sound
        natural, which is right for a dosage and wrong for the number
        somebody is meant to dial while having a heart attack.

        The model writes the words, so the instruction goes in the
        prompt. Verify how it actually sounds on the first real call:
        this is a mitigation, not a guarantee.
        """
        text = prompt.build_instructions()
        block = text[text.index("EMERGENCY"):text.index("ACCESSIBILITY")]
        assert "one digit at a time" in block

    def test_a_configured_emergency_number_is_actually_reachable_in_the_prompt(self):
        """
        The number is configuration, so the substitution is what makes it
        real. A prompt that still said "your local emergency number"
        would be read out verbatim to a caller describing chest pain --
        which is what the default does, and why it must be set.
        """
        import config as agent_config

        text = prompt.build_instructions()
        assert agent_config.EMERGENCY_NUMBER in text
        assert "your local emergency number" not in text, (
            "EMERGENCY_NUMBER is still the placeholder; set it in voice-agent/.env"
        )

    def test_the_agent_is_told_not_to_diagnose_or_advise(self):
        text = prompt.build_instructions()
        assert "Do not diagnose" in text
        assert "Never give medical advice" in text

    def test_a_recognised_name_is_described_as_a_guess(self):
        """
        Otherwise the model proceeds as though the caller is identified,
        which on a shared handset is how one family member hears
        another's appointments.
        """
        text = prompt.build_instructions(recognised_name="Priya")
        assert "Priya" in text
        assert "That is a guess" in text
        assert "still run find_patient" in text

    def test_no_name_means_no_caller_section_at_all(self):
        assert "CALLER:" not in prompt.build_instructions()

    def test_the_refill_boundary_is_in_the_prompt_too(self):
        """
        Said in three places -- the backend route, the tool description,
        and here -- because it is the one claim the agent must never
        make, and a prompt is the layer a model actually reads on the way
        to speaking.
        """
        text = prompt.build_instructions()
        assert "RECORDS a repeat-prescription request" in text
        assert "It never approves one." in text
        assert "Never say it is approved, ready" in text
        assert "Never advise on dose" in text

    def test_the_agent_may_not_claim_an_unconfirmed_booking(self):
        text = prompt.build_instructions()
        assert "AFTER the tool" in text
        assert "never guess at the outcome" in text

    def test_it_does_not_promise_a_language_the_voice_cannot_speak(self):
        """
        The LiveKit stack could offer Hindi and Telugu because Sarvam
        could speak them. Deepgram's voices cannot, and the API will not
        accept Sarvam as a speak provider -- so a prompt claiming three
        languages would have the agent answer in one it physically cannot
        pronounce.
        """
        english_only = prompt.build_instructions(tts_provider="deepgram")
        assert "Speak English only." in english_only
        assert "Telugu" not in english_only.split("EMERGENCY")[0]

        with_elevenlabs = prompt.build_instructions(tts_provider="eleven_labs")
        assert "Telugu" in with_elevenlabs
        assert "Hindi" in with_elevenlabs

    def test_a_caller_speaking_another_language_is_handed_to_a_person(self):
        """
        An English-only agent on an Indian clinic line will meet Hindi and
        Telugu callers on day one. The two bad outcomes are the model
        attempting a few words through a voice that cannot form them --
        which comes out as noise -- and the model ploughing on in English
        at somebody who has already shown they cannot follow it.

        Transferring is the honest answer, and it needs saying in the
        prompt, because nothing else in the system can detect this.
        """
        text = prompt.build_instructions()
        block = text.split("EMERGENCY")[0]
        assert "cannot answer in" in block
        assert "transfer_to_human" in block
        assert "do not simply carry on in" in block


class TestEnglishOnlyIsConfiguredEndToEnd:
    """
    English-only is a decision, not an accident of the defaults, so the
    pieces that would each silently undo it are checked together.

    Any one of them can be flipped in `.env` on the day Hindi and Telugu
    are added; what must not happen is one of them drifting on its own,
    leaving the agent listening for a language it cannot answer or
    claiming one it cannot say.
    """

    def test_the_listener_is_english_not_code_switching(self):
        """
        `multi` spreads the model across ten languages so a caller can
        switch mid-sentence -- worth paying for only when the agent can
        answer in them. It cannot, so `multi` would buy a slightly worse
        transcription of the English it is going to reply to.
        """
        provider = settings.listen_block()["provider"]
        assert provider["language"] == "en"
        assert "language_hints" not in provider

    def test_the_voice_is_an_english_one(self):
        provider = settings.speak_block()["provider"]
        assert provider["type"] == "deepgram"
        assert str(provider["model"]).endswith("-en")

    def test_the_prompt_agrees_with_the_configured_voice(self):
        """
        The prompt's language claim is derived from the TTS provider
        rather than written next to it, so the two cannot disagree.
        """
        text = prompt.build_instructions(tts_provider=settings.DEEPGRAM_TTS_PROVIDER)
        assert "English only" in text

    def test_hindi_and_telugu_remain_one_env_change_away(self):
        """
        Deferred, not deleted. The route back is configuration, and it
        stays tested so it does not rot before it is needed.
        """
        assert prompt.spoken_languages("eleven_labs") != prompt.spoken_languages("deepgram")
        assert "Hindi" in prompt.spoken_languages("eleven_labs")
        assert "Telugu" in prompt.spoken_languages("eleven_labs")

    def test_escalation_rules_are_only_added_when_a_clinic_has_some(self):
        """
        The prompt is re-sent as context on every turn. An empty section
        would be paid for on every call, for nothing.
        """
        assert "ESCALATION:" not in prompt.build_instructions(escalation_rules=[])
        assert "ESCALATION:" in prompt.build_instructions(
            escalation_rules=["Chest pain - transfer immediately"]
        )

    def test_it_names_tools_that_actually_exist(self):
        """
        `config.SYSTEM_PROMPT` drifted into naming six tools that had
        been replaced, and nobody noticed because a prompt does not fail
        to compile. Any tool this prompt instructs the model to call has
        to be one the model was actually given.
        """
        import re

        text = prompt.build_instructions()
        mentioned = set(re.findall(r"\b(?:call |use )([a-z_]{4,})\b", text))
        # Only consider words that look like our tool names.
        candidates = {m for m in mentioned if "_" in m}
        unknown = candidates - tools.TOOL_NAMES
        assert not unknown, f"prompt names tools that do not exist: {unknown}"


# =====================================================================
# Call record
# =====================================================================


class TestCallRecord:
    def test_the_transcript_is_translated_into_the_backends_vocabulary(self):
        from twilio_deepgram.call_session import CallSession

        session = CallSession("CA1", "+91900", "+91911")
        session.record_utterance("assistant", "Hello, how can I help?")
        session.record_utterance("user", "I need an appointment")
        session.record_utterance("system", "ignored")
        session.record_utterance("user", "   ")

        assert session.transcript == [
            {"who": "ai", "text": "Hello, how can I help?"},
            {"who": "patient", "text": "I need an appointment"},
        ]

    def test_a_recognised_caller_is_asked_rather_than_assumed(self):
        from twilio_deepgram.call_session import CallSession

        session = CallSession("CA1", "+91900", "+91911")
        session.recognised_name = "Priya"
        greeting = session.greeting("Welcome to HealthLink.")
        assert greeting == "Welcome to HealthLink. Am I speaking with Priya?"

    def test_the_clinic_greets_before_asking_who_is_calling(self):
        """
        Putting the question first produced "Hello, am I speaking with
        Vishal? Hello, this is MediCOR..." -- two greetings in one
        breath, because almost every clinic greeting opens with one.
        Only visible once caller recognition started matching anybody.
        """
        from twilio_deepgram.call_session import CallSession

        session = CallSession("CA1", "+91900", "+91911")
        session.recognised_name = "Vishal"
        greeting = session.greeting(
            "Hello, this is MediCOR, your clinic AI receptionist. How can I help you?"
        )
        assert greeting.lower().count("hello") == 1
        assert greeting.index("MediCOR") < greeting.index("Vishal")

    def test_an_unrecognised_caller_gets_the_clinics_greeting_unchanged(self):
        from twilio_deepgram.call_session import CallSession

        session = CallSession("CA1", "+91900", None)
        assert session.greeting("Welcome to HealthLink.") == "Welcome to HealthLink."

    def test_there_is_a_greeting_even_with_no_clinic_setting(self):
        from twilio_deepgram.call_session import CallSession

        session = CallSession("CA1", None, None)
        assert "MediCOR" in session.greeting(None)

    def test_a_call_that_never_resolved_a_tenant_finalises_without_raising(self):
        """
        The backend being unreachable at call start leaves no call id.
        Teardown still has to run cleanly -- an exception here would be
        raised inside a websocket handler at hang-up, where nothing is
        left to catch it usefully.
        """
        from twilio_deepgram.call_session import CallSession

        session = CallSession("CA1", None, None)
        run(session.finalize())
        run(session.aclose())


class TestGatewayConfiguration:
    def test_a_missing_deepgram_key_is_reported(self, monkeypatch):
        monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", None)
        assert any("DEEPGRAM_API_KEY" in p for p in settings.startup_problems())

    def test_running_the_wrong_stack_is_reported(self, monkeypatch):
        monkeypatch.setattr(settings, "VOICE_PROVIDER", "livekit")
        problems = settings.startup_problems()
        assert any("VOICE_PROVIDER" in p for p in problems)
        assert any("agent.py" in p for p in problems)

    def test_a_correctly_configured_gateway_reports_nothing(self, monkeypatch):
        import config as agent_config

        monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "dg-key")
        monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tw-token")
        monkeypatch.setattr(settings, "SARVAM_API_KEY", "sv-key")
        monkeypatch.setattr(settings, "LLM_API_KEY", "llm-key")
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://gw.example.com")
        monkeypatch.setattr(settings, "VOICE_PROVIDER", "twilio")
        monkeypatch.setattr(settings, "VOICE_AGENT_PROVIDER", "deepgram")
        monkeypatch.setattr(agent_config, "DEFAULT_TRANSFER_NUMBER", "+919876543210")
        monkeypatch.setattr(agent_config, "EMERGENCY_NUMBER", "112")
        assert settings.startup_problems() == []

    def test_a_missing_transfer_number_is_reported_not_discovered(self, monkeypatch):
        """
        Four paths quietly stop working without it -- "I want a person",
        every escalation rule, the emergency transfer offer, and the
        apology after a model failure. The agent will still *offer* a
        transfer it cannot perform, so this belongs at startup rather
        than in a caller's experience.

        Not fatal: a clinic that has not chosen a staff line should still
        be able to answer calls and book appointments.
        """
        import config as agent_config

        monkeypatch.setattr(agent_config, "DEFAULT_TRANSFER_NUMBER", None)
        problems = settings.startup_problems()
        assert any("DEFAULT_TRANSFER_NUMBER" in p for p in problems)
        assert any("apologise instead of connecting" in p for p in problems)

    def test_the_emergency_placeholder_is_reported(self, monkeypatch):
        """
        Left unset, the agent reads the literal words "your local
        emergency number" to somebody describing chest pain.
        """
        import config as agent_config

        monkeypatch.setattr(
            agent_config, "EMERGENCY_NUMBER", "your local emergency number"
        )
        assert any("EMERGENCY_NUMBER" in p for p in settings.startup_problems())
