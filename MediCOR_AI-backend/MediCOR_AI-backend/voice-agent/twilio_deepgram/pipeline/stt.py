"""
Deepgram streaming speech-to-text.

    wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&...

Telephony audio goes in exactly as Twilio sent it -- 8 kHz mu-law, no
conversion -- and transcripts and turn signals come back as JSON.

THE FOUR EVENTS THAT MATTER, AND WHY EACH IS ASKED FOR

  * `SpeechStarted` (needs `vad_events=true`) is **barge-in**. It fires
    the moment the caller opens their mouth, long before any transcript
    exists, which is the only signal early enough to stop the agent
    talking over them. Without `vad_events` this message never arrives
    and interruption is as slow as transcription.

  * `is_final` marks a transcript Deepgram will not revise. Safe to act
    on; not necessarily the end of the caller's turn.

  * `speech_final` marks the end of a *turn* -- `is_final` plus a pause
    long enough to count as the caller having finished. This is what
    triggers a reply. Driven by `endpointing`.

  * `UtteranceEnd` (needs `utterance_end_ms`) is the backstop for when
    `speech_final` never comes, which happens on a noisy line where the
    endpointer never sees enough silence. Without it a caller on a busy
    street can talk and get no answer at all.

The two together are belt and braces on purpose: acting only on
`speech_final` drops turns, and acting only on `UtteranceEnd` adds its
full silence window to every reply.

This module holds no conversation logic. It emits events; `orchestrator`
decides what a turn means.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlencode

import websockets

from .. import settings
from . import audio

logger = logging.getLogger("twilio-deepgram.stt")


class SpeechToTextError(Exception):
    """The STT socket could not be established or was lost mid-call."""


class DeepgramSTT:
    """One streaming transcription socket, for one call."""

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._closed = False

    def _url(self) -> str:
        params: dict[str, Any] = {
            "model": settings.DEEPGRAM_STT_MODEL,
            "encoding": audio_encoding(),
            "sample_rate": audio.SAMPLE_RATE,
            "channels": 1,
            # Partial transcripts. Not spoken, but they are what makes a
            # caller's first words available before they stop talking.
            "interim_results": "true",
            # Turn detection. See the module docstring.
            "endpointing": settings.DEEPGRAM_STT_ENDPOINTING_MS,
            "utterance_end_ms": settings.DEEPGRAM_STT_UTTERANCE_END_MS,
            "vad_events": "true",
            # Punctuation matters more than it looks: the transcript is
            # both the LLM's input and what gets stored as the call
            # record a clinician may read.
            "punctuate": "true",
            "smart_format": "true",
        }
        if settings.DEEPGRAM_STT_LANGUAGE:
            params["language"] = settings.DEEPGRAM_STT_LANGUAGE
        if settings.DEEPGRAM_STT_VERSION:
            params["version"] = settings.DEEPGRAM_STT_VERSION
        for term in settings.DEEPGRAM_STT_KEYTERMS:
            params.setdefault("keyterm", term)
        return f"{settings.DEEPGRAM_LISTEN_URL}?{urlencode(params)}"

    async def connect(self) -> None:
        if not settings.DEEPGRAM_API_KEY:
            raise SpeechToTextError("DEEPGRAM_API_KEY is not set")
        url = self._url()
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    additional_headers={
                        "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"
                    },
                    max_queue=64,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=settings.DEEPGRAM_CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            raise SpeechToTextError("Timed out connecting to Deepgram STT") from e
        except Exception as e:  # noqa: BLE001 - normalised for one call site
            raise SpeechToTextError(f"Could not connect to Deepgram STT: {e}") from e
        # The URL carries the API key in no part of it, but it does carry
        # every tuning parameter, which is worth having in the log when a
        # call transcribes badly.
        logger.info("STT socket open (%s)", url.split("?", 1)[1])

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(chunk)
        except Exception as e:  # noqa: BLE001
            logger.debug("STT send failed: %s", e)

    async def finish(self) -> None:
        """
        Ask Deepgram to flush and close.

        Sent as a message rather than by closing the socket, so any
        transcript still being assembled is returned instead of
        discarded -- the caller's last few words are usually in it.
        """
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """
        Normalised transcription events.

        Deepgram's wire shape (`channel.alternatives[0].transcript`) is
        flattened here so the orchestrator never has to walk it, and so a
        mock can produce the same events without imitating the whole
        payload:

            {"type": "speech_started"}
            {"type": "transcript", "text": str,
             "is_final": bool, "speech_final": bool}
            {"type": "utterance_end"}
        """
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue

                kind = message.get("type")
                if kind == "SpeechStarted":
                    yield {"type": "speech_started"}
                elif kind == "UtteranceEnd":
                    yield {"type": "utterance_end"}
                elif kind == "Results":
                    alternatives = (message.get("channel") or {}).get("alternatives") or []
                    text = (alternatives[0].get("transcript") if alternatives else "") or ""
                    if not text.strip():
                        # Deepgram emits empty finals during silence.
                        # Passing them on would make the orchestrator
                        # treat a pause as a turn.
                        continue
                    yield {
                        "type": "transcript",
                        "text": text.strip(),
                        "is_final": bool(message.get("is_final")),
                        "speech_final": bool(message.get("speech_final")),
                    }
                elif kind in ("Error", "Warning"):
                    logger.warning("Deepgram STT %s: %s", kind, message)
        except websockets.exceptions.ConnectionClosed as e:
            if not self._closed:
                logger.warning("STT socket closed unexpectedly: %s", e)
        except Exception:  # noqa: BLE001
            logger.exception("STT receive loop failed")
        finally:
            self._closed = True


def audio_encoding() -> str:
    """Deepgram's name for what Twilio sends."""
    return "mulaw"
