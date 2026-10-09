import os
import certifi

# Fix for macOS SSL Certificate errors - MUST be before other imports
os.environ['SSL_CERT_FILE'] = certifi.where()

import asyncio
import logging
import re
import json
from datetime import datetime
from dotenv import load_dotenv

from livekit import agents, api
from livekit.agents import AgentSession, Agent, RoomInputOptions, ModelSettings, RunContext
from livekit.plugins import (
    openai,
    cartesia,
    deepgram,
    noise_cancellation,
    silero,
    sarvam,
)
from livekit.agents import llm
from typing import Annotated, AsyncIterable, Optional

# Load environment variables
load_dotenv(".env")

# Supported voice languages:
# English = en-IN | Hindi = hi-IN | Telugu = te-IN
# Sarvam STT detects the caller language; Sarvam TTS follows it per turn.


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound-agent")

import config
import backend_client

# TRUNK ID - Now loaded from config.py
# You can find this by running 'python setup_trunk.py --list' or checking LiveKit Dashboard 


# Maps the clinic-facing voice profile labels shown on /app/agent onto
# actual TTS voice ids. Anything unrecognised returns None, which leaves
# _build_tts on its existing env/default behaviour — a clinic cannot break
# its own audio by typing something unexpected into that field.
_VOICE_PROFILE_IDS = {
    "anushka": "anushka",
    "aravind": "aravind",
    "amartya": "amartya",
    "dhruv": "dhruv",
    "alloy": "alloy",
    "echo": "echo",
    "shimmer": "shimmer",
}


def _tts_voice_for(tenant) -> Optional[str]:
    """Best-effort map from a clinic's voice_profile label to a TTS voice id."""
    profile = (getattr(tenant, "voice_profile", None) or "").strip().lower()
    if not profile:
        return None
    for key, voice in _VOICE_PROFILE_IDS.items():
        if key in profile:
            return voice
    return None


def _build_tts(config_provider: str = None, config_voice: str = None):
    """Configure the Text-to-Speech provider based on env vars or dynamic config."""
    # Priority: Config > Env Var > Default
    provider = (config_provider or os.getenv("TTS_PROVIDER", config.DEFAULT_TTS_PROVIDER)).lower()
    
    # If using Sarvam Voice names (Anushka/Aravind), force Sarvam provider
    # bulbul:v3 speaker names; the v2 set (anushka/aravind/amartya/dhruv)
    # is kept so an org whose voice profile predates the v3 migration
    # still routes to Sarvam rather than silently falling back.
    if config_voice in ["anushka", "aravind", "amartya", "dhruv",
                        "priya", "ritu", "shreya", "kavya", "neha", "ishita",
                        "rahul", "amit", "rohan", "aditya", "varun", "kabir"]:
        provider = "sarvam"

    if provider == "cartesia":
        logger.info("Using Cartesia TTS")
        model = os.getenv("CARTESIA_TTS_MODEL", config.CARTESIA_MODEL)
        voice = os.getenv("CARTESIA_TTS_VOICE", config.CARTESIA_VOICE)
        return cartesia.TTS(model=model, voice=voice)
    
    if provider == "sarvam":
        logger.info(f"Using Sarvam TTS (Voice: {config_voice})")
        model = os.getenv("SARVAM_TTS_MODEL", config.SARVAM_MODEL)
        # Use dynamic voice or env var or default
        voice = config_voice or os.getenv("SARVAM_VOICE", "anushka")
        language = os.getenv("SARVAM_LANGUAGE", config.SARVAM_LANGUAGE)
        pace = float(os.getenv("SARVAM_TTS_PACE", "0.80"))
        # Clarity tuning for telephony:
        # - loudness > 1.0 so the voice isn't quiet/thin over a SIP call
        #   (bulbul:v2 supports 0.5-2.0; 1.3 is a noticeably louder but
        #   still natural level).
        # - enable_preprocessing so numbers/dates/abbreviations (dosages,
        #   appointment times, phone numbers) are normalized into clearly
        #   spoken form instead of read digit-by-digit or misparsed.
        # - linear16 (uncompressed PCM) instead of mp3 avoids a second
        #   lossy-compression pass before LiveKit re-encodes for the SIP
        #   trunk, which was adding audible artifacts.
        loudness = float(os.getenv("SARVAM_TTS_LOUDNESS", "1.3"))
        enable_preprocessing = os.getenv("SARVAM_TTS_PREPROCESS", "true").lower() == "true"
        audio_codec = os.getenv("SARVAM_TTS_CODEC", "linear16")
        return sarvam.TTS(
            model=model,
            speaker=voice,
            target_language_code=language,
            pace=pace,
            loudness=loudness,
            enable_preprocessing=enable_preprocessing,
            output_audio_codec=audio_codec,
        )

    if provider == "deepgram":
        logger.info("Using Deepgram TTS")
        model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-asteria-en")
        return deepgram.TTS(model=model)

    # Default to OpenAI
    logger.info(f"Using OpenAI TTS (Voice: {config_voice})")
    model = os.getenv("OPENAI_TTS_MODEL", "tts-1")
    voice = config_voice or os.getenv("OPENAI_TTS_VOICE", config.DEFAULT_TTS_VOICE)
    return openai.TTS(model=model, voice=voice)


def _build_llm(config_provider: str = None):
    """Configure the LLM provider based on config or env vars."""
    provider = (config_provider or os.getenv("LLM_PROVIDER", config.DEFAULT_LLM_PROVIDER)).lower()

    if provider == "groq":
        logger.info("Using Groq LLM")
        # 64 was tuned for llama-3.1-8b-instant, a plain instruct model
        # that emits tool-call JSON directly. openai/gpt-oss-* are
        # reasoning models: some of the completion-token budget goes to
        # internal reasoning before the actual clinic_action tool call
        # (which has 13 possible fields) is emitted. At 64 tokens total,
        # that JSON was getting truncated mid-object, which surfaced as
        # "Failed to parse tool call arguments as JSON".
        #
        # 300 was still occasionally too tight once the schema fix above
        # made tool calls succeed: on the *follow-up* turn (the model has
        # just received a clinic_action tool result and has to reason about
        # it before producing the spoken reply), gpt-oss models can spend
        # the entire 300-token budget on their internal reasoning trace and
        # get cut off before emitting any visible content or tool call --
        # the turn then silently produces nothing to say (no error is
        # raised, since a truncated-but-empty completion isn't invalid).
        # 500 leaves more headroom for that reasoning + a short reply.
        max_completion_tokens = int(
            os.getenv("GROQ_MAX_COMPLETION_TOKENS", "500")
        )
        groq_model = os.getenv("GROQ_MODEL", config.GROQ_MODEL)

        llm_kwargs = dict(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY"),
            model=groq_model,
            temperature=float(
                os.getenv("GROQ_TEMPERATURE", "0.2")
            ),
            max_completion_tokens=max_completion_tokens,
            parallel_tool_calls=False,
            max_retries=0,
            # ROOT CAUSE FIX (tool schema / malformed JSON):
            # livekit.plugins.openai.LLM defaults to OpenAI "strict" function-
            # calling mode. In strict mode every declared parameter is put in
            # the JSON schema's "required" list (optional ones are merely
            # typed as `[<type>, "null"]`), because that's what the real
            # OpenAI API expects for strict tool calls. Groq's own tool-call
            # validator enforces that "required" list literally: it expects
            # every key to be *present* in the returned arguments object
            # (even if the value is null), and rejects the call outright if a
            # key is missing -- which is exactly the
            # "did not match schema: missing properties: ..." error seen for
            # clinic_action. It also pushes the small/fast Groq models to try
            # to enumerate all 13 keys on every single tool call, which is
            # the other half of the "Failed to parse tool call arguments as
            # JSON" errors (the model runs out of budget / gets confused
            # mid-object trying to satisfy the strict schema).
            # Setting _strict_tool_schema=False switches to a plain JSON
            # schema where pydantic's own Optional[...] = None fields stay
            # truly optional (only "action" is required) and can simply be
            # omitted, matching how this same plugin already handles other
            # OpenAI-compatible providers that don't support strict mode
            # (see LLM.with_cerebras / LLM.with_sambanova upstream).
            _strict_tool_schema=False,
        )

        # The livekit openai plugin only auto-sets reasoning_effort for
        # literal OpenAI model names (gpt-5*) -- it doesn't recognise
        # Groq's "openai/gpt-oss-*" model IDs, so without this the model
        # runs at its default reasoning effort, which costs latency and
        # tokens we don't want on a live call turn. "low" keeps tool-call
        # behaviour reliable while minimizing reasoning overhead.
        if "gpt-oss" in groq_model:
            llm_kwargs["reasoning_effort"] = os.getenv("GROQ_REASONING_EFFORT", "low")

        return openai.LLM(**llm_kwargs)
    
    # Default to OpenAI
    logger.info("Using OpenAI LLM")
    return openai.LLM(model=config.DEFAULT_LLM_MODEL)



# Spoken only if a tool call leaves the line silent for over a second.
# Deliberately short and generic. NOTE: this is English, while the agent
# also serves Hindi and Telugu callers -- a language-aware phrase needs the
# detected language at tool-call time, which is not plumbed through yet.
HOLDING_PHRASE = "One moment."


class TransferFunctions(llm.ToolContext):
    """
    Step 4: one compact function schema for Groq.

    All clinic operations are routed through one tool. This keeps the tool
    definition sent to Groq much smaller than exposing six separate schemas.
    """

    def __init__(
        self,
        ctx: agents.JobContext,
        phone_number: str = None,
        api: "backend_client.TenantClient" = None,
    ):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number
        # Backend client scoped to THIS call's clinic. Per-call, never
        # module-global: one worker process can run concurrent calls for
        # different tenants, and a shared client would cross-contaminate
        # whose patient records get written.
        self.api = api
        self.call_id: Optional[str] = None
        self.patient_id: Optional[str] = None
        self.appointment_id: Optional[str] = None
        self.actions: list[str] = []
        # The AgentSession, attached after it is constructed. Needed so a
        # returning caller's stored language can be applied to TTS the
        # moment `patient_lookup` identifies them.
        self.session = None
        # PF-16: set only if the caller actually asked during this call.
        # Reported at finalisation, where the backend decides whether it
        # becomes a standing preference.
        self.speech_pace_requested: Optional[str] = None
        # Language this caller actually spoke, kept for the finalize call.
        # The backend copies it onto the patient record; see the F5 note
        # in routers/calls.py for why that rule lives there and not here.
        self.detected_language: Optional[str] = None
        # Cache of the last availability lookup so `book`/`reschedule` can
        # recover a full ISO datetime even if the LLM only echoes back a
        # short time like "10:00" instead of the exact value we returned.
        self.last_availability: Optional[dict] = None
        # GAP-T9. Whether this clinic has rules the *backend* evaluates,
        # and whether the caller's number matched a patient. Both are set
        # by `entrypoint`; see `maybe_escalate` for what they gate.
        self.escalation_enabled: bool = False
        self.caller_is_known: Optional[bool] = None
        self.escalated: bool = False

    def _speak_slower(self) -> bool:
        """
        Drop the speaking pace for the rest of this call.

        Shared by the caller-asked path and the returning-patient path so
        there is one definition of what "slower" does. Returns whether it
        took effect: not every configured TTS provider exposes pace, and
        that must degrade quietly rather than derail the call.
        """
        if self.session is None:
            return False
        try:
            self.session.tts.update_options(pace=config.SLOW_TTS_PACE)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not slow the speaking pace: %s", e)
            return False
        return True

    def _note_action(self, action: str) -> None:
        if not self.actions or self.actions[-1] != action:
            self.actions.append(action)

    def _speak_language(self, language: Optional[str]) -> None:
        """
        Switch TTS to a language we already know this caller speaks.

        Used when `patient_lookup` matches a returning patient, so the
        rest of the call is spoken in their language instead of waiting
        for detection to catch up turn by turn.

        Never fatal, and never widens the language set: the same three
        values the live detection path is clamped to are the only ones
        allowed through, so a bad stored value cannot reach Sarvam.
        """
        if language not in ("en-IN", "hi-IN", "te-IN"):
            return
        if language == self.detected_language:
            return
        session = self.session
        if session is None:
            return
        try:
            session.tts.update_options(target_language_code=language)
            self.detected_language = language
            logger.info(f"Applied caller's stored language: {language}")
        except Exception as e:  # noqa: BLE001 - a live call must not die over a voice option
            logger.warning(f"Could not apply stored language {language}: {e}")

    def _resolve_start_at(
        self, start_at: Optional[str], date: Optional[str], doctor_id: Optional[str]
    ) -> Optional[str]:
        """
        Groq's small model frequently passes back a bare time (e.g. "10:00")
        instead of the full ISO datetime a slot actually has, causing the
        backend's 422 "input is too short" error. Recover the real value:
        1. If it already looks like a full datetime, use it as-is.
        2. Else try to match it against the slots from the last
           `availability` call for this doctor (most reliable — exact
           value the backend gave us).
        3. Else fall back to combining it with `date`.
        """
        if not start_at:
            return start_at
        if "T" in start_at and len(start_at) >= 16:
            return start_at

        if self.last_availability and self.last_availability.get("doctor_id") == doctor_id:
            for slot in self.last_availability.get("slots", []):
                slot_start = str(slot.get("start_at", ""))
                if slot_start[-8:-3] == start_at or slot_start.endswith(start_at):
                    return slot_start

        fallback_date = date or (self.last_availability or {}).get("date")
        if fallback_date:
            time_part = start_at if len(start_at) > 5 else f"{start_at}:00"
            return f"{fallback_date}T{time_part}"

        return start_at

    @llm.function_tool(
        description=(
            "Clinic action router. action must be one of: "
            "patient_lookup, patient_register, doctors, availability, "
            "appointments, book, reschedule, cancel, clinic_info, services, "
            "faq, medical_history, refill_request, transfer. "
            "refill_request RECORDS a repeat-prescription request for the "
            "clinic to review; it never approves one. "
            "Only pass the parameters that action actually needs; omit every "
            "other parameter entirely (do not send null placeholders for "
            "them). Never invent a query, name, phone, dob, email, "
            "patient_id, doctor_id, appointment_id, date, or start_at that "
            "the caller has not actually given you. email is optional for "
            "patient_register — only pass it if the caller volunteers an "
            "email address themselves; never ask for one unprompted."
        )
    )
    async def clinic_action(
        self,
        run_ctx: RunContext,
        action: str,
        query: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        dob: Optional[str] = None,
        email: Optional[str] = None,
        patient_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        date: Optional[str] = None,
        start_at: Optional[str] = None,
        reason: Optional[str] = None,
        question: Optional[str] = None,
        medication: Optional[str] = None,
        destination: Optional[str] = None,
    ):
        """
        Thin wrapper around the real implementation.

        Its only job is the holding phrase. A booking or availability
        lookup is two LLM passes plus an HTTP round trip, which measured
        3-5s of dead air on a real call -- long enough that callers say
        "hello?". `with_filler` speaks only after the session has been
        continuously idle for `delay`, so a fast action stays silent and
        it can never talk over the caller or over the agent's own reply.
        It fires at most once per tool call (interval=None).

        `run_ctx` is injected by livekit-agents on type, not name, and is
        excluded from the schema sent to Groq -- the one-tool schema stays
        exactly as small as it was.
        """
        async with run_ctx.with_filler(HOLDING_PHRASE, delay=1.2):
            return await self._clinic_action_impl(
                action=action,
                query=query,
                name=name,
                phone=phone,
                dob=dob,
                email=email,
                patient_id=patient_id,
                doctor_id=doctor_id,
                appointment_id=appointment_id,
                date=date,
                start_at=start_at,
                reason=reason,
                question=question,
                medication=medication,
                destination=destination,
            )

    async def _clinic_action_impl(
        self,
        action: str,
        query: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        dob: Optional[str] = None,
        email: Optional[str] = None,
        patient_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        date: Optional[str] = None,
        start_at: Optional[str] = None,
        reason: Optional[str] = None,
        question: Optional[str] = None,
        medication: Optional[str] = None,
        destination: Optional[str] = None,
    ):
        action = (action or "").lower().strip()

        # -------------------- PATIENT --------------------
        if action == "patient_lookup":
            # The LLM sometimes sends the caller's identifying info via the
            # more specific `name`/`phone` fields instead of `query` (both
            # are valid per the schema). Accept either shape so a caller who
            # has already given their name and phone number isn't asked
            # again just because the model picked the other field.
            lookup_value = query or phone or name
            if not lookup_value:
                return {"error": "phone or full name is required"}

            logger.info(f"Looking up patient: {lookup_value}")
            try:
                matches = await self.api.search_patients(lookup_value)
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            if len(matches) == 1:
                self.patient_id = matches[0].get("id")
                # A returning caller we already know the language of:
                # switch now rather than waiting for detection to catch
                # up over the next turn or two.
                self._speak_language(matches[0].get("preferred_language"))
                # PF-16: likewise apply a stored slower pace as soon as we
                # know who this is, rather than making them ask again.
                if matches[0].get("speech_pace_preference") == "slow":
                    self._speak_slower()

            # Strip before the LLM sees it. This dict goes straight into a
            # live prompt and every field costs tokens against the same
            # Groq TPM budget as the reply — and the model has no use for
            # either, since both are handled by the TTS layer rather than
            # by anything it can say.
            for match in matches:
                match.pop("preferred_language", None)
                match.pop("speech_pace_preference", None)

            return {"found": bool(matches), "patients": matches}

        if action == "patient_register":
            if not name or not phone:
                return {"error": "name and phone are required"}

            logger.info(f"Registering patient: {name}")
            try:
                patient = await self.api.register_patient(
                    name=name,
                    phone=phone,
                    dob=dob or None,
                    email=email or None,
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self.patient_id = patient.get("id")
            self._note_action("Registered new patient")
            return patient

        # -------------------- DOCTORS --------------------
        if action == "doctors":
            logger.info("Listing doctors")
            try:
                return {"doctors": await self.api.list_doctors()}
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

        if action == "availability":
            if not doctor_id or not date:
                return {"error": "doctor_id and date are required"}

            logger.info(f"Checking availability: doctor={doctor_id} date={date}")
            try:
                result = await self.api.get_availability(
                    doctor_id=doctor_id,
                    date=date,
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self.last_availability = {
                "doctor_id": doctor_id,
                "date": date,
                "slots": result.get("slots", []) if isinstance(result, dict) else [],
            }
            return result

        # -------------------- APPOINTMENTS --------------------
        if action == "appointments":
            if not patient_id:
                return {"error": "patient_id is required"}

            logger.info(f"Finding appointments for patient={patient_id}")
            try:
                return {
                    "appointments": await self.api.list_patient_appointments(
                        patient_id=patient_id
                    )
                }
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

        if action == "book":
            if not patient_id or not doctor_id or not start_at:
                return {
                    "error": "patient_id, doctor_id and start_at are required"
                }

            start_at = self._resolve_start_at(start_at, date, doctor_id)

            logger.info(
                f"Booking appointment: patient={patient_id}, "
                f"doctor={doctor_id}, start_at={start_at}"
            )
            try:
                appt = await self.api.book_appointment(
                    patient_id=patient_id,
                    doctor_id=doctor_id,
                    start_at=start_at,
                    reason=reason,
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self.patient_id = patient_id
            self.appointment_id = appt.get("id")
            self._note_action("Booked appointment")
            return appt

        if action == "reschedule":
            if not appointment_id or not start_at:
                return {"error": "appointment_id and start_at are required"}

            start_at = self._resolve_start_at(start_at, date, doctor_id)

            try:
                appt = await self.api.reschedule_appointment(
                    appointment_id=appointment_id,
                    start_at=start_at,
                    doctor_id=doctor_id,
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self.appointment_id = appointment_id
            self._note_action("Rescheduled appointment")
            return appt

        if action == "cancel":
            if not appointment_id:
                return {"error": "appointment_id is required"}

            try:
                result = await self.api.cancel_appointment(
                    appointment_id=appointment_id
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self.appointment_id = appointment_id
            self._note_action("Cancelled appointment")
            return result

        # -------------------- CLINIC INFO --------------------
        if action == "clinic_info":
            logger.info("Fetching clinic info")
            try:
                result = await self.api.get_clinic_info()
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self._note_action("Answered clinic info question")
            return result

        if action == "services":
            logger.info("Listing services")
            try:
                services = await self.api.list_services()
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self._note_action("Answered services question")
            return {"services": services}

        if action == "faq":
            if not question:
                return {"error": "question is required"}

            logger.info(f"Knowledge-base question: {question}")
            try:
                result = await self.api.ask_knowledge_base(
                    question=question
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self._note_action("Answered FAQ")
            return result

        # -------------------- MEDICAL HISTORY --------------------
        if action == "medical_history":
            if not patient_id:
                return {"error": "patient_id is required"}

            logger.info(f"Fetching medical history: patient={patient_id}")
            try:
                history = await self.api.get_patient_history(
                    patient_id=patient_id
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self._note_action("Reviewed patient medical history")
            return history

        # -------------------- REFILL REQUEST --------------------
        # Records that the caller asked for a repeat prescription. It does
        # NOT approve one: the backend only ever writes a `pending` row
        # from this route, and approving is a staff-only endpoint this
        # agent cannot reach. Never tell the caller a refill is approved,
        # ready, or safe to take.
        if action == "refill_request":
            if not self.patient_id:
                # An anonymous medication request is not actionable by
                # staff, so identify the caller first rather than queueing
                # something nobody can resolve.
                return {
                    "error": (
                        "Identify the patient with patient_lookup before "
                        "recording a refill request."
                    )
                }
            wanted = (medication or query or "").strip()
            if not wanted:
                return {"error": "medication is required"}

            try:
                created = await self.api.create_refill_request(
                    patient_id=self.patient_id,
                    medication=wanted,
                    note=reason,
                    call_id=self.call_id,
                )
            except backend_client.BackendAPIError as e:
                return {"error": str(e)}

            self._note_action(f"Recorded refill request: {wanted}")
            # Deliberately narrow. The model is told it was written down
            # and nothing more, so it has no material with which to imply
            # the refill is granted.
            return {
                "recorded": True,
                "medication": created.get("medication", wanted),
                "status": "pending clinic review",
            }

        # -------------------- TRANSFER --------------------
        if action == "speak_slower":
            # Deliberately not a backend call: how the agent sounds is a
            # property of this session, not of the clinic's records. The
            # backend hears about it once, at the end, and decides whether
            # it becomes a standing preference.
            if not self._speak_slower():
                return "Unable to change the speaking pace right now."
            # Recorded only because the caller asked. The returning-patient
            # path above deliberately does NOT set this: re-reporting a
            # preference we already hold would be noise, not evidence.
            self.speech_pace_requested = "slow"
            self._note_action("Slowed speaking pace at the caller's request")
            return "Speaking pace slowed for the rest of this call."

        if action == "transfer":
            return await self._do_transfer(destination)

        return {
            "error": (
                "Unknown action. Use patient_lookup, patient_register, doctors, "
                "availability, appointments, book, reschedule, cancel, "
                "clinic_info, services, faq, medical_history, refill_request, "
                "or transfer."
            )
        }

    async def maybe_escalate(self, utterance: Optional[str]) -> bool:
        """
        Ask the backend whether this turn should put the caller through
        to a person, and do it if so (GAP-T9).

        WHY THIS IS NOT PROMPT WORK

        A clinic's escalation rules used to be prose in the system
        prompt, matched by the model. That is unrepeatable -- two
        identical calls can resolve differently -- and unauditable,
        because no rule ever "applied": a model produced a transfer and
        the reason lived in its weights. Rules with a structured
        condition are now decided by `app/escalation_service.py`, and
        this is where that verdict reaches the call.

        The agent still holds no business logic. It sends what the caller
        said; which rule fires, and where it transfers to, are entirely
        the backend's answer.

        Four properties worth keeping:

          * **It costs nothing when unconfigured.** `escalation_enabled`
            comes from resolve-tenant, and the backend's reply switches
            it off for the rest of the call the moment it reports the
            clinic has no such rules.

          * **It fires once.** `escalated` latches, so a caller who says
            a keyword three times is transferred once rather than having
            a second transfer issued against a call already leaving.

          * **It never breaks the call.** A backend that is down or slow
            leaves the call exactly as it was -- the model's own
            escalation path and the EMERGENCY block are both still there,
            which is the layering `escalation_service` describes.

          * **The reason is recorded.** `_note_action` puts the rule's
            title into the call's action list, which is the same list the
            AI-7 judge reads to detect a claim with nothing behind it.
            That is the auditability the prompt-only version could not
            offer.
        """
        if not self.escalation_enabled or self.escalated or self.api is None:
            return False

        try:
            verdict = await self.api.check_escalation(
                utterance=utterance,
                caller_is_known=self.caller_is_known,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Escalation check skipped: %s", e)
            return False

        if not isinstance(verdict, dict):
            return False

        if not verdict.get("has_rules", True):
            # The clinic has none. Stop asking for the rest of the call --
            # this is what makes the single-tenant fallback path, which
            # never resolves a tenant, cost one request instead of one
            # per turn.
            self.escalation_enabled = False
            return False

        if not verdict.get("escalate"):
            return False

        self.escalated = True
        title = verdict.get("rule_title") or "escalation rule"
        matched = verdict.get("matched_on")
        logger.info(
            "Escalation rule %r fired (%s%s)", title,
            verdict.get("condition_type"),
            f", matched {matched!r}" if matched else "",
        )
        result = await self._do_transfer(verdict.get("destination"))
        # Named, not just "transferred": afterwards somebody has to be
        # able to answer *which* rule did this, and that was precisely
        # what the model-only path could never say.
        self._note_action(f"Escalated by rule: {title}")
        if not str(result).startswith("Transfer initiated"):
            # The transfer did not take. Unlatch so a later turn can try
            # again rather than leaving the caller with the AI on a call
            # the clinic said should have reached a person.
            self.escalated = False
            logger.warning("Escalation transfer did not complete: %s", result)
            return False
        return True

    async def _do_transfer(self, destination: Optional[str]) -> str:
        """
        Put the caller through to a person.

        Extracted from the `transfer` action for GAP-T9 so the
        server-evaluated escalation path performs a transfer the *same*
        way the model-requested one does. A second copy of this would be
        a second place for SIP addressing to drift, and the two paths
        would eventually behave differently for no stated reason.
        """
        if destination is None:
            destination = config.DEFAULT_TRANSFER_NUMBER
            if not destination:
                return "Error: No default transfer number configured."

        if "@" not in destination:
            if config.SIP_DOMAIN:
                clean_dest = destination.replace("tel:", "").replace("sip:", "")
                destination = f"sip:{clean_dest}@{config.SIP_DOMAIN}"
            elif not destination.startswith(("tel:", "sip:")):
                destination = f"tel:{destination}"
        elif not destination.startswith("sip:"):
            destination = f"sip:{destination}"

        participant_identity = None

        if self.phone_number:
            participant_identity = f"sip_{self.phone_number}"
        else:
            for p in self.ctx.room.remote_participants.values():
                participant_identity = p.identity
                break

        if not participant_identity:
            return "Failed to transfer: caller could not be identified."

        try:
            await self.ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=self.ctx.room.name,
                    participant_identity=participant_identity,
                    transfer_to=destination,
                    play_dialtone=False,
                )
            )
            self._note_action("Transferred to staff")
            return "Transfer initiated successfully."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return f"Error executing transfer: {e}"


def _normalize_agent_language(language: str | None) -> str:
    """
    Normalize Sarvam's detected language to the three languages supported
    by this receptionist.

    IMPORTANT:
    Sarvam can occasionally misclassify short Indian-language/English
    utterances as another language (for example Bengali or Malayalam).
    Those languages must NEVER be passed to Sarvam TTS.
    """
    value = (language or "").lower().replace("_", "-").strip()

    if value.startswith("hi"):
        return "hi-IN"
    if value.startswith("te"):
        return "te-IN"
    if value.startswith("en"):
        return "en-IN"

    # Any unsupported language is deliberately forced to English.
    return "en-IN"


def _detect_supported_caller_language(
    language: str | None,
    transcript: str | None,
) -> str:
    """
    Return ONLY en-IN, hi-IN, or te-IN.

    We use the STT language when it is one of the supported languages.
    For short/ambiguous utterances, we inspect the actual script as a
    safety fallback. This prevents Bengali/Malayalam/Tamil/etc. detection
    from changing the TTS language.

    Script fallback:
      Devanagari -> Hindi
      Telugu script -> Telugu
      everything else -> English
    """
    detected = _normalize_agent_language(language)
    raw_language = (language or "").lower().replace("_", "-").strip()
    text = transcript or ""

    # If Sarvam explicitly detected one of our supported languages,
    # trust it. This also supports Hindi/Telugu spoken in Latin script.
    if raw_language.startswith(("hi", "te", "en")):
        return detected

    # Sarvam detected something outside our supported set.
    # Use the actual transcript script before falling back to English.
    if re.search(r"[\u0900-\u097F]", text):
        return "hi-IN"

    if re.search(r"[\u0C00-\u0C7F]", text):
        return "te-IN"

    # Unsupported script/language or Latin text with an unsupported
    # language label: keep TTS safely in English.
    return "en-IN"


def _extract_transcript(session: "AgentSession") -> list[dict]:
    """
    Best-effort extraction of the conversation so far from the agent
    session's chat history, in the shape the backend's transcript/bulk
    endpoint expects: {"who": "ai" | "patient", "text": str}.

    Defensive against minor API differences across livekit-agents
    versions (chat history items can be plain objects or dicts) —
    never raises; returns [] if history isn't available.
    """
    messages: list[dict] = []
    try:
        history = getattr(session, "history", None)
        items = getattr(history, "items", None)
        if items is None and hasattr(history, "to_dict"):
            items = history.to_dict().get("items", [])
        for item in items or []:
            if isinstance(item, dict):
                role = item.get("role")
                content = item.get("content")
            else:
                role = getattr(item, "role", None)
                content = getattr(item, "content", None)

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                text = " ".join(str(c) for c in content if isinstance(c, str)).strip()
            elif isinstance(content, str):
                text = content.strip()
            else:
                text = ""

            if not text:
                continue

            messages.append({"who": "ai" if role == "assistant" else "patient", "text": text})
    except Exception as e:
        logger.warning(f"Could not extract transcript from session history: {e}")
    return messages


def _extract_dialed_number(ctx: agents.JobContext, config_dict: dict) -> Optional[str]:
    """
    The number the caller DIALLED (not the number they called from) —
    i.e. which of the clinic's lines rang. This is what identifies the
    tenant.

    Checked in order of reliability:
      1. Explicit metadata from the dispatcher. A LiveKit SIP inbound
         dispatch rule, or the dashboard's outbound dispatch, can state
         it outright, which needs no waiting.
      2. The SIP participant's `sip.trunkPhoneNumber` attribute, which is
         the trunk number the call arrived on.

    Deliberately does NOT fall back to the caller's own number: routing a
    call to whichever clinic happened to match the CALLER's number would
    put a patient through to the wrong practice.
    """
    for key in ("dialed_number", "to_number", "called_number", "trunk_phone_number"):
        value = config_dict.get(key)
        if value:
            return str(value)

    try:
        for p in ctx.room.remote_participants.values():
            attrs = getattr(p, "attributes", None) or {}
            number = attrs.get("sip.trunkPhoneNumber")
            if number:
                return number
    except Exception:
        pass
    return None


async def _await_dialed_number(ctx: agents.JobContext, config_dict: dict) -> Optional[str]:
    """
    Same as `_extract_dialed_number`, but waits briefly for the SIP
    participant to appear.

    For inbound calls the participant usually joins a moment after the
    agent job starts, so its attributes are not populated yet at
    entrypoint time (this is the same lag `_backfill_caller_phone`
    exists to work around). Unlike that backfill, tenant identity is
    needed BEFORE the session starts, so this one has to wait — briefly,
    and only when tenant routing is actually enabled.
    """
    number = _extract_dialed_number(ctx, config_dict)
    if number or not config.BACKEND_PLATFORM_KEY:
        return number

    deadline = config.DIALED_NUMBER_WAIT_SECONDS
    waited = 0.0
    while waited < deadline:
        await asyncio.sleep(config.DIALED_NUMBER_POLL_SECONDS)
        waited += config.DIALED_NUMBER_POLL_SECONDS
        number = _extract_dialed_number(ctx, config_dict)
        if number:
            logger.info(f"Dialled number resolved after {waited:.2f}s: {number}")
            return number
    return None


def _extract_caller_phone(ctx: agents.JobContext, fallback: Optional[str]) -> Optional[str]:
    """Best-effort caller number: known (outbound) number, else pulled
    from the SIP participant's attributes/identity (inbound)."""
    if fallback:
        return fallback
    try:
        for p in ctx.room.remote_participants.values():
            attrs = getattr(p, "attributes", None) or {}
            phone = attrs.get("sip.phoneNumber") or attrs.get("sip.trunkPhoneNumber")
            # Only ever hand back a real string. Under `agent.py console` the
            # room and its participants are mocks, so these lookups return a
            # truthy MagicMock rather than raising — which then reached the
            # backend as the caller_phone and failed the whole call-log insert
            # with "Object of type MagicMock is not JSON serializable".
            if isinstance(phone, str) and phone:
                return phone
            identity = getattr(p, "identity", None)
            if isinstance(identity, str) and identity.startswith("sip_"):
                return identity[len("sip_"):]
    except Exception:
        pass
    return None


async def _backfill_caller_phone(ctx: agents.JobContext, fnc_ctx: "TransferFunctions") -> None:
    """
    For inbound SIP calls, the call log is created (see entrypoint) before
    session.start() has connected the SIP participant into the room, so its
    'sip.phoneNumber' attribute usually isn't set yet at that point and
    _extract_caller_phone comes back empty. This polls briefly in the
    background for that attribute to appear and, once it does, patches it
    onto the already-created call log via
    the tenant client's update_call_caller_phone — a display-only field, so this
    never touches dialling, SIP participant creation, transfers, or the
    call's status. No-ops (and gives up) if the call already has a number,
    has no call_id yet, or ends before a number ever appears.
    """
    if not fnc_ctx.call_id:
        return
    for _ in range(10):  # poll for up to ~5s; the room/session is otherwise unaffected
        await asyncio.sleep(0.5)
        if not fnc_ctx.call_id:
            return  # call already finalized/torn down
        phone = _extract_caller_phone(ctx, None)
        if phone:
            try:
                await fnc_ctx.api.update_call_caller_phone(fnc_ctx.call_id, phone)
            except backend_client.BackendAPIError as e:
                logger.warning(f"Could not backfill caller_phone for call={fnc_ctx.call_id}: {e}")
            return


# "Smart"/typographic punctuation some LLMs like to emit (curly quotes,
# en/em dashes, the non-breaking hyphen, ellipsis char, non-breaking
# space) mapped to their plain-ASCII equivalents.
#
# Why this matters: Sarvam's TTS streams text word-by-word and rejects
# any chunk that has no character belonging to an allowed language/
# script, returning "400: Text must contain at least one character from
# the allowed languages." A character such as U+2011 (non-breaking
# hyphen, e.g. in "check‑up") is not a normal "word" character, so it
# can be flushed to the TTS websocket as its own isolated chunk -
# tripping that check and silently dropping that part of the agent's
# spoken reply (the caller hears a gap; the transcript still shows the
# full text, which is what makes this failure mode confusing to spot).
# Normalizing to ASCII punctuation before the text reaches the TTS
# pipeline avoids the failure mode entirely.
_SMART_PUNCTUATION_MAP = {
    "\u2011": "-",   # non-breaking hyphen
    "\u2012": "-",   # figure dash
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u2018": "'",   # left single quotation mark
    "\u2019": "'",   # right single quotation mark / apostrophe
    "\u201c": '"',   # left double quotation mark
    "\u201d": '"',   # right double quotation mark
    "\u2026": "...", # horizontal ellipsis
    "\u00a0": " ",   # non-breaking space
}
_SMART_PUNCTUATION_RE = re.compile("|".join(re.escape(c) for c in _SMART_PUNCTUATION_MAP))


def _sanitize_tts_text(text: str) -> str:
    """Replace typographic punctuation with plain ASCII before synthesis."""
    if not text:
        return text
    return _SMART_PUNCTUATION_RE.sub(lambda m: _SMART_PUNCTUATION_MAP[m.group(0)], text)


class OutboundAssistant(Agent):
    """
    An AI agent tailored for outbound calls.
    Attempts to be helpful and concise.
    """
    def __init__(self, tools: list, instructions: Optional[str] = None) -> None:
        # The fallback is `build_instructions()`, not a second prompt kept
        # in configuration. There used to be one there, and it had drifted
        # badly: it named six tools that no longer exist (they were
        # replaced by the single `clinic_action`), offered English and
        # Hindi when the pipeline handles Telugu too, and carried no
        # emergency block at all. Unreachable in practice, but a fallback
        # that would produce a broken and unsafe agent is worse than no
        # fallback -- and pointing it at the real builder costs nothing.
        super().__init__(
            instructions=instructions or build_instructions(),
            tools=tools,
        )

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ):
        """
        Sanitize LLM output before it reaches the TTS engine.

        See `_sanitize_tts_text` above for why this is needed: without
        it, Sarvam TTS can reject an entire streamed reply (and retry it
        two more times, identically failing) whenever the LLM emits a
        typographic character that ends up isolated in its own chunk.
        """

        async def _sanitized() -> AsyncIterable[str]:
            async for chunk in text:
                cleaned = _sanitize_tts_text(chunk)
                if cleaned:
                    yield cleaned

        async for frame in Agent.default.tts_node(self, _sanitized(), model_settings):
            yield frame




def build_instructions(recognised_name: Optional[str] = None) -> str:
    """
    The system prompt every live call actually receives.

    Module level, and returning a value rather than assigning one, so it
    can be asserted on. Two things in here are safety-relevant enough that
    "it looked right when I read it" is not good enough:

      * the EMERGENCY block, which outranks everything else and did not
        exist at all until PF-03;
      * the caller-recognition note, which tells the model the name it
        just greeted someone by is a guess drawn from a phone number.

    Kept deliberately small. Groq counts prompt tokens against the same
    TPM budget as the reply, so every line here is paid for on every turn
    of every call.
    """
    instructions = """
You are MediCOR, a clinic phone receptionist.

LANGUAGE:
- Support ONLY English, Hindi, Telugu.
- English by default; Hindi -> Hindi; Telugu -> Telugu.
- Never speak another language.
- Keep replies to 1-2 short sentences and speak clearly/slowly.
- Use plain ASCII punctuation only (straight quotes ", ' and a simple
  hyphen -). Avoid parenthetical asides/examples.

EMERGENCY (this outranks every other instruction here):
- If the caller describes a possible medical emergency - chest pain,
  trouble breathing, severe or uncontrolled bleeding, stroke signs
  (face droop, arm weakness, slurred speech), collapse or
  unconsciousness, a serious injury, poisoning or overdose, or self-harm
  - stop whatever you were doing.
- Do not book, look up, or continue any other task.
- Say this to them, then offer to transfer immediately:
    "EMERGENCY_LINE"
- Say the emergency number one digit at a time, so it cannot be heard as
  an ordinary number.
- Do not diagnose, do not ask screening questions, and do not advise on
  what to do while waiting. Say the line and offer the transfer.
- If in any doubt at all, treat it as an emergency. Being wrong here
  costs a transfer; being wrong the other way costs far more.

ACCESSIBILITY (honour these immediately, without asking why):
- "slower", "speak slowly", "I cannot follow you" -> call clinic_action
  with action "speak_slower", then continue.
- "say that again", "repeat", "sorry?" -> repeat your last sentence in
  simpler words. Do not call a tool for this.
- "a person", "a human", "someone real" -> transfer straight away. Do not
  try to help first and do not ask them to explain.

RULES:
- Never guess clinic, patient, doctor, appointment, date or time data.
- Never give medical advice.
- Use one clinic_action tool call at a time.
- Do not use tools for greetings.
- Never say IDs or raw timestamps.
- Transfer if requested, outside scope, or an ESCALATION rule applies.

PATIENT:
- patient_lookup first for an existing patient.
- patient_register only after confirming name and phone.

APPOINTMENT:
- Ask specialty/reason and preferred day before checking availability.
- Use doctors, then availability.
- Offer 2-3 returned slots; book only the exact chosen slot.
- Find existing appointments before reschedule/cancel.
- Require explicit cancellation confirmation.

INFO:
- clinic_info for clinic details; services for services/fees; faq for other clinic questions.
- medical_history only when explicitly requested.

REFILLS:
- refill_request only RECORDS the request for the clinic to review.
- Identify the patient first, then confirm the medicine name back to them.
- Say it has been passed to the clinic. Never say it is approved, ready,
  collected, or safe to take.
- Never advise on dose, timing, or whether to keep taking anything.
- Anything clinical beyond recording the name: transfer.

If the caller says goodbye, say goodbye.
"""

    # The emergency wording is configuration, not code: the number is
    # territory-specific and the sentence is a clinical decision that
    # belongs to the clinic. Substituted rather than interpolated with an
    # f-string because the prompt is full of braces the model needs to
    # see literally.
    instructions = instructions.replace(
        "EMERGENCY_LINE",
        config.EMERGENCY_INSTRUCTION.replace("{number}", config.EMERGENCY_NUMBER),
    )

    # The greeting opens by asking whether it is this person. The model
    # has to know that is a guess drawn from the calling number, not an
    # established fact -- otherwise it proceeds as though the caller has
    # been identified, which for a shared handset is how one family member
    # ends up hearing another's appointments.
    if recognised_name:
        instructions += (
            f"\n\nCALLER:\n"
            f"- This number usually belongs to {recognised_name}. That is a guess,\n"
            f"  not a confirmed identity.\n"
            f"- Your greeting already asked whether it is them. If they say yes,\n"
            f"  still run patient_lookup before using any of their details.\n"
            f"- If they say no, or are someone else, ignore the name completely.\n"
        )
    return instructions


async def entrypoint(ctx: agents.JobContext):
    """
    Main entrypoint for the agent.
    
    For outbound calls:
    1. Checks for 'phone_number' in the job metadata.
    2. Connects to the room.
    3. Initiates the SIP call to the phone number.
    4. Waits for answer before speaking.
    """
    logger.info(f"Connecting to room: {ctx.room.name}")

    # parse the phone number AND config from the metadata
    phone_number = None
    config_dict = {}
    
    # Check Job Metadata (Legacy/Dispatch)
    try:
        if ctx.job.metadata:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number")
            config_dict = data
    except Exception:
        pass
        
    # Check Room Metadata (Dashboard/Route.ts) - Overrides Job Metadata if present
    try:
        if ctx.room.metadata:
            data = json.loads(ctx.room.metadata)
            if data.get("phone_number"):
                phone_number = data.get("phone_number")
            config_dict.update(data) # Merge configs
    except Exception:
        logger.warning("No valid JSON metadata found in Room.")

    # --- Which clinic is this call for? ------------------------------
    # Before Phase 2 the answer was always "whichever clinic owns the
    # single BACKEND_API_KEY in .env". Now the dialled number decides,
    # and the backend hands back a short-lived org-scoped token.
    #
    # Every failure path here falls back to that same env key, so a call
    # is never lost to a routing problem — it just gets handled by the
    # default clinic, exactly as it would have before.
    dialed_number = await _await_dialed_number(ctx, config_dict)
    tenant = await backend_client.resolve_tenant(dialed_number)
    if tenant is None:
        tenant = backend_client.fallback_tenant()
    logger.info(f"Tenant for this call: {tenant}")

    # Initialize function context
    fnc_ctx = TransferFunctions(ctx, phone_number, api=tenant.client)

    # Declared up front (and guarded in _finalize_call below) so that if
    # session construction/start fails before this is assigned, the
    # shutdown-time finalizer doesn't crash on a NameError.
    session = None

    # --- Call intelligence: create the call log up front, and register a
    # shutdown-time finalizer that saves the full transcript + final
    # metadata (duration, status, patient/appointment association) no
    # matter how the call ends (hangup, transfer, dial failure, error).
    # All summarization/sentiment logic runs server-side in the backend.
    direction = "outbound" if phone_number else "inbound"
    started_at = datetime.now().astimezone()
    call_state = {"status": "in_progress"}
    caller_phone = _extract_caller_phone(ctx, phone_number)

    try:
        call = await tenant.client.create_call_log(
            direction=direction,
            status="in_progress",
            caller_phone=caller_phone,
            started_at=started_at.isoformat(),
        )
        fnc_ctx.call_id = call.get("id")
        if not caller_phone:
            # Fire-and-forget: don't block call setup on this. See
            # _backfill_caller_phone's docstring — display-only, never
            # touches dialling/SIP/transfer/call status.
            asyncio.create_task(_backfill_caller_phone(ctx, fnc_ctx))
    except backend_client.BackendAPIError as e:
        logger.warning(f"Could not create call log: {e}")

    async def _finalize_call():
        if not fnc_ctx.call_id:
            return
        ended_at = datetime.now().astimezone()
        duration_seconds = int((ended_at - started_at).total_seconds())
        final_status = "failed" if call_state["status"] == "failed" else "completed"

        try:
            transcript = _extract_transcript(session) if session is not None else []
            if transcript:
                await tenant.client.save_transcript_bulk(fnc_ctx.call_id, transcript)
        except backend_client.BackendAPIError as e:
            logger.warning(f"Could not save transcript for call={fnc_ctx.call_id}: {e}")

        try:
            await tenant.client.finalize_call(
                fnc_ctx.call_id,
                status=final_status,
                duration_seconds=duration_seconds,
                ended_at=ended_at.isoformat(),
                patient_id=fnc_ctx.patient_id,
                appointment_id=fnc_ctx.appointment_id,
                caller_phone=_extract_caller_phone(ctx, caller_phone),
                actions_taken="\n".join(fnc_ctx.actions) if fnc_ctx.actions else None,
                # None when the caller never spoke (a silent or failed
                # call), which is exactly when we should NOT overwrite a
                # patient's known language with a guess.
                detected_language=fnc_ctx.detected_language,
                speech_pace_requested=fnc_ctx.speech_pace_requested,
            )
        except backend_client.BackendAPIError as e:
            logger.warning(f"Could not finalize call={fnc_ctx.call_id}: {e}")

    async def _finalize_call_then_close():
        # LiveKit runs all registered shutdown callbacks concurrently via
        # asyncio.gather(), not in registration order. The tenant client's
        # aclose()
        # is effectively synchronous (it just closes an idle connection
        # pool) so if it were registered as its own callback it would win
        # the race and close the shared httpx client while _finalize_call's
        # HTTP requests were still in flight -- causing
        # "Cannot send a request, as the client has been closed." Doing
        # both steps sequentially in one callback guarantees the finalize
        # HTTP calls complete before the client is torn down.
        try:
            await _finalize_call()
        finally:
            await tenant.client.aclose()

    if hasattr(ctx, "add_shutdown_callback"):
        ctx.add_shutdown_callback(_finalize_call_then_close)

    # PF-02: recognise a returning caller from the number they rang from.
    #
    # The caller's number was already captured -- written to the call log,
    # and backfilled if it arrived late -- but it was explicitly
    # display-only and never used to identify anyone. So a patient who had
    # called ten times was still asked who they were on the eleventh, from
    # a number the clinic already held. The patient search endpoint has
    # always matched on the phone field, so both halves existed and were
    # simply never joined.
    #
    # Three rules keep this safe:
    #
    #  * Exactly one match, or nothing happens. Several matches means the
    #    number is shared, and guessing is worse than asking.
    #
    #  * It greets, it does not authenticate. `patient_id` is deliberately
    #    NOT set here, and the normal patient lookup still runs. Handsets
    #    are shared within a household; treating a phone number as proof
    #    of identity would hand one family member another's appointments.
    #
    #  * First name only. Reading a full name aloud to whoever picked up
    #    would disclose that a specific named person is a patient of this
    #    clinic -- to someone who has not been identified at all.
    #
    # Never fatal: a call must not fail because a courtesy lookup did.
    recognised_name: Optional[str] = None
    recognised_language: Optional[str] = None
    recognised_pace: Optional[str] = None
    try:
        if caller_phone:
            matches = await tenant.client.search_patients(caller_phone)
            if len(matches) == 1:
                full_name = (matches[0].get("name") or "").strip()
                recognised_name = full_name.split()[0] if full_name else None
                pref = matches[0].get("preferred_language")
                if pref in ("en-IN", "hi-IN", "te-IN"):
                    recognised_language = pref
                if matches[0].get("speech_pace_preference") == "slow":
                    recognised_pace = "slow"
                logger.info("Caller number matched one patient; greeting by first name")
            elif len(matches) > 1:
                logger.info(
                    "Caller number matches %d patients - not personalising", len(matches)
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("Caller recognition skipped: %s", e)

    # GAP-T9. What the deterministic escalation check needs to know, set
    # once per call rather than re-derived per turn:
    #
    #  * `caller_is_known` is None when nothing looked (no caller number,
    #    or the lookup itself failed). That is deliberately NOT the same
    #    as False -- an `unknown_caller` rule must not fire on a call
    #    where recognition never ran, or a clinic that has one would
    #    transfer every call from a deployment with no caller ID.
    #
    #  * `escalation_enabled` starts True on the single-tenant fallback,
    #    which never resolves a tenant and therefore cannot be told up
    #    front. The backend's first reply switches it off if the clinic
    #    has no such rules, so that path costs one request per call, not
    #    one per turn.
    if caller_phone:
        fnc_ctx.caller_is_known = recognised_name is not None
    fnc_ctx.escalation_enabled = (
        tenant.escalation_deterministic if tenant.resolved else True
    )

    instructions = build_instructions(recognised_name)

    # The clinic's own escalation rules, fetched with the tenant and only
    # rendered here — deciding which rules exist and how they read is the
    # backend's job (see escalation_rules_for in routers/agent_runtime).
    # Nothing is appended when a clinic has none, because this prompt is
    # kept small on purpose: Groq counts prompt tokens against the TPM
    # budget, so an unused section would cost every call for nothing.
    # On the single-tenant fallback path there is no resolve-tenant call,
    # so no rules arrived with the tenant. Fetch them with the org
    # credential we do have — otherwise this feature is inert in exactly
    # the mode most deployments run in (BACKEND_PLATFORM_KEY unset).
    # Never fatal: a call must not fail because a rules lookup did.
    if not tenant.escalation_rules:
        try:
            tenant.escalation_rules = await tenant.client.get_escalation_rules()
        except Exception as e:  # noqa: BLE001 - rules are advisory, calls are not
            logger.warning(f"Could not load escalation rules: {e}")

    if tenant.escalation_rules:
        rules_block = "\n".join(f"- {rule}" for rule in tenant.escalation_rules)
        instructions += (
            "\nESCALATION:\n"
            "- If any rule below matches what the caller says, use the "
            "transfer action immediately and tell them you are putting "
            "them through.\n"
            + rules_block
            + "\n"
        )
        logger.info(
            f"Loaded {len(tenant.escalation_rules)} escalation rule(s) for this call"
        )

    # Initialize the Agent Session with plugins. Building the session
    # (loading VAD/STT/LLM/TTS plugins) and starting it are wrapped
    # together — a misconfigured provider (bad API key, unsupported
    # voice, unreachable endpoint) should end this call cleanly rather
    # than crash the whole worker process.
    try:
        logger.info(f"REGISTERED AGENT TOOLS (Step 4 single-schema): {list(fnc_ctx.function_tools.keys())}")
        logger.info("STEP 3 LANGUAGE POLICY: TTS limited to en-IN, hi-IN, te-IN")
        logger.info("STEP 4 GROQ OPTIMIZATION: single clinic_action tool schema")
        session = AgentSession(
            vad=silero.VAD.load(),
            # Sarvam STT supports English, Hindi and Telugu and can
            # detect the caller language automatically.
            stt=sarvam.STT(
                model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
                language="unknown",
                mode="transcribe",
                sample_rate=16000,
            ),
            llm=_build_llm(config_dict.get("model_provider")),
            tts=_build_tts(
                config_dict.get("model_provider"),
                # Explicit dispatch metadata still wins; otherwise use the
                # voice this clinic chose on its Agent Settings page.
                config_dict.get("voice_id") or _tts_voice_for(tenant),
            ),
            turn_handling={
                # Default min_delay is 0.5s. Sarvam's final STT transcript
                # has been observed arriving ~0.6-0.7s after the VAD-based
                # turn commit on short utterances (see the "transcript
                # arrives after turn has been committed" warning). Raising
                # this slightly gives the final transcript a better chance
                # to land before the turn is committed, without adding
                # noticeable dead air to the call.
                "endpointing": {
                    "min_delay": 0.7,
                },
                # Starts LLM inference on the partial transcript instead of
                # waiting for the turn to be committed, which takes roughly
                # 0.5-1s off every reply. Measured gaps before this were
                # 3-5s per turn, most of it two LLM passes on a 70B model.
                #
                # Safe for booking: a speculative generation that the caller
                # invalidates is cancelled at the authorization gate in
                # agent_activity.py, and perform_tool_executions runs only
                # *after* that gate — so clinic_action never fires for a turn
                # that was discarded. Verified against livekit-agents 1.7.1;
                # re-check if that dependency is upgraded.
                "preemptive_generation": {
                    "enabled": True,
                },
            },
            max_tool_steps=1,
        )
        # Let the tool layer reach the session, so a returning caller's
        # stored language can be applied to TTS as soon as
        # `patient_lookup` identifies them.
        fnc_ctx.session = session

        await session.start(
            room=ctx.room,
            agent=OutboundAssistant(tools=list(fnc_ctx.function_tools.values()), instructions=instructions),
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
                close_on_disconnect=True, # Close room when agent disconnects
            ),
        )

        @session.on("user_input_transcribed")
        def _on_user_input_transcribed(event):
            if not event.is_final:
                return

            raw_language = getattr(event, "language", None)
            transcript = getattr(event, "transcript", None)

            # LiveKit/Sarvam event objects can expose transcript under
            # different attributes depending on plugin version.
            if not transcript:
                transcript = getattr(event, "text", None)

            detected = _detect_supported_caller_language(
                raw_language,
                transcript,
            )

            logger.info(
                "Caller language detected: %s | transcript=%r | "
                "SUPPORTED TTS language: %s",
                raw_language,
                transcript,
                detected,
            )

            # Safety guarantee: only these three values can reach Sarvam TTS.
            if detected not in ("en-IN", "hi-IN", "te-IN"):
                detected = "en-IN"

            # GAP-T9: the clinic's own rules, evaluated on the server,
            # before this turn reaches the model. Fired as a task because
            # this handler is synchronous and must not block the audio
            # pipeline; failures are contained inside `maybe_escalate`.
            if transcript and fnc_ctx.escalation_enabled:
                asyncio.create_task(fnc_ctx.maybe_escalate(transcript))

            try:
                session.tts.update_options(
                    target_language_code=detected
                )
                # Remembered for the finalize call, which hands it to the
                # backend to store on the patient. Set only after the
                # switch actually succeeded, so a language the TTS
                # rejected is never recorded as one the caller speaks.
                fnc_ctx.detected_language = detected
            except Exception as e:
                logger.warning(
                    f"Could not update Sarvam TTS language: {e}"
                )

    except Exception as e:
        logger.error(f"Failed to start agent session: {e}")
        call_state["status"] = "failed"
        ctx.shutdown()
        return

    # Logic to dial out:
    # 1. If 'phone_number' is present, we MIGHT need to dial.
    # 2. Check if a SIP participant is already in the room (Dashboard dispatch case).
    
    # The clinic's own greeting, configured at /app/agent. Falls back to
    # the previous hardcoded line when the clinic hasn't set one (or when
    # we're on the single-tenant path and never asked).
    greeting = (tenant.greeting_script or "").strip() or (
        "Hello, this is MediCOR, your clinic AI receptionist. How can I help you?"
    )

    # Speak to a recognised caller in the language they last actually used,
    # from the greeting onward rather than after their first sentence.
    # `preferred_language` rides on the patient list shape for exactly this
    # purpose, and is stripped before that list ever reaches the model.
    if recognised_language:
        try:
            session.tts.update_options(target_language_code=recognised_language)
            logger.info("Greeting returning caller in %s", recognised_language)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not preset TTS language: %s", e)

    # PF-16: a patient who asked for a slower voice last time should not
    # have to ask again. Applied before the greeting, so the very first
    # sentence they hear is already at their pace.
    if recognised_pace == "slow":
        try:
            session.tts.update_options(pace=config.SLOW_TTS_PACE)
            logger.info("Applying this caller's stored slower speaking pace")
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not preset TTS pace: %s", e)

    if recognised_name:
        # Phrased as a question on purpose: confirm, never assume.
        greeting = f"Hello, am I speaking with {recognised_name}? " + greeting

    should_dial = False
    if phone_number:
        # Check if any remote participant looks like our user (sip_PHONE)
        user_already_here = False
        for p in ctx.room.remote_participants.values():
            if f"sip_{phone_number}" in p.identity or "sip_" in p.identity:
                user_already_here = True
                break
        
        if not user_already_here:
            should_dial = True
            logger.info("User not in room. Agent will initiate dial-out.")
        else:
            logger.info("User already in room (Dashboard dispatched). output Only generated greeting.")

    if should_dial:
        logger.info(f"Initiating outbound SIP call to {phone_number}...")
        try:
            # Create a SIP participant to dial out
            # This effectively "calls" the phone number and brings them into this room
            # --- CONNECTING TO THE PHONE NETWORK ---
            # This step actually "dials" the number over the configured SIP
            # trunk (Twilio Elastic SIP Trunking).
            # It invites the phone number into this digital room.
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=config.SIP_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number}", # Unique ID for the SIP user
                    wait_until_answered=True, # Important: Wait for pickup before continuing
                )
            )
            logger.info("Call answered! Agent is now listening.")
            
            # Note: We do NOT generate an initial reply here immediately.
            # Usually for outbound, we want to hear "Hello?" from the user first,
            # OR we can speak immediately. 
            # If you want the agent to speak first, uncomment the lines below:
            
            await session.say(greeting)
            
        except Exception as e:
            logger.error(f"Failed to place outbound call: {e}")
            call_state["status"] = "failed"
            # Ensure we clean up if the call fails
            ctx.shutdown()
    else:
        # Fallback for inbound calls (if this agent is used for that) OR Dashboard calls where user is already there
        logger.info("Detecting if we should greet...")
        # Give a small delay for audio to stabilize if user just joined
        try:
            await session.say(greeting)
        except Exception as e:
            logger.error(f"Failed to generate initial greeting: {e}")


if __name__ == "__main__":
    # The agent name "outbound-caller" is used by the dispatch script to find this worker
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="outbound-caller", 
        )
    )
