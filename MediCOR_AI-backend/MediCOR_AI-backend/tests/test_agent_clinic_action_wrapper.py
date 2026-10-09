"""
`clinic_action` is a wrapper now — prove it still forwards everything.

The tool was split into a thin decorated wrapper (which speaks a holding
phrase when a call runs long) and `_clinic_action_impl` (the real 270-line
body). Nothing in the suite called the tool before that change, so a typo
in the 14-parameter forwarding list would have reached a live call
silently — the caller would just get an action missing its arguments.

Two things are pinned here: that every parameter arrives at the
implementation unchanged, and that the injected `RunContext` never leaks
into the schema sent to Groq. That schema is kept deliberately small for
the TPM budget, and an extra property in it is a real cost per call.

    pytest tests/test_agent_clinic_action_wrapper.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")
os.environ.setdefault("LIVEKIT_URL", "wss://unused")
os.environ.setdefault("LIVEKIT_API_KEY", "unused")
os.environ.setdefault("LIVEKIT_API_SECRET", "unused")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "voice-agent"))

import asyncio
from contextlib import asynccontextmanager

import pytest

import agent as agent_module


class _StubRunContext:
    """Stands in for livekit's RunContext, recording filler use."""

    def __init__(self):
        self.filler_source = None
        self.filler_delay = None
        self.entered = False

    @asynccontextmanager
    async def with_filler(self, source, *, delay=0, interval=None, max_steps=None):
        self.filler_source = source
        self.filler_delay = delay
        self.entered = True
        yield


ALL_ARGS = dict(
    action="Book",  # deliberately cased/padded to check normalisation still happens
    query="q",
    name="Sarah Jenkins",
    phone="+919876543210",
    dob="1984-02-11",
    email="sarah@example.com",
    patient_id="pat1",
    doctor_id="doc1",
    appointment_id="appt1",
    date="2026-09-07",
    start_at="10:00",
    reason="follow up",
    question="do you have parking?",
    medication="Metformin 500mg",
    destination="+911111111111",
)


def _fns():
    return agent_module.TransferFunctions(ctx=None)


def test_every_parameter_reaches_the_implementation():
    fns = _fns()
    seen = {}

    async def _impl(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    fns._clinic_action_impl = _impl
    run_ctx = _StubRunContext()

    result = asyncio.run(fns.clinic_action(run_ctx, **ALL_ARGS))

    assert result == {"ok": True}
    assert seen == ALL_ARGS, "a parameter was dropped or renamed in the forwarding list"


def test_the_holding_phrase_is_armed_but_only_after_a_delay():
    """
    It must not fire instantly: `with_filler` waits for the session to be
    continuously idle, so a fast action stays silent and the phrase can
    never talk over the caller.
    """
    fns = _fns()

    async def _impl(**kwargs):
        return "done"

    fns._clinic_action_impl = _impl
    run_ctx = _StubRunContext()

    asyncio.run(fns.clinic_action(run_ctx, action="clinic_info"))

    assert run_ctx.entered is True
    assert run_ctx.filler_source == agent_module.HOLDING_PHRASE
    assert run_ctx.filler_delay >= 1.0, "firing sooner risks speaking over the caller"


def test_the_tool_schema_groq_sees_is_unchanged():
    from livekit.agents.llm.utils import build_legacy_openai_schema

    schema = build_legacy_openai_schema(_fns().function_tools["clinic_action"])
    props = schema["function"]["parameters"]["properties"]

    assert "run_ctx" not in props, "the injected RunContext leaked into the LLM schema"
    # Frozen deliberately. Every property here is sent to Groq on every
    # single call and counts against the same TPM budget as the reply, so
    # this list failing is the point: a new parameter must be argued for,
    # not slipped in.
    #
    # `medication` (F9) was added on that basis. It is used only by
    # `refill_request`, and the alternative was overloading `query` —
    # documented as the patient-search term — on a clinical action, which
    # is the last place to let the model guess which meaning applies. The
    # precedent is `question`, already dedicated to the `faq` action.
    assert set(props) == {
        "action", "query", "name", "phone", "dob", "email", "patient_id",
        "doctor_id", "appointment_id", "date", "start_at", "reason",
        "question", "medication", "destination",
    }


def test_only_one_tool_is_exposed():
    """The single-router schema is what keeps the Groq prompt small."""
    assert list(_fns().function_tools) == ["clinic_action"]


@pytest.mark.parametrize(
    "action",
    ["patient_lookup", "doctors", "availability", "book", "cancel",
     "clinic_info", "services", "faq", "transfer"],
)
def test_the_action_string_still_reaches_the_implementation(action):
    fns = _fns()
    seen = {}

    async def _impl(**kwargs):
        seen.update(kwargs)
        return {}

    fns._clinic_action_impl = _impl
    asyncio.run(fns.clinic_action(_StubRunContext(), action=action))
    assert seen["action"] == action
