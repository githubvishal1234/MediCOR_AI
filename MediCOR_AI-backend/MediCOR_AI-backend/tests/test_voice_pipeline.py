"""
The Deepgram-STT + LLM + Sarvam-TTS pipeline engine.

    pytest tests/test_voice_pipeline.py -v

Four things here fail *silently* on a live call rather than raising, and
they are what this file is for:

  * **mu-law encoding.** One wrong bit and the call is static. Checked
    exhaustively against the standard library, over every representable
    sample value.

  * **Barge-in.** Three things must happen together when a caller
    interrupts -- Twilio's buffer cleared, the reply abandoned, and the
    audio still arriving for it discarded. Doing two of the three gives a
    call that looks interruptible in the logs and is not on the phone.

  * **Answering a turn once.** `speech_final` and `UtteranceEnd` both
    mean "they stopped", and both can arrive for the same sentence.

  * **A tool result reaching the model.** If the loop drops it, the agent
    books an appointment and then tells the caller it could not.

No network, no API keys, no audio device.
"""

import asyncio
import audioop
import json
import os
import struct
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

from twilio_deepgram import settings, tools  # noqa: E402
from twilio_deepgram.pipeline import audio, mocks  # noqa: E402
from twilio_deepgram.pipeline.llm import Reply, ToolCall, _parse_tool_calls, to_openai_tools  # noqa: E402
from twilio_deepgram.pipeline.orchestrator import Orchestrator  # noqa: E402
from twilio_deepgram.pipeline.tts import sentences  # noqa: E402


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =====================================================================
# Audio
# =====================================================================


class TestMulawEncoding:
    def test_mulaw_matches_the_stdlib_over_every_sample(self):
        """
        Exhaustive, because "sounds fine on my desk" is not a test for a
        codec and a one-bit error is inaudible on speech but audible as
        hiss on silence.

        This caught a real bug: the first implementation encoded at full
        16-bit width, which is right for positive samples and one off for
        every negative one. `audioop` is the reference; the pure-Python
        path is what survives Python 3.13, where `audioop` is gone.
        """
        samples = list(range(-32768, 32768))
        pcm = struct.pack(f"<{len(samples)}h", *samples)

        was = audio._HAVE_AUDIOOP
        audio._HAVE_AUDIOOP = False
        try:
            mine = audio.pcm16_to_mulaw(pcm)
        finally:
            audio._HAVE_AUDIOOP = was

        assert mine == audioop.lin2ulaw(pcm, 2)

    def test_silence_is_mulaw_zero_not_byte_zero(self):
        """0x00 is not silence in mu-law; it is close to full scale."""
        assert set(audio.silence(20)) == {0xFF}
        assert len(audio.silence(1000)) == audio.SAMPLE_RATE

    def test_frames_are_twilio_sized(self):
        frames = list(audio.frames(b"\xff" * 400))
        assert [len(f) for f in frames] == [160, 160, 80]

    def test_mulaw_from_sarvam_is_passed_through_untouched(self):
        """
        The configured default. Sarvam emits exactly what Twilio wants,
        so the fast path must do nothing at all to it.
        """
        payload = b"\x01\x02\x03"
        assert audio.to_twilio_mulaw(payload, "mulaw") is payload

    def test_a_wav_header_is_parsed_not_skipped(self):
        """
        A 44-byte header played as audio is a burst of noise at the start
        of every utterance -- quiet enough to be mistaken for a glitch.
        The header is not always 44 bytes, so it is parsed.
        """
        import io
        import wave

        buf = io.BytesIO()
        writer = wave.open(buf, "wb")
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(struct.pack("<4h", 1000, -1000, 2000, -2000))
        writer.close()

        pcm, rate = audio.strip_wav_header(buf.getvalue())
        assert rate == 8000
        assert len(pcm) == 8

    def test_non_wav_data_survives_the_wav_parser(self):
        assert audio.strip_wav_header(b"raw") == (b"raw", None)

    def test_an_undecodable_codec_is_refused_rather_than_played(self):
        """
        mp3 needs a codec library this project does not carry. Emitting
        the compressed bytes as if they were audio is loud static down a
        phone line; refusing names the setting that fixes it.
        """
        with pytest.raises(ValueError, match="SARVAM_TTS_CODEC"):
            audio.to_twilio_mulaw(b"\x00", "mp3")


# =====================================================================
# Sentence splitting
# =====================================================================


class TestSentenceSplitting:
    def test_a_reply_is_split_so_speech_can_start_early(self):
        assert sentences("Hello there. How can I help?") == [
            "Hello there.",
            "How can I help?",
        ]

    def test_a_title_does_not_end_a_sentence(self):
        """
        "Dr. Rao" split mid-name would be synthesised as two utterances
        with a pause in the middle of somebody's name.
        """
        assert sentences("You are booked with Dr. Rao at ten.") == [
            "You are booked with Dr. Rao at ten."
        ]

    def test_empty_text_produces_nothing_to_say(self):
        assert sentences("") == []
        assert sentences("   ") == []


# =====================================================================
# The model layer
# =====================================================================


class TestLLMPlumbing:
    def test_tool_schemas_are_converted_not_duplicated(self):
        """
        One definition of what the agent can do, reshaped for whichever
        API is being spoken to. A second hand-maintained copy would drift,
        and the one that drifted would be whichever is used less.
        """
        converted = to_openai_tools(tools.FUNCTION_SCHEMAS)
        assert len(converted) == len(tools.FUNCTION_SCHEMAS)
        assert {c["function"]["name"] for c in converted} == tools.TOOL_NAMES
        for item in converted:
            assert item["type"] == "function"
            assert item["function"]["parameters"]["type"] == "object"

    def test_tool_arguments_arrive_as_a_json_string(self):
        calls = _parse_tool_calls([
            {"id": "c1", "function": {"name": "find_patient",
                                      "arguments": '{"query": "9876500001"}'}}
        ])
        assert len(calls) == 1
        assert calls[0].arguments == {"query": "9876500001"}

    def test_a_malformed_tool_call_is_skipped_not_raised(self):
        """
        A model emitting broken JSON must not end the call. Skipping
        leaves the turn with no tool call, which the orchestrator handles
        as "it said something instead".
        """
        calls = _parse_tool_calls([
            {"id": "c1", "function": {"name": "book_appointment", "arguments": "{oops"}},
            {"id": "c2", "function": {"name": "list_doctors", "arguments": "{}"}},
        ])
        assert [c.name for c in calls] == ["list_doctors"]

    def test_a_tool_call_with_no_name_is_ignored(self):
        assert _parse_tool_calls([{"id": "c1", "function": {}}]) == []


# =====================================================================
# The orchestrator
# =====================================================================


class FakeSTT:
    """Emits a scripted event list, then stays open like a real socket."""

    def __init__(self, events):
        self._events = list(events)
        self.audio_bytes = 0
        self.closed = asyncio.Event()

    async def connect(self):
        return None

    async def send_audio(self, chunk):
        self.audio_bytes += len(chunk)

    async def finish(self):
        return None

    async def close(self):
        self.closed.set()

    async def events(self):
        for event in self._events:
            await asyncio.sleep(0.01)
            yield event
        await self.closed.wait()


class FakeTTS:
    def __init__(self):
        self.spoken = []
        self.pace = None
        self.language = None
        self._queue = asyncio.Queue()
        self._closed = False

    async def connect(self):
        return None

    async def speak(self, text):
        self.spoken.append(text)
        await self._queue.put(b"\xff" * 160)

    async def flush(self):
        await self._queue.put(b"")

    async def set_pace(self, pace):
        self.pace = pace
        return True

    async def set_language(self, language_code):
        self.language = language_code
        return True

    async def close(self):
        self._closed = True
        await self._queue.put(None)

    async def audio_chunks(self):
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk


class FakeLLM:
    """Returns a queued list of replies, one per turn."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.messages = []
        self.tool_results = []

    async def aclose(self):
        return None

    def add_user(self, text):
        self.messages.append(("user", text))

    def add_assistant(self, reply):
        self.messages.append(("assistant", reply.text))

    def add_tool_result(self, call, content):
        self.tool_results.append((call.name, content))
        self.messages.append(("tool", call.name))

    async def respond(self):
        if not self._replies:
            return Reply("Anything else?")
        return self._replies.pop(0)


def build(stt=None, llm=None, tts=None, api=None):
    """An orchestrator wired to fakes, plus the recorders."""
    sent, cleared, utterances, ended = [], [], [], []

    async def send_audio(chunk):
        sent.append(chunk)

    async def clear_playback():
        cleared.append(True)

    async def on_end_call():
        ended.append(True)

    async def on_transfer(destination):
        return "ok"

    async def on_speak_slower():
        return True

    class API:
        async def list_doctors(self):
            return [{"id": "doc-1", "name": "Dr Rao"}]

        async def check_escalation(self, **kwargs):
            return {"escalate": False, "has_rules": False}

    runner = tools.ToolRunner(
        api=api or API(),
        on_transfer=on_transfer,
        on_end_call=on_end_call,
        on_speak_slower=on_speak_slower,
    )
    orch = Orchestrator(
        stt=stt or FakeSTT([]),
        llm=llm or FakeLLM([]),
        tts=tts or FakeTTS(),
        runner=runner,
        send_audio=send_audio,
        clear_playback=clear_playback,
        on_utterance=lambda role, text: utterances.append((role, text)),
        on_end_call=on_end_call,
    )
    return orch, {"sent": sent, "cleared": cleared, "utterances": utterances, "ended": ended}


class TestTheGreeting:
    def test_the_greeting_is_recorded_exactly_once(self):
        """
        The orchestrator reports the greeting through `on_utterance`, and
        the bridge used to record it a second time -- so the clinic's
        opening line appeared twice in every stored transcript. Caught by
        reading a real call record, not by any unit test, which is why it
        is pinned here.
        """
        async def exercise():
            orch, rec = build()
            await orch.start("Hello, this is the clinic.")
            await orch.stop()
            return rec

        rec = run(exercise())
        greetings = [t for r, t in rec["utterances"] if t == "Hello, this is the clinic."]
        assert len(greetings) == 1

    def test_an_empty_greeting_records_nothing(self):
        async def exercise():
            orch, rec = build()
            await orch.start("")
            await orch.stop()
            return rec

        assert run(exercise())["utterances"] == []


class TestBargeIn:
    def test_interrupting_the_agent_clears_twilios_buffer(self):
        """
        Twilio plays what it has been given at real time. When the caller
        starts talking, seconds of agent speech can still be queued
        there; without a `clear` it keeps playing over them.
        """
        async def exercise():
            orch, rec = build()
            orch.agent_speaking = True
            await orch._on_barge_in()
            return orch, rec

        orch, rec = run(exercise())
        assert rec["cleared"], "Twilio was never told to stop playing"
        assert orch.generation == 1
        assert orch.agent_speaking is False

    def test_speech_at_the_start_of_a_turn_is_not_an_interruption(self):
        """
        `SpeechStarted` also fires when the caller answers normally.
        Bumping the generation then would throw away a reply nobody
        interrupted.
        """
        async def exercise():
            orch, rec = build()
            orch.agent_speaking = False
            await orch._on_barge_in()
            return orch, rec

        orch, rec = run(exercise())
        assert rec["cleared"] == []
        assert orch.generation == 0

    def test_audio_from_an_abandoned_reply_is_discarded(self):
        """
        The third leg of barge-in. Sarvam keeps sending audio for a
        moment after the interruption; playing it would undo the `clear`
        that was just sent.
        """
        async def exercise():
            tts = FakeTTS()
            orch, rec = build(tts=tts)
            pump = asyncio.ensure_future(orch._pump_tts())

            await orch._say("This is the reply.", orch.generation)
            await asyncio.sleep(0.05)
            before = len(rec["sent"])

            orch.generation += 1          # the caller interrupts
            await tts.speak("more audio for the old reply")
            await asyncio.sleep(0.05)

            pump.cancel()
            return before, len(rec["sent"])

        before, after = run(exercise())
        assert before > 0, "nothing was ever sent"
        assert after == before, "audio from an interrupted reply reached the caller"

    def test_a_reply_is_not_spoken_if_it_was_already_interrupted(self):
        async def exercise():
            tts = FakeTTS()
            orch, _ = build(tts=tts)
            stale = orch.generation
            orch.generation += 1
            await orch._say("stale reply", stale)
            return tts

        assert run(exercise()).spoken == []


class TestTurnHandling:
    def test_one_sentence_is_answered_once(self):
        """
        `speech_final` and `UtteranceEnd` can both arrive for the same
        turn. Answering each would have the agent reply twice.
        """
        async def exercise():
            llm = FakeLLM([Reply("Certainly.")])
            orch, rec = build(llm=llm)
            await orch._answer("I want an appointment")
            await asyncio.sleep(0.05)
            await orch._answer("I want an appointment")
            await asyncio.sleep(0.05)
            return rec

        rec = run(exercise())
        user_turns = [u for u in rec["utterances"] if u[0] == "user"]
        assert len(user_turns) == 1

    def test_a_turn_already_being_answered_is_not_answered_again(self):
        async def exercise():
            orch, rec = build(llm=FakeLLM([Reply("one")]))
            orch._turn_in_flight = True
            await orch._answer("hello")
            return rec

        assert run(exercise())["utterances"] == []

    def test_utterance_end_answers_when_speech_final_never_came(self):
        """
        The backstop for a line too noisy for the endpointer. Without it
        a caller in the street can talk and never get a reply.
        """
        async def exercise():
            stt = FakeSTT([
                {"type": "transcript", "text": "hello there",
                 "is_final": True, "speech_final": False},
                {"type": "utterance_end"},
            ])
            orch, rec = build(stt=stt, llm=FakeLLM([Reply("Hello.")]))
            pump = asyncio.ensure_future(orch._pump_stt())
            await asyncio.sleep(0.2)
            pump.cancel()
            await stt.close()
            return rec

        rec = run(exercise())
        assert ("user", "hello there") in rec["utterances"]

    def test_an_interim_transcript_is_never_answered(self):
        async def exercise():
            stt = FakeSTT([
                {"type": "transcript", "text": "I want",
                 "is_final": False, "speech_final": False},
            ])
            orch, rec = build(stt=stt, llm=FakeLLM([Reply("x")]))
            pump = asyncio.ensure_future(orch._pump_stt())
            await asyncio.sleep(0.1)
            pump.cancel()
            await stt.close()
            return rec

        assert run(exercise())["utterances"] == []


class TestToolLoop:
    def test_a_tool_result_is_given_back_to_the_model(self):
        """
        If the loop drops the result, the agent books an appointment and
        then tells the caller it could not.
        """
        async def exercise():
            llm = FakeLLM([
                Reply("Let me check.", [ToolCall("c1", "list_doctors", {})]),
                Reply("Dr Rao is available."),
            ])
            orch, rec = build(llm=llm)
            await orch._run_turn("who is available?", orch.generation)
            return llm, rec

        llm, rec = run(exercise())
        assert [name for name, _ in llm.tool_results] == ["list_doctors"]
        assert "Dr Rao" in json.dumps(llm.tool_results)
        spoken = [text for role, text in rec["utterances"] if role == "assistant"]
        assert "Dr Rao is available." in spoken

    def test_text_alongside_a_tool_call_is_spoken_first(self):
        """
        "Let me check that" during a lookup is the difference between a
        pause and a silence.
        """
        async def exercise():
            llm = FakeLLM([
                Reply("Let me check.", [ToolCall("c1", "list_doctors", {})]),
                Reply("Done."),
            ])
            orch, rec = build(llm=llm)
            await orch._run_turn("check please", orch.generation)
            return rec

        spoken = [t for r, t in run(exercise())["utterances"] if r == "assistant"]
        assert spoken[0] == "Let me check."

    def test_the_tool_loop_is_bounded(self):
        """
        A model that keeps asking for the same lookup would otherwise
        hold a caller on a silent line and bill for it.
        """
        async def exercise():
            llm = FakeLLM([
                Reply("", [ToolCall(f"c{i}", "list_doctors", {})]) for i in range(50)
            ])
            orch, _ = build(llm=llm)
            await orch._run_turn("loop", orch.generation)
            return llm

        llm = run(exercise())
        assert len(llm.tool_results) == settings.LLM_MAX_TOOL_ROUNDS

    def test_a_model_failure_apologises_and_offers_a_person(self):
        """
        The caller must never hear about a model. They hear that the
        agent is struggling, and get handed to someone who is not.
        """
        class Broken(FakeLLM):
            async def respond(self):
                from twilio_deepgram.pipeline.llm import LLMError

                raise LLMError("502 from the provider")

        async def exercise():
            orch, rec = build(llm=Broken([]))
            await orch._run_turn("hello", orch.generation)
            return rec, orch

        rec, orch = run(exercise())
        spoken = " ".join(t for r, t in rec["utterances"] if r == "assistant")
        assert "trouble" in spoken.lower()
        assert "someone" in spoken.lower()
        for leak in ("502", "LLMError", "provider", "model"):
            assert leak not in spoken
        assert orch.runner.transferred is True

    def test_end_call_reaches_the_bridge(self):
        async def exercise():
            llm = FakeLLM([Reply("Goodbye.", [ToolCall("c1", "end_call", {})])])
            orch, rec = build(llm=llm)
            await orch._run_turn("bye", orch.generation)
            return rec

        assert run(exercise())["ended"]


# =====================================================================
# Mock mode
# =====================================================================


class TestMockMode:
    def test_the_mocks_present_the_same_interface_as_the_real_clients(self):
        """
        The mocks stand in for the real clients rather than subclassing
        them, so a method added to a real client with no counterpart here
        fails in tests instead of silently in mock mode.
        """
        from twilio_deepgram.pipeline.llm import ChatLLM
        from twilio_deepgram.pipeline.stt import DeepgramSTT
        from twilio_deepgram.pipeline.tts import SarvamTTS

        def surface(cls):
            return {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}

        assert surface(DeepgramSTT) <= surface(mocks.MockSTT)
        assert surface(SarvamTTS) <= surface(mocks.MockTTS)
        assert surface(ChatLLM) <= surface(mocks.MockLLM)

    def test_the_mock_llm_drives_real_tool_calls(self):
        """
        The point of mock mode: the tools, the backend and the database
        are all real, so a deployment can be proved correct before any
        service is paid for.
        """
        async def exercise():
            llm = mocks.MockLLM("prompt", tools.FUNCTION_SCHEMAS)
            llm.add_user("my number is 9876500001")
            reply = await llm.respond()
            return reply

        reply = run(exercise())
        assert reply.wants_tools
        assert reply.tool_calls[0].name == "find_patient"
        assert reply.tool_calls[0].arguments["query"] == "9876500001"

    def test_the_mock_llm_only_calls_tools_that_exist(self):
        llm = mocks.MockLLM("prompt", tools.FUNCTION_SCHEMAS)
        for phrase in ("book an appointment", "when are you open",
                       "I want a person", "speak slower", "goodbye"):
            llm.messages.append({"role": "user", "content": phrase})
            reply = run(llm.respond())
            for call in reply.tool_calls:
                assert call.name in tools.TOOL_NAMES

    def test_the_mock_tts_produces_playable_silence(self):
        async def exercise():
            tts = mocks.MockTTS()
            await tts.connect()
            await tts.speak("Hello, this is the clinic.")
            await tts.flush()
            chunks = []
            async for chunk in tts.audio_chunks():
                if chunk == b"":
                    break
                chunks.append(chunk)
            return chunks

        chunks = run(exercise())
        assert chunks
        assert all(set(c) <= {0xFF} for c in chunks)
        assert all(len(c) <= audio.FRAME_BYTES for c in chunks)

    def test_mock_mode_is_announced_as_a_problem(self):
        """
        A gateway left in mock mode answers real calls with a script.
        That must never be a surprise, so it is reported at startup and
        on /health like any other misconfiguration.
        """
        was = settings.VOICE_MOCK_MODE
        settings.VOICE_MOCK_MODE = True
        try:
            problems = settings.startup_problems()
        finally:
            settings.VOICE_MOCK_MODE = was
        assert any("MOCK" in p and "FAKE" in p for p in problems)

    def test_mock_mode_does_not_demand_the_keys_it_replaces(self):
        was = (settings.VOICE_MOCK_MODE, settings.DEEPGRAM_API_KEY,
               settings.SARVAM_API_KEY, settings.LLM_API_KEY)
        settings.VOICE_MOCK_MODE = True
        settings.DEEPGRAM_API_KEY = None
        settings.SARVAM_API_KEY = None
        settings.LLM_API_KEY = None
        try:
            problems = " ".join(settings.startup_problems())
        finally:
            (settings.VOICE_MOCK_MODE, settings.DEEPGRAM_API_KEY,
             settings.SARVAM_API_KEY, settings.LLM_API_KEY) = was
        assert "DEEPGRAM_API_KEY is not set" not in problems
        assert "SARVAM_API_KEY is not set" not in problems


# =====================================================================
# Configuration
# =====================================================================


class TestEngineConfiguration:
    def test_the_pipeline_is_the_default_engine(self):
        """
        Because Sarvam is the only available voice that speaks Indian
        English, and Deepgram's managed agent cannot use it.
        """
        assert settings.VOICE_ENGINE == "pipeline"

    def test_the_tts_is_configured_for_telephony(self):
        """
        mu-law at 8 kHz is exactly what Twilio wants, so the audio needs
        no conversion at all. Anything else adds a resample to every
        utterance.
        """
        assert settings.SARVAM_TTS_CODEC == "mulaw"
        assert settings.SARVAM_TTS_SAMPLE_RATE == audio.SAMPLE_RATE

    def test_the_voice_speaks_indian_english(self):
        assert settings.SARVAM_TTS_LANGUAGE == "en-IN"

    def test_endpointing_is_not_left_at_deepgrams_default(self):
        """
        Deepgram defaults `endpointing` to 10 ms, which on a phone call
        cuts people off between words.
        """
        assert settings.DEEPGRAM_STT_ENDPOINTING_MS >= 200
        assert settings.DEEPGRAM_STT_UTTERANCE_END_MS >= 1000

    def test_an_unknown_engine_is_refused(self):
        was = settings.VOICE_ENGINE
        settings.VOICE_ENGINE = "banana"
        try:
            assert any("VOICE_ENGINE" in p for p in settings.startup_problems())
        finally:
            settings.VOICE_ENGINE = was

    def test_bulbul_v3_is_not_sent_v2_only_fields(self):
        """
        v3 takes `temperature` and rejects `pitch`/`loudness`; v2 is the
        other way round. Sending the wrong pair is a rejected config and
        a silent call.
        """
        from twilio_deepgram.pipeline.tts import SarvamTTS

        was = settings.SARVAM_TTS_MODEL
        try:
            settings.SARVAM_TTS_MODEL = "bulbul:v3"
            data = SarvamTTS()._config()["data"]
            assert "pitch" not in data and "loudness" not in data

            settings.SARVAM_TTS_MODEL = "bulbul:v2"
            data = SarvamTTS()._config()["data"]
            assert "pitch" in data and "loudness" in data
            assert "temperature" not in data
        finally:
            settings.SARVAM_TTS_MODEL = was

    def test_the_stt_asks_for_the_events_turn_taking_depends_on(self):
        """
        `vad_events` gives barge-in and `utterance_end_ms` gives the
        noisy-line backstop. Without either the call still transcribes,
        which is what makes their absence easy to miss.
        """
        from twilio_deepgram.pipeline.stt import DeepgramSTT

        url = DeepgramSTT()._url()
        assert "vad_events=true" in url
        assert "utterance_end_ms=" in url
        assert "interim_results=true" in url
        assert "encoding=mulaw" in url
        assert "sample_rate=8000" in url

    def test_no_credential_appears_in_the_stt_url(self):
        """
        The URL is logged. The key travels in a header.
        """
        from twilio_deepgram.pipeline.stt import DeepgramSTT

        was = settings.DEEPGRAM_API_KEY
        settings.DEEPGRAM_API_KEY = "secret-key-value"
        try:
            assert "secret-key-value" not in DeepgramSTT()._url()
        finally:
            settings.DEEPGRAM_API_KEY = was
