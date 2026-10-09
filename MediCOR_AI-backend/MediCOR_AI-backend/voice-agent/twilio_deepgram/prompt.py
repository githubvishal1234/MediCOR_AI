"""
The system prompt every live Deepgram call receives.

A deliberate port of `agent.build_instructions()`, not a rewrite. That
prompt is the highest-stakes string in the product and it was argued
into its present shape one incident at a time; starting fresh here would
have thrown away the emergency block, the accessibility rules, the
refill boundary and the caller-recognition caveat, and nobody would have
noticed until a call went wrong.

WHAT CHANGED, AND WHY EACH CHANGE WAS NECESSARY

  * **Tool names.** The LiveKit prompt describes one `clinic_action`
    router with fourteen action values, because Groq counts every
    property of every tool schema against the same TPM budget as the
    reply, and fourteen small schemas cost more than one. Deepgram's
    managed LLMs carry no such budget, so tools.py exposes discrete,
    self-describing functions and this prompt names them. See the note
    in tools.py -- that is a considered reversal of a considered
    decision, not a lapse.

  * **Language.** The LiveKit stack could speak Hindi and Telugu because
    Sarvam could. **This one is English-only, deliberately and for now.**
    Deepgram's TTS cannot pronounce either language and the API will not
    accept Sarvam as a speak provider, so promising three languages here
    would have the agent cheerfully answer in a language it physically
    cannot say. The language line is therefore built from what the
    deployment has actually configured, and the prompt tells the model to
    hand a non-English caller to a person rather than to struggle on --
    which is the honest behaviour, and better than the LiveKit stack's
    for any language outside its three.

WHAT DID NOT CHANGE, AND MUST NOT

  * The EMERGENCY block is first and outranks everything after it.
  * Its wording is configuration, not code (config.EMERGENCY_*), because
    the number is territory-specific and the sentence is a clinical
    statement belonging to the clinic.
  * The refill rules say, in the prompt as well as in the backend, that
    recording a request is not approving one.
  * A recognised caller's name is described as a guess.

`tests/test_twilio_deepgram_prompt.py` pins all of that.
"""

from typing import Optional

import config as agent_config

# Which spoken languages the configured TTS can actually produce.
#
# **This deployment is English-only, by decision.** Not merely by
# limitation: Deepgram's Aura voices could not speak Hindi or Telugu even
# if we wanted them to, but the choice to ship English first and add
# languages later is a product one, and it is the reason the LiveKit +
# Sarvam stack is still runnable rather than deleted.
#
# Keyed on the TTS provider because that is what physically decides it.
# The `eleven_labs` row is the documented route to Hindi and Telugu when
# that decision is revisited; it is configuration, not a plan.
#
# Deliberately a claim about the *voice*, not about the listener: the STT
# understands more than the agent can speak back, and telling the model it
# may reply in a language the TTS will mangle is worse than not offering
# it. That asymmetry is why the VOICE block below tells the model to
# transfer rather than to struggle on.
_SPOKEN_LANGUAGES_BY_TTS_PROVIDER = {
    "deepgram": "English only",
    "open_ai": "English only",
    "aws_polly": "English only",
    "eleven_labs": "English, Hindi or Telugu - match the caller",
    "cartesia": "English only",
}


def spoken_languages(tts_provider: str) -> str:
    """What the prompt should claim the agent can say out loud."""
    return _SPOKEN_LANGUAGES_BY_TTS_PROVIDER.get(
        (tts_provider or "").strip().lower(), "English"
    )


def build_instructions(
    recognised_name: Optional[str] = None,
    escalation_rules: Optional[list[str]] = None,
    tts_provider: str = "deepgram",
) -> str:
    """
    Assemble the prompt for one call.

    Returns a value rather than assigning a module constant so it can be
    asserted on -- the same reason `agent.build_instructions` is shaped
    this way. Kept short: a prompt is re-sent as context on every turn,
    so every line here is paid for repeatedly.
    """
    instructions = """
You are MediCOR, a clinic phone receptionist on a live telephone call.

VOICE:
- Speak SPOKEN_LANGUAGES.
- If the caller is speaking a language you cannot answer in, do not try.
  Say once, in English, that you will put them through to someone who can
  help, then call transfer_to_human. Do not apologise at length, do not
  attempt a few words of their language, and do not simply carry on in
  English hoping they follow.
- Keep replies to 1-2 short sentences. This is a phone call, not an email.
- Ask ONE question at a time and wait for the answer.
- Never read out an ID, a raw timestamp, or a URL.
- Use plain ASCII punctuation only.

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
- "slower", "speak slowly", "I cannot follow you" -> call speak_slower,
  then carry on.
- "say that again", "repeat", "sorry?" -> repeat your last sentence in
  simpler words. Do not call a tool for this.
- "a person", "a human", "someone real" -> call transfer_to_human straight
  away. Do not try to help first and do not ask them to explain.

TRUTHFULNESS (the rule this whole system exists to enforce):
- Never invent a patient, doctor, appointment, time, fee, or policy.
- Only offer appointment times that check_available_slots actually
  returned to you.
- Only say an appointment is booked, moved, or cancelled AFTER the tool
  has come back successfully. If a tool fails, say you could not complete
  it and offer to put them through - never guess at the outcome.
- If a tool returns an error, do not read it out. Apologise briefly and
  offer to transfer.
- Never give medical advice.

PATIENT:
- find_patient first, using their phone number or full name.
- If there is no match, register_patient - but only after they have given
  you their name and confirmed their phone number back to you.
- get_patient_history only when they explicitly ask about their own
  records.

APPOINTMENT (do not skip a step):
- Ask what they need and roughly when.
- list_doctors, then check_available_slots for that doctor and date.
- Offer at most 2-3 of the times you were given.
- Repeat the chosen day and time back and get a clear yes.
- book_appointment, then tell them it is confirmed.
- get_patient_appointments before reschedule_appointment or
  cancel_appointment, so you are acting on the right one.
- Require an explicit confirmation before cancelling.

INFO:
- clinic_info for hours, address and contact details.
- list_services for what the clinic offers and what it costs.
- answer_question for anything else about this clinic.

REFILLS:
- request_refill RECORDS a repeat-prescription request for the clinic to
  review. It never approves one.
- Identify the patient first, then read the medicine name back to them.
- Say it has been passed to the clinic. Never say it is approved, ready,
  collected, or safe to take.
- Never advise on dose, timing, or whether to keep taking anything.
- Anything clinical beyond writing down the name: transfer_to_human.

ENDING:
- When the caller says goodbye, or the reason for the call is done and
  they need nothing else, say a short goodbye and call end_call.
"""

    # The emergency wording is configuration, not code: the number is
    # territory-specific and the sentence is a clinical decision that
    # belongs to the clinic. Substituted rather than interpolated because
    # the prompt is full of characters an f-string would fight with.
    instructions = instructions.replace(
        "EMERGENCY_LINE",
        agent_config.EMERGENCY_INSTRUCTION.replace(
            "{number}", agent_config.EMERGENCY_NUMBER
        ),
    )
    instructions = instructions.replace("SPOKEN_LANGUAGES", spoken_languages(tts_provider))

    # The greeting opens by asking whether it is this person. The model
    # has to know that is a guess drawn from the calling number, not an
    # established fact -- otherwise it proceeds as though the caller has
    # been identified, which for a shared handset is how one family
    # member ends up hearing another's appointments.
    if recognised_name:
        instructions += (
            "\nCALLER:\n"
            f"- This number usually belongs to {recognised_name}. That is a guess,\n"
            "  not a confirmed identity.\n"
            "- Your greeting already asked whether it is them. If they say yes,\n"
            "  still run find_patient before using any of their details.\n"
            "- If they say no, or are someone else, ignore the name completely.\n"
        )

    # The clinic's own prose rules, rendered by the backend. Nothing is
    # appended when a clinic has none: an empty section would still be
    # re-sent as context on every turn of every call, for nothing.
    if escalation_rules:
        rules_block = "\n".join(f"- {rule}" for rule in escalation_rules)
        instructions += (
            "\nESCALATION:\n"
            "- If any rule below matches what the caller says, call\n"
            "  transfer_to_human immediately and tell them you are putting\n"
            "  them through.\n" + rules_block + "\n"
        )

    return instructions
