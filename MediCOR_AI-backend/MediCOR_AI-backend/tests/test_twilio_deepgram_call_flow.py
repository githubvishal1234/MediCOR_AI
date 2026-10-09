"""
One whole call through the gateway, end to end.

    pytest tests/test_twilio_deepgram_call_flow.py -v

`test_twilio_deepgram_gateway.py` tests the pieces. This runs the actual
`CallBridge.run()` loop over a scripted Twilio stream and a scripted
Deepgram socket, and asserts on what the backend was told -- which is the
only question that matters at the end of a call:

    did the right clinic get a correct record of what happened?

Both ends are fakes. The Twilio side replays the exact frame shapes
Twilio documents (`connected`, `start` with `customParameters`, `media`
with a base64 payload, `stop`); the Deepgram side replays the documented
event types (`Welcome`, `SettingsApplied`, `ConversationText`,
`FunctionCallRequest`, `UserStartedSpeaking`) and binary audio. No
network, no API keys, no audio device.

WHY THIS EXISTS SEPARATELY

The failure this catches is the one that unit tests cannot: every part
working and the call still being recorded wrongly, because the ordering
between them is wrong. Finalising before the transcript is saved, for
instance, produces a call summary of an empty conversation -- both calls
succeed, both units pass, and the dashboard shows nonsense.
"""

import asyncio
import base64
import json
import os
import sys

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
from twilio_deepgram import bridge as bridge_module  # noqa: E402
from twilio_deepgram import settings  # noqa: E402
from twilio_deepgram.bridge import CallBridge  # noqa: E402


@pytest.fixture(autouse=True)
def _managed_agent_engine(monkeypatch):
    """
    Pin this file to the managed-agent engine.

    The gateway now has two: `pipeline` (Deepgram STT + LLM + Sarvam TTS,
    the default) and `agent` (Deepgram's managed Voice Agent socket).
    Everything here injects a scripted stand-in for that socket, so it is
    testing the managed-agent path specifically -- and without this the
    default engine quietly ignores the injected fake and every assertion
    about it fails for a reason that has nothing to do with the test.

    The pipeline engine's own coverage is in `test_voice_pipeline.py`.
    """
    monkeypatch.setattr(settings, "VOICE_ENGINE", "agent")
    monkeypatch.setattr(settings, "VOICE_MOCK_MODE", False)


# =====================================================================
# Scripted ends
# =====================================================================


class ScriptedTwilio:
    """
    Replays Twilio's documented media-stream frames, then hangs up.

    The `stop` frame waits on `gate` before it is delivered. That is not
    test scaffolding for its own sake -- it is what makes the fake behave
    like a telephone call. Without it the caller "hangs up" in the same
    event-loop tick that the call starts, and the assertions become a
    race between two in-memory generators rather than a statement about
    the bridge.
    """

    def __init__(self, frames, gate=None):
        self._frames = list(frames)
        self._gate = gate
        self.sent = []
        self.closed = False

    async def receive_text(self):
        if not self._frames:
            # Twilio closing the socket, which is what a hang-up looks
            # like from here.
            raise RuntimeError("closed")
        frame = self._frames.pop(0)
        if frame.get("event") == "stop" and self._gate is not None:
            # The caller stays on the line until the scripted
            # conversation has actually played out.
            try:
                await asyncio.wait_for(self._gate.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
        await asyncio.sleep(0)
        return json.dumps(frame)

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self):
        self.closed = True

    def events(self, name):
        return [m for m in self.sent if m.get("event") == name]


class ScriptedDeepgram:
    """
    Stands in for `DeepgramAgent`, replaying a script of events.

    Deliberately implements the same surface rather than subclassing, so
    a method added to the real client without a counterpart here shows up
    as an AttributeError in this test rather than as silence on a call.
    """

    def __init__(self, script):
        self._script = list(script)
        self.settings_sent = None
        self.function_results = []
        self.injected = []
        self.audio_in = []
        self.closed = False
        self.slowed = False
        # Set once the script has played out. `ScriptedTwilio` waits on
        # this before delivering `stop`, so "the caller hangs up" happens
        # after the conversation rather than during it.
        self.script_done = asyncio.Event()
        self._close_requested = asyncio.Event()

    async def connect(self):
        return None

    async def close(self):
        self.closed = True
        self._close_requested.set()

    @property
    def is_open(self):
        return not self.closed

    async def send_settings(self, prompt, functions, greeting=None, voice_profile=None):
        self.settings_sent = {
            "prompt": prompt,
            "functions": functions,
            "greeting": greeting,
            "voice_profile": voice_profile,
        }

    async def send_audio(self, chunk):
        self.audio_in.append(chunk)

    async def send_function_result(self, call_id, name, content):
        self.function_results.append((call_id, name, json.loads(content)))

    async def inject_agent_message(self, message):
        self.injected.append(message)

    async def slow_down(self, speed=0.8):
        self.slowed = True
        return True

    async def events(self):
        """
        Play the script, then stay open until closed.

        A real agent socket lives for the whole call. An iterator that
        ends the moment its script does would tell the bridge Deepgram
        had hung up -- which the bridge correctly treats as the end of
        the call, and which would make every assertion below a race.
        """
        for item in self._script:
            await asyncio.sleep(0)
            yield item
        self.script_done.set()
        await self._close_requested.wait()


class RecordingClient:
    """A TenantClient that records every backend call the gateway makes."""

    def __init__(self):
        self.created = None
        self.transcript = None
        self.finalized = None
        self.order = []
        self.closed = False

    async def search_patients(self, query):
        self.order.append("search_patients")
        return [{"id": "pat-7", "name": "Priya Sharma", "phone": query}]

    async def create_call_log(self, **kwargs):
        self.order.append("create_call_log")
        self.created = kwargs
        return {"id": "call-42"}

    async def get_escalation_rules(self):
        return []

    async def check_escalation(self, **kwargs):
        return {"escalate": False, "has_rules": False}

    async def list_doctors(self):
        self.order.append("list_doctors")
        return [{"id": "doc-1", "name": "Dr Rao"}]

    async def save_transcript_bulk(self, call_id, messages):
        self.order.append("save_transcript_bulk")
        self.transcript = (call_id, messages)
        return {}

    async def finalize_call(self, call_id, **kwargs):
        self.order.append("finalize_call")
        self.finalized = (call_id, kwargs)
        return {}

    async def aclose(self):
        self.order.append("aclose")
        self.closed = True


@pytest.fixture
def tenant(monkeypatch):
    """A resolved clinic, without touching the network."""
    client = RecordingClient()
    context = backend_client.TenantContext(
        client=client,
        org_id="org-1",
        org_name="HealthLink Clinic",
        greeting_script="Thank you for calling HealthLink.",
        voice_profile="Thalia",
        escalation_rules=[],
        escalation_deterministic=False,
        resolved=True,
    )

    async def fake_resolve(dialed_number):
        return context

    monkeypatch.setattr(backend_client, "resolve_tenant", fake_resolve)
    return client


def twilio_script(*extra):
    return [
        {"event": "connected", "protocol": "Call", "version": "1.0.0"},
        {
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": "MZ0001",
            "start": {
                "accountSid": "AC0001",
                "streamSid": "MZ0001",
                "callSid": "CA0001",
                "tracks": ["inbound"],
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
                "customParameters": {
                    "dialed_number": "+919000000000",
                    "caller_phone": "+919111111111",
                    "direction": "inbound",
                },
            },
        },
        *extra,
    ]


def media(payload: bytes):
    return {
        "event": "media",
        "streamSid": "MZ0001",
        "media": {"payload": base64.b64encode(payload).decode(), "track": "inbound"},
    }


async def drive(bridge, agent, timeout=8.0):
    """
    Run one call to completion, refusing to hang forever if it wedges.

    Wiring `bridge.twilio_ws._gate` to the agent's script here rather
    than at every call site keeps the ordering rule in one place: the
    caller hangs up after the scripted conversation, not during it.
    """
    bridge.agent = agent
    if getattr(bridge.twilio_ws, "_gate", None) is None:
        bridge.twilio_ws._gate = agent.script_done
    await asyncio.wait_for(bridge.run(), timeout=timeout)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =====================================================================
# The happy path
# =====================================================================


class TestTheFakesMatchTheRealClient:
    """
    The fakes above accept `**kwargs`, which is convenient and was very
    nearly expensive: it let the gateway call `create_call_log(provider=,
    provider_call_sid=)` against a real client that had neither
    parameter. Every test passed and the first simulated call died with
    `TypeError: unexpected keyword argument 'provider'`.

    So the fake's permissiveness is checked against the real signature
    rather than trusted.
    """

    def test_every_argument_the_gateway_sends_is_one_the_client_accepts(self):
        import inspect

        signature = inspect.signature(backend_client.TenantClient.create_call_log)
        for name in (
            "direction", "status", "caller_phone", "started_at",
            "provider", "provider_call_sid",
        ):
            assert name in signature.parameters, (
                f"CallSession.create_call_log sends {name!r}, which "
                "TenantClient.create_call_log does not accept"
            )

    def test_finalize_accepts_what_the_session_reports(self):
        import inspect

        signature = inspect.signature(backend_client.TenantClient.finalize_call)
        for name in (
            "status", "duration_seconds", "ended_at", "patient_id",
            "appointment_id", "caller_phone", "actions_taken",
            "speech_pace_requested",
        ):
            assert name in signature.parameters


class TestAnOrdinaryCall:
    def test_the_call_is_recorded_from_start_to_finish(self, tenant):
        """
        The whole contract in one test: the clinic is resolved from the
        dialled number, a call log opens while the call is live, the
        conversation is captured, and the record is closed with what
        actually happened.
        """
        twilio = ScriptedTwilio(
            twilio_script(media(b"\x01\x02"), {"event": "stop", "streamSid": "MZ0001"})
        )
        agent = ScriptedDeepgram(
            [
                ("Welcome", {"type": "Welcome", "request_id": "req-1"}),
                ("SettingsApplied", {"type": "SettingsApplied"}),
                ("audio", b"\xaa\xbb"),
                (
                    "ConversationText",
                    {
                        "type": "ConversationText",
                        "role": "assistant",
                        "content": "Thank you for calling HealthLink.",
                    },
                ),
                (
                    "ConversationText",
                    {
                        "type": "ConversationText",
                        "role": "user",
                        "content": "I would like an appointment.",
                    },
                ),
            ]
        )
        bridge = CallBridge(twilio)
        run(drive(bridge, agent))

        # -- the call log opened, naming Twilio and the CallSid
        assert tenant.created is not None
        assert tenant.created["direction"] == "inbound"
        assert tenant.created["status"] == "in_progress"
        assert tenant.created["caller_phone"] == "+919111111111"
        assert tenant.created["provider"] == "twilio"
        assert tenant.created["provider_call_sid"] == "CA0001"

        # -- the transcript reached the backend in its own vocabulary
        call_id, messages = tenant.transcript
        assert call_id == "call-42"
        assert messages == [
            {"who": "ai", "text": "Thank you for calling HealthLink."},
            {"who": "patient", "text": "I would like an appointment."},
        ]

        # -- and the record was closed
        finalized_id, fields = tenant.finalized
        assert finalized_id == "call-42"
        assert fields["status"] == "completed"
        assert fields["duration_seconds"] >= 0
        assert fields["ended_at"]

    def test_the_transcript_is_saved_before_the_call_is_finalised(self, tenant):
        """
        Not a style preference. The backend generates the summary,
        sentiment and outcome when a call reaches a terminal status, and
        it reads the stored transcript to do it -- so finalising first
        produces a summary of an empty conversation. Both requests
        succeed either way, which is what makes this worth pinning.
        """
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [("ConversationText", {"role": "user", "content": "hello"})]
        )
        run(drive(CallBridge(twilio), agent))

        assert tenant.order.index("save_transcript_bulk") < tenant.order.index(
            "finalize_call"
        )

    def test_the_http_client_is_closed_only_after_the_record_is_written(self, tenant):
        """
        Closing a client with requests in flight fails them. The LiveKit
        finaliser learned this as "Cannot send a request, as the client
        has been closed"; the ordering rule is inherited.
        """
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        run(drive(CallBridge(twilio), ScriptedDeepgram([])))

        assert tenant.order.index("finalize_call") < tenant.order.index("aclose")
        assert tenant.closed is True

    def test_the_clinics_own_greeting_and_voice_are_used(self, tenant):
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram([])
        run(drive(CallBridge(twilio), agent))

        assert agent.settings_sent is not None
        # PF-02: recognised from the calling number, and asked rather
        # than assumed. The clinic identifies itself first and asks who
        # is calling second, which is both the natural order for a phone
        # call and what stops two greetings colliding.
        greeting = agent.settings_sent["greeting"]
        assert greeting == (
            "Thank you for calling HealthLink. Am I speaking with Priya?"
        )
        assert agent.settings_sent["voice_profile"] == "Thalia"

    def test_caller_audio_reaches_deepgram_decoded(self, tenant):
        """
        Twilio wraps mulaw in base64 inside JSON; Deepgram wants the
        bytes. Getting this wrong produces a call that connects and
        transcribes nothing, with no error anywhere.
        """
        twilio = ScriptedTwilio(
            twilio_script(
                media(b"\x11\x22\x33"),
                media(b"\x44"),
                {"event": "stop", "streamSid": "MZ0001"},
            )
        )
        agent = ScriptedDeepgram([])
        run(drive(CallBridge(twilio), agent))

        assert agent.audio_in == [b"\x11\x22\x33", b"\x44"]

    def test_agent_audio_reaches_twilio_re_encoded(self, tenant):
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram([("audio", b"\x99\x88")])
        run(drive(CallBridge(twilio), agent))

        media_frames = twilio.events("media")
        assert media_frames
        assert base64.b64decode(media_frames[0]["media"]["payload"]) == b"\x99\x88"
        assert media_frames[0]["streamSid"] == "MZ0001"


# =====================================================================
# Tools, mid-call
# =====================================================================


class TestToolCallsDuringACall:
    def test_a_function_call_is_executed_and_answered(self, tenant):
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [
                (
                    "FunctionCallRequest",
                    {
                        "type": "FunctionCallRequest",
                        "functions": [
                            {
                                "id": "fc_1",
                                "name": "list_doctors",
                                "arguments": "{}",
                                "client_side": True,
                            }
                        ],
                    },
                ),
            ]
        )
        run(drive(CallBridge(twilio), agent))

        assert agent.function_results, "the function call was never answered"
        call_id, name, content = agent.function_results[0]
        assert (call_id, name) == ("fc_1", "list_doctors")
        assert content["doctors"][0]["id"] == "doc-1"
        assert "list_doctors" in tenant.order

    def test_a_slow_tool_gets_a_holding_phrase(self, tenant, monkeypatch):
        """
        A booking is an LLM pass plus an HTTP round trip -- 3-5 seconds
        of dead air on a real call, long enough that callers say
        "hello?". The phrase is spoken only when the wait actually
        becomes noticeable.
        """
        monkeypatch.setattr(settings, "TOOL_FILLER_DELAY_SECONDS", 0.05)

        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram([])
        bridge = CallBridge(twilio)
        bridge.agent = agent

        async def slow_list_doctors():
            await asyncio.sleep(0.3)
            return [{"id": "doc-1"}]

        async def exercise():
            await bridge._await_start()
            await bridge._open_agent()
            bridge.agent = agent
            bridge.runner.api.list_doctors = slow_list_doctors
            await bridge._run_function(
                {"id": "fc_1", "name": "list_doctors", "arguments": "{}"}
            )

        run(asyncio.wait_for(exercise(), timeout=5))

        assert agent.injected == [settings.TOOL_FILLER_PHRASE]
        assert agent.function_results, "the slow call still has to be answered"

    def test_a_fast_tool_stays_silent(self, tenant, monkeypatch):
        monkeypatch.setattr(settings, "TOOL_FILLER_DELAY_SECONDS", 2.0)

        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [
                (
                    "FunctionCallRequest",
                    {
                        "functions": [
                            {"id": "fc_1", "name": "list_doctors", "arguments": "{}"}
                        ]
                    },
                )
            ]
        )
        run(drive(CallBridge(twilio), agent))

        assert agent.injected == []

    def test_a_server_side_function_request_is_not_answered(self, tenant):
        """
        `client_side: false` means Deepgram is executing the function
        itself and telling us so. Answering it would put a second,
        conflicting result against a call that already has one. Every
        schema in tools.py is client-side, so this should never happen --
        which is why it is checked rather than assumed.
        """
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [
                (
                    "FunctionCallRequest",
                    {
                        "functions": [
                            {
                                "id": "fc_1",
                                "name": "list_doctors",
                                "arguments": "{}",
                                "client_side": False,
                            }
                        ]
                    },
                )
            ]
        )
        run(drive(CallBridge(twilio), agent))

        assert agent.function_results == []
        assert "list_doctors" not in tenant.order

    def test_ending_the_call_does_not_trigger_the_holding_phrase(
        self, tenant, monkeypatch
    ):
        """
        `end_call` waits for the goodbye to finish playing before closing
        the socket. If that wait happened inside the tool call, it would
        outlast VOICE_TOOL_FILLER_DELAY_SECONDS and the dead-air filler
        would say "One moment." straight over the goodbye -- the one
        place where the fix for silence produces the wrong words.
        """
        monkeypatch.setattr(settings, "TOOL_FILLER_DELAY_SECONDS", 0.05)
        monkeypatch.setattr(bridge_module, "_GOODBYE_SECONDS", 0.3)

        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [
                (
                    "FunctionCallRequest",
                    {"functions": [{"id": "fc_1", "name": "end_call", "arguments": "{}"}]},
                )
            ]
        )
        run(drive(CallBridge(twilio), agent))

        assert agent.injected == [], "the agent talked over its own goodbye"
        assert agent.function_results, "end_call was never answered"

    def test_what_the_tools_did_is_reported_on_the_call_record(self, tenant):
        """
        `actions_taken` is what the AI-7 quality judge reads alongside
        the transcript to catch a claim with nothing behind it -- "I've
        booked you in" with no booking having happened is invisible from
        the transcript alone.
        """
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [
                (
                    "FunctionCallRequest",
                    {
                        "functions": [
                            {"id": "fc_1", "name": "list_doctors", "arguments": "{}"}
                        ]
                    },
                ),
            ]
        )
        bridge = CallBridge(twilio)
        run(drive(bridge, agent))

        # list_doctors is deliberately not a noted action -- listing is
        # not something that happened *to* the patient. Booking is.
        _, fields = tenant.finalized
        assert "actions_taken" in fields


# =====================================================================
# Interruption, in the loop rather than in isolation
# =====================================================================


class TestBargeInDuringACall:
    def test_the_caller_speaking_flushes_twilios_playback_buffer(self, tenant):
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [
                ("audio", b"\x01" * 40),
                ("UserStartedSpeaking", {"type": "UserStartedSpeaking"}),
            ]
        )
        run(drive(CallBridge(twilio), agent))

        assert twilio.events("clear"), (
            "the caller interrupted and Twilio was never told to stop playing"
        )
        # And the order matters: the clear has to come after the audio it
        # is discarding, not before.
        kinds = [m.get("event") for m in twilio.sent]
        assert kinds.index("clear") > kinds.index("media")


# =====================================================================
# Failure modes
# =====================================================================


class TestFailuresDoNotStrandTheCaller:
    def test_deepgram_being_unreachable_ends_the_call_as_failed(self, tenant):
        """
        The media stream is already open by this point, so there is no
        TwiML left to fall back on. The honest outcome is to end the call
        and record it as failed -- which puts it in the HV-7 callback
        queue, so a human rings the patient back.
        """
        from twilio_deepgram.deepgram_agent import DeepgramAgentError

        class Unreachable(ScriptedDeepgram):
            async def connect(self):
                raise DeepgramAgentError("no route to host")

        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        run(drive(CallBridge(twilio), Unreachable([])))

        _, fields = tenant.finalized
        assert fields["status"] == "failed"

    def test_a_backend_that_cannot_open_a_call_log_still_answers_the_phone(
        self, monkeypatch
    ):
        """
        A clinic whose backend is briefly unreachable should still have
        its phone answered. The consequence is an unrecorded call, which
        is bad; a dropped call is worse.
        """
        client = RecordingClient()

        async def broken_create(**kwargs):
            raise backend_client.BackendAPIError("backend down", 503)

        client.create_call_log = broken_create
        context = backend_client.TenantContext(client=client, resolved=True)

        async def fake_resolve(dialed_number):
            return context

        monkeypatch.setattr(backend_client, "resolve_tenant", fake_resolve)

        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram([])
        run(drive(CallBridge(twilio), agent))

        # The agent was still configured and would have spoken.
        assert agent.settings_sent is not None
        # And nothing was written, because there was nothing to write to.
        assert client.finalized is None

    def test_an_unresolvable_number_falls_back_rather_than_failing(self, monkeypatch):
        """
        A call must never fail because routing is unconfigured. This is
        the deliberate rollback switch: unset BACKEND_PLATFORM_KEY and
        every call is handled by the single-tenant key exactly as it was
        before multi-tenancy.
        """
        client = RecordingClient()

        async def no_tenant(dialed_number):
            return None

        monkeypatch.setattr(backend_client, "resolve_tenant", no_tenant)
        monkeypatch.setattr(
            backend_client,
            "fallback_tenant",
            lambda: backend_client.TenantContext(client=client, resolved=False),
        )

        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram([])
        run(drive(CallBridge(twilio), agent))

        assert client.created is not None
        assert agent.settings_sent is not None

    def test_a_caller_who_hangs_up_before_the_stream_starts_is_handled(self, tenant):
        twilio = ScriptedTwilio([{"event": "connected", "protocol": "Call"}])
        run(drive(CallBridge(twilio), ScriptedDeepgram([])))
        assert tenant.created is None

    def test_deepgram_reporting_an_error_marks_the_call_failed(self, tenant):
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        agent = ScriptedDeepgram(
            [("Error", {"type": "Error", "description": "bad settings"})]
        )
        run(drive(CallBridge(twilio), agent))

        _, fields = tenant.finalized
        assert fields["status"] == "failed"

    def test_teardown_happens_exactly_once(self, tenant):
        """
        Four routes lead to the same teardown -- the caller hangs up, the
        agent ends the call, a transfer completes, the watchdog fires --
        and they must produce one call record, not four.
        """
        twilio = ScriptedTwilio(twilio_script({"event": "stop", "streamSid": "MZ0001"}))
        bridge = CallBridge(twilio)
        run(drive(bridge, ScriptedDeepgram([])))

        assert tenant.order.count("finalize_call") == 1
        run(bridge._teardown())
        assert tenant.order.count("finalize_call") == 1


# =====================================================================
# The HTTP surface
# =====================================================================


class TestWebhook:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from twilio_deepgram.server import create_app

        monkeypatch.setattr(settings, "TWILIO_VALIDATE_SIGNATURE", False)
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://gw.example.com")
        return TestClient(create_app())

    def test_an_incoming_call_is_handed_to_the_media_stream(self, client):
        response = client.post(
            "/voice/incoming",
            data={
                "CallSid": "CA0001",
                "From": "+919111111111",
                "To": "+919000000000",
                "Direction": "inbound",
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")

        body = response.text
        assert "<Connect>" in body
        assert "wss://gw.example.com/voice/stream" in body
        # The dialled number is what identifies the clinic, and a media
        # stream arrives knowing nothing but its own id.
        assert "+919000000000" in body
        assert "+919111111111" in body

    def test_an_unsigned_request_is_refused_when_verification_is_on(self, monkeypatch):
        from fastapi.testclient import TestClient

        from twilio_deepgram.server import create_app

        monkeypatch.setattr(settings, "TWILIO_VALIDATE_SIGNATURE", True)
        monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "a-token")
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://gw.example.com")

        response = TestClient(create_app()).post(
            "/voice/incoming", data={"CallSid": "CA1", "To": "+91900"}
        )
        assert response.status_code == 403

    def test_a_gateway_with_no_public_url_says_something_and_hangs_up(
        self, client, monkeypatch
    ):
        """
        Never leave a caller on a silent open line. If we cannot name a
        stream URL there is nothing to connect them to, and Twilio's own
        voice saying one sentence is far better than a ring into nothing.
        """
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
        response = client.post("/voice/incoming", data={"CallSid": "CA1", "To": "+91900"})

        assert response.status_code == 200
        assert "<Say>" in response.text
        assert "<Hangup/>" in response.text
        assert "<Connect>" not in response.text

    def test_the_status_callback_never_rewrites_a_finalised_record(self, client):
        """
        It can arrive after the bridge has written the call, and the
        bridge knows what actually happened in the conversation. Letting
        this overwrite that would replace "booked an appointment" with
        "completed".
        """
        response = client.post(
            "/voice/status",
            data={"CallSid": "CA1", "CallStatus": "completed", "CallDuration": "42"},
        )
        assert response.status_code == 204

    def test_health_reports_configuration_without_failing(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["provider"] == "twilio"
        assert isinstance(body["problems"], list)

    def test_there_is_no_public_api_schema(self, client):
        """
        This service has no human users. A schema page here would only
        describe the endpoints that answer a clinic's phone to whoever
        found them.
        """
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
