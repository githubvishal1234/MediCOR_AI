"""
The live system prompt (PF-03 emergency handling, PF-02 caller recognition).

`agent.build_instructions()` returns the exact string every call's model
receives. Before it was hoisted out of `entrypoint()` nothing could assert
on it: the highest-stakes text in the product sat inside a 400-line async
function that needs a LiveKit room to run, and "it looked right when I
read it" was the only check it had.

Two things here are safety-relevant rather than cosmetic:

  * **The EMERGENCY block.** Until PF-03 the prompt contained no notion of
    urgency at all -- a caller describing chest pain was taken into the
    appointment-booking flow. Escalation existed only if a clinic had
    thought to write a routing rule and the model happened to match it,
    which is advisory, not a safety net.

  * **The caller-recognition note.** The greeting opens by asking whether
    it is a specific person, guessed from their phone number. If the model
    is not told that is a guess, it proceeds as though the caller has been
    identified -- which, for a handset shared within a household, is how
    one family member hears another's appointments.

    pytest tests/test_agent_prompt_safety.py -v
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

import pytest  # noqa: E402


def _stub_plugins():
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


@pytest.fixture(scope="module")
def agent():
    _stub_plugins()
    import agent as agent_module
    return agent_module


@pytest.fixture()
def prompt(agent):
    return agent.build_instructions()


# ----------------------------------------------------------- emergency

def test_the_prompt_has_an_emergency_block(prompt):
    assert "EMERGENCY" in prompt, (
        "the live prompt has no emergency handling; a caller describing "
        "chest pain would be taken into the booking flow"
    )


def test_the_emergency_block_outranks_everything_else(prompt):
    """
    Ordering is not decoration in a prompt this small. The model reads the
    emergency instruction before it reads anything about booking, and the
    block says so explicitly.
    """
    assert prompt.index("EMERGENCY") < prompt.index("APPOINTMENT:")
    assert prompt.index("EMERGENCY") < prompt.index("RULES:")
    assert "outranks every other instruction" in prompt


def test_the_emergency_block_names_symptoms_not_diagnoses(prompt):
    """
    It lists what a caller might *say*, so the model can recognise it, and
    forbids the agent from doing anything clinical with that recognition.
    """
    block = prompt[prompt.index("EMERGENCY"):prompt.index("RULES:")]
    for symptom in ("chest pain", "breathing", "bleeding", "stroke", "self-harm"):
        assert symptom in block.lower(), f"{symptom} missing from the emergency block"
    assert "do not diagnose" in block.lower()
    assert "do not advise" in block.lower()


def test_the_spoken_wording_is_configuration_not_hardcoded(agent, monkeypatch):
    """
    The number is territory-specific and the sentence is a clinical
    decision belonging to the clinic, so neither may be frozen in code.
    """
    import config

    monkeypatch.setattr(config, "EMERGENCY_NUMBER", "999")
    monkeypatch.setattr(
        config, "EMERGENCY_INSTRUCTION", "Ring {number} immediately."
    )
    out = agent.build_instructions()
    assert "Ring 999 immediately." in out
    # The placeholder must be fully substituted -- shipping the literal
    # token to the model would have it say "EMERGENCY_LINE" out loud.
    assert "EMERGENCY_LINE" not in out


def test_the_placeholder_never_survives_into_the_live_prompt(prompt):
    assert "EMERGENCY_LINE" not in prompt
    assert "{number}" not in prompt


def test_uncertainty_resolves_towards_escalation(prompt):
    """
    The asymmetry is deliberate and worth pinning: being wrong towards
    escalation costs a transfer, being wrong the other way costs far more.
    """
    block = prompt[prompt.index("EMERGENCY"):prompt.index("RULES:")]
    assert "doubt" in block.lower()


# -------------------------------------------------- caller recognition

def test_no_caller_block_when_nobody_was_recognised(prompt):
    """
    The prompt is charged against the token budget of every turn of every
    call, so a section that says nothing must not be there at all.
    """
    assert "CALLER:" not in prompt


def test_a_recognised_name_is_presented_as_a_guess(agent):
    out = agent.build_instructions(recognised_name="Anitha")
    assert "CALLER:" in out
    assert "Anitha" in out
    assert "guess" in out.lower()
    assert "not a confirmed identity" in out


def test_recognition_does_not_excuse_the_normal_lookup(agent):
    """
    Greeting someone by name is a courtesy; it is not authentication. The
    model is told to run the ordinary lookup even after a "yes".
    """
    out = agent.build_instructions(recognised_name="Anitha")
    assert "still run patient_lookup" in out
    assert "ignore the name completely" in out


# ------------------------------------------------------ other standing rules

def test_the_clinical_boundaries_are_still_stated(prompt):
    """
    These predate PF-03 and must survive any future edit of the block
    above them.
    """
    lowered = prompt.lower()
    assert "never give medical advice" in lowered
    assert "never guess" in lowered
    # PF-16
    assert "ACCESSIBILITY" in prompt
    # F9: the agent records a refill request, it never approves one.
    assert "only RECORDS the request" in prompt
    assert "never say it is approved" in lowered


def test_the_prompt_stays_small(prompt):
    """
    Groq counts prompt tokens against the same budget as the reply, on
    every turn. This is a smoke alarm for a prompt that has quietly grown
    by accretion, not a hard limit -- raise it deliberately if a section
    genuinely earns its place.
    """
    assert len(prompt) < 4000, (
        f"the live prompt is {len(prompt)} chars; every call pays for it twice over"
    )


def test_there_is_no_second_prompt_to_drift(agent):
    """
    Replaced the test that used to watch `config.SYSTEM_PROMPT`.

    That constant was a fallback prompt kept in configuration, and it had
    drifted exactly as a duplicate does: it named six tools that no longer
    existed (all replaced by the single `clinic_action`), offered English
    and Hindi when the pipeline handles Telugu, and carried no emergency
    block at all. It was unreachable in practice, which is the only reason
    it never hurt anyone.

    It is gone, and the fallback now calls `build_instructions()` -- so
    there is one prompt, and it is the tested one. This guards that: a
    second prompt constant reappearing in the agent's configuration is the
    thing to catch, because the copy nobody looks at is the copy that goes
    stale.
    """
    import config

    assert not hasattr(config, "SYSTEM_PROMPT"), (
        "a second prompt has reappeared in the agent's configuration; the "
        "live one is agent.build_instructions() and a duplicate will drift"
    )

    src = open(agent.__file__, encoding="utf-8").read()
    assert "instructions or build_instructions()" in src, (
        "the fallback no longer points at the real prompt builder"
    )
    # And the live prompt is genuinely the current contract.
    assert "clinic_action" in agent.build_instructions()
