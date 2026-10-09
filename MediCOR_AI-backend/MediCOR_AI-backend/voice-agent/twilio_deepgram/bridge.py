"""
The audio bridge: one Twilio media stream joined to one Deepgram Voice
Agent socket, for the length of one call.

    Twilio  --base64 mulaw JSON-->  bridge  --raw mulaw bytes-->  Deepgram
    Twilio  <--base64 mulaw JSON--  bridge  <--raw mulaw bytes--  Deepgram

Two protocols, the same audio. Twilio wraps 8 kHz mulaw in base64 inside
JSON; Deepgram wants the bytes. That translation is most of this file's
line count and none of its difficulty.

The difficulty is in three places, and each is commented where it
happens:

  * **Barge-in.** Twilio buffers everything we send it and plays it out
    at real-time speed, so when a caller interrupts, seconds of already-
    sent agent speech are still queued in Twilio's buffer and will keep
    playing over them. Deepgram tells us the caller has started speaking;
    we have to tell Twilio to throw that buffer away. Without the
    `clear`, interruption *appears* not to work even though everything
    upstream did its job.

  * **Tool calls must not stall the audio.** A booking is an HTTP round
    trip. If it is awaited inside the receive loop, agent audio stops
    flowing to Twilio for its duration and the caller hears the line die.
    Every function call runs as its own task.

  * **The call has to end exactly once.** A caller hangs up, the agent
    calls `end_call`, a transfer completes, the watchdog fires -- four
    routes to the same teardown, which must finalise one call record, not
    four, and must not leave the other tasks running.
"""

import asyncio
import base64
import json
import logging
import time
from typing import Any, Optional

from . import settings, tools, twilio_api
from .call_session import CallSession
from .deepgram_agent import DeepgramAgent, DeepgramAgentError
from .prompt import build_instructions

logger = logging.getLogger("twilio-deepgram.bridge")

# Teardown drains Deepgram's last events before giving up on them. See
# `_teardown` for why cutting this to zero silently truncates
# transcripts.
#
# Two numbers rather than one, because a fixed wait is wrong in both
# directions: long enough to catch a slow straggler is long enough to
# hold every finished call's websocket handler open for no reason, and
# short enough to be free misses the frames it exists to catch. So the
# drain ends as soon as the line has been *quiet* for `_QUIET_SECONDS`,
# with `_DRAIN_SECONDS` as the hard ceiling. The common case -- a couple
# of frames already sitting in the receive queue -- costs a quarter of a
# second.
#
# Neither is an env setting: they are properties of how the two
# protocols race, not deployment preferences.
_QUIET_SECONDS = 0.25
_DRAIN_SECONDS = 1.5

# How long the agent's goodbye is given to finish playing out of Twilio's
# buffer before the socket is closed. See `_hang_up_after_goodbye`.
_GOODBYE_SECONDS = 2.5


class CallBridge:
    """
    One call. Constructed per websocket connection, never reused.
    """

    def __init__(self, twilio_ws: Any):
        self.twilio_ws = twilio_ws
        self.stream_sid: Optional[str] = None
        self.session: Optional[CallSession] = None
        # Exactly one of these carries the conversation, chosen by
        # VOICE_ENGINE. `agent` is Deepgram's managed socket;
        # `orchestrator` is the Deepgram-STT + LLM + Sarvam-TTS pipeline.
        self.agent = DeepgramAgent()
        self.orchestrator: Optional[Any] = None
        self.runner: Optional[tools.ToolRunner] = None

        self._stop = asyncio.Event()
        self._finalized = False
        self._tasks: set[asyncio.Task] = set()
        self._started_monotonic = time.monotonic()
        # When the agent last sent us anything. Teardown uses it to tell
        # "still arriving" from "finished", instead of waiting a fixed
        # period on every call. See `_drain`.
        self._last_event_at = time.monotonic()

    # -- Twilio writes -------------------------------------------------

    async def _send_twilio(self, payload: dict[str, Any]) -> None:
        try:
            await self.twilio_ws.send_text(json.dumps(payload))
        except Exception as e:  # noqa: BLE001 - the caller has usually just hung up
            logger.debug("Twilio send failed: %s", e)
            self._stop.set()

    async def _send_audio_to_twilio(self, chunk: bytes) -> None:
        if not self.stream_sid:
            return
        await self._send_twilio(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": base64.b64encode(chunk).decode("ascii")},
            }
        )

    async def _send_caller_audio(self, chunk: bytes) -> None:
        """Caller audio to whichever engine is listening."""
        if self.orchestrator is not None:
            await self.orchestrator.send_caller_audio(chunk)
        else:
            await self.agent.send_audio(chunk)

    async def _clear_twilio_playback(self) -> None:
        """
        Discard whatever agent audio Twilio still has queued.

        This is the whole of barge-in on this side. Deepgram stops
        generating the moment it hears the caller; Twilio does not know
        that and will happily finish playing the sentence it was already
        handed. Sending `clear` is what makes the agent actually stop
        mid-word, which is the behaviour a caller experiences as being
        listened to.
        """
        if not self.stream_sid:
            return
        await self._send_twilio({"event": "clear", "streamSid": self.stream_sid})

    # -- lifecycle -----------------------------------------------------

    async def run(self) -> None:
        """
        Drive one call from Twilio's `start` event to teardown.
        """
        try:
            if not await self._await_start():
                return
            if not await self._open_agent():
                return

            self._spawn(self._pump_twilio_to_deepgram(), "twilio-reader")
            if settings.VOICE_ENGINE != "pipeline":
                # The pipeline engine has its own reader loops, started
                # by the orchestrator; the managed agent's single socket
                # is drained here.
                self._spawn(self._pump_deepgram_to_twilio(), "deepgram-reader")
            self._spawn(self._watchdog(), "watchdog")

            await self._stop.wait()
        except Exception:  # noqa: BLE001
            logger.exception("Call bridge failed")
            if self.session is not None:
                self.session.status = "failed"
        finally:
            await self._teardown()

    def _spawn(self, coro: Any, name: str) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _await_start(self) -> bool:
        """
        Read Twilio frames until the `start` event, which is the one that
        carries the call's identity.

        Twilio sends `connected` first and may send media before we are
        ready; anything arriving before `start` is discarded, because
        there is nowhere to send it yet and it is the caller's first
        fraction of a second of line noise.
        """
        while True:
            try:
                raw = await self.twilio_ws.receive_text()
            except Exception:  # noqa: BLE001 - caller hung up during setup
                logger.info("Twilio stream closed before it started")
                return False

            try:
                message = json.loads(raw)
            except ValueError:
                continue

            event = message.get("event")
            if event == "start":
                start = message.get("start") or {}
                params = start.get("customParameters") or {}
                self.stream_sid = message.get("streamSid") or start.get("streamSid")
                self.session = CallSession(
                    call_sid=start.get("callSid") or "",
                    # Passed through the TwiML as <Parameter> children,
                    # because a media stream otherwise arrives knowing
                    # only its own id -- not who was rung, and therefore
                    # not which clinic this call belongs to.
                    dialed_number=params.get("dialed_number"),
                    caller_phone=params.get("caller_phone"),
                    direction=params.get("direction") or "inbound",
                )
                logger.info(
                    "Call %s started (stream=%s, to=%s)",
                    self.session.call_sid,
                    self.stream_sid,
                    self.session.dialed_number,
                )
                return True
            if event == "stop":
                return False

    async def _open_agent(self) -> bool:
        """
        Resolve the clinic, open the call record, and configure the
        agent.

        Order matters for the greeting: the clinic's greeting text, its
        chosen voice and its escalation rules all come from tenant
        resolution, and all three have to be in hand before the Settings
        message goes out, because Deepgram starts speaking the greeting
        as soon as it has them.
        """
        assert self.session is not None
        session = self.session

        tenant = await session.resolve_tenant()
        await session.recognise_caller()
        await session.create_call_log()

        # A clinic's prose rules travel with the tenant. On the
        # single-tenant fallback path there is no resolution call, so
        # fetch them with the credential we do have -- otherwise this
        # feature is inert in exactly the mode most deployments run in.
        rules = list(tenant.escalation_rules or [])
        if not rules:
            try:
                rules = await tenant.client.get_escalation_rules()
            except Exception as e:  # noqa: BLE001 - rules are advisory, calls are not
                logger.warning("Could not load escalation rules: %s", e)
                rules = []

        self.runner = tools.ToolRunner(
            api=tenant.client,
            on_transfer=self._perform_transfer,
            on_end_call=self._end_call,
            on_speak_slower=self._speak_slower,
        )
        self.runner.call_id = session.call_id
        # A clinic with no server-evaluated rules must not pay a round
        # trip on every caller turn for an answer that is always "no".
        # The fallback path cannot be told up front, so it starts
        # enabled and the backend's first reply switches it off.
        self.runner.escalation_enabled = (
            tenant.escalation_deterministic if tenant.resolved else True
        )

        prompt = build_instructions(
            recognised_name=session.recognised_name,
            escalation_rules=rules,
            tts_provider=self._prompt_voice_provider(),
        )
        greeting = session.greeting(tenant.greeting_script)

        if settings.VOICE_ENGINE == "pipeline":
            return await self._open_pipeline(prompt, greeting)
        return await self._open_managed_agent(prompt, greeting, tenant.voice_profile)

    def _prompt_voice_provider(self) -> str:
        """
        Which voice the prompt should describe itself as having.

        The prompt's language claim is derived from the speaking provider
        rather than written beside it, so the two cannot disagree. On the
        pipeline engine that provider is Sarvam, which speaks Indian
        English (and Hindi and Telugu when they are switched on).
        """
        if settings.VOICE_ENGINE == "pipeline":
            return "sarvam"
        return settings.DEEPGRAM_TTS_PROVIDER

    async def _open_pipeline(self, prompt: str, greeting: str) -> bool:
        """
        Deepgram STT -> LLM -> Sarvam TTS, orchestrated here.

        The engine that lets the agent speak with an Indian-English
        voice, which Deepgram's managed agent cannot do at all. See
        pipeline/__init__.py.
        """
        from .pipeline.orchestrator import Orchestrator

        if settings.VOICE_MOCK_MODE:
            from .pipeline.mocks import MockLLM, MockSTT, MockTTS

            stt, llm, tts = MockSTT(), MockLLM(prompt, tools.FUNCTION_SCHEMAS), MockTTS()
            logger.warning("MOCK MODE: speech, model and voice are all faked")
        else:
            from .pipeline.llm import ChatLLM
            from .pipeline.stt import DeepgramSTT
            from .pipeline.tts import SarvamTTS

            stt, llm, tts = (
                DeepgramSTT(),
                ChatLLM(prompt, tools.FUNCTION_SCHEMAS),
                SarvamTTS(),
            )

        self.orchestrator = Orchestrator(
            stt=stt,
            llm=llm,
            tts=tts,
            runner=self.runner,
            send_audio=self._send_audio_to_twilio,
            clear_playback=self._clear_twilio_playback,
            on_utterance=self._record_utterance,
            on_end_call=self._end_call,
        )

        try:
            await self.orchestrator.start(greeting)
        except Exception as e:  # noqa: BLE001 - STT/TTS both raise their own types
            # The media stream is already open, so there is no TwiML left
            # to fall back on. Record the failure and end the call rather
            # than hold a silent line open.
            logger.error("Could not start the voice pipeline: %s", e)
            if self.session is not None:
                self.session.status = "failed"
            self._stop.set()
            return False

        # The greeting is NOT recorded here: `Orchestrator.start` already
        # reports it through `on_utterance`, and recording it again put
        # the clinic's opening line in every transcript twice.
        return True

    async def _open_managed_agent(
        self, prompt: str, greeting: str, voice_profile: Optional[str]
    ) -> bool:
        """Deepgram's Voice Agent: STT, turn-taking, LLM and TTS in one socket."""
        try:
            await self.agent.connect()
        except DeepgramAgentError as e:
            logger.error("Could not reach Deepgram: %s", e)
            if self.session is not None:
                self.session.status = "failed"
            self._stop.set()
            return False

        await self.agent.send_settings(
            prompt=prompt,
            functions=tools.FUNCTION_SCHEMAS,
            greeting=greeting,
            voice_profile=voice_profile,
        )
        return True

    def _record_utterance(self, role: str, text: str) -> None:
        """Both engines feed the transcript through here."""
        if self.session is not None:
            self.session.record_utterance(role, text)
        # The clinic's own deterministic escalation rules, evaluated on
        # the server. Fired as a task so a rules lookup never delays the
        # audio pipeline; failures are contained inside maybe_escalate.
        if role == "user" and self.runner is not None:
            self._spawn(self.runner.maybe_escalate(text), "escalation")

    # -- pumps ---------------------------------------------------------

    async def _pump_twilio_to_deepgram(self) -> None:
        """Caller audio, and the events that end a call."""
        while not self._stop.is_set():
            try:
                raw = await self.twilio_ws.receive_text()
            except Exception:  # noqa: BLE001 - normal at hang-up
                break

            try:
                message = json.loads(raw)
            except ValueError:
                continue

            event = message.get("event")
            if event == "media":
                payload = (message.get("media") or {}).get("payload")
                if payload:
                    await self._send_caller_audio(base64.b64decode(payload))
            elif event == "stop":
                logger.info("Twilio reported the call ended")
                break
            elif event == "mark":
                # Sent back to us after our audio has finished playing.
                # Nothing depends on it today; logged at debug because it
                # is the only signal that distinguishes "we sent it" from
                # "the caller heard it".
                logger.debug("Twilio mark: %s", (message.get("mark") or {}).get("name"))

        self._stop.set()

    async def _pump_deepgram_to_twilio(self) -> None:
        """
        Agent audio and every control event the agent raises.

        Deliberately does NOT stop at `_stop`. Twilio's `stop` event and
        Deepgram's last few frames race: when a caller hangs up
        mid-sentence, the `ConversationText` for what they just said is
        usually still arriving. Breaking out here on `_stop` drops it,
        which loses the end of the transcript on a large share of calls
        -- and the backend then summarises a conversation missing its
        last turn. `_teardown` gives this loop a bounded window to drain
        instead.
        """
        async for kind, payload in self.agent.events():
            self._last_event_at = time.monotonic()
            if kind == "audio":
                # Pointless once the caller is gone, and the socket is
                # probably closed. Events still matter; audio does not.
                if not self._stop.is_set():
                    await self._send_audio_to_twilio(payload)
                continue
            await self._handle_agent_event(kind, payload)

        # Deepgram closed. If the caller is still on the line they are
        # now talking to nobody, so end the call rather than leave it up.
        self._stop.set()

    async def _handle_agent_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "UserStartedSpeaking":
            # Barge-in. See _clear_twilio_playback for why this is not
            # optional.
            await self._clear_twilio_playback()
            return

        if kind == "ConversationText":
            # Shared with the pipeline engine, so the transcript and the
            # escalation check behave identically whichever is running.
            self._record_utterance(payload.get("role") or "", payload.get("content") or "")
            return

        if kind == "FunctionCallRequest":
            for function in payload.get("functions") or []:
                # `client_side: false` means Deepgram is executing the
                # function itself and is telling us so. Answering one
                # would be a second, conflicting result for a call that
                # already has one. Every schema in tools.py is
                # client-side (none carries an `endpoint`), so this
                # should never fire -- which is exactly why it is checked
                # rather than assumed.
                if function.get("client_side") is False:
                    logger.debug(
                        "Deepgram is handling %s itself", function.get("name")
                    )
                    continue
                self._spawn(self._run_function(function), "tool")
            return

        if kind in ("Error", "Warning"):
            # Deepgram's own diagnostics. Logged in full because this is
            # the only place a misconfigured Settings message surfaces,
            # and it surfaces as a call that connects and says nothing.
            logger.warning("Deepgram %s: %s", kind, payload)
            if kind == "Error" and self.session is not None:
                self.session.status = "failed"
            return

        if kind == "Welcome":
            logger.info("Deepgram welcome: request_id=%s", payload.get("request_id"))
        elif kind == "SettingsApplied":
            logger.info("Deepgram settings applied")
        elif kind == "InjectionRefused":
            # We tried to speak a holding phrase while the agent was
            # already talking. Correct refusal; nothing to do.
            logger.debug("Deepgram refused an injection: %s", payload)

    # -- tool execution ------------------------------------------------

    async def _run_function(self, function: dict[str, Any]) -> None:
        """
        Execute one client-side function and answer it.

        Runs as its own task so audio keeps flowing while the backend
        works. The holding phrase is spoken only if the wait actually
        becomes noticeable -- a fast lookup stays silent, and a phrase
        spoken over a reply that has already started is refused by
        Deepgram rather than talked over the agent.
        """
        if self.runner is None:
            return

        call_id = function.get("id") or ""
        name = function.get("name") or ""
        raw_args = function.get("arguments")

        # Deepgram sends arguments as a JSON *string*. A model that emits
        # malformed JSON must produce a tool error the model can recover
        # from, not an exception that leaves the caller in silence.
        arguments: dict[str, Any] = {}
        if isinstance(raw_args, dict):
            arguments = raw_args
        elif isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
                arguments = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                logger.warning("Tool %s got unparseable arguments: %r", name, raw_args)
                await self.agent.send_function_result(
                    call_id,
                    name,
                    json.dumps({"ok": False, "error": "Those arguments were malformed."}),
                )
                return

        logger.info("Tool call: %s(%s)", name, ", ".join(sorted(arguments)))

        async def _filler() -> None:
            if settings.TOOL_FILLER_DELAY_SECONDS and settings.TOOL_FILLER_PHRASE:
                await self.agent.inject_agent_message(settings.TOOL_FILLER_PHRASE)

        content = await tools.gather_with_timeout(
            self.runner.execute(name, arguments),
            timeout=settings.TOOL_FILLER_DELAY_SECONDS or 3600,
            on_timeout=_filler,
        )
        await self.agent.send_function_result(call_id, name, content)

    # -- call control callbacks (handed to ToolRunner) -----------------

    async def _perform_transfer(self, destination: Optional[str]) -> str:
        """
        Put the caller through to a person.

        Returns "ok", or a short reason the model can act on. Note what
        happens afterwards: rewriting the live call's TwiML ends the
        blocking `<Connect>`, so Twilio closes this websocket and the
        call finalises through the ordinary path. There is no separate
        "transferred" teardown to keep in step.
        """
        import config as agent_config  # local: only the transfer path needs it

        target = (destination or agent_config.DEFAULT_TRANSFER_NUMBER or "").strip()
        if not target:
            return "No transfer number is configured for this clinic."
        if self.session is None or not self.session.call_sid:
            return "This call cannot be transferred."

        # Never transfer a caller to the number they just rang.
        #
        # That number reaches this agent, so the "transfer" would put
        # them back through to the AI -- and the AI would offer to
        # transfer them again. An unbounded loop, billed by the minute on
        # both legs, with the caller never reaching a person and the
        # watchdog as the only thing that ends it.
        #
        # It is a likely misconfiguration rather than an exotic one: a
        # clinic's "main number" is exactly what someone would paste into
        # a transfer field, and on this stack the main number is the AI.
        if twilio_api.same_number(target, self.session.dialed_number):
            logger.error(
                "Refusing to transfer call %s: the transfer number is the same "
                "line the caller dialled, which would loop them back into the "
                "agent. Set DEFAULT_TRANSFER_NUMBER (or the rule's destination) "
                "to a staff line that is NOT the clinic's published AI number.",
                self.session.call_sid,
            )
            return (
                "The transfer number is misconfigured, so I cannot put them "
                "through. Apologise and give them the clinic's number to call "
                "back on."
            )

        ok = await twilio_api.redirect_to_dial(
            self.session.call_sid,
            target,
            caller_id=settings.TWILIO_PHONE_NUMBER,
        )
        if not ok:
            return "The transfer did not go through."
        logger.info("Transferred call %s to %s", self.session.call_sid, target)
        return "ok"

    async def _end_call(self) -> None:
        """
        Hang up, once the agent has finished saying goodbye.

        **Returns immediately and schedules the hang-up**, rather than
        sleeping here. Sleeping would hold the tool call open past
        `VOICE_TOOL_FILLER_DELAY_SECONDS`, and the filler would then
        speak "One moment." on top of the goodbye -- the one place where
        the dead-air fix would itself produce the wrong words.
        """
        self._spawn(self._hang_up_after_goodbye(), "hangup")

    async def _hang_up_after_goodbye(self) -> None:
        """
        Wait out the goodbye, then end the call.

        Closing the socket cuts playback instantly and Twilio still has
        audio buffered, so ending the moment the tool returns clips the
        last words: the difference between "thank you, goodbye" and
        "thank you, go-".
        """
        await asyncio.sleep(_GOODBYE_SECONDS)
        self._stop.set()

    async def _speak_slower(self) -> bool:
        """
        Drop the speaking pace for the rest of the call (PF-16).

        Each engine changes pace its own way -- Sarvam takes a new config
        frame, the managed agent an UpdateSpeak -- and neither is allowed
        to break a call if the provider will not do it.
        """
        if self.orchestrator is not None:
            try:
                return await self.orchestrator.tts.set_pace(settings.SARVAM_SLOW_PACE)
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not slow the speaking pace: %s", e)
                return False
        return await self.agent.slow_down()

    # -- watchdog and teardown -----------------------------------------

    async def _watchdog(self) -> None:
        """
        A hard ceiling on one call.

        A voice agent talking to a silent open line bills by the minute
        until something stops it, and "something" should not be a monthly
        invoice.
        """
        while not self._stop.is_set():
            await asyncio.sleep(5)
            if time.monotonic() - self._started_monotonic > settings.MAX_CALL_SECONDS:
                logger.warning(
                    "Call %s exceeded %ss; ending it",
                    getattr(self.session, "call_sid", "?"),
                    settings.MAX_CALL_SECONDS,
                )
                self._stop.set()
                return

    async def _drain(self, tasks: set) -> None:
        """
        Wait for the agent's last events, then stop waiting.

        Finishes as soon as either every remaining task is done or the
        line has been quiet for `_QUIET_SECONDS` -- so the ordinary case
        (a couple of frames already in the receive queue) costs a
        fraction of a second, while a tool call still mid-flight gets up
        to `_DRAIN_SECONDS` to come back and be recorded.
        """
        if not tasks:
            return
        deadline = time.monotonic() + _DRAIN_SECONDS
        while time.monotonic() < deadline:
            if all(task.done() for task in tasks):
                return
            if time.monotonic() - self._last_event_at > _QUIET_SECONDS:
                return
            await asyncio.sleep(0.05)

    async def _teardown(self) -> None:
        """
        End everything exactly once.

        The ordering is the whole of this method:

          1. Stop reading Twilio and stop the watchdog. The caller is
             gone; nothing they could send matters now.
          2. **Drain, briefly.** Deepgram's last events are usually still
             arriving when the caller hangs up -- the transcript line for
             the sentence they hung up on, and any tool call already in
             flight. Cancelling here instead loses the end of the
             conversation on a large share of calls, silently, and the
             backend then summarises a transcript missing its last turn.
             Bounded, because a caller who has hung up must not hold this
             coroutine open on a slow backend.
          3. Close the agent socket, then cancel whatever is left.
          4. Write the record, then close the HTTP client -- in that
             order, because closing a client with requests in flight
             fails them. The same rule `_finalize_call_then_close`
             follows in agent.py, learned there the same way.
        """
        if self._finalized:
            return
        self._finalized = True

        current = asyncio.current_task()
        draining = set()
        for task in list(self._tasks):
            if task is current:
                continue
            if task.get_name() in ("twilio-reader", "watchdog"):
                task.cancel()
            else:
                draining.add(task)

        await self._drain(draining)

        if self.orchestrator is not None:
            # Closes the STT and TTS sockets and the model's HTTP client.
            # Before the call record is written, so nothing is still
            # arriving while the transcript is being assembled.
            try:
                await self.orchestrator.stop()
            except Exception:  # noqa: BLE001 - teardown must not raise
                logger.debug("Orchestrator stop failed", exc_info=True)
        await self.agent.close()

        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

        if self.session is not None:
            runner = self.runner
            await self.session.finalize(
                patient_id=runner.patient_id if runner else None,
                appointment_id=runner.appointment_id if runner else None,
                actions=runner.actions if runner else None,
                speech_pace_requested=runner.speech_pace_requested if runner else None,
            )
            await self.session.aclose()

        try:
            await self.twilio_ws.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info("Call bridge closed")
