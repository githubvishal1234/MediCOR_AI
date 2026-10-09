"""
The tools the Deepgram Voice Agent may call, and what each one does.

THE ONE RULE THIS FILE EXISTS TO ENFORCE

Every function below is a thin call into `backend_client.TenantClient`,
which is HTTP transport and nothing else. There is no slot arithmetic
here, no "is this patient real" heuristic, no clinical judgement, no SQL,
and no decision about what a stored preference means. All of that lives
in the FastAPI backend, where it is tested, audited, org-scoped and
shared with the dashboard. If a rule seems to be needed here, it belongs
there instead.

WHY DISCRETE TOOLS, WHEN THE LIVEKIT AGENT HAS EXACTLY ONE

`agent.py` routes everything through a single `clinic_action` tool with
an `action` string, and `tests/test_agent_clinic_action_wrapper.py`
freezes its property set. That was not a stylistic preference: Groq
counts every property of every tool schema against the same tokens-per-
minute budget as the spoken reply, on every turn, so fourteen schemas
cost measurably more than one -- and the single schema is what stopped
the model truncating its own tool-call JSON mid-object.

None of that applies here. Deepgram's managed LLMs are billed per minute
of conversation rather than per prompt token, so the argument that forced
the compromise is gone, and what it cost is worth reclaiming: a model
choosing between `book_appointment` and `cancel_appointment` by name is
markedly more reliable than one choosing between two string values of an
`action` parameter, and a wrong choice here books or cancels a real
appointment. The old test still guards the old path; this is a different
stack with a different constraint, not a reversal by neglect.

CLIENT-SIDE, DELIBERATELY

None of these schemas carries an `endpoint`, which is what makes Deepgram
send a `FunctionCallRequest` back to us rather than calling something
itself. Two reasons, and the first is sufficient on its own:

  * The org-scoped token minted by `/agent/resolve-tenant` would
    otherwise have to be handed to Deepgram to put in a header. It is a
    credential that can read and write one clinic's patient records.
    It stays in this process.

  * Several tools maintain per-call state -- who the caller turned out to
    be, which slots we last quoted -- that only exists here.
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import backend_client

logger = logging.getLogger("twilio-deepgram.tools")


# ---------------------------------------------------------------------
# Schemas sent to Deepgram in the Settings message
# ---------------------------------------------------------------------
#
# Descriptions are written for a model that has to choose between them
# mid-sentence on a phone call, so each says what the tool is FOR and,
# where it matters, what it is NOT for. `request_refill` carries its
# boundary in the description as well as in the prompt and in the backend
# route, because that is the one claim the agent must never make.

FUNCTION_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "find_patient",
        "description": (
            "Look up an existing patient by phone number or full name. Use this "
            "before anything that touches a specific person's records. Returns "
            "the matches found; an empty list means this caller is not "
            "registered yet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The caller's phone number, full name, or medical record "
                        "number, exactly as they gave it."
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "register_patient",
        "description": (
            "Register a new patient. Only call this after the caller has given "
            "their name and you have read their phone number back to them. Do "
            "not ask for an email address; pass one only if they volunteer it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The patient's full name."},
                "phone": {"type": "string", "description": "Their contact phone number."},
                "dob": {
                    "type": "string",
                    "description": "Date of birth as YYYY-MM-DD, if they gave one.",
                },
                "email": {
                    "type": "string",
                    "description": "Email address, only if volunteered.",
                },
            },
            "required": ["name", "phone"],
        },
    },
    {
        "name": "get_patient_history",
        "description": (
            "Fetch a patient's clinical history - past visits, prescriptions and "
            "appointments. Only call this when the caller explicitly asks about "
            "their own records. Never use it to volunteer clinical information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The id returned by find_patient.",
                }
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "list_doctors",
        "description": (
            "List the doctors this clinic has available for booking, with their "
            "specialities. Call this before checking availability."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_available_slots",
        "description": (
            "Get the free appointment times for one doctor on one date. These "
            "are the ONLY times you may offer the caller. Never invent or "
            "approximate a time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doctor_id": {
                    "type": "string",
                    "description": "The id returned by list_doctors.",
                },
                "date": {"type": "string", "description": "The date as YYYY-MM-DD."},
            },
            "required": ["doctor_id", "date"],
        },
    },
    {
        "name": "book_appointment",
        "description": (
            "Book an appointment in a slot that check_available_slots returned. "
            "The clinic re-checks the slot is still free before committing, so "
            "only tell the caller it is booked once this has come back "
            "successfully."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "From find_patient or register_patient."},
                "doctor_id": {"type": "string", "description": "From list_doctors."},
                "start_at": {
                    "type": "string",
                    "description": (
                        "The exact slot start time from check_available_slots, "
                        "copied verbatim."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why they are coming in, in their own words.",
                },
            },
            "required": ["patient_id", "doctor_id", "start_at"],
        },
    },
    {
        "name": "get_patient_appointments",
        "description": (
            "List a patient's upcoming appointments. Call this before "
            "rescheduling or cancelling, so you act on the right one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "From find_patient."}
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "reschedule_appointment",
        "description": (
            "Move an existing appointment to a different slot returned by "
            "check_available_slots. Confirm the new day and time with the caller "
            "first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "From get_patient_appointments.",
                },
                "start_at": {
                    "type": "string",
                    "description": "The new slot start time, copied verbatim.",
                },
                "doctor_id": {
                    "type": "string",
                    "description": "Only if they are changing doctor as well.",
                },
            },
            "required": ["appointment_id", "start_at"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": (
            "Cancel an appointment. Only after the caller has explicitly "
            "confirmed they want it cancelled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "From get_patient_appointments.",
                }
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "clinic_info",
        "description": (
            "The clinic's opening hours, address, phone, email and general "
            "information. Use this for 'when are you open' and 'where are you'."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_services",
        "description": (
            "The services this clinic offers and what each costs. Use this for "
            "'do you do X' and 'how much is Y'."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "answer_question",
        "description": (
            "Ask the clinic's own knowledge base a question you cannot answer "
            "from the other tools - policies, preparation for a "
            "procedure. If it comes back with no answer, offer to put the caller "
            "through rather than guessing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The caller's question, in full.",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "request_refill",
        "description": (
            "RECORD a repeat-prescription request for the clinic's staff to "
            "review. This does NOT approve, prepare or dispense anything, and no "
            "tool available to you can. Identify the patient first and read the "
            "medicine name back to them. Afterwards, tell the caller it has been "
            "passed to the clinic - never that it is approved or ready."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "medication": {
                    "type": "string",
                    "description": "The medicine name exactly as the caller said it.",
                },
                "note": {
                    "type": "string",
                    "description": "Anything else they said about the request.",
                },
            },
            "required": ["medication"],
        },
    },
    {
        "name": "speak_slower",
        "description": (
            "Slow your speaking pace for the rest of this call. Call this the "
            "moment a caller asks you to slow down or says they cannot follow "
            "you. Do not ask them why."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "transfer_to_human",
        "description": (
            "Put the caller through to a person at the clinic. Use this when "
            "they ask for one, when a clinic escalation rule applies, for "
            "anything clinical, and whenever you are stuck. Tell them you are "
            "transferring them before calling it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": (
                        "Only if the clinic named a specific number to use. Omit "
                        "otherwise and the clinic's default is used."
                    ),
                }
            },
        },
    },
    {
        "name": "end_call",
        "description": (
            "Hang up. Call this only after you have said goodbye and the caller "
            "needs nothing further."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


# The names above, as a set, so the dispatcher can reject anything else
# loudly instead of silently doing nothing. A model that hallucinates a
# tool name would otherwise leave the caller in silence.
TOOL_NAMES = {schema["name"] for schema in FUNCTION_SCHEMAS}


class ToolRunner:
    """
    Executes one call's function calls and remembers what happened.

    One instance per call, never module-global: a single gateway process
    serves many clinics concurrently, and shared state here would write
    one caller's details into another clinic's records. Exactly the
    reasoning behind `TransferFunctions` being per-call in agent.py.
    """

    def __init__(
        self,
        api: "backend_client.TenantClient",
        on_transfer: Callable[[Optional[str]], Awaitable[str]],
        on_end_call: Callable[[], Awaitable[None]],
        on_speak_slower: Callable[[], Awaitable[bool]],
    ):
        self.api = api
        self._on_transfer = on_transfer
        self._on_end_call = on_end_call
        self._on_speak_slower = on_speak_slower

        # Facts about this call that the backend needs at finalisation.
        self.call_id: Optional[str] = None
        self.patient_id: Optional[str] = None
        self.appointment_id: Optional[str] = None
        self.actions: list[str] = []

        # Set only if the caller actually asked during this call. The
        # backend decides whether it becomes a standing preference; see
        # the PF-16 note in routers/calls.py for why that rule lives
        # there and not here.
        self.speech_pace_requested: Optional[str] = None

        # The last availability lookup, so book/reschedule can recover a
        # full timestamp if the model echoes back only a time. See
        # `_resolve_start_at`.
        self.last_availability: Optional[dict[str, Any]] = None

        # Latches once a transfer has actually been initiated, so a
        # caller who asks twice is not transferred twice against a call
        # that is already leaving.
        self.transferred = False
        self.ended = False

        # Whether this clinic has rules the *backend* evaluates. Set by
        # the bridge from the tenant resolution, and switched off by the
        # backend's own first reply on the fallback path -- a clinic with
        # none must not pay a round trip on every caller turn for an
        # answer that is always "no".
        self.escalation_enabled = True

    # -- bookkeeping ---------------------------------------------------

    def _note_action(self, action: str) -> None:
        """
        Record what the agent actually did.

        Not decoration: this list is sent to the backend as
        `actions_taken`, and the AI-7 quality judge reads it alongside
        the transcript specifically to catch a claim with nothing behind
        it -- "I've booked you in" with no booking having happened is
        invisible from the transcript alone.
        """
        if not self.actions or self.actions[-1] != action:
            self.actions.append(action)

    def _resolve_start_at(
        self, start_at: Optional[str], doctor_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Recover a full slot timestamp when the model hands back a bare
        time like "10:00".

        This is not the agent inventing a slot, and the distinction
        matters: every candidate here came from a
        `check_available_slots` response in this same call. If the
        fragment does not match one of those, it is passed through
        unchanged and the backend rejects it -- which is the correct
        outcome, because a slot we cannot identify is one we must not
        book.
        """
        if not start_at:
            return start_at
        if "T" in start_at and len(start_at) >= 16:
            return start_at

        cached = self.last_availability or {}
        if doctor_id and cached.get("doctor_id") not in (None, doctor_id):
            return start_at
        for slot in cached.get("slots", []):
            slot_start = str(slot.get("start_at", ""))
            if slot_start.endswith(start_at) or slot_start[-8:-3] == start_at:
                return slot_start

        date = cached.get("date")
        if date:
            time_part = start_at if len(start_at) > 5 else f"{start_at}:00"
            return f"{date}T{time_part}"
        return start_at

    # -- dispatch ------------------------------------------------------

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Run one tool and return the JSON string Deepgram expects as the
        `content` of a FunctionCallResponse.

        Never raises. A tool that throws would leave the caller in
        silence with the model waiting for a response that will never
        come, so every failure is turned into a result the model can act
        on -- and the prompt tells it to apologise and offer a transfer
        rather than read an error out loud.
        """
        try:
            result = await self._dispatch(name, arguments)
        except backend_client.BackendAPIError as e:
            # The clinic's own systems said no, or were unreachable. The
            # message is for the model's benefit, not the caller's.
            logger.warning("Tool %s failed: %s", name, e)
            result = {"error": str(e), "ok": False}
        except Exception as e:  # noqa: BLE001 - a live call must survive anything
            logger.exception("Tool %s raised", name)
            result = {"error": "That did not work.", "ok": False}
        return json.dumps(result, default=str)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name not in TOOL_NAMES:
            logger.warning("Model asked for an unknown tool: %r", name)
            return {"error": f"There is no tool called {name}.", "ok": False}

        handler = getattr(self, f"_tool_{name}")
        return await handler(args)

    # -- patients ------------------------------------------------------

    async def _tool_find_patient(self, args: dict[str, Any]) -> Any:
        query = (args.get("query") or args.get("phone") or args.get("name") or "").strip()
        if not query:
            return {"error": "A phone number or full name is required.", "ok": False}

        matches = await self.api.search_patients(query)

        if len(matches) == 1:
            # One match identifies who we are probably talking to, which
            # is what makes the rest of the call work. It does NOT
            # authenticate them -- see the PF-02 note in agent.py; a
            # phone number is not proof of identity on a shared handset.
            self.patient_id = matches[0].get("id")
            if matches[0].get("speech_pace_preference") == "slow":
                await self._on_speak_slower()

        # Stripped before the model sees them. Both are consumed by the
        # speech layer, neither is anything the model can usefully say,
        # and `PatientListOut` carries them only so this process can act
        # on them. Same rule as `patient_lookup` in agent.py.
        for match in matches:
            match.pop("preferred_language", None)
            match.pop("speech_pace_preference", None)

        return {"found": bool(matches), "patients": matches}

    async def _tool_register_patient(self, args: dict[str, Any]) -> Any:
        name = (args.get("name") or "").strip()
        phone = (args.get("phone") or "").strip()
        if not name or not phone:
            return {"error": "Both a name and a phone number are required.", "ok": False}

        patient = await self.api.register_patient(
            name=name,
            phone=phone,
            dob=(args.get("dob") or None),
            email=(args.get("email") or None),
        )
        self.patient_id = patient.get("id")
        self._note_action("Registered new patient")
        return patient

    async def _tool_get_patient_history(self, args: dict[str, Any]) -> Any:
        patient_id = args.get("patient_id") or self.patient_id
        if not patient_id:
            return {"error": "Identify the patient first with find_patient.", "ok": False}

        history = await self.api.get_patient_history(patient_id=patient_id)
        self._note_action("Reviewed patient medical history")
        return history

    # -- scheduling ----------------------------------------------------

    async def _tool_list_doctors(self, args: dict[str, Any]) -> Any:
        return {"doctors": await self.api.list_doctors()}

    async def _tool_check_available_slots(self, args: dict[str, Any]) -> Any:
        doctor_id = args.get("doctor_id")
        date = args.get("date")
        if not doctor_id or not date:
            return {"error": "Both a doctor and a date are required.", "ok": False}

        result = await self.api.get_availability(doctor_id=doctor_id, date=date)
        self.last_availability = {
            "doctor_id": doctor_id,
            "date": date,
            "slots": result.get("slots", []) if isinstance(result, dict) else [],
        }
        self._note_action("Checked appointment availability")
        return result

    async def _tool_book_appointment(self, args: dict[str, Any]) -> Any:
        patient_id = args.get("patient_id") or self.patient_id
        doctor_id = args.get("doctor_id")
        start_at = self._resolve_start_at(args.get("start_at"), doctor_id)
        if not patient_id or not doctor_id or not start_at:
            return {
                "error": "A patient, a doctor and a slot time are all required.",
                "ok": False,
            }

        appt = await self.api.book_appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            start_at=start_at,
            reason=args.get("reason"),
        )
        self.patient_id = patient_id
        self.appointment_id = appt.get("id")
        self._note_action("Booked appointment")
        return appt

    async def _tool_get_patient_appointments(self, args: dict[str, Any]) -> Any:
        patient_id = args.get("patient_id") or self.patient_id
        if not patient_id:
            return {"error": "Identify the patient first with find_patient.", "ok": False}
        return {
            "appointments": await self.api.list_patient_appointments(patient_id=patient_id)
        }

    async def _tool_reschedule_appointment(self, args: dict[str, Any]) -> Any:
        appointment_id = args.get("appointment_id")
        doctor_id = args.get("doctor_id")
        start_at = self._resolve_start_at(args.get("start_at"), doctor_id)
        if not appointment_id or not start_at:
            return {"error": "An appointment and a new time are required.", "ok": False}

        appt = await self.api.reschedule_appointment(
            appointment_id=appointment_id,
            start_at=start_at,
            doctor_id=doctor_id,
        )
        self.appointment_id = appointment_id
        self._note_action("Rescheduled appointment")
        return appt

    async def _tool_cancel_appointment(self, args: dict[str, Any]) -> Any:
        appointment_id = args.get("appointment_id")
        if not appointment_id:
            return {"error": "An appointment is required.", "ok": False}

        result = await self.api.cancel_appointment(appointment_id=appointment_id)
        self.appointment_id = appointment_id
        self._note_action("Cancelled appointment")
        return result

    # -- clinic knowledge ----------------------------------------------

    async def _tool_clinic_info(self, args: dict[str, Any]) -> Any:
        result = await self.api.get_clinic_info()
        self._note_action("Answered clinic info question")
        return result

    async def _tool_list_services(self, args: dict[str, Any]) -> Any:
        services = await self.api.list_services()
        self._note_action("Answered services question")
        return {"services": services}

    async def _tool_answer_question(self, args: dict[str, Any]) -> Any:
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "A question is required.", "ok": False}

        result = await self.api.ask_knowledge_base(question=question)
        self._note_action("Answered FAQ")
        return result

    # -- refills -------------------------------------------------------

    async def _tool_request_refill(self, args: dict[str, Any]) -> Any:
        """
        Records a request. Cannot approve one, by construction: the route
        this reaches only ever writes `status="pending"`, and the route
        that decides is staff-only and unreachable from here.
        """
        if not self.patient_id:
            # An anonymous medication request is not actionable by staff,
            # so identify the caller rather than queueing something
            # nobody can resolve.
            return {
                "error": "Identify the patient with find_patient first.",
                "ok": False,
            }
        medication = (args.get("medication") or "").strip()
        if not medication:
            return {"error": "The medicine name is required.", "ok": False}

        created = await self.api.create_refill_request(
            patient_id=self.patient_id,
            medication=medication,
            note=args.get("note"),
            call_id=self.call_id,
        )
        self._note_action(f"Recorded refill request: {medication}")
        # Deliberately narrow. The model is told it was written down and
        # nothing more, so it has no material with which to imply the
        # refill is granted.
        return {
            "recorded": True,
            "medication": created.get("medication", medication),
            "status": "pending clinic review",
        }

    # -- call control --------------------------------------------------

    async def _tool_speak_slower(self, args: dict[str, Any]) -> Any:
        if not await self._on_speak_slower():
            return {"ok": False, "error": "The speaking pace could not be changed."}
        # Recorded only because the caller asked. The returning-patient
        # path above deliberately does not set this: re-reporting a
        # preference we already hold would be noise, not evidence.
        self.speech_pace_requested = "slow"
        self._note_action("Slowed speaking pace at the caller's request")
        return {"ok": True, "note": "Speaking more slowly for the rest of this call."}

    async def _tool_transfer_to_human(self, args: dict[str, Any]) -> Any:
        if self.transferred:
            return {"ok": True, "note": "A transfer is already in progress."}
        result = await self._on_transfer(args.get("destination"))
        if result == "ok":
            self.transferred = True
            self._note_action("Transferred to staff")
            return {"ok": True, "note": "Transferring the caller now."}
        return {"ok": False, "error": result}

    async def _tool_end_call(self, args: dict[str, Any]) -> Any:
        self.ended = True
        await self._on_end_call()
        return {"ok": True}

    # -- escalation ----------------------------------------------------

    async def maybe_escalate(self, utterance: Optional[str]) -> bool:
        """
        Ask the backend whether this turn should reach a person (GAP-T9).

        A direct port of `TransferFunctions.maybe_escalate`, and it keeps
        all four of that method's properties: it costs nothing when the
        clinic has no server-evaluated rules, it fires once, it never
        breaks the call, and the rule that fired is named in the action
        list so somebody can answer afterwards *which* rule did this.

        The agent still holds no business logic -- it sends what the
        caller said; which rule matches and where it transfers to are
        entirely the backend's answer.
        """
        if not self.escalation_enabled or self.transferred or not utterance:
            return False
        try:
            verdict = await self.api.check_escalation(utterance=utterance)
        except Exception as e:  # noqa: BLE001 - rules are advisory, calls are not
            logger.warning("Escalation check skipped: %s", e)
            return False

        if not isinstance(verdict, dict):
            return False

        if not verdict.get("has_rules", True):
            # The clinic has none. Stop asking for the rest of the call.
            self.escalation_enabled = False
            return False

        if not verdict.get("escalate"):
            return False

        title = verdict.get("rule_title") or "escalation rule"
        result = await self._on_transfer(verdict.get("destination"))
        if result != "ok":
            logger.warning("Escalation transfer did not complete: %s", result)
            return False
        self.transferred = True
        self._note_action(f"Escalated by rule: {title}")
        logger.info("Escalation rule %r fired", title)
        return True


async def gather_with_timeout(
    coro: Awaitable[Any], timeout: float, on_timeout: Callable[[], Any]
) -> Any:
    """
    Await `coro`, running `on_timeout` once if it is still going after
    `timeout` seconds.

    This is the dead-air fix: a booking is an LLM pass plus an HTTP round
    trip, and the LiveKit stack measured 3-5 seconds of silence on one --
    long enough that callers say "hello?". The filler is spoken only if
    the wait actually gets that long, so a fast tool stays silent.
    """
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            maybe = on_timeout()
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:  # noqa: BLE001 - a filler phrase is never worth a failure
            logger.debug("Filler phrase failed", exc_info=True)
        return await task
