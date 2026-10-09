"""
The Deepgram Voice Agent websocket connection.

Deepgram's Voice Agent API is a single websocket that carries the whole
conversation: we send raw caller audio in, it sends synthesised agent
audio back, and JSON control frames travel in both directions for
settings, transcripts, interruptions and tool calls. STT, turn detection,
the LLM and TTS all live behind it, which is the point of the migration --
none of those are separate services this process has to orchestrate any
more.

WHY RAW `websockets` AND NOT THE DEEPGRAM SDK

The protocol is documented JSON plus binary frames, `websockets` is
already installed in this venv (uvicorn[standard] depends on it), and the
SDK would add a dependency with its own pydantic and httpx floors to a
virtual environment shared with a FastAPI application pinned to
`pydantic==2.9.2`. The project has been bitten by dependency drift in
this venv before. When the SDK's typed events become worth that, swapping
this module is a contained change: nothing outside it knows the wire
format.

WHAT THIS MODULE DOES NOT DO

It does not know about Twilio, call logs, patients or tenants. It speaks
the protocol and raises events; `bridge.py` decides what any of it means.
That separation is what makes the barge-in path testable without a phone.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import websockets

from . import settings

logger = logging.getLogger("twilio-deepgram.agent")


class DeepgramAgentError(Exception):
    """The agent socket could not be established or was lost mid-call."""


class DeepgramAgent:
    """
    One Voice Agent websocket, for one call.

    Usage is deliberately explicit rather than a context manager with
    callbacks: `bridge.py` runs the receive loop itself so it can order
    Twilio writes against Deepgram events, which is what barge-in
    correctness depends on.
    """

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._closed = False
        # The TTS speed currently in force. Kept so `slow_down()` is
        # idempotent -- a caller who asks twice must not end up at a pace
        # nobody can understand.
        self._speed: Optional[float] = None

    # -- lifecycle -----------------------------------------------------

    async def connect(self) -> None:
        if not settings.DEEPGRAM_API_KEY:
            raise DeepgramAgentError("DEEPGRAM_API_KEY is not set")

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    settings.DEEPGRAM_AGENT_URL,
                    additional_headers={
                        "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"
                    },
                    # Deepgram sends 20ms audio frames; the default 1MB
                    # cap is ample, but an unbounded queue is not -- a
                    # slow Twilio socket must not let this one buffer the
                    # whole call in memory.
                    max_queue=64,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=settings.DEEPGRAM_CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            raise DeepgramAgentError("Timed out connecting to Deepgram") from e
        except Exception as e:  # noqa: BLE001 - normalised for one call site
            raise DeepgramAgentError(f"Could not connect to Deepgram: {e}") from e

        logger.info("Deepgram agent socket open")

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("Deepgram socket close failed", exc_info=True)
            self._ws = None

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed

    # -- sending -------------------------------------------------------

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not send %s to Deepgram: %s", payload.get("type"), e)

    async def send_settings(
        self,
        prompt: str,
        functions: list[dict[str, Any]],
        greeting: Optional[str] = None,
        voice_profile: Optional[str] = None,
    ) -> None:
        """
        The Settings message, which must be the first thing sent and must
        precede any audio.

        `greeting` is what the agent says before the caller has said
        anything. Handing it to Deepgram rather than synthesising it
        ourselves matters: it is then spoken through the same TTS session
        as the rest of the call and is interruptible like anything else,
        so a caller who starts talking over it is heard.
        """
        payload: dict[str, Any] = {
            "type": "Settings",
            "audio": settings.audio_block(),
            "agent": {
                "listen": settings.listen_block(),
                "think": settings.think_block(prompt, functions),
                "speak": settings.speak_block(voice_profile),
            },
        }
        if greeting:
            payload["agent"]["greeting"] = greeting

        # Logged without the prompt: it is long, it is re-derivable, and
        # a clinic's escalation rules are in it.
        logger.info(
            "Deepgram settings: listen=%s think=%s speak=%s tools=%d",
            payload["agent"]["listen"]["provider"].get("model"),
            payload["agent"]["think"]["provider"].get("model"),
            payload["agent"]["speak"]["provider"].get("model")
            or payload["agent"]["speak"]["provider"].get("model_id"),
            len(functions),
        )
        await self._send_json(payload)

    async def send_audio(self, chunk: bytes) -> None:
        """Raw caller audio, in the encoding declared in Settings."""
        if self._ws is None:
            return
        try:
            await self._ws.send(chunk)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not forward audio to Deepgram: %s", e)

    async def send_function_result(self, call_id: str, name: str, content: str) -> None:
        """
        Answer one FunctionCallRequest.

        `content` is a string, not an object -- Deepgram passes it to the
        model verbatim, so the caller is responsible for having
        serialised it (tools.ToolRunner.execute does).
        """
        await self._send_json(
            {
                "type": "FunctionCallResponse",
                "id": call_id,
                "name": name,
                "content": content,
            }
        )

    async def inject_agent_message(self, message: str) -> None:
        """
        Make the agent say something we decided on, not the model.

        Used for the holding phrase during a slow tool call. Deepgram
        refuses an injection while a turn is already in progress and says
        so with `InjectionRefused`; that is the correct outcome and is
        logged rather than retried -- speaking over the model's own reply
        would be worse than the silence this is trying to fill.
        """
        await self._send_json({"type": "InjectAgentMessage", "content": message})

    async def slow_down(self, speed: float = 0.8) -> bool:
        """
        Drop the speaking pace for the rest of the call (PF-16).

        A closed decision rather than a number the model can choose: the
        value is a vendor parameter, and an out-of-range figure is either
        a mid-call rejection or an unintelligible voice. The stored
        clinical fact remains "this patient asked for slower"; what that
        means in vendor terms is a deployment concern.

        Returns whether it took effect. Not every speak provider exposes
        a speed, and that must degrade quietly rather than derail a call.
        """
        if self._ws is None:
            return False
        if self._speed is not None and self._speed <= speed:
            return True

        provider = dict(settings.speak_block().get("provider", {}))
        provider["speed"] = speed
        await self._send_json({"type": "UpdateSpeak", "speak": {"provider": provider}})
        self._speed = speed
        return True

    # NOTE: no KeepAlive is sent, and that is deliberate rather than an
    # omission. Deepgram's KeepAlive exists for a socket with no audio
    # flowing; on a telephone call Twilio streams 8 kHz media
    # continuously from the moment the stream opens until the caller
    # hangs up, including through silence, so the socket is never idle.
    # A timer sending a message that is never needed is a timer that can
    # only ever be wrong.

    # -- receiving -----------------------------------------------------

    async def events(self) -> AsyncIterator[tuple[str, Any]]:
        """
        Yield ("audio", bytes) or ("<Type>", dict) for everything the
        agent sends.

        A binary frame is synthesised speech; a text frame is a control
        message whose `type` names it. Anything unparseable is dropped
        with a warning rather than ending the call -- a protocol addition
        on Deepgram's side must not take a clinic's phone line down.
        """
        if self._ws is None:
            return
        try:
            async for message in self._ws:
                if isinstance(message, (bytes, bytearray)):
                    yield "audio", bytes(message)
                    continue
                try:
                    parsed = json.loads(message)
                except ValueError:
                    logger.warning("Unparseable frame from Deepgram: %r", message[:200])
                    continue
                if not isinstance(parsed, dict):
                    continue
                yield str(parsed.get("type") or "Unknown"), parsed
        except websockets.exceptions.ConnectionClosed as e:
            # Expected at the end of every call. Only worth a warning if
            # it happened while we still thought the call was live.
            if not self._closed:
                logger.warning("Deepgram socket closed unexpectedly: %s", e)
        except Exception:  # noqa: BLE001
            logger.exception("Deepgram receive loop failed")
        finally:
            self._closed = True
