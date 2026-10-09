"""
The conversation loop: STT in, LLM and tools in the middle, TTS out.

Deepgram's managed Voice Agent does this inside one socket. Using Sarvam
for the voice means it cannot (its speak-provider list has no Sarvam), so
this file is what that decision costs -- and it is the whole of what it
costs. Everything else in the gateway is shared with the managed-agent
path.

    caller audio ──► STT ──► transcript ──► LLM ──► reply text ──► TTS ──► caller
                              │                │
                              │                └──► tool calls ──► FastAPI ──► SQL Server
                              │                                        │
                              └── speech_started ──► BARGE-IN          └──► result back to the LLM

THE THREE HARD PARTS

  * **Barge-in.** Deepgram's `SpeechStarted` fires on voice activity,
    before any transcript exists -- the only signal early enough to stop
    the agent mid-word. Three things must then happen together: Twilio is
    told to drop the audio it has buffered (it plays out in real time and
    would otherwise keep talking over the caller for seconds), the
    in-flight reply is abandoned, and any TTS audio still arriving for it
    is discarded. Doing one or two of the three produces a call that
    *looks* interruptible in the logs and is not on the phone.

  * **A turn must not be answered twice.** `speech_final` and
    `UtteranceEnd` both mean "they have stopped", and both can arrive for
    the same turn. Answering each would have the agent reply twice to one
    sentence.

  * **Tools must not stall the audio.** A booking is an HTTP round trip.
    Awaiting it inside the receive loop stops everything, so a turn runs
    as its own task and the pumps keep going.

`generation` is what makes the first two work: every reply is stamped
with the generation it belongs to, and anything from an older one is
dropped on the floor. It increments on every interruption. That is
cheaper and far more reliable than trying to cancel work that has already
been handed to another service.
"""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

from .. import settings, tools
from . import audio
from .llm import LLMError, Reply, ToolCall

logger = logging.getLogger("twilio-deepgram.orchestrator")

# Spoken when the model itself fails. Deliberately not an error message:
# the caller is told the agent is struggling and offered a person, which
# is true and actionable, rather than being told about a model.
_FALLBACK_REPLY = (
    "Sorry, I'm having trouble just now. Let me put you through to someone."
)


class Orchestrator:
    """One conversation. Constructed per call, never reused."""

    def __init__(
        self,
        stt: Any,
        llm: Any,
        tts: Any,
        runner: tools.ToolRunner,
        send_audio: Callable[[bytes], Any],
        clear_playback: Callable[[], Any],
        on_utterance: Optional[Callable[[str, str], None]] = None,
        on_end_call: Optional[Callable[[], Any]] = None,
    ):
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.runner = runner
        self._send_audio = send_audio
        self._clear_playback = clear_playback
        self._on_utterance = on_utterance or (lambda role, text: None)
        self._on_end_call = on_end_call

        # Bumped on every barge-in. Work stamped with an older value is
        # discarded rather than cancelled -- see the module docstring.
        self.generation = 0
        # True between handing text to the TTS and its audio running out.
        self.agent_speaking = False
        # Guards against answering one turn twice.
        self._turn_in_flight = False
        self._last_answered = ""

        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._interim = ""

    # -- lifecycle -----------------------------------------------------

    def spawn(self, coro: Any, name: str) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start(self, greeting: str) -> None:
        """Open the services and say the first thing."""
        await self.stt.connect()
        await self.tts.connect()
        self.spawn(self._pump_stt(), "stt-reader")
        self.spawn(self._pump_tts(), "tts-reader")
        if greeting:
            await self._say(greeting, self.generation)
            self._on_utterance("assistant", greeting)

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self.stt.close()
        await self.tts.close()
        await self.llm.aclose()

    async def send_caller_audio(self, chunk: bytes) -> None:
        await self.stt.send_audio(chunk)

    # -- inbound speech ------------------------------------------------

    async def _pump_stt(self) -> None:
        async for event in self.stt.events():
            if self._stop.is_set():
                return
            kind = event.get("type")

            if kind == "speech_started":
                await self._on_barge_in()
            elif kind == "transcript":
                await self._on_transcript(event)
            elif kind == "utterance_end":
                # The backstop for a line too noisy for the endpointer to
                # ever call `speech_final`. Without it, a caller on a busy
                # street can talk and never get an answer.
                if self._interim:
                    await self._answer(self._interim)

    async def _on_barge_in(self) -> None:
        """
        The caller has started speaking.

        Only an interruption if the agent was talking. `SpeechStarted`
        also fires at the natural start of every turn, and clearing
        Twilio's buffer then would be harmless but pointless -- while
        bumping the generation would throw away a reply nobody
        interrupted.
        """
        if not self.agent_speaking:
            return
        self.generation += 1
        self.agent_speaking = False
        await self._clear_playback()
        logger.info("Caller interrupted; abandoning reply (generation %d)", self.generation)

    async def _on_transcript(self, event: dict[str, Any]) -> None:
        text = (event.get("text") or "").strip()
        if not text:
            return
        if not event.get("is_final"):
            self._interim = text
            return

        self._interim = text
        if event.get("speech_final"):
            await self._answer(text)

    async def _answer(self, text: str) -> None:
        """
        Reply to one completed caller turn.

        Guarded twice over: `_turn_in_flight` stops `speech_final` and
        `UtteranceEnd` both answering the same sentence, and
        `_last_answered` stops a repeated final transcript doing it.
        """
        text = text.strip()
        if not text or self._turn_in_flight or text == self._last_answered:
            return
        self._interim = ""
        self._last_answered = text
        self._turn_in_flight = True
        self._on_utterance("user", text)
        self.spawn(self._run_turn(text, self.generation), "turn")

    # -- the turn ------------------------------------------------------

    async def _run_turn(self, text: str, generation: int) -> None:
        started = time.monotonic()
        try:
            self.llm.add_user(text)
            await self._converse(generation)
        except Exception:  # noqa: BLE001 - a live call outlives any one turn
            logger.exception("Turn failed")
            if generation == self.generation:
                await self._say(_FALLBACK_REPLY, generation)
                self._on_utterance("assistant", _FALLBACK_REPLY)
                await self._transfer_after_failure()
        finally:
            self._turn_in_flight = False
            logger.info("Turn handled in %.2fs", time.monotonic() - started)

    async def _converse(self, generation: int) -> None:
        """
        Model, tools, model again -- until it has something to say.

        Bounded by `LLM_MAX_TOOL_ROUNDS`. Unbounded, a model that keeps
        asking for the same lookup would hold a caller on a silent line
        indefinitely and bill for the privilege.
        """
        for round_number in range(settings.LLM_MAX_TOOL_ROUNDS):
            if generation != self.generation:
                return  # interrupted; this reply is no longer wanted

            try:
                reply = await self.llm.respond()
            except LLMError as e:
                logger.warning("Model failed: %s", e)
                await self._say(_FALLBACK_REPLY, generation)
                self._on_utterance("assistant", _FALLBACK_REPLY)
                await self._transfer_after_failure()
                return

            self.llm.add_assistant(reply)

            # Anything it says alongside a tool call is spoken first:
            # "let me check that" during a lookup is the difference
            # between a pause and a silence.
            if reply.text:
                await self._say(reply.text, generation)
                self._on_utterance("assistant", reply.text)

            if not reply.wants_tools:
                return

            for call in reply.tool_calls:
                if generation != self.generation:
                    return
                await self._run_tool(call, generation)

        logger.warning("Tool rounds exhausted; ending the turn")

    async def _run_tool(self, call: ToolCall, generation: int) -> None:
        """
        Execute one tool and give the model its result.

        `ToolRunner.execute` never raises -- every failure comes back as
        a result the model can speak around, which is what stops a dead
        backend becoming a dead phone line.
        """
        logger.info("Tool call: %s(%s)", call.name, ", ".join(sorted(call.arguments)))

        filler = None
        if settings.TOOL_FILLER_DELAY_SECONDS and settings.TOOL_FILLER_PHRASE:
            filler = self.spawn(self._filler(generation), "filler")

        content = await self.runner.execute(call.name, call.arguments)

        if filler is not None:
            filler.cancel()
        self.llm.add_tool_result(call, content)

        # `end_call` and `transfer_to_human` act on the call itself, so
        # the tool layer has already done the work; what is left is to
        # stop talking.
        if call.name == "end_call" and self._on_end_call is not None:
            await self._on_end_call()

    async def _filler(self, generation: int) -> None:
        """
        Say "one moment" only if the wait becomes noticeable.

        The LiveKit stack measured 3-5 seconds of dead air on a booking,
        which is long enough that callers say "hello?". Cancelled the
        instant the tool returns, so a fast lookup stays silent.
        """
        await asyncio.sleep(settings.TOOL_FILLER_DELAY_SECONDS)
        if generation == self.generation and not self.agent_speaking:
            await self._say(settings.TOOL_FILLER_PHRASE, generation)

    # -- outbound speech -----------------------------------------------

    async def _say(self, text: str, generation: int) -> None:
        """
        Send text to be spoken, sentence by sentence.

        Split so synthesis of the first clause starts before the model
        has finished the rest, which is worth about a second on a typical
        reply.
        """
        if generation != self.generation:
            return
        self.agent_speaking = True
        self._speaking_generation = generation
        for sentence in _split(text):
            if generation != self.generation:
                return
            await self.tts.speak(sentence)
        await self.tts.flush()

    async def _pump_tts(self) -> None:
        """
        Synthesised audio out to Twilio.

        The generation check here is the third leg of barge-in: audio for
        an abandoned reply is still arriving from Sarvam for a moment
        after the caller interrupts, and playing it would undo the
        `clear` that was just sent.
        """
        async for chunk in self.tts.audio_chunks():
            if self._stop.is_set():
                return
            if getattr(self, "_speaking_generation", 0) != self.generation:
                continue  # belongs to an interrupted reply
            if chunk == b"":
                self.agent_speaking = False
                continue
            await self._send_audio(chunk)

    async def _transfer_after_failure(self) -> None:
        """
        Hand a caller to a person after the agent has failed them.

        Best-effort and never fatal: if the transfer itself fails there
        is nothing further to try, and the apology has at least been
        spoken.
        """
        try:
            await self.runner.execute("transfer_to_human", {})
        except Exception:  # noqa: BLE001
            logger.exception("Could not transfer after a failure")


def _split(text: str) -> list[str]:
    """Sentence split, imported lazily so mock mode needs no TTS module."""
    from .tts import sentences

    return sentences(text) or ([text] if text.strip() else [])
