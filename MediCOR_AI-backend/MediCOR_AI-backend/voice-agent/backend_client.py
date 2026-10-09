"""
Backend API client for the MediCOR AI FastAPI backend.

Architectural note:
The voice agent has NO business logic of its own. Every action that
touches patient data, doctor schedules, or appointments must go through
this client to the backend. Do not add local business rules here (no
slot math, no validation, no "is this patient real" heuristics) — this
module's only job is talking to the backend over HTTP and handing back
structured data for the LLM tool layer in agent.py to use.

--- Multi-tenancy (Phase 2) -------------------------------------------

This used to be a module of bare functions sharing ONE module-global
`httpx.AsyncClient` with a single `BACKEND_API_KEY` baked into its
headers. That pinned the whole worker process to one clinic: whichever
number a patient dialled, their record was written into that one
organization.

It is now a `TenantClient` **per call**. `agent.py` resolves which clinic
a call belongs to (from the dialled number) and constructs one client for
that call; nothing tenant-specific lives at module scope. A worker
handling two concurrent calls for two different clinics cannot mix them
up, because there is no shared mutable state to mix.

Auth, in order of preference:

  1. An org-scoped bearer token from `resolve_tenant()` — short-lived,
     minted by the backend once the dialled number identifies a clinic.
  2. `BACKEND_API_KEY` from .env, sent as `X-API-Key` — the pre-Phase-2
     single-tenant path, kept as a fallback so an existing deployment
     keeps working unchanged while tenant routing is rolled out.
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

import config

logger = logging.getLogger("backend-client")


class BackendAPIError(Exception):
    """Raised when the backend returns an error or is unreachable."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class TenantContext:
    """Which clinic this call is for, and how to talk to the backend as them."""

    def __init__(
        self,
        client: "TenantClient",
        org_id: Optional[str] = None,
        org_name: Optional[str] = None,
        timezone: Optional[str] = None,
        phone_number: Optional[str] = None,
        voice_profile: Optional[str] = None,
        greeting_script: Optional[str] = None,
        escalation_rules: Optional[list[str]] = None,
        escalation_deterministic: bool = False,
        resolved: bool = False,
    ):
        self.client = client
        self.org_id = org_id
        self.org_name = org_name
        self.timezone = timezone
        self.phone_number = phone_number
        self.voice_profile = voice_profile
        self.greeting_script = greeting_script
        # Rendered by the backend, not interpreted here. The single-tenant
        # fallback leaves this empty: there is no resolve-tenant call on
        # that path, so no rules are fetched and no escalation section is
        # added to the prompt.
        self.escalation_rules = escalation_rules or []
        # Whether this clinic has any rule the *backend* evaluates
        # (GAP-T9). False means do not call `check_escalation` at all --
        # a clinic with none must not pay a round trip on every caller
        # turn for an answer that is always "no".
        self.escalation_deterministic = bool(escalation_deterministic)
        # False when we fell back to the single-tenant env key, i.e. the
        # call is being handled by whichever clinic BACKEND_API_KEY belongs
        # to rather than by one we positively identified.
        self.resolved = resolved

    def __repr__(self) -> str:
        who = f"{self.org_name} ({self.org_id})" if self.resolved else "single-tenant fallback"
        return f"<TenantContext {who}>"


class TenantClient:
    """
    One authenticated HTTP client, scoped to one clinic, for one call.

    Construct via `for_token()` or `for_api_key()`. Always `aclose()` it
    when the call ends — see `_finalize_call_then_close` in agent.py for
    why the ordering there matters.
    """

    def __init__(self, headers: dict[str, str]):
        self._client = httpx.AsyncClient(
            base_url=config.BACKEND_API_URL,
            headers=headers,
            timeout=config.BACKEND_API_TIMEOUT_SECONDS,
        )

    @classmethod
    def for_token(cls, access_token: str) -> "TenantClient":
        """Org-scoped bearer token from tenant resolution."""
        return cls({"Authorization": f"Bearer {access_token}"})

    @classmethod
    def for_api_key(cls, api_key: Optional[str]) -> "TenantClient":
        """The pre-Phase-2 single-tenant service key."""
        if not api_key:
            logger.warning(
                "BACKEND_API_KEY is not set and no tenant was resolved — backend "
                "API calls will fail. Either assign this number to a clinic in the "
                "Super Admin console, or set BACKEND_API_KEY in .env."
            )
        return cls({"X-API-Key": api_key or ""})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        """
        Centralized request/error handling so every backend call behaves the
        same way: raises BackendAPIError (with the backend's own detail message
        and status code preserved) on failure, otherwise returns parsed JSON.

        Transient failures (connection errors, timeouts, 5xx) are retried a
        bounded number of times with backoff before giving up — a single
        blip on the backend shouldn't force the agent to apologise and
        transfer a live caller. A 4xx is never retried (the request itself
        is wrong). Any exception not already anticipated (e.g. a malformed
        JSON body) is also normalized into BackendAPIError so every call
        site in agent.py can keep catching just that one type.
        """
        attempts = config.BACKEND_API_MAX_RETRIES + 1
        last_exc: Optional[BackendAPIError] = None

        for attempt in range(1, attempts + 1):
            try:
                resp = await self._client.request(method, path, **kwargs)
                resp.raise_for_status()
                return resp.json() if resp.content else None
            except httpx.HTTPStatusError as e:
                detail = e.response.text
                try:
                    detail = e.response.json().get("detail", detail)
                except Exception:
                    pass
                if e.response.status_code < 500:
                    logger.error(f"Backend {method} {path} -> {e.response.status_code}: {detail}")
                    raise BackendAPIError(str(detail), status_code=e.response.status_code) from e
                logger.warning(
                    f"Backend {method} {path} attempt {attempt}/{attempts} -> "
                    f"{e.response.status_code}: {detail}"
                )
                last_exc = BackendAPIError(str(detail), status_code=e.response.status_code)
            except httpx.RequestError as e:
                logger.warning(f"Backend {method} {path} attempt {attempt}/{attempts} unreachable: {e}")
                last_exc = BackendAPIError("Backend is unreachable")
            except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
                logger.error(f"Unexpected error calling backend {method} {path}: {e}")
                raise BackendAPIError("Unexpected error communicating with the backend") from e

            if attempt < attempts:
                await asyncio.sleep(config.BACKEND_API_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

        raise last_exc or BackendAPIError("Backend is unreachable")

    # ---------- Patients ----------

    async def search_patients(self, query: str) -> list[dict[str, Any]]:
        """Look up patients by name, MRN, or phone number."""
        return await self._request("GET", "/patients", params={"search": query})

    async def get_patient(self, patient_id: str) -> dict[str, Any]:
        """Fetch full patient detail (vitals, prescriptions, appointments) by id."""
        return await self._request("GET", f"/patients/{patient_id}")

    async def register_patient(
        self,
        name: str,
        phone: Optional[str] = None,
        dob: Optional[str] = None,
        email: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Registers a new patient. MRN is auto-generated by the backend.
        Returns the created patient record (includes id and mrn). `email` is
        optional — only set if the caller volunteers one; used to send an
        email confirmation once an appointment is booked.
        """
        payload = {"name": name, "phone": phone, "dob": dob, "email": email}
        return await self._request(
            "POST", "/patients", json={k: v for k, v in payload.items() if v is not None}
        )

    # ---------- Doctors & Scheduling ----------

    async def list_doctors(self) -> list[dict[str, Any]]:
        """Lists active doctors available for booking."""
        return await self._request("GET", "/doctors")

    async def get_availability(self, doctor_id: str, date: str) -> dict[str, Any]:
        """
        Gets open slots for a doctor on a given date (YYYY-MM-DD).
        All schedule math (working hours, existing bookings, the clinic's
        timezone) happens server-side.
        """
        return await self._request(
            "GET", "/appointments/availability", params={"doctor_id": doctor_id, "date": date}
        )

    async def book_appointment(
        self, patient_id: str, doctor_id: str, start_at: str, reason: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Books an appointment. start_at must be an ISO-8601 datetime string,
        ideally one returned by get_availability. The backend re-validates the
        slot is still free (and everything else) before committing.
        """
        payload = {
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "start_at": start_at,
            "reason": reason,
        }
        return await self._request(
            "POST", "/appointments/book", json={k: v for k, v in payload.items() if v is not None}
        )

    async def list_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]:
        """
        Lists a patient's upcoming, non-cancelled appointments. Use this to
        find the appointment a caller is referring to before rescheduling or
        cancelling it.
        """
        return await self._request("GET", f"/appointments/patient/{patient_id}")

    async def reschedule_appointment(
        self, appointment_id: str, start_at: str, doctor_id: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Moves an existing appointment to a new slot, ideally one returned by
        get_availability. Omit doctor_id to keep the same doctor. The backend
        re-validates the new slot is still free before committing.
        """
        payload = {"start_at": start_at, "doctor_id": doctor_id}
        return await self._request(
            "POST",
            f"/appointments/{appointment_id}/reschedule",
            json={k: v for k, v in payload.items() if v is not None},
        )

    async def cancel_appointment(self, appointment_id: str) -> dict[str, Any]:
        """Cancels an appointment."""
        return await self._request("POST", f"/appointments/{appointment_id}/cancel")

    # ---------- EHR (clinical history, sync status) ----------
    #
    # All EHR/business logic (which provider, whether it's configured, what
    # "synced" means) lives in the backend's app/ehr_service.py. The agent
    # never talks to an EHR directly and never has to know which vendor (if
    # any) is connected — it just asks the backend for a patient's history.

    async def create_refill_request(
        self,
        patient_id: str,
        medication: str,
        note: Optional[str] = None,
        call_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Records that a caller asked for a repeat prescription.

        A REQUEST, never an approval. The backend only ever creates a
        `pending` row from this route; approving or declining is a
        separate, staff-only endpoint the agent cannot reach. Do not add
        a status argument here.
        """
        payload = {
            "patient_id": patient_id,
            "medication": medication,
            "note": note,
            "call_id": call_id,
        }
        return await self._request(
            "POST", "/refills", json={k: v for k, v in payload.items() if v is not None}
        )

    async def get_ehr_status(self) -> dict[str, Any]:
        """Checks whether an EHR integration is connected/configured for this org."""
        return await self._request("GET", "/ehr/status")

    async def get_patient_history(self, patient_id: str) -> dict[str, Any]:
        """
        Fetches a patient's clinical history — prescriptions, past visits,
        and appointments — from the backend's system of record (mirrored to
        the EHR when one is connected). Use this instead of ever guessing or
        inventing medical history. Works even when no EHR is connected; the
        response's `ehr_source` field tells you whether it is.
        """
        return await self._request("GET", f"/ehr/patients/{patient_id}/history")

    # ---------- Knowledge base (clinic info, services, FAQs) ----------

    async def get_clinic_info(self) -> dict[str, Any]:
        """
        Fetches structured clinic facts: timings, address, phone, email,
        website, and general info.
        """
        return await self._request("GET", "/knowledge-base/clinic-info")

    async def get_escalation_rules(self) -> list[str]:
        """
        The clinic's escalation rules, already rendered one line each.

        Only needed on the single-tenant fallback path — a resolved tenant
        gets these on the resolve-tenant response and never calls this.
        """
        return await self._request("GET", "/agent/escalation-rules")

    async def check_escalation(
        self,
        utterance: Optional[str] = None,
        caller_is_known: Optional[bool] = None,
    ) -> dict[str, Any]:
        """
        Asks the backend whether this call should reach a person (GAP-T9).

        Transport only, like everything else here. The clinic's rules,
        the matching and the choice of destination are all the backend's;
        this posts what the caller just said and returns the verdict.

        The `escalation_rules` above are the other half of the same
        feature and are NOT redundant with this: those are rules written
        as prose for the model to interpret, which is wider and vaguer;
        this is the set the server can decide exactly. A clinic can have
        both, and most will.
        """
        return await self._request(
            "POST",
            "/agent/escalation-check",
            json={"utterance": utterance, "caller_is_known": caller_is_known},
        )

    async def list_services(self) -> list[dict[str, Any]]:
        """Lists the clinic's active services, each with a fee label if set."""
        return await self._request("GET", "/knowledge-base/services")

    async def ask_knowledge_base(self, question: str) -> dict[str, Any]:
        """
        Asks a freeform question (general info, misc FAQs)
        against the org's knowledge base. The backend does the matching and
        returns a ready-to-speak answer plus which FAQ (if any) it matched.
        """
        return await self._request("GET", "/knowledge-base/ask", params={"q": question})

    # ---------- Call intelligence (transcripts, summaries, metadata) ----------
    #
    # The agent has no summarization/sentiment logic of its own — it only
    # reports what happened (direction, timestamps, transcript, which tools
    # succeeded). The backend generates the AI summary and sentiment from
    # that data. See app/call_intelligence.py.

    async def create_call_log(
        self,
        direction: str,
        status: str = "in_progress",
        caller_phone: Optional[str] = None,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
        reason: Optional[str] = None,
        started_at: Optional[str] = None,
        provider: Optional[str] = None,
        provider_call_sid: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Creates a call log at the start of a call. Works the same for
        inbound and outbound — pass direction accordingly. Returns the
        created call record (id needed for the transcript/finalize calls).

        `provider` / `provider_call_sid` name the telephony stack that
        carried the call and the carrier's own id for it — "twilio" and a
        CallSid, from the gateway in voice-agent/twilio_deepgram/. Both
        are optional and the LiveKit path sends neither, which is honest:
        it has no carrier id to report. See models.CallLog.provider.
        """
        payload = {
            "direction": direction,
            "status": status,
            "caller_phone": caller_phone,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "reason": reason,
            "started_at": started_at,
            "provider": provider,
            "provider_call_sid": provider_call_sid,
        }
        return await self._request(
            "POST", "/calls", json={k: v for k, v in payload.items() if v is not None}
        )

    async def update_call_caller_phone(self, call_id: str, caller_phone: str) -> dict[str, Any]:
        """
        Best-effort backfill of the caller's phone number on an already-created
        call log. For inbound SIP calls the caller's number can arrive on the
        participant's attributes a moment after the call log is first created
        (see _extract_caller_phone / the backfill task in agent.py), so this
        lets the agent patch it in as soon as it becomes available — without
        touching status, duration, or any other field, so it's safe to call at
        any point while the call is still in progress.
        """
        return await self._request("PATCH", f"/calls/{call_id}", json={"caller_phone": caller_phone})

    async def save_transcript_bulk(
        self, call_id: str, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Saves the complete call transcript in one request, replacing any
        prior transcript for this call. Call once at the end of the call
        with the full conversation history. `messages` items look like
        {"who": "ai" | "patient", "text": str, "time_label": str | None}.
        """
        return await self._request(
            "PUT", f"/calls/{call_id}/transcript/bulk", json={"messages": messages}
        )

    async def finalize_call(
        self,
        call_id: str,
        status: str,
        duration_seconds: Optional[int] = None,
        ended_at: Optional[str] = None,
        patient_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        caller_phone: Optional[str] = None,
        outcome: Optional[str] = None,
        actions_taken: Optional[str] = None,
        detected_language: Optional[str] = None,
        speech_pace_requested: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Updates final call metadata once the call ends (status, duration,
        timestamps, and patient/appointment association if resolved during
        the call). Call this AFTER save_transcript_bulk so the backend's
        auto-generated AI summary and sentiment (triggered when status is
        terminal) have the transcript to work from. Leave outcome unset to
        let the backend infer it from actions_taken/transcript.
        """
        payload = {
            "status": status,
            "duration_seconds": duration_seconds,
            "ended_at": ended_at,
            "patient_id": patient_id,
            "appointment_id": appointment_id,
            "caller_phone": caller_phone,
            "outcome": outcome,
            "actions_taken": actions_taken,
            # What the caller actually spoke. The backend decides what to
            # do with it (it copies it onto the patient so the next call
            # can open in the same language) — this client only reports.
            "detected_language": detected_language,
            "speech_pace_requested": speech_pace_requested,
        }
        return await self._request(
            "PATCH", f"/calls/{call_id}", json={k: v for k, v in payload.items() if v is not None}
        )


# ---------- Tenant resolution ----------

async def resolve_tenant(dialed_number: Optional[str]) -> Optional[TenantContext]:
    """
    Asks the backend which clinic owns `dialed_number`, and builds a
    client scoped to them.

    Returns None when the deployment isn't set up for tenant routing (no
    platform key), when no number could be determined, or when the number
    isn't assigned to any clinic. In every one of those cases the caller
    is expected to fall back to the single-tenant API key — a call must
    never fail just because routing is unconfigured.

    Uses its own short-lived HTTP client: this runs before we know which
    tenant we are, so it must not borrow tenant-scoped credentials.
    """
    if not config.BACKEND_PLATFORM_KEY:
        return None
    if not dialed_number:
        logger.info("No dialled number available — using the single-tenant fallback key.")
        return None

    try:
        async with httpx.AsyncClient(
            base_url=config.BACKEND_API_URL,
            headers={"X-Platform-Key": config.BACKEND_PLATFORM_KEY},
            timeout=config.BACKEND_API_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.post(
                "/agent/resolve-tenant", json={"dialed_number": dialed_number}
            )
            if resp.status_code == 404:
                logger.warning(
                    f"No clinic is assigned to {dialed_number} — falling back to the "
                    "single-tenant key. Assign it in Super Admin -> Phone Numbers."
                )
                return None
            if resp.status_code == 503:
                logger.info("Backend has tenant resolution disabled — using the fallback key.")
                return None
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001 - resolution must never break a call
        logger.error(f"Tenant resolution failed for {dialed_number}: {e}")
        return None

    token = data.get("access_token")
    if not token:
        logger.error("Tenant resolution returned no access token — using the fallback key.")
        return None

    return TenantContext(
        client=TenantClient.for_token(token),
        org_id=data.get("org_id"),
        org_name=data.get("org_name"),
        timezone=data.get("timezone"),
        phone_number=data.get("phone_number"),
        voice_profile=data.get("voice_profile"),
        greeting_script=data.get("greeting_script"),
        escalation_rules=data.get("escalation_rules") or [],
        escalation_deterministic=bool(data.get("escalation_deterministic")),
        resolved=True,
    )


def fallback_tenant() -> TenantContext:
    """
    The pre-Phase-2 behaviour: one clinic, identified by the
    `BACKEND_API_KEY` in .env. Used whenever resolution is disabled or
    comes back empty, so an existing single-tenant deployment keeps
    working exactly as it did.
    """
    return TenantContext(
        client=TenantClient.for_api_key(config.BACKEND_API_KEY),
        resolved=False,
    )
