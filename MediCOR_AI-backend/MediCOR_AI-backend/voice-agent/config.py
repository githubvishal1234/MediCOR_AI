# =====================================================================
#  Voice agent configuration.
#
#  Customise the agent's persona, models, and behaviour here.
#
#  NOTE: the live system prompt is NOT here. It is built by
#  `agent.build_instructions()`, which is where the emergency block, the
#  accessibility rules and the single-tool contract live, and which has a
#  test (tests/test_agent_prompt_safety.py). A SYSTEM_PROMPT constant
#  used to sit below as a fallback; it had drifted to naming six tools
#  that no longer exist and carried no emergency handling, so it was
#  removed rather than left to be picked up by accident.
#
#  A per-clinic *greeting* is a different thing again, and comes from
#  that clinic's Agent Settings via tenant resolution.
# =====================================================================
import os
from dotenv import load_dotenv

load_dotenv()

# =========================================================================================
#
#  Use this file to customize your agent's personality, models, and behaviour.
#
# =========================================================================================

# --- 1. AGENT PERSONA & PROMPTS ---
# The prompt itself lives in agent.build_instructions(); what remains
# here is the wording a clinic is expected to own (see EMERGENCY_* below).

# If the user initiates the call (inbound) or is already there:
fallback_greeting = "Greet the user immediately."


# --- 2. SPEECH-TO-TEXT (STT) SETTINGS ---
# STT is Sarvam, not Deepgram, and it is NOT configured from this file.
#
# agent.py constructs it directly inside AgentSession:
#
#     stt=sarvam.STT(model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
#                    language="unknown", mode="transcribe", sample_rate=16000)
#
# `language="unknown"` lets Sarvam detect the caller's language per turn;
# the `user_input_transcribed` handler then clamps TTS to en-IN/hi-IN/te-IN.
#
# Three constants used to sit here — STT_PROVIDER="deepgram",
# STT_MODEL="nova-2", STT_LANGUAGE="en" — describing a Deepgram pipeline
# that nothing read. They were removed rather than left to mislead: a
# tree-wide grep found definitions and zero readers. Deepgram survives
# only as an unselected TTS branch in agent.py's _build_tts(), reachable
# by setting TTS_PROVIDER=deepgram.
#
# To change STT, edit the sarvam.STT(...) call in agent.py or set
# SARVAM_STT_MODEL in .env.


# --- 3. TEXT-TO-SPEECH (TTS) SETTINGS ---
# Choose your voice provider: "openai", "sarvam" (Indian voices), or "cartesia" (Ultra-fast)
DEFAULT_TTS_PROVIDER = "openai"
DEFAULT_TTS_VOICE = "alloy"      # OpenAI: alloy, echo, shimmer | Sarvam: anushka, aravind

# Sarvam AI Specifics (for Indian Context)
SARVAM_MODEL = "bulbul:v3"  # v2 was deprecated by Sarvam; it now 400s
SARVAM_LANGUAGE = "en-IN" # or hi-IN

# Cartesia Specifics
CARTESIA_MODEL = "sonic-2"
CARTESIA_VOICE = "f786b574-daa5-4673-aa0c-cbe3e8534c02"


# --- 4. LARGE LANGUAGE MODEL (LLM) SETTINGS ---
# Choose "openai" or "groq"
DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_LLM_MODEL = "gpt-4o-mini" # OpenAI default

# Groq Specifics (Faster inference)
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TEMPERATURE = 0.7


# --- 5. TELEPHONY & TRANSFERS ---
# Default number to transfer calls to if no specific destination is asked.
# ---------------------------------------------------------------------------
# Accessibility (PF-16)
# ---------------------------------------------------------------------------
#
# The pace Sarvam TTS is set to when a caller asks to be spoken to more
# slowly. The default speaking pace is SARVAM_TTS_PACE (0.80); this is
# the slower one.
#
# A figure rather than a vocabulary here, and a vocabulary rather than a
# figure in the database, on purpose: the clinic's stored fact is "this
# patient asked for slower", and what that means in vendor terms is a
# deployment concern that can be tuned without rewriting patient records.
SLOW_TTS_PACE = float(os.getenv("SLOW_TTS_PACE", "0.62"))

# ---------------------------------------------------------------------------
# Emergency handling (PF-03)
# ---------------------------------------------------------------------------
#
# Until this existed, the prompt the model actually receives contained no
# concept of urgency at all. A caller describing chest pain was taken into
# the appointment-booking flow like anyone else. Escalation existed only
# if an individual clinic had thought to write a routing rule for it, and
# then only if the model happened to match it -- which is advisory, not a
# safety net.
#
# Three deliberate choices here:
#
#  * These are CONFIGURATION, not code. The number differs by country
#    (112 across the EU, 911 in the US, 108/102 in much of India) and the
#    wording is a clinical decision that belongs to the clinic, not to
#    whoever last edited agent.py.
#
#  * The wording tells the caller to seek emergency care and offers an
#    immediate transfer. It deliberately does NOT triage, name conditions
#    back to the caller, or advise on what to do in the meantime -- that
#    would be medical advice, which the agent is forbidden from giving and
#    is not competent to give.
#
#  * It is not a substitute for a clinician reviewing it. Anyone deploying
#    this should have EMERGENCY_INSTRUCTION signed off by someone
#    qualified, and set EMERGENCY_NUMBER for their territory.
EMERGENCY_NUMBER = os.getenv("EMERGENCY_NUMBER", "your local emergency number")

# Spoken to the caller, verbatim intent, when the agent judges the call to
# describe a medical emergency. Kept short because it has to be heard and
# acted on, not admired.
EMERGENCY_INSTRUCTION = os.getenv(
    "EMERGENCY_INSTRUCTION",
    "This sounds like a medical emergency. Please hang up and call "
    "{number} now, or go to your nearest emergency department. "
    "I can put you through to the clinic straight away if you prefer.",
)

DEFAULT_TRANSFER_NUMBER = os.getenv("DEFAULT_TRANSFER_NUMBER")

# SIP trunk details. Carrier-neutral by name on purpose: the carrier is
# a deployment decision, not a code one. Currently Twilio Elastic SIP
# Trunking; provision the trunks with setup_twilio_trunks.py.
#
#   SIP_TRUNK_ID  the LiveKit OUTBOUND trunk id, handed to
#                 create_sip_participant() to dial out.
#   SIP_DOMAIN    the carrier's termination host, used to build the
#                 transfer URI sip:<number>@<SIP_DOMAIN>.
#
# Both may be None before the trunk exists. That is handled, not fatal:
# inbound never consults them, and the transfer path already falls back
# to a tel: URI when SIP_DOMAIN is unset.
SIP_TRUNK_ID = os.getenv("SIP_TRUNK_ID")
SIP_DOMAIN = os.getenv("SIP_DOMAIN")


# --- 6. BACKEND API (MediCOR AI) ---
# The voice agent has no business logic of its own — patient lookups,
# appointment booking, etc. are always delegated to the FastAPI backend.
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")
BACKEND_API_TIMEOUT_SECONDS = float(os.getenv("BACKEND_API_TIMEOUT_SECONDS", "8"))

# Phase 8: bounded retry/backoff for transient backend failures (timeouts,
# connection errors, 5xx). A live phone call can't just hang waiting on
# the backend, but a single transient blip shouldn't force the agent to
# apologise/transfer either — a couple of quick retries covers most of
# those. 4xx responses (bad request, 404, etc.) are never retried.
BACKEND_API_MAX_RETRIES = int(os.getenv("BACKEND_API_MAX_RETRIES", "2"))
BACKEND_API_RETRY_BACKOFF_SECONDS = float(
    os.getenv("BACKEND_API_RETRY_BACKOFF_SECONDS", "0.4")
)

# --- 7. MULTI-TENANT ROUTING (Phase 2) ---
# Platform-level shared secret used ONLY to ask the backend which clinic
# owns the number a caller dialled (POST /agent/resolve-tenant). Must
# match AGENT_PLATFORM_KEY on the backend.
#
# Leave it unset to disable tenant routing entirely: the agent then uses
# BACKEND_API_KEY for every call, exactly as it did before Phase 2. That
# is the safe default and the rollback switch.
BACKEND_PLATFORM_KEY = os.getenv("BACKEND_PLATFORM_KEY")

# How long to wait for the SIP participant's attributes to reveal which
# number was dialled, before giving up and using the fallback key.
#
# For inbound calls the participant often joins a beat after the agent
# job starts, so its `sip.trunkPhoneNumber` attribute isn't there yet.
# This wait happens BEFORE the session starts, so it delays the greeting —
# keep it short. It is skipped entirely when BACKEND_PLATFORM_KEY is
# unset, so single-tenant deployments pay nothing for it.
DIALED_NUMBER_WAIT_SECONDS = float(os.getenv("DIALED_NUMBER_WAIT_SECONDS", "1.5"))
DIALED_NUMBER_POLL_SECONDS = float(os.getenv("DIALED_NUMBER_POLL_SECONDS", "0.15"))
