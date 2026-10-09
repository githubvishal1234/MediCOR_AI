"""
One call's identity, record and ending.

Everything here is bookkeeping the backend owns and this process merely
reports: which clinic the call belongs to, that a call started, what was
said, and how it ended. No decision made here changes what a caller
hears.

THE TWO THINGS THAT GOT SIMPLER IN THE MIGRATION

  * **The dialled number arrives with the call.** `agent.py` had to poll
    a SIP participant's attributes for up to `DIALED_NUMBER_WAIT_SECONDS`
    because LiveKit populates them a beat after the job starts -- a wait
    that delayed the greeting on every single call. Twilio puts `To` in
    the webhook body, so tenant resolution now happens before the media
    stream even opens and costs nothing.

  * **The caller's number arrives with the call too.** The whole
    `_backfill_caller_phone` background poll in agent.py exists because
    LiveKit could not tell us who was ringing until after the call log
    had been written. Twilio's `From` is there from the first byte, so
    the number goes on the record at creation and the backfill is gone.

WHAT DELIBERATELY DID NOT CHANGE

Tenant resolution still goes through `backend_client.resolve_tenant`,
falling back to the single-tenant API key on every failure path, because
**a call must never fail because routing is unconfigured**. Caller
recognition still requires exactly one match, still greets rather than
authenticates, and still uses a first name only. Those rules are argued
at length in agent.py and MULTI-TENANT-ROLLOUT.md and are not re-argued
here -- they are simply kept.
"""

import logging
from datetime import datetime
from typing import Any, Optional

import backend_client

logger = logging.getLogger("twilio-deepgram.session")


class CallSession:
    """
    The record of one telephone call, from resolution to finalisation.
    """

    def __init__(
        self,
        call_sid: str,
        dialed_number: Optional[str],
        caller_phone: Optional[str],
        direction: str = "inbound",
    ):
        self.call_sid = call_sid
        self.dialed_number = dialed_number
        self.caller_phone = caller_phone
        self.direction = direction

        self.tenant: Optional[backend_client.TenantContext] = None
        self.call_id: Optional[str] = None
        self.started_at = datetime.now().astimezone()

        # Built from Deepgram's ConversationText events, in the shape the
        # backend's bulk endpoint wants.
        self.transcript: list[dict[str, str]] = []

        # PF-02, set only when the caller's number matched exactly one
        # patient. A greeting, never an identity.
        self.recognised_name: Optional[str] = None

        # "completed" unless something actually went wrong. A call that
        # ends because the caller hung up is a completed call.
        self.status = "completed"

    # -- setup ---------------------------------------------------------

    async def resolve_tenant(self) -> backend_client.TenantContext:
        """
        Which clinic owns the number that was dialled.

        Every failure -- routing disabled, number unassigned, backend
        unreachable -- falls back to the single-tenant key, exactly as
        the LiveKit path does. That is the deliberate rollback switch,
        not an oversight: unsetting `BACKEND_PLATFORM_KEY` reverts the
        whole deployment to pre-multi-tenant behaviour.
        """
        tenant = await backend_client.resolve_tenant(self.dialed_number)
        if tenant is None:
            tenant = backend_client.fallback_tenant()
        self.tenant = tenant
        logger.info("Call %s -> %s", self.call_sid, tenant)
        return tenant

    async def recognise_caller(self) -> Optional[str]:
        """
        Greet a returning caller by their first name (PF-02).

        Three rules, all load-bearing and all inherited unchanged:

          * **Exactly one match, or nothing happens.** Several matches
            means a shared handset, and guessing is worse than asking.
          * **It greets, it does not authenticate.** The tools still run
            `find_patient`; nothing here sets a patient id. Treating a
            phone number as proof of identity would hand one family
            member another's appointments.
          * **First name only.** Reading a full name aloud to whoever
            picked up would disclose that a specific named person is a
            patient of this clinic, to someone not yet identified.

        Never fatal: a call must not fail because a courtesy lookup did.
        """
        if not self.caller_phone or self.tenant is None:
            return None
        try:
            matches = await self.tenant.client.search_patients(self.caller_phone)
        except Exception as e:  # noqa: BLE001
            logger.warning("Caller recognition skipped: %s", e)
            return None

        if len(matches) != 1:
            if len(matches) > 1:
                logger.info(
                    "Caller number matches %d patients - not personalising", len(matches)
                )
            return None

        full_name = (matches[0].get("name") or "").strip()
        self.recognised_name = full_name.split()[0] if full_name else None
        return self.recognised_name

    def greeting(self, configured: Optional[str]) -> str:
        """
        What the agent opens with.

        The clinic's own greeting from Agent Settings, or the product
        default. A recognised caller gets a confirming question -- phrased
        as a question on purpose, because the name is a guess drawn from
        a phone number and the prompt says so too.

        The question goes AFTER the clinic's greeting, not before it.
        Before it produced "Hello, am I speaking with Vishal? Hello, this
        is MediCOR..." -- two greetings in one breath, because almost
        every clinic greeting opens with one. It is also the wrong order
        for a phone call: a receptionist says who they are first and asks
        who is calling second.
        """
        text = (configured or "").strip() or (
            "Hello, this is MediCOR, your clinic AI receptionist. How can I help you?"
        )
        if self.recognised_name:
            text = f"{text} Am I speaking with {self.recognised_name}?"
        return text

    async def create_call_log(self) -> Optional[str]:
        """
        Open the call record, so the dashboard shows the call while it is
        still happening.

        Failure is logged and swallowed: a clinic whose backend is
        briefly unreachable should still have its phone answered. The
        consequence is an unrecorded call, which is bad; a dropped call
        is worse.
        """
        if self.tenant is None:
            return None
        try:
            call = await self.tenant.client.create_call_log(
                direction=self.direction,
                status="in_progress",
                caller_phone=self.caller_phone,
                started_at=self.started_at.isoformat(),
                provider="twilio",
                provider_call_sid=self.call_sid,
            )
        except backend_client.BackendAPIError as e:
            logger.warning("Could not create call log for %s: %s", self.call_sid, e)
            return None
        self.call_id = call.get("id")
        return self.call_id

    # -- during the call -----------------------------------------------

    def record_utterance(self, role: str, text: str) -> None:
        """
        Append one turn to the transcript.

        `role` is Deepgram's ("user" | "assistant"); the backend's
        vocabulary is ("patient" | "ai"). Translated here rather than at
        the point of storage so the mapping has one home.
        """
        text = (text or "").strip()
        if not text or role not in ("user", "assistant"):
            return
        self.transcript.append(
            {"who": "ai" if role == "assistant" else "patient", "text": text}
        )

    # -- teardown ------------------------------------------------------

    async def finalize(
        self,
        patient_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        actions: Optional[list[str]] = None,
        speech_pace_requested: Optional[str] = None,
    ) -> None:
        """
        Save the transcript, then close the record.

        Ordering is not incidental. The backend generates the call
        summary, sentiment and outcome when the status becomes terminal,
        reading the stored transcript to do it -- so finalising before
        the transcript lands produces a summary of an empty conversation.
        The same ordering rule the LiveKit finaliser follows.

        Both steps are individually guarded: a transcript that fails to
        save must not also cost us the call's duration and outcome.
        """
        if self.tenant is None or not self.call_id:
            return

        ended_at = datetime.now().astimezone()
        duration_seconds = int((ended_at - self.started_at).total_seconds())

        if self.transcript:
            try:
                await self.tenant.client.save_transcript_bulk(
                    self.call_id, self.transcript
                )
            except backend_client.BackendAPIError as e:
                logger.warning("Could not save transcript for %s: %s", self.call_id, e)

        try:
            await self.tenant.client.finalize_call(
                self.call_id,
                status=self.status,
                duration_seconds=duration_seconds,
                ended_at=ended_at.isoformat(),
                patient_id=patient_id,
                appointment_id=appointment_id,
                caller_phone=self.caller_phone,
                actions_taken="\n".join(actions) if actions else None,
                # Deliberately NOT reporting a detected language. The
                # LiveKit path sends one because Sarvam detects the
                # caller's language per turn and the value is fed back
                # into Sarvam TTS on their next call. Deepgram's agent
                # socket does not report a per-turn language, and this
                # stack's TTS cannot speak Hindi or Telugu anyway -- so
                # anything sent here would be a guess written onto a
                # patient record. Silence is the honest report. See
                # VOICE_STACK_MIGRATION.md.
                speech_pace_requested=speech_pace_requested,
            )
        except backend_client.BackendAPIError as e:
            logger.warning("Could not finalize call %s: %s", self.call_id, e)

    async def aclose(self) -> None:
        if self.tenant is not None:
            try:
                await self.tenant.client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("Tenant client close failed", exc_info=True)
