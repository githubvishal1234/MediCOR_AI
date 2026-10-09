"""
Sarvam Bulbul text-to-speech, streaming.

    wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3

WHY SARVAM RATHER THAN DEEPGRAM'S OWN VOICE

Deepgram's Aura voices have American, British, Australian, Irish and
Filipino English -- and no Indian English. For a clinic whose callers are
Indian, an Indian-English voice is not a nicety; it is the difference
between a receptionist that sounds like the practice and one that sounds
like an overseas call centre. Bulbul speaks `en-IN` natively, and the
same model covers Hindi and Telugu when those are switched on later.

That choice is also why this stack is a pipeline rather than Deepgram's
managed Voice Agent: **the Voice Agent API accepts only `deepgram`,
`cartesia`, `eleven_labs`, `open_ai` and `aws_polly` as speak providers.**
Sarvam cannot be plugged into it at all. Using Sarvam means orchestrating
STT, LLM and TTS ourselves, which is what `orchestrator.py` does.

WHY THE WEBSOCKET AND NOT THE REST ENDPOINT

REST returns the whole utterance in one response, so the caller hears
nothing until the entire sentence has been synthesised. The websocket
streams audio out as it is produced (Sarvam documents sub-250 ms to first
byte), and it takes text in progressively -- so a reply can begin playing
while the rest of it is still being written. On a phone call that
difference is the whole experience.

AUDIO FORMAT

`output_audio_codec=mulaw` at `speech_sample_rate=8000` is exactly what
Twilio wants, so the bytes go from Sarvam to Twilio with no conversion
at all. Sarvam's REST documentation does not list mu-law among its
codecs even though the websocket documentation does, so `audio.py` can
convert from `linear16` or `wav` if a particular account rejects it --
set `SARVAM_TTS_CODEC` and nothing else changes.
"""

import asyncio
import base64
import json
import logging
import re
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlencode

import websockets

from .. import settings
from . import audio

logger = logging.getLogger("twilio-deepgram.tts")


class TextToSpeechError(Exception):
    """The TTS socket could not be established or was lost mid-call."""


# Split a reply into speakable pieces at sentence ends. Sending the first
# clause the moment it exists, rather than waiting for the model to
# finish writing, is worth roughly a second on a typical reply.
#
# The lookbehind excludes a few abbreviations that would otherwise be
# read as sentence ends and chop a number in half mid-utterance.
_SENTENCE_END = re.compile(r"(?<![A-Z])(?<!Dr)(?<!Mr)(?<!Mrs)(?<!Ms)([.!?])\s+")


def sentences(text: str) -> list[str]:
    """Split a reply into utterances for progressive synthesis."""
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENTENCE_END.sub(r"\1\n", text).split("\n")
    return [p.strip() for p in parts if p.strip()]


class SarvamTTS:
    """One streaming synthesis socket, for one call."""

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._closed = False
        self._language = settings.SARVAM_TTS_LANGUAGE
        self._pace = settings.SARVAM_TTS_PACE

    def _url(self) -> str:
        return f"{settings.SARVAM_TTS_WS_URL}?{urlencode({'model': settings.SARVAM_TTS_MODEL})}"

    def _config(self) -> dict[str, Any]:
        """
        The config frame, built for the model actually in use.

        bulbul:v3 takes `temperature` and rejects `pitch`/`loudness`;
        bulbul:v2 is the other way round. Sending a field the model does
        not accept is a rejected config and a silent call, so the two are
        kept apart rather than merged into one hopeful dictionary.
        """
        data: dict[str, Any] = {
            "language_code": self._language,
            "speaker": settings.SARVAM_TTS_SPEAKER,
            "model": settings.SARVAM_TTS_MODEL,
            "pace": self._pace,
            "speech_sample_rate": str(settings.SARVAM_TTS_SAMPLE_RATE),
            "output_audio_codec": settings.SARVAM_TTS_CODEC,
            # Numbers, dates and dosages read as words rather than
            # digit-by-digit. On a clinic line this is most of what gets
            # spoken aloud.
            "enable_preprocessing": settings.SARVAM_TTS_PREPROCESS,
        }
        if settings.SARVAM_TTS_MODEL.startswith("bulbul:v3"):
            if settings.SARVAM_TTS_TEMPERATURE is not None:
                data["temperature"] = settings.SARVAM_TTS_TEMPERATURE
        else:
            data["loudness"] = settings.SARVAM_TTS_LOUDNESS
            data["pitch"] = settings.SARVAM_TTS_PITCH
        return {"type": "config", "data": data}

    async def connect(self) -> None:
        if not settings.SARVAM_API_KEY:
            raise TextToSpeechError("SARVAM_API_KEY is not set")
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self._url(),
                    additional_headers={
                        "Api-Subscription-Key": settings.SARVAM_API_KEY
                    },
                    max_queue=64,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=settings.SARVAM_CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            raise TextToSpeechError("Timed out connecting to Sarvam TTS") from e
        except Exception as e:  # noqa: BLE001
            raise TextToSpeechError(f"Could not connect to Sarvam TTS: {e}") from e

        await self._send(self._config())
        logger.info(
            "TTS socket open (%s, %s, %s @ %sHz)",
            settings.SARVAM_TTS_MODEL,
            settings.SARVAM_TTS_SPEAKER,
            settings.SARVAM_TTS_CODEC,
            settings.SARVAM_TTS_SAMPLE_RATE,
        )

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not send %s to Sarvam: %s", payload.get("type"), e)

    async def speak(self, text: str) -> None:
        """Queue text for synthesis. Audio arrives on `audio_chunks()`."""
        text = (text or "").strip()
        if not text:
            return
        await self._send({"type": "text", "data": {"text": text}})

    async def flush(self) -> None:
        """Tell Sarvam no more text is coming for this utterance."""
        await self._send({"type": "flush"})

    async def set_language(self, language_code: str) -> bool:
        """
        Switch voice language mid-call.

        Unused while the deployment is English-only, and deliberately
        kept: it is the hook the language work will need, and it costs a
        reconnect rather than a rewrite. Returns whether it took.
        """
        if language_code == self._language:
            return True
        self._language = language_code
        await self._send(self._config())
        return True

    async def set_pace(self, pace: float) -> bool:
        """
        Slow the voice for the rest of the call (PF-16).

        The pace figure is deployment configuration and the stored
        clinical fact stays "this patient asked for slower" -- the same
        split the LiveKit stack draws, for the same reason.
        """
        self._pace = pace
        await self._send(self._config())
        return True

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def audio_chunks(self) -> AsyncIterator[bytes]:
        """
        mu-law audio ready for Twilio, plus an empty bytes object to mark
        the end of each utterance.

        The sentinel matters: the orchestrator needs to know when an
        utterance has finished playing to decide whether the agent is
        still speaking, and therefore whether the next thing the caller
        says is an interruption or an answer.
        """
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                if isinstance(raw, (bytes, bytearray)):
                    # Undocumented but harmless: treat a binary frame as
                    # audio in the configured codec.
                    yield self._convert(bytes(raw))
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue

                kind = message.get("type")
                data = message.get("data") or {}

                if kind == "audio":
                    encoded = data.get("audio")
                    if encoded:
                        try:
                            yield self._convert(base64.b64decode(encoded))
                        except Exception as e:  # noqa: BLE001
                            logger.warning("Undecodable TTS audio dropped: %s", e)
                elif kind == "event":
                    if data.get("event_type") == "final":
                        yield b""  # utterance finished
                elif kind == "error":
                    # Sarvam's own diagnostics. Logged in full because
                    # this is where a bad speaker name or an unsupported
                    # codec surfaces, and it surfaces as a silent call.
                    logger.error("Sarvam TTS error: %s", data)
        except websockets.exceptions.ConnectionClosed as e:
            if not self._closed:
                logger.warning("TTS socket closed unexpectedly: %s", e)
        except Exception:  # noqa: BLE001
            logger.exception("TTS receive loop failed")
        finally:
            self._closed = True

    def _convert(self, payload: bytes) -> bytes:
        """Whatever Sarvam sent, as the mu-law Twilio needs."""
        try:
            return audio.to_twilio_mulaw(
                payload,
                settings.SARVAM_TTS_CODEC,
                settings.SARVAM_TTS_SAMPLE_RATE,
            )
        except ValueError as e:
            # A misconfigured codec would otherwise emit noise down the
            # line. Say so once and drop the audio.
            logger.error("%s", e)
            return b""
