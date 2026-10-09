"""
Deciding, on the server, whether a call should reach a person (GAP-T9).

WHAT WAS WRONG

`RoutingRule` was a title and a sentence of prose. Both went into the
agent's system prompt, and whether a rule fired was whether the language
model recognised it on that particular call. `escalation_rules_for` said
as much in its own docstring, and named the missing piece: "a
deterministic pre-check belongs here as well eventually -- it needs a
real `keywords` column first, so it is deliberately not faked out of this
prose."

The consequence is not that the model is bad at this. It is that the
behaviour is unrepeatable and unauditable: two identical calls can
resolve differently, no test can pin a clinic's rule, and afterwards
nothing records which rule applied because no rule ever "applied" -- a
model produced a transfer and the reason lived in its weights.

WHAT THIS DOES INSTEAD -- AND WHAT IT DELIBERATELY DOES NOT

Three condition types are evaluated here, from the same inputs, to the
same answer, every time. `model` stays exactly as it was.

That coexistence is the design, not a migration stage. A clinic's
English keyword list will not catch a caller who says the Telugu for
"chest pain"; the prose rule and the EMERGENCY block in the prompt are
still doing work behind it. **Escalation is layered.** Anyone tempted to
delete the prompt path once this exists should read that sentence again
-- the deterministic layer is narrower than the model layer, not better
than it, and removing the wider one to keep the sharper one loses calls.

WHY IT FAILS OPEN, EXCEPT ONCE

An escalation rule that fails by transferring costs a transfer; one that
fails by not transferring can cost far more. So a rule with a NULL
`enabled`, an unrecognised condition type, or a malformed keyword list
errs towards the caller reaching a person.

`outside_hours` is the exception and inverts that. It is evaluated from
doctors' working hours, and a clinic with no doctors configured has no
hours -- under a fail-open reading, *every* call at *every* time would
escalate, at which point every caller is transferred to a line that is,
by the rule's own premise, unstaffed. An unanswerable condition
therefore does not fire. That is a deliberate exception to the rule
above, and it is the only one.

MATCHING IS SUBSTRING, NOT WORD-BOUNDARY

`\\b` does not do what an English reader expects against Devanagari or
Telugu, and the whole reason `keywords` is a Unicode column is that
those scripts must work. Substring matching over-matches slightly
("urgent" inside a longer word) and over-matching means an extra
transfer, which is the safe direction here.
"""

import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import models, timezones

logger = logging.getLogger("escalation")

#: The closed vocabulary. `schemas` serves this to the settings screen
#: rather than the screen hardcoding it -- the same rule F14 arrived at
#: for webhook events, after a UI offered three events that did not exist.
CONDITION_TYPES = (
    "model",           # prose in the prompt; the model decides. The default.
    "keyword",         # the caller said one of these terms
    "unknown_caller",  # the number matched no patient on file
    "outside_hours",   # no doctor is working at this time
)

#: Conditions this module can answer. `model` is not among them by
#: definition: it is answered by the model, in the prompt.
DETERMINISTIC_TYPES = tuple(t for t in CONDITION_TYPES if t != "model")

#: A clinic could otherwise paste an essay and have every call scan it.
MAX_KEYWORDS = 40
MAX_KEYWORD_CHARS = 60


@dataclass
class Match:
    """A rule that fired, and everything needed to act on it."""

    rule_id: str
    title: str
    condition_type: str
    destination: Optional[str]
    matched_on: Optional[str] = None  # the keyword, when there was one


def normalize(text: str) -> str:
    """
    Casefold and NFKC-normalise, so a keyword matches what was actually
    said rather than how it happened to be encoded.

    NFKC matters here specifically: Devanagari and Telugu both have
    characters with more than one valid encoding, and a clinic typing a
    keyword on one keyboard while Sarvam emits the other would produce a
    rule that silently never matches.
    """
    return unicodedata.normalize("NFKC", (text or "")).casefold()


def parse_keywords(raw: Optional[str]) -> list[str]:
    """Split the stored comma-separated list, bounded and normalised."""
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        term = normalize(part).strip()
        if term and len(term) <= MAX_KEYWORD_CHARS:
            out.append(term)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


def active_rules(db: Session, org_id: str) -> list[models.RoutingRule]:
    """
    This clinic's enabled rules, in evaluation order.

    A NULL `enabled` counts as enabled: the model defaults it to True,
    `sync_missing_columns` never backfills, and for an escalation rule
    failing towards a human is the safe direction.
    """
    rows = (
        db.query(models.RoutingRule)
        .filter(
            models.RoutingRule.org_id == org_id,
            or_(
                models.RoutingRule.enabled == True,  # noqa: E712 - SQL Server BIT compatibility
                models.RoutingRule.enabled.is_(None),
            ),
        )
        .all()
    )
    # Ordered in Python, not SQL: `priority` is nullable (rows written
    # before 0032 hold NULL) and the dialects disagree about where NULLs
    # sort. Title breaks a tie so the order is total and repeatable.
    return sorted(rows, key=lambda r: (r.priority if r.priority is not None else 100,
                                       (r.title or "").casefold()))


def condition_of(rule: models.RoutingRule) -> str:
    """
    NULL, blank, or a value this version does not recognise all read as
    `model` -- the behaviour every rule had before 0032, and the one that
    still puts the rule in front of the model rather than dropping it.

    Dropping an unrecognised rule would mean a downgrade silently
    switching off a clinic's escalation. Treating it as prose keeps it
    working, just less sharply.
    """
    value = (rule.condition_type or "").strip().lower()
    if value not in CONDITION_TYPES:
        if value:
            logger.warning(
                "routing rule %s has unrecognised condition_type %r; "
                "treating it as prose for the model",
                rule.id, rule.condition_type,
            )
        return "model"
    return value


def _no_doctor_is_working(db: Session, org_id: str, now_local: datetime) -> bool:
    """
    Whether the clinic is outside its working hours.

    Derived from `Doctor.work_start_hour` / `work_end_hour`, which is the
    only structured hours this schema holds -- `ClinicInfo.timings_text`
    is free text a human wrote and is not parseable into a decision that
    routes live calls.

    Say what that means honestly: this is "no doctor is rostered at this
    hour", not "reception has gone home". They are usually the same and
    occasionally are not.

    Returns False when the clinic has no doctors at all. See the module
    docstring: this is the one condition that fails *closed*, because a
    clinic with no configured hours would otherwise escalate every call
    at every hour, to a line the rule itself presumes is unstaffed.
    """
    doctors = (
        db.query(models.Doctor)
        .filter(models.Doctor.org_id == org_id)
        .all()
    )
    if not doctors:
        return False

    hour = now_local.hour
    for doctor in doctors:
        start = doctor.work_start_hour
        end = doctor.work_end_hour
        if start is None or end is None:
            continue
        if start <= hour < end:
            return False
    return True


def evaluate(
    db: Session,
    org_id: str,
    *,
    utterance: Optional[str] = None,
    caller_is_known: Optional[bool] = None,
    now_local: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
) -> Optional[Match]:
    """
    The first rule that fires, or None.

    Every argument is optional because the caller does not always know
    everything: a condition whose input is missing simply does not
    match, rather than guessing. `caller_is_known=None` means "nobody
    checked", which is not the same as "the caller is unknown", and
    conflating them would escalate every call from a clinic that never
    ran caller recognition.

    `now_local` is the clinic's wall clock (see app/timezones.py). It is
    derived from `timezone_name` when not supplied; passing a naive UTC
    datetime here would put a UTC+5:30 clinic's evening five and a half
    hours out, which is the same trap `send_appointment_reminders` has a
    dedicated test for.
    """
    text = normalize(utterance) if utterance else ""

    for rule in active_rules(db, org_id):
        kind = condition_of(rule)

        if kind == "keyword":
            if not text:
                continue
            for term in parse_keywords(rule.keywords):
                if term in text:
                    return Match(
                        rule_id=rule.id, title=rule.title, condition_type=kind,
                        destination=rule.destination, matched_on=term,
                    )

        elif kind == "unknown_caller":
            if caller_is_known is False:
                return Match(
                    rule_id=rule.id, title=rule.title, condition_type=kind,
                    destination=rule.destination,
                )

        elif kind == "outside_hours":
            when = now_local or timezones.now_local(timezones.resolve(timezone_name))
            if _no_doctor_is_working(db, org_id, when):
                return Match(
                    rule_id=rule.id, title=rule.title, condition_type=kind,
                    destination=rule.destination,
                )

        # `model` is not evaluated here by definition -- it is answered in
        # the prompt, by the model, which is the point of the type.

    return None


def has_deterministic_rules(db: Session, org_id: str) -> bool:
    """
    Whether it is worth the agent asking at all.

    The agent consults `POST /agent/escalation-check` once per caller
    turn, and a clinic with no structured rules must not pay a round trip
    per turn for an answer that is always None. Resolve-tenant sends this
    so the agent knows to stay quiet.
    """
    return any(
        condition_of(rule) in DETERMINISTIC_TYPES
        for rule in active_rules(db, org_id)
    )
