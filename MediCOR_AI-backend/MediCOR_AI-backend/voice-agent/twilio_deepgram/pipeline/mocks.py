"""
Stand-in STT, LLM and TTS, so the whole call path runs with no API keys.

Switched on with `VOICE_MOCK_MODE=true`. Every other part of the system
is the real one: the real Twilio websocket handling, the real
orchestrator, the real tool layer, the real backend over HTTP, the real
SQL Server. Only the three paid services are replaced.

WHAT THIS IS FOR

Three things, and the third is the one that justifies the file:

  1. Working on the call flow before any account exists.
  2. Tests that exercise a whole conversation without a network.
  3. **Proving a deployment is wired up correctly before spending
     anything.** Point Twilio at the gateway in mock mode and ring the
     number: if you hear the scripted reply, then the webhook, the
     signature check, the media stream, the tenant resolution, the call
     log, the tool layer, the database and the dashboard are all
     working, and the only untested things left are the three API keys.
     That is a much better place to be than discovering a proxy problem
     while also debugging a Deepgram config.

WHAT IT IS NOT

Not a simulator of speech. The mock STT does not listen to the audio it
is given -- it plays a script on a timer. Anything about recognition
accuracy, turn detection, latency or interruption *feel* has to be
tested against the real services. What is tested here is the wiring.

The mocks deliberately implement the same interfaces as the real
clients rather than subclassing them, so a method added to a real client
without a counterpart here fails loudly in tests instead of silently in
mock mode.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

from .. import settings
from . import audio
from .llm import Reply, ToolCall

logger = logging.getLogger("twilio-deepgram.mock")


# The scripted caller. Each entry is (seconds_after_call_start, sentence).
# Chosen to walk the booking path end to end, because that is the flow
# with the most moving parts: identify, choose, confirm, book, close.
DEFAULT_SCRIPT: list[tuple[float, str]] = [
    (3.0, "Hello, I would like to book an appointment please."),
    (8.0, "My phone number is 9876500001."),
    (13.0, "Any doctor is fine, tomorrow morning if possible."),
    (19.0, "Yes, ten o'clock works for me."),
    (25.0, "That's all, thank you. Goodbye."),
]


def _parse_script(raw: Optional[str]) -> list[tuple[float, str]]:
    """
    Read a custom script from `VOICE_MOCK_SCRIPT`.

    JSON list of `[seconds, "text"]` pairs. Malformed input falls back to
    the default with a warning rather than failing: this is a testing
    aid, and a typo in it should not stop the gateway from starting.
    """
    if not raw:
        return DEFAULT_SCRIPT
    try:
        parsed = json.loads(raw)
        return [(float(at), str(text)) for at, text in parsed]
    except Exception as e:  # noqa: BLE001
        logger.warning("VOICE_MOCK_SCRIPT is not usable (%s); using the default", e)
        return DEFAULT_SCRIPT


class MockSTT:
    """
    Emits a scripted conversation on a timer, ignoring the audio.

    Each line produces the same three events a real turn produces --
    `speech_started`, an interim `transcript`, then a final one with
    `speech_final` -- so the orchestrator's turn handling is genuinely
    exercised rather than bypassed.
    """

    def __init__(self, script: Optional[list[tuple[float, str]]] = None):
        self.script = script or _parse_script(settings.VOICE_MOCK_SCRIPT)
        self.audio_received = 0
        self._closed = False

    async def connect(self) -> None:
        logger.info("MOCK STT: %d scripted utterance(s)", len(self.script))

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_received += len(chunk)

    async def finish(self) -> None:
        return None

    async def close(self) -> None:
        self._closed = True

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        elapsed = 0.0
        for at, text in self.script:
            delay = max(0.0, at - elapsed)
            await asyncio.sleep(delay)
            elapsed = at
            if self._closed:
                return
            yield {"type": "speech_started"}
            await asyncio.sleep(0.05)
            yield {"type": "transcript", "text": text, "is_final": False, "speech_final": False}
            await asyncio.sleep(0.05)
            yield {"type": "transcript", "text": text, "is_final": True, "speech_final": True}

        # Then stay open, as a real socket does for the rest of the call.
        while not self._closed:
            await asyncio.sleep(0.1)


class MockLLM:
    """
    A rule-based receptionist.

    Deliberately keyword-driven and obvious. It exists to move the
    conversation through the tool layer, not to be convincing -- and a
    mock that tried to sound like a model would invite reading its
    output as evidence about the real one.

    It does make real tool calls, which is the point: `find_patient`,
    `list_doctors`, `check_available_slots` and `book_appointment` all
    run against the real backend and the real database in mock mode.
    """

    def __init__(self, system_prompt: str, tool_schemas: list[dict[str, Any]]):
        self.system_prompt = system_prompt
        self.tool_names = {s["name"] for s in tool_schemas}
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._last_tool: Optional[str] = None
        self._counter = 0

    async def aclose(self) -> None:
        return None

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, reply: Reply) -> None:
        self.messages.append({"role": "assistant", "content": reply.text})

    def add_tool_result(self, call: ToolCall, content: str) -> None:
        self.messages.append({"role": "tool", "name": call.name, "content": content})
        self._last_tool = call.name

    def _call(self, name: str, **arguments: Any) -> ToolCall:
        self._counter += 1
        return ToolCall(f"mock_{self._counter}", name, arguments)

    async def respond(self) -> Reply:
        await asyncio.sleep(settings.VOICE_MOCK_LATENCY_SECONDS)

        # Answering a tool result.
        if self.messages and self.messages[-1].get("role") == "tool":
            done, self._last_tool = self._last_tool, None
            if done == "find_patient":
                return Reply("Thank you. Which day suits you?")
            if done == "list_doctors":
                return Reply("I can offer tomorrow morning. Shall I check the times?")
            if done == "check_available_slots":
                return Reply("I have ten o'clock and half past two. Which would you like?")
            if done == "book_appointment":
                return Reply("That is booked for you. Anything else?")
            return Reply("Thank you, I have that. Anything else?")

        last = ""
        for message in reversed(self.messages):
            if message.get("role") == "user":
                last = (message.get("content") or "").lower()
                break

        if any(word in last for word in ("bye", "goodbye", "that's all", "thats all")):
            return Reply("Thank you for calling. Goodbye.", [self._call("end_call")])
        if any(word in last for word in ("person", "human", "someone real")):
            return Reply("Putting you through now.", [self._call("transfer_to_human")])
        if "slower" in last or "slow down" in last:
            return Reply("Of course.", [self._call("speak_slower")])
        if any(ch.isdigit() for ch in last):
            digits = "".join(ch for ch in last if ch.isdigit())
            return Reply("", [self._call("find_patient", query=digits)])
        if "book" in last or "appointment" in last:
            return Reply("Certainly.", [self._call("list_doctors")])
        if "open" in last or "hours" in last or "where" in last:
            return Reply("", [self._call("clinic_info")])

        return Reply("I can help with appointments. What do you need?")


class MockTTS:
    """
    Produces mu-law silence of a plausible length for whatever it is
    given, then the end-of-utterance sentinel.

    Silence rather than a tone on purpose: a test that plays this down a
    real phone line should sound like nothing, not like a fault, and
    silence keeps the audio-length arithmetic obvious -- roughly speech
    rate at `VOICE_MOCK_SPEECH_RATE` characters per second.
    """

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self.language = settings.SARVAM_TTS_LANGUAGE
        self.pace = settings.SARVAM_TTS_PACE

    async def connect(self) -> None:
        logger.info("MOCK TTS: no audio will be synthesised")

    async def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.spoken.append(text)
        milliseconds = int(1000 * len(text) / max(1.0, settings.VOICE_MOCK_SPEECH_RATE))
        # Delivered in Twilio-sized frames, like the real thing, so
        # anything downstream that batches or paces is exercised.
        for frame in audio.frames(audio.silence(milliseconds)):
            await self._queue.put(frame)

    async def flush(self) -> None:
        await self._queue.put(b"")

    async def set_language(self, language_code: str) -> bool:
        self.language = language_code
        return True

    async def set_pace(self, pace: float) -> bool:
        self.pace = pace
        return True

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)

    async def audio_chunks(self) -> AsyncIterator[bytes]:
        while not self._closed:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk
