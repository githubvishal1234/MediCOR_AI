"""
What the agent does with an escalation verdict (GAP-T9).

The backend decides *whether* to escalate; this is the other half --
whether that decision actually reaches the caller. A verdict that is
correct in `escalation_service` and never causes a transfer is worth
nothing, and the branching here is where that goes wrong: a latch, an
off-switch, and a failure path that must not leave the caller stranded
with the AI on a call the clinic said should have reached a person.

Nothing here touches LiveKit. `maybe_escalate` is exercised against a
fake backend client and a stubbed transfer, which is the whole reason
the transfer was extracted out of the tool action in the first place.

    pytest tests/test_agent_escalation_call.py -v
"""

import asyncio
import os
import sys
import types

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BACKEND_DIR, "voice-agent"))

import pytest  # noqa: E402


def _stub_plugins():
    names = ("openai", "cartesia", "deepgram", "noise_cancellation",
             "silero", "sarvam")
    for name in names:
        full = f"livekit.plugins.{name}"
        if full not in sys.modules:
            sys.modules[full] = types.ModuleType(full)
    if "livekit.plugins" not in sys.modules:
        pkg = types.ModuleType("livekit.plugins")
        pkg.__path__ = []
        for name in names:
            setattr(pkg, name, sys.modules[f"livekit.plugins.{name}"])
        sys.modules["livekit.plugins"] = pkg


@pytest.fixture(scope="module")
def agent_module():
    _stub_plugins()
    import agent
    return agent


class FakeApi:
    """Stands in for the per-call TenantClient. Transport only, as it is."""

    def __init__(self, verdict=None, raises=False):
        self.verdict = verdict or {"escalate": False, "has_rules": True}
        self.raises = raises
        self.calls = []

    async def check_escalation(self, utterance=None, caller_is_known=None):
        self.calls.append({"utterance": utterance, "caller_is_known": caller_is_known})
        if self.raises:
            raise RuntimeError("backend unreachable")
        return self.verdict


@pytest.fixture()
def ctx(agent_module):
    """A TransferFunctions with the transfer stubbed and nothing real behind it."""
    fn = agent_module.TransferFunctions.__new__(agent_module.TransferFunctions)
    fn.actions = []
    fn.api = FakeApi()
    fn.escalation_enabled = True
    fn.escalated = False
    fn.caller_is_known = None

    transfers = []

    async def fake_transfer(destination):
        transfers.append(destination)
        return "Transfer initiated successfully."

    fn._do_transfer = fake_transfer
    fn.transfers = transfers
    return fn


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ================================================== the verdict is acted on

def test_an_escalate_verdict_transfers_to_the_rules_destination(ctx):
    ctx.api.verdict = {
        "escalate": True, "has_rules": True, "rule_title": "Emergencies",
        "condition_type": "keyword", "destination": "+15550100",
        "matched_on": "chest pain",
    }
    assert run(ctx.maybe_escalate("bad chest pain")) is True
    assert ctx.transfers == ["+15550100"]


def test_no_destination_falls_through_to_the_agents_default(ctx):
    """
    Null is not "nowhere" -- `_do_transfer` resolves it to the configured
    default, which is where every escalation went before rules could name
    their own.
    """
    ctx.api.verdict = {"escalate": True, "has_rules": True,
                       "rule_title": "Emergencies", "destination": None}
    run(ctx.maybe_escalate("pain"))
    assert ctx.transfers == [None]


def test_the_rule_that_fired_is_named_in_the_call_record(ctx):
    """
    "Transferred to staff" alone loses the thing this feature exists to
    provide. `actions_taken` is also what the AI-7 judge reads, so the
    reason has to be in it.
    """
    ctx.api.verdict = {"escalate": True, "has_rules": True,
                       "rule_title": "Emergencies", "destination": "+1"}
    run(ctx.maybe_escalate("chest pain"))
    assert any("Emergencies" in a for a in ctx.actions)


def test_an_ordinary_turn_transfers_nothing(ctx):
    ctx.api.verdict = {"escalate": False, "has_rules": True}
    assert run(ctx.maybe_escalate("I would like an appointment")) is False
    assert ctx.transfers == []


# ============================================================ it fires once

def test_a_caller_repeating_a_keyword_is_transferred_once(ctx):
    """
    A second transfer against a call already leaving is not a second
    escalation, it is a bug the caller experiences.
    """
    ctx.api.verdict = {"escalate": True, "has_rules": True,
                       "rule_title": "Emergencies", "destination": "+1"}
    run(ctx.maybe_escalate("chest pain"))
    run(ctx.maybe_escalate("chest pain again"))
    run(ctx.maybe_escalate("still chest pain"))
    assert ctx.transfers == ["+1"]
    assert len(ctx.api.calls) == 1, "kept asking after it had already escalated"


def test_a_transfer_that_did_not_take_can_be_retried(ctx):
    """
    The latch must not strand the caller with the AI on a call the clinic
    said should have reached a person.
    """
    async def failing_transfer(destination):
        return "Error: No default transfer number configured."

    ctx._do_transfer = failing_transfer
    ctx.api.verdict = {"escalate": True, "has_rules": True,
                       "rule_title": "Emergencies", "destination": None}

    assert run(ctx.maybe_escalate("chest pain")) is False
    assert ctx.escalated is False, "latched on a transfer that never happened"


# ======================================================= it costs nothing

def test_a_clinic_with_no_rules_is_asked_once_and_then_never_again(ctx):
    """
    This is what makes the single-tenant fallback affordable: that path
    never resolves a tenant, so it starts enabled and the backend's first
    answer switches it off.
    """
    ctx.api.verdict = {"escalate": False, "has_rules": False}
    run(ctx.maybe_escalate("hello"))
    run(ctx.maybe_escalate("second turn"))
    run(ctx.maybe_escalate("third turn"))
    assert len(ctx.api.calls) == 1
    assert ctx.escalation_enabled is False


def test_it_does_not_ask_when_resolution_said_not_to(ctx):
    ctx.escalation_enabled = False
    assert run(ctx.maybe_escalate("chest pain")) is False
    assert ctx.api.calls == []


# ============================================== it never breaks the call

def test_a_backend_failure_leaves_the_call_exactly_as_it_was(ctx):
    """
    The model's own escalation path and the EMERGENCY prompt block are
    both still there -- which is the layering the whole feature rests on.
    A check that raised into the audio pipeline would be worse than no
    check.
    """
    ctx.api = FakeApi(raises=True)
    assert run(ctx.maybe_escalate("chest pain")) is False
    assert ctx.transfers == []
    assert ctx.escalated is False


def test_a_nonsense_response_is_ignored(ctx):
    ctx.api.verdict = "not a dict"
    assert run(ctx.maybe_escalate("chest pain")) is False


def test_it_forwards_what_it_knows_about_the_caller(ctx):
    ctx.caller_is_known = False
    run(ctx.maybe_escalate("hello"))
    assert ctx.api.calls[0] == {"utterance": "hello", "caller_is_known": False}


# =========================================== the transfer is not duplicated

def test_both_escalation_paths_use_one_transfer_implementation(agent_module):
    """
    The model-requested transfer and the server-evaluated one must not
    drift apart. A second copy of the SIP addressing would eventually
    behave differently for no stated reason.
    """
    import inspect

    src = inspect.getsource(agent_module.TransferFunctions)
    assert src.count("transfer_sip_participant") == 1, (
        "the SIP transfer is implemented more than once"
    )
    assert "return await self._do_transfer(destination)" in src
    assert 'await self._do_transfer(verdict.get("destination"))' in src
