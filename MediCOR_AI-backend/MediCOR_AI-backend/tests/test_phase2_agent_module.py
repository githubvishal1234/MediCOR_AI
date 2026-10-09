"""
Phase 2 — agent.py's new tenant-routing helpers.

Imports the real `voice-agent/agent.py` against the real
`livekit.agents` package, stubbing only the vendor plugin modules
(Deepgram/Sarvam/Cartesia/OpenAI/Silero), which need API keys and audio
devices to import. Everything under test here is pure logic that runs
before any of those are touched.

Deciding which clinic a call belongs to is the one piece of new
reasoning inside the highest-risk file in the project, so it gets tested
directly rather than by inspection.

    pytest tests/test_phase2_agent_module.py -v
"""

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

import asyncio  # noqa: E402

import pytest  # noqa: E402


def _stub_plugins():
    """
    Stand in for the LiveKit vendor plugins.

    Only their module objects are needed at import time — agent.py calls
    into them inside _build_tts/_build_llm/AgentSession, none of which
    these tests reach.
    """
    for name in ("openai", "cartesia", "deepgram", "noise_cancellation", "silero", "sarvam"):
        full = f"livekit.plugins.{name}"
        if full not in sys.modules:
            sys.modules[full] = types.ModuleType(full)

    if "livekit.plugins" not in sys.modules:
        pkg = types.ModuleType("livekit.plugins")
        pkg.__path__ = []
        for name in ("openai", "cartesia", "deepgram", "noise_cancellation", "silero", "sarvam"):
            setattr(pkg, name, sys.modules[f"livekit.plugins.{name}"])
        sys.modules["livekit.plugins"] = pkg


_stub_plugins()

import agent  # noqa: E402  - voice-agent/agent.py
import config as agent_config  # noqa: E402


class _FakeParticipant:
    def __init__(self, attributes=None, identity="sip_+915551234567"):
        self.attributes = attributes or {}
        self.identity = identity


class _FakeRoom:
    def __init__(self, participants=()):
        self.remote_participants = {str(i): p for i, p in enumerate(participants)}
        self.name = "test-room"
        self.metadata = None


class _FakeCtx:
    def __init__(self, participants=()):
        self.room = _FakeRoom(participants)


# ---------------------------------------------------------------------
# Which number was dialled
# ---------------------------------------------------------------------

def test_metadata_states_the_dialled_number():
    ctx = _FakeCtx()
    for key in ("dialed_number", "to_number", "called_number", "trunk_phone_number"):
        assert agent._extract_dialed_number(ctx, {key: "+919876543210"}) == "+919876543210"


def test_falls_back_to_the_sip_trunk_attribute():
    ctx = _FakeCtx([_FakeParticipant({"sip.trunkPhoneNumber": "+918001234567"})])
    assert agent._extract_dialed_number(ctx, {}) == "+918001234567"


def test_metadata_wins_over_the_participant_attribute():
    ctx = _FakeCtx([_FakeParticipant({"sip.trunkPhoneNumber": "+918001234567"})])
    assert agent._extract_dialed_number(ctx, {"dialed_number": "+919999999999"}) == "+919999999999"


def test_the_callers_own_number_is_never_used_for_routing():
    """
    Routing on the CALLER's number would put a patient through to
    whichever clinic happened to match it. Only the dialled number
    identifies the tenant.
    """
    ctx = _FakeCtx([
        _FakeParticipant(
            {"sip.phoneNumber": "+915551234567"},  # caller ID, not the line they rang
            identity="sip_+915551234567",
        )
    ])
    assert agent._extract_dialed_number(ctx, {}) is None


def test_nothing_available_returns_none():
    assert agent._extract_dialed_number(_FakeCtx(), {}) is None
    assert agent._extract_dialed_number(_FakeCtx([_FakeParticipant({})]), {}) is None


def test_extraction_survives_a_hostile_room_object():
    """A malformed room must not crash call setup."""
    class Exploding:
        @property
        def remote_participants(self):
            raise RuntimeError("room not ready")

    ctx = types.SimpleNamespace(room=Exploding())
    assert agent._extract_dialed_number(ctx, {}) is None


# ---------------------------------------------------------------------
# The bounded wait
# ---------------------------------------------------------------------

def test_no_waiting_when_tenant_routing_is_disabled(monkeypatch):
    """
    Single-tenant deployments must pay nothing for this. With no platform
    key the helper returns immediately rather than delaying the greeting.
    """
    monkeypatch.setattr(agent_config, "BACKEND_PLATFORM_KEY", None)
    monkeypatch.setattr(agent_config, "DIALED_NUMBER_WAIT_SECONDS", 5.0)

    async def run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await agent._await_dialed_number(_FakeCtx(), {})
        return result, loop.time() - started

    result, elapsed = asyncio.run(run())
    assert result is None
    assert elapsed < 0.5, f"waited {elapsed:.2f}s with routing disabled"


def test_no_waiting_when_the_number_is_already_known(monkeypatch):
    monkeypatch.setattr(agent_config, "BACKEND_PLATFORM_KEY", "some-key")
    monkeypatch.setattr(agent_config, "DIALED_NUMBER_WAIT_SECONDS", 5.0)

    async def run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await agent._await_dialed_number(_FakeCtx(), {"dialed_number": "+911234567890"})
        return result, loop.time() - started

    result, elapsed = asyncio.run(run())
    assert result == "+911234567890"
    assert elapsed < 0.5


def test_waits_for_a_late_joining_sip_participant(monkeypatch):
    """
    For inbound calls the participant often joins just after the agent
    job starts, so its attributes are not there yet at entrypoint time.
    """
    monkeypatch.setattr(agent_config, "BACKEND_PLATFORM_KEY", "some-key")
    monkeypatch.setattr(agent_config, "DIALED_NUMBER_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(agent_config, "DIALED_NUMBER_POLL_SECONDS", 0.05)

    ctx = _FakeCtx()

    async def run():
        async def join_late():
            await asyncio.sleep(0.2)
            ctx.room.remote_participants["late"] = _FakeParticipant(
                {"sip.trunkPhoneNumber": "+918887776665"}
            )

        task = asyncio.create_task(join_late())
        result = await agent._await_dialed_number(ctx, {})
        await task
        return result

    assert asyncio.run(run()) == "+918887776665"


def test_the_wait_is_bounded(monkeypatch):
    """The greeting must not be held hostage by a number that never arrives."""
    monkeypatch.setattr(agent_config, "BACKEND_PLATFORM_KEY", "some-key")
    monkeypatch.setattr(agent_config, "DIALED_NUMBER_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(agent_config, "DIALED_NUMBER_POLL_SECONDS", 0.05)

    async def run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await agent._await_dialed_number(_FakeCtx(), {})
        return result, loop.time() - started

    result, elapsed = asyncio.run(run())
    assert result is None
    assert elapsed < 1.5, f"wait overran its budget ({elapsed:.2f}s)"


# ---------------------------------------------------------------------
# Per-clinic voice
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "profile,expected",
    [
        ("Anushka (Warm, Indian English)", "anushka"),
        ("Dr. Aravind", "aravind"),
        ("alloy", "alloy"),
        ("Dr. Sarah (Calm, Professional)", None),  # unrecognised -> leave defaults alone
        ("", None),
        (None, None),
    ],
)
def test_voice_profile_mapping(profile, expected):
    tenant = types.SimpleNamespace(voice_profile=profile)
    assert agent._tts_voice_for(tenant) == expected


def test_unknown_voice_profile_cannot_break_audio():
    """
    A clinic types free text into the voice-profile field. Anything
    unrecognised must return None so _build_tts keeps its env/default
    behaviour rather than passing junk to the TTS provider.
    """
    assert agent._tts_voice_for(types.SimpleNamespace(voice_profile="!!! nonsense !!!")) is None
    assert agent._tts_voice_for(types.SimpleNamespace()) is None


# ---------------------------------------------------------------------
# Per-call client wiring
# ---------------------------------------------------------------------

def test_transfer_functions_holds_its_own_client():
    """
    Tenant state lives on the per-call object, not at module scope —
    that is what stops two concurrent calls sharing a clinic.
    """
    import backend_client

    client_a = backend_client.TenantClient.for_api_key("key-a")
    client_b = backend_client.TenantClient.for_api_key("key-b")
    ctx_a = agent.TransferFunctions(_FakeCtx(), api=client_a)
    ctx_b = agent.TransferFunctions(_FakeCtx(), api=client_b)

    assert ctx_a.api is client_a
    assert ctx_b.api is client_b
    assert ctx_a.api is not ctx_b.api

    # And no module-level client survives for them to fall back onto.
    assert not hasattr(backend_client, "_client")

    async def _close():
        await asyncio.gather(client_a.aclose(), client_b.aclose())

    asyncio.run(_close())


def test_resolve_start_at_recovery_is_unchanged():
    """
    The small-model workaround that turns a bare "10:00" back into a full
    slot timestamp. It depends on availability returning naive
    clinic-local strings, so it is pinned here as well as on the backend.
    """
    fnc = agent.TransferFunctions(_FakeCtx(), api=None)
    fnc.last_availability = {
        "doctor_id": "doc-1",
        "date": "2030-03-04",
        "slots": [{"start_at": "2030-03-04T10:00:00"}, {"start_at": "2030-03-04T10:30:00"}],
    }
    assert fnc._resolve_start_at("10:30", None, "doc-1") == "2030-03-04T10:30:00"
    assert fnc._resolve_start_at("2030-03-04T09:00:00", None, "doc-1") == "2030-03-04T09:00:00"
    assert fnc._resolve_start_at("11:00", "2030-03-04", "other-doc") == "2030-03-04T11:00:00"
