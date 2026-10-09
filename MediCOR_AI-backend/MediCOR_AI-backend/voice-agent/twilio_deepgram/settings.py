"""
Configuration for the Twilio + Deepgram gateway.

Everything here is read from the environment, and the module's job is to
turn that into the provider blocks Deepgram's Settings message wants.
Nothing is hardcoded that a deployment might reasonably need to change --
in particular no model name is baked in below without an env override,
because Deepgram's model line-up moves and a rename should be a .env edit
rather than a code change.

WHAT IS DELIBERATELY *NOT* HERE

The system prompt (see prompt.py) and the tool schemas (see tools.py).
Both are safety-relevant enough to be tested directly, and a constant in
a settings module is exactly where the old `config.SYSTEM_PROMPT` drifted
into describing an agent that no longer existed.

ENGLISH ONLY, AND THAT IS A DECISION

This stack ships **English only**, on purpose and for now. Hindi and
Telugu come later, once English is proven on real calls.

Two separate things happen to point the same way, and it is worth not
confusing them:

  * The *decision* is to do one language properly first.
  * The *constraint* is that Deepgram's own TTS voices (Aura-1, Aura-2,
    Flux TTS) cover English, a few European languages and Japanese --
    they do not speak Hindi or Telugu -- and the Voice Agent API accepts
    only `deepgram`, `cartesia`, `eleven_labs`, `open_ai` and
    `aws_polly` as speak providers, so the Sarvam voices the LiveKit
    stack used cannot be plugged in here at all.

So the defaults below are English end to end: `nova-3` listening in
`en`, an Aura-2 English voice speaking, and a prompt that tells the model
to hand a non-English caller straight to a person rather than struggle
on in a language it cannot pronounce.

Every part of the speak block is nonetheless configurable and nothing is
hardcoded, because adding languages should be a `.env` change rather than
a rewrite. When that day comes: `DEEPGRAM_TTS_PROVIDER=eleven_labs`
(Flash v2.5 covers Hindi and Telugu) plus the endpoint and key, and
`DEEPGRAM_STT_LANGUAGE` back to `multi` -- see VOICE_STACK_MIGRATION.md.
Until then the LiveKit + Sarvam path remains the only one that speaks
Telugu, which is why it has not been deleted.
"""

import json
import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv

# The gateway shares voice-agent/.env with the LiveKit worker. They read
# disjoint keys (this one touches nothing named LIVEKIT_* or SARVAM_*),
# so one file can configure whichever stack is running.
load_dotenv()

logger = logging.getLogger("twilio-deepgram.settings")


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: Optional[float]) -> Optional[float]:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %r", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %r", name, raw, default)
        return default


def _env_list(name: str) -> list[str]:
    raw = _env(name)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _env_json(name: str) -> dict[str, Any]:
    """
    An arbitrary JSON object from the environment, for the provider
    fields Deepgram documents but this file cannot usefully enumerate
    (AWS credentials, a Cartesia voice object, a future provider's
    options).

    A malformed value is logged and ignored rather than raised: a typo in
    an optional extra must not stop a clinic's phone line from starting.
    """
    raw = _env(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        logger.warning("%s is not valid JSON (%s); ignoring it", name, e)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("%s must be a JSON object; ignoring it", name)
        return {}
    return parsed


# ---------------------------------------------------------------------
# Which stack is running
# ---------------------------------------------------------------------
#
# Not a runtime switch inside one process -- the two stacks are two
# programs -- but a guard against running the wrong one. A deployment
# that has not yet decided is told so at startup instead of silently
# answering calls with a half-configured agent.
VOICE_PROVIDER = (_env("VOICE_PROVIDER", "twilio") or "twilio").lower()
VOICE_AGENT_PROVIDER = (_env("VOICE_AGENT_PROVIDER", "deepgram") or "deepgram").lower()

# Which conversation engine runs inside the gateway.
#
#   "pipeline"  Deepgram streaming STT -> our LLM -> Sarvam Bulbul TTS.
#               The default, because Sarvam is the only available voice
#               that speaks Indian English, and Deepgram's managed agent
#               cannot use it -- its speak-provider list is deepgram /
#               cartesia / eleven_labs / open_ai / aws_polly.
#
#   "agent"     Deepgram's managed Voice Agent: STT, turn-taking, LLM and
#               TTS behind one socket. Fewer moving parts, no Sarvam.
#
# Both share the Twilio bridge, tenant resolution, tools, prompt and call
# record. See twilio_deepgram/pipeline/__init__.py.
VOICE_ENGINE = (_env("VOICE_ENGINE", "pipeline") or "pipeline").lower()

# Replace the three paid services (STT, LLM, TTS) with local fakes.
#
# Everything else stays real: the Twilio webhook and its signature check,
# the media stream, tenant resolution, the tool layer, the backend, SQL
# Server and the dashboard. That makes it possible to prove a deployment
# is wired up correctly -- by ringing the number and hearing a scripted
# reply -- before spending anything, and to work on the call flow before
# any account exists. See pipeline/mocks.py.
VOICE_MOCK_MODE = _env_bool("VOICE_MOCK_MODE", False)
VOICE_MOCK_SCRIPT = _env("VOICE_MOCK_SCRIPT")
VOICE_MOCK_LATENCY_SECONDS = _env_float("VOICE_MOCK_LATENCY_SECONDS", 0.3) or 0.0
# Characters per second, used to give mock speech a plausible duration.
VOICE_MOCK_SPEECH_RATE = _env_float("VOICE_MOCK_SPEECH_RATE", 14.0) or 14.0


# ---------------------------------------------------------------------
# Gateway HTTP server
# ---------------------------------------------------------------------

GATEWAY_HOST = _env("VOICE_GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = _env_int("VOICE_GATEWAY_PORT", 8080)

# The externally reachable https:// base for this gateway -- an ngrok URL
# in development, the real hostname in production.
#
# Two things depend on it and both fail confusingly without it: Twilio
# signs the URL it *called*, which behind a proxy or a tunnel is not the
# URL this process sees; and the <Stream> element needs an absolute wss://
# URL to hand back. Same reasoning, and deliberately the same shape, as
# the backend's PUBLIC_BASE_URL.
PUBLIC_BASE_URL = (_env("VOICE_GATEWAY_PUBLIC_URL", "") or "").rstrip("/")

# Path prefix for every route this gateway serves.
ROUTE_PREFIX = _env("VOICE_GATEWAY_ROUTE_PREFIX", "/voice") or "/voice"


def reachable_by_twilio() -> bool:
    """
    Whether `PUBLIC_BASE_URL` is somewhere Twilio could actually connect.

    Twilio dials out from its own infrastructure, so it needs a public
    host over TLS: a loopback address, a private-range address or plain
    http will all work perfectly for `simulate_call.py` and not at all
    for a real call. That asymmetry is worth detecting, because the
    failure is silent from this side -- the gateway serves valid TwiML,
    Twilio cannot open the stream, and the caller hears nothing.

    Deliberately a coarse check. It cannot tell whether a public
    hostname actually resolves, and does not try; it catches the case
    that is certain to be wrong rather than guessing at the rest.
    """
    url = PUBLIC_BASE_URL.lower()
    if not url:
        return False
    if not url.startswith("https://"):
        return False
    host = url[len("https://"):].split("/", 1)[0].split(":", 1)[0]
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False
    # RFC 1918 and link-local. A gateway on a LAN address is reachable
    # from a developer's browser and from nowhere Twilio can dial.
    if host.startswith(("10.", "192.168.", "169.254.")):
        return False
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return False
    return True


def stream_websocket_url() -> str:
    """
    The wss:// URL Twilio should open for the media stream.

    Derived from PUBLIC_BASE_URL rather than configured separately: two
    settings answering "where does this gateway live" is two things to
    get out of step, and a mismatch here presents as a call that connects
    and then sits in silence.
    """
    base = PUBLIC_BASE_URL
    if not base:
        return ""
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}{ROUTE_PREFIX}/stream"


# ---------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------

TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = _env("TWILIO_PHONE_NUMBER")

# Verify the X-Twilio-Signature on every inbound webhook. On by default,
# and the only reason to turn it off is a local test harness that cannot
# sign -- never a production deployment, because these endpoints answer
# the phone and start a paid conversation.
TWILIO_VALIDATE_SIGNATURE = _env_bool("TWILIO_VALIDATE_SIGNATURE", True)

TWILIO_API_BASE = _env("TWILIO_API_BASE", "https://api.twilio.com") or "https://api.twilio.com"
TWILIO_HTTP_TIMEOUT_SECONDS = _env_float("TWILIO_HTTP_TIMEOUT_SECONDS", 8.0) or 8.0


# ---------------------------------------------------------------------
# Deepgram Voice Agent
# ---------------------------------------------------------------------

DEEPGRAM_API_KEY = _env("DEEPGRAM_API_KEY")
DEEPGRAM_AGENT_URL = (
    _env("DEEPGRAM_AGENT_URL", "wss://agent.deepgram.com/v1/agent/converse")
    or "wss://agent.deepgram.com/v1/agent/converse"
)

# Twilio's media format, both directions. Not configurable, because it is
# not a preference: this is what Twilio sends and what Twilio accepts, and
# anything else produces a call that connects to silence or to noise.
TWILIO_AUDIO_ENCODING = "mulaw"
TWILIO_AUDIO_SAMPLE_RATE = 8000

# How long to wait for the agent socket to open before giving up on a
# call. There is no KeepAlive setting to go with it: Twilio streams audio
# continuously for the whole call, so the socket is never idle. See the
# note in deepgram_agent.py.
DEEPGRAM_CONNECT_TIMEOUT_SECONDS = (
    _env_float("DEEPGRAM_CONNECT_TIMEOUT_SECONDS", 10.0) or 10.0
)

# --- Listen (STT) ---
#
# nova-3 with language="en", matching the English-only decision above.
#
# **Not "multi", and the difference is not cosmetic.** `multi` is the
# code-switching mode: it spreads the model's probability mass across ten
# languages so a caller can move between them mid-sentence. That is worth
# paying for when the agent can *answer* in those languages. It cannot,
# so all `multi` buys here is a slightly worse transcription of the
# English the agent is actually going to reply to -- and a Hindi sentence
# transcribed perfectly still gets an English answer.
#
# Indian-accented English is handled by `en`; that is a matter of
# acoustic modelling, not of language selection.
#
# When Hindi and Telugu are added (see the note at the top of this file),
# this comes back to "multi" for Hindi, or "te" for a Telugu-first
# clinic -- nova-3 supports Telugu only as a dedicated language, never as
# part of "multi".
DEEPGRAM_STT_MODEL = _env("DEEPGRAM_STT_MODEL", "nova-3") or "nova-3"
DEEPGRAM_STT_VERSION = _env("DEEPGRAM_STT_VERSION")  # "v1" | "v2"; None = provider default
DEEPGRAM_STT_LANGUAGE = _env("DEEPGRAM_STT_LANGUAGE", "en")
# Flux models take hints rather than a language. Set only when using one.
DEEPGRAM_STT_LANGUAGE_HINTS = _env_list("DEEPGRAM_STT_LANGUAGE_HINTS")
# Words the model would otherwise mishear and that this product cannot
# afford to get wrong -- drug names, the clinic's name, "appointment".
DEEPGRAM_STT_KEYTERMS = _env_list("DEEPGRAM_STT_KEYTERMS")
# Flux-only end-of-turn tuning. Left unset (provider default) unless a
# deployment has measured a reason: too eager cuts callers off mid-
# sentence, too patient adds dead air to every single turn.
DEEPGRAM_STT_EOT_THRESHOLD = _env_float("DEEPGRAM_STT_EOT_THRESHOLD", None)
DEEPGRAM_STT_EOT_TIMEOUT_MS = _env("DEEPGRAM_STT_EOT_TIMEOUT_MS")

# --- Think (LLM) ---
#
# open_ai/gpt-4o-mini is Deepgram-managed, needs no endpoint of our own,
# and is the model Deepgram's own telephony guide uses -- which makes it
# the right *starting* point rather than the right permanent answer.
# Change it in .env after measuring, not before.
DEEPGRAM_LLM_PROVIDER = _env("DEEPGRAM_LLM_PROVIDER", "open_ai") or "open_ai"
DEEPGRAM_LLM_MODEL = _env("DEEPGRAM_LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini"
DEEPGRAM_LLM_TEMPERATURE = _env_float("DEEPGRAM_LLM_TEMPERATURE", 0.2)
# Required by Deepgram for `groq` and `aws_bedrock`, optional for the
# providers they manage. Supplying it is also how a deployment brings its
# own contract with OpenAI/Anthropic/Google.
DEEPGRAM_LLM_ENDPOINT_URL = _env("DEEPGRAM_LLM_ENDPOINT_URL")
DEEPGRAM_LLM_ENDPOINT_HEADERS = _env_json("DEEPGRAM_LLM_ENDPOINT_HEADERS")
DEEPGRAM_LLM_PROVIDER_EXTRA = _env_json("DEEPGRAM_LLM_PROVIDER_EXTRA")

# --- Speak (TTS) ---
DEEPGRAM_TTS_PROVIDER = _env("DEEPGRAM_TTS_PROVIDER", "deepgram") or "deepgram"
DEEPGRAM_TTS_MODEL = _env("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
DEEPGRAM_TTS_VERSION = _env("DEEPGRAM_TTS_VERSION")
DEEPGRAM_TTS_MODEL_ID = _env("DEEPGRAM_TTS_MODEL_ID")  # eleven_labs / cartesia
DEEPGRAM_TTS_VOICE = _env("DEEPGRAM_TTS_VOICE")  # open_ai / aws_polly / cartesia
DEEPGRAM_TTS_LANGUAGE = _env("DEEPGRAM_TTS_LANGUAGE")
DEEPGRAM_TTS_ENDPOINT_URL = _env("DEEPGRAM_TTS_ENDPOINT_URL")
DEEPGRAM_TTS_ENDPOINT_HEADERS = _env_json("DEEPGRAM_TTS_ENDPOINT_HEADERS")
DEEPGRAM_TTS_PROVIDER_EXTRA = _env_json("DEEPGRAM_TTS_PROVIDER_EXTRA")

# Maps the clinic-facing voice-profile labels shown on /app/agent onto
# Deepgram voices. Unrecognised leaves the configured default alone -- a
# clinic must not be able to break its own audio by typing something
# unexpected into that field. Same contract as _VOICE_PROFILE_IDS in
# agent.py, a different vocabulary, because these are different vendors:
# a clinic that picked "Anushka" (a Sarvam voice) gets the deployment
# default here rather than a silent substitution it never chose.
# All verified against Deepgram's TTS model list rather than
# extrapolated from the Aura-1 names, which share several of them.
# `draco` and `pandora` are the British-accented pair; the rest are
# American. There is no Indian-English Aura-2 voice.
VOICE_PROFILE_MODELS: dict[str, str] = {
    "thalia": "aura-2-thalia-en",
    "asteria": "aura-2-asteria-en",
    "luna": "aura-2-luna-en",
    "apollo": "aura-2-apollo-en",
    "arcas": "aura-2-arcas-en",
    "orion": "aura-2-orion-en",
    "draco": "aura-2-draco-en",
    "pandora": "aura-2-pandora-en",
}

# Spoken if a tool call leaves the line silent for longer than this. The
# LiveKit stack learned this the hard way: a booking is an LLM pass plus
# an HTTP round trip, which measured 3-5s of dead air -- long enough that
# callers say "hello?". Set to 0 to disable.
TOOL_FILLER_DELAY_SECONDS = _env_float("VOICE_TOOL_FILLER_DELAY_SECONDS", 1.2) or 0.0
TOOL_FILLER_PHRASE = _env("VOICE_TOOL_FILLER_PHRASE", "One moment.")

# Hard ceiling on a single call, as a runaway-cost backstop. A voice
# agent that gets into a loop against a silent line bills by the minute
# until something stops it.
MAX_CALL_SECONDS = _env_int("VOICE_MAX_CALL_SECONDS", 1800)


# =====================================================================
#  PIPELINE ENGINE  (VOICE_ENGINE=pipeline)
# =====================================================================
#
# Only read when the pipeline engine is running. The managed-agent engine
# uses the DEEPGRAM_LLM_* / DEEPGRAM_TTS_* settings above instead.

# --- Deepgram streaming STT ---
DEEPGRAM_LISTEN_URL = (
    _env("DEEPGRAM_LISTEN_URL", "wss://api.deepgram.com/v1/listen")
    or "wss://api.deepgram.com/v1/listen"
)
# Milliseconds of silence that ends a turn. Deepgram's default is 10 ms,
# which is far too eager for a phone call -- it cuts people off between
# words. 300 ms is a natural pause without adding a wait to every reply.
DEEPGRAM_STT_ENDPOINTING_MS = _env_int("DEEPGRAM_STT_ENDPOINTING_MS", 300)
# The backstop for a line so noisy the endpointer never sees silence.
# Without it a caller in the street can talk and never get an answer.
DEEPGRAM_STT_UTTERANCE_END_MS = _env_int("DEEPGRAM_STT_UTTERANCE_END_MS", 1000)

# --- The LLM ---
#
# Defaults point at Groq, which this project already runs on live calls
# through agent.py and which the backend uses for call summaries. Nothing
# is Groq-specific: any OpenAI-compatible /chat/completions endpoint works
# by changing the base URL and model.
LLM_BASE_URL = (
    _env("VOICE_LLM_BASE_URL", "https://api.groq.com/openai/v1")
    or "https://api.groq.com/openai/v1"
)
LLM_API_KEY = _env("VOICE_LLM_API_KEY") or _env("GROQ_API_KEY")
LLM_MODEL = _env("VOICE_LLM_MODEL", "openai/gpt-oss-20b") or "openai/gpt-oss-20b"
LLM_TEMPERATURE = _env_float("VOICE_LLM_TEMPERATURE", 0.2)
# Enough for a two-sentence reply plus a tool call. Reasoning models
# spend part of this budget before emitting anything, which is what made
# 64 too small on the LiveKit stack and 300 occasionally too tight.
LLM_MAX_TOKENS = _env_int("VOICE_LLM_MAX_TOKENS", 500)
LLM_TIMEOUT_SECONDS = _env_float("VOICE_LLM_TIMEOUT_SECONDS", 12.0) or 12.0
# How many model/tool round trips one caller turn may take before the
# turn is abandoned. Unbounded, a model that keeps asking for the same
# lookup holds a caller on a silent line and bills for it.
LLM_MAX_TOOL_ROUNDS = _env_int("VOICE_LLM_MAX_TOOL_ROUNDS", 5)
# Conversation messages kept before the oldest are dropped. The system
# prompt is never trimmed.
LLM_MAX_HISTORY_MESSAGES = _env_int("VOICE_LLM_MAX_HISTORY_MESSAGES", 40)
# Provider-specific extras merged into the request body, as JSON --
# e.g. {"reasoning_effort": "low"} for Groq's gpt-oss models.
LLM_EXTRA_BODY = _env_json("VOICE_LLM_EXTRA_BODY")

# --- Sarvam Bulbul TTS ---
SARVAM_API_KEY = _env("SARVAM_API_KEY")
SARVAM_TTS_WS_URL = (
    _env("SARVAM_TTS_WS_URL", "wss://api.sarvam.ai/text-to-speech/ws")
    or "wss://api.sarvam.ai/text-to-speech/ws"
)
SARVAM_TTS_MODEL = _env("SARVAM_TTS_MODEL", "bulbul:v3") or "bulbul:v3"
SARVAM_TTS_SPEAKER = _env("SARVAM_VOICE", "anushka") or "anushka"
# en-IN: Indian English, which is the whole reason for choosing Sarvam
# over Deepgram's own voices. hi-IN and te-IN are supported by the same
# model when those languages are switched on.
SARVAM_TTS_LANGUAGE = _env("SARVAM_LANGUAGE", "en-IN") or "en-IN"
SARVAM_TTS_PACE = _env_float("SARVAM_TTS_PACE", 1.0) or 1.0
# mu-law at 8000 Hz is exactly what Twilio wants, so the audio needs no
# conversion at all. Sarvam's REST docs do not list mu-law while its
# WebSocket docs do; if an account rejects it, set linear16 or wav and
# pipeline/audio.py converts instead.
SARVAM_TTS_CODEC = _env("SARVAM_TTS_CODEC", "mulaw") or "mulaw"
SARVAM_TTS_SAMPLE_RATE = _env_int("SARVAM_TTS_SAMPLE_RATE", 8000)
# Numbers, dates and dosages read as words rather than digit by digit.
SARVAM_TTS_PREPROCESS = _env_bool("SARVAM_TTS_PREPROCESS", True)
# bulbul:v3 only.
SARVAM_TTS_TEMPERATURE = _env_float("SARVAM_TTS_TEMPERATURE", None)
# bulbul:v2 only; sending either to v3 is rejected.
SARVAM_TTS_LOUDNESS = _env_float("SARVAM_TTS_LOUDNESS", 1.3) or 1.3
SARVAM_TTS_PITCH = _env_float("SARVAM_TTS_PITCH", 0.0) or 0.0
SARVAM_CONNECT_TIMEOUT_SECONDS = (
    _env_float("SARVAM_CONNECT_TIMEOUT_SECONDS", 10.0) or 10.0
)
# The pace applied when a caller asks to be spoken to more slowly
# (PF-16). A figure here and a vocabulary in the database, deliberately:
# the clinic's stored fact is "this patient asked for slower", and what
# that means in vendor terms is a deployment concern.
SARVAM_SLOW_PACE = _env_float("SLOW_TTS_PACE", 0.75) or 0.75


def voice_model_for(voice_profile: Optional[str]) -> Optional[str]:
    """Best-effort map from a clinic's voice_profile label to a TTS model."""
    profile = (voice_profile or "").strip().lower()
    if not profile:
        return None
    for key, model in VOICE_PROFILE_MODELS.items():
        if key in profile:
            return model
    return None


def _prune(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Drop keys whose value is None.

    Most of the options above are legitimately unset, and an unset option
    has to mean "leave the provider on its own default" rather than
    "send null". Building the dict with holes and pruning once is far
    easier to read than a dozen `if x is not None` branches, and it makes
    that intent obvious.
    """
    return {k: v for k, v in payload.items() if v is not None}


def listen_block() -> dict[str, Any]:
    provider: dict[str, Any] = _prune(
        {
            "type": "deepgram",
            "model": DEEPGRAM_STT_MODEL,
            "version": DEEPGRAM_STT_VERSION,
            "language": DEEPGRAM_STT_LANGUAGE,
            "eot_threshold": DEEPGRAM_STT_EOT_THRESHOLD,
        }
    )
    if DEEPGRAM_STT_LANGUAGE_HINTS:
        provider["language_hints"] = DEEPGRAM_STT_LANGUAGE_HINTS
        # Flux takes hints instead of a language; sending both is a
        # contradiction rather than belt and braces.
        provider.pop("language", None)
    if DEEPGRAM_STT_KEYTERMS:
        provider["keyterms"] = DEEPGRAM_STT_KEYTERMS
    if DEEPGRAM_STT_EOT_TIMEOUT_MS:
        try:
            provider["eot_timeout_ms"] = int(DEEPGRAM_STT_EOT_TIMEOUT_MS)
        except ValueError:
            logger.warning("DEEPGRAM_STT_EOT_TIMEOUT_MS is not an integer; ignoring it")
    return {"provider": provider}


def think_block(prompt: str, functions: list[dict[str, Any]]) -> dict[str, Any]:
    provider: dict[str, Any] = _prune(
        {
            "type": DEEPGRAM_LLM_PROVIDER,
            "model": DEEPGRAM_LLM_MODEL,
            "temperature": DEEPGRAM_LLM_TEMPERATURE,
        }
    )
    provider.update(DEEPGRAM_LLM_PROVIDER_EXTRA)

    block: dict[str, Any] = {"provider": provider, "prompt": prompt}
    if functions:
        block["functions"] = functions
    if DEEPGRAM_LLM_ENDPOINT_URL:
        endpoint: dict[str, Any] = {"url": DEEPGRAM_LLM_ENDPOINT_URL}
        if DEEPGRAM_LLM_ENDPOINT_HEADERS:
            endpoint["headers"] = DEEPGRAM_LLM_ENDPOINT_HEADERS
        block["endpoint"] = endpoint
    return block


def speak_block(voice_profile: Optional[str] = None) -> dict[str, Any]:
    """
    The speak block, with the clinic's chosen voice applied when we
    recognise it.

    Only the Deepgram provider honours `voice_profile`: the labels on the
    Agent Settings screen name Deepgram and Sarvam voices, and quietly
    mapping one of them onto an ElevenLabs voice id would give a clinic a
    voice it never chose.
    """
    provider: dict[str, Any] = _prune(
        {
            "type": DEEPGRAM_TTS_PROVIDER,
            "model": (
                (voice_model_for(voice_profile) or DEEPGRAM_TTS_MODEL)
                if DEEPGRAM_TTS_PROVIDER == "deepgram"
                else DEEPGRAM_TTS_MODEL
            ),
            "version": DEEPGRAM_TTS_VERSION,
            "model_id": DEEPGRAM_TTS_MODEL_ID,
            "voice": DEEPGRAM_TTS_VOICE,
            "language": DEEPGRAM_TTS_LANGUAGE,
        }
    )
    provider.update(DEEPGRAM_TTS_PROVIDER_EXTRA)

    block: dict[str, Any] = {"provider": provider}
    if DEEPGRAM_TTS_ENDPOINT_URL:
        endpoint: dict[str, Any] = {"url": DEEPGRAM_TTS_ENDPOINT_URL}
        if DEEPGRAM_TTS_ENDPOINT_HEADERS:
            endpoint["headers"] = DEEPGRAM_TTS_ENDPOINT_HEADERS
        block["endpoint"] = endpoint
    return block


def audio_block() -> dict[str, Any]:
    """Twilio's format, in and out. See TWILIO_AUDIO_ENCODING above."""
    return {
        "input": {
            "encoding": TWILIO_AUDIO_ENCODING,
            "sample_rate": TWILIO_AUDIO_SAMPLE_RATE,
        },
        "output": {
            "encoding": TWILIO_AUDIO_ENCODING,
            "sample_rate": TWILIO_AUDIO_SAMPLE_RATE,
            "container": "none",
        },
    }


def startup_problems() -> list[str]:
    """
    Everything that would make this gateway answer the phone badly,
    collected so it can be reported at once instead of one crash at a
    time on a live call.

    Reported rather than raised: a developer running this to inspect
    `/health` or to exercise the TwiML endpoint should get a loud list,
    not an import error. `run.py` decides what is fatal.
    """
    problems: list[str] = []

    if VOICE_ENGINE not in ("pipeline", "agent"):
        problems.append(
            f"VOICE_ENGINE={VOICE_ENGINE!r} is not a known engine. "
            "Use 'pipeline' (Deepgram STT + Sarvam TTS) or 'agent' "
            "(Deepgram's managed Voice Agent)."
        )

    if VOICE_MOCK_MODE:
        # Mock mode replaces exactly the three keys below, so demanding
        # them would defeat its purpose. Said out loud on every startup
        # because a gateway left in mock mode answers real calls with a
        # script, and that must never be a surprise.
        problems.append(
            "VOICE_MOCK_MODE=true - speech, model and voice are FAKE. Real calls "
            "will hear a scripted reply. Unset it before going live."
        )
    else:
        if not DEEPGRAM_API_KEY:
            problems.append(
                "DEEPGRAM_API_KEY is not set - the gateway cannot transcribe speech."
            )
        if VOICE_ENGINE == "pipeline":
            if not SARVAM_API_KEY:
                problems.append(
                    "SARVAM_API_KEY is not set - the gateway has no voice and calls "
                    "will connect to silence."
                )
            if not LLM_API_KEY:
                problems.append(
                    "Neither VOICE_LLM_API_KEY nor GROQ_API_KEY is set - the agent "
                    "cannot think."
                )

    # Read here rather than at import: `config` is the LiveKit-era module
    # both stacks share, and importing it at module scope would make this
    # settings module depend on it for one value.
    try:
        import config as agent_config

        transfer_number = agent_config.DEFAULT_TRANSFER_NUMBER
        emergency_number = agent_config.EMERGENCY_NUMBER
    except Exception:  # noqa: BLE001 - a config problem must not hide the rest
        transfer_number, emergency_number = None, None

    if not transfer_number:
        # Not fatal, and deliberately not: a clinic that has not chosen a
        # staff line should still be able to answer calls and book
        # appointments. But four paths quietly stop working -- "I want a
        # person", every escalation rule, the emergency offer, and the
        # apology after a model failure -- and the agent will offer a
        # transfer it cannot perform. Better said at startup than
        # discovered by a caller.
        problems.append(
            "DEFAULT_TRANSFER_NUMBER is not set - the agent cannot put anyone "
            "through to a person. Escalation rules, 'I want a human', and the "
            "emergency transfer offer will all apologise instead of connecting."
        )

    if emergency_number and "your local emergency number" in str(emergency_number):
        # The shipped placeholder. Left in, the agent reads those five
        # words aloud to somebody describing chest pain.
        problems.append(
            "EMERGENCY_NUMBER is still the placeholder text - a caller describing "
            "an emergency would hear it read out. Set it (112 in India)."
        )

    if not TWILIO_AUTH_TOKEN:
        problems.append(
            "TWILIO_AUTH_TOKEN is not set - webhook signatures cannot be verified, "
            "so every inbound call will be rejected."
        )
    if not PUBLIC_BASE_URL:
        problems.append(
            "VOICE_GATEWAY_PUBLIC_URL is not set - the TwiML cannot name a wss:// "
            "stream URL, so calls will connect to silence."
        )
    elif not reachable_by_twilio():
        # Set, but only useful locally. Reported rather than treated as
        # correct, because the whole point of setting it to a local
        # address is to unblock the simulator -- and a deployment that
        # then forgets to change it would answer real calls with TwiML
        # naming a host Twilio cannot resolve. The caller hears silence
        # and nothing in the gateway logs an error, because from its own
        # side everything worked.
        problems.append(
            f"VOICE_GATEWAY_PUBLIC_URL={PUBLIC_BASE_URL} is a LOCAL address. "
            "simulate_call.py works, but Twilio cannot reach it - real calls "
            "will connect to silence. Use an ngrok https URL or your real "
            "hostname before going live."
        )
    if VOICE_PROVIDER != "twilio":
        problems.append(
            f"VOICE_PROVIDER={VOICE_PROVIDER!r}, but this process is the Twilio "
            "gateway. Set VOICE_PROVIDER=twilio, or run voice-agent/agent.py instead."
        )
    if VOICE_AGENT_PROVIDER != "deepgram":
        problems.append(
            f"VOICE_AGENT_PROVIDER={VOICE_AGENT_PROVIDER!r}, but this process speaks "
            "to Deepgram. Set VOICE_AGENT_PROVIDER=deepgram."
        )
    return problems
