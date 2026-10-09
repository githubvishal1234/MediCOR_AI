"""
The scheduled jobs themselves. `app/scheduler.py` is the machinery; this
is the policy.

Keeping them apart matters because the decisions here are clinical/product
judgements — how long is "abandoned", what status an unfinished call
deserves — and they should be reviewable without reading a scheduling
loop.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import (
    evaluation_service,
    llm_service,
    models,
    notification_service,
    scheduler,
    timezones,
    webhook_service,
)
from .config import settings
from .realtime import call_summary, manager

logger = logging.getLogger("jobs")

# Statuses that mean the call is over. Mirrors _TERMINAL_STATUSES in
# routers/calls.py; kept as its own copy rather than imported to avoid a
# circular import between a router and a background job.
_TERMINAL = {"completed", "failed", "no_answer"}


def reap_stale_calls(db: Session) -> Optional[str]:
    """
    Close out calls that were never finalised.

    THE BUG THIS FIXES

    `voice-agent/agent.py` finalises a call from a LiveKit shutdown
    callback. That callback does not run if the worker process is killed,
    OOMs, loses its host, or the room is torn down abnormally — and
    nothing else ever revisits the row. The call then stays `in_progress`
    permanently, which is not merely cosmetic:

      * the dashboard shows a live call that ended hours ago;
      * `analytics_service` treats it as **connected** (only `failed` and
        `no_answer` are counted as missed), so it sits in the resolution
        rate's denominator forever and drags it down;
      * it never crosses into a terminal status, so it never receives the
        LLM summary, never reaches the HV-7 callback queue, and never
        triggers usage threshold checks.

    Before this job the only thing that touched such rows was
    `seed._reconcile_stale_demo_calls`, which is demo-only (it matches
    four hardcoded patient names), development-only, and runs once at
    startup. Real calls were never reconciled.

    WHICH STATUS, AND WHY IT IS DECIDED BY EVIDENCE

    A stalled call is not automatically a failure. The transcript says
    which happened:

      * **transcript messages present** -> a real conversation took place
        and only the bookkeeping was lost, so the call is `completed`.
        Marking it `failed` would push a patient who was actually served
        into the callback queue and invite a pointless second call.

      * **no transcript** -> nothing was ever said. `failed` is honest,
        and HV-7 will correctly offer it for callback.

    WHAT IT DELIBERATELY DOES NOT DO

    It does not invent `duration_seconds`. The true duration is unknowable
    once the agent is gone, and a fabricated one would flow straight into
    `ai_handled_minutes` — the metric that tells a clinic how much staff
    time the AI saved. `analytics_service` already guards on falsy
    durations, so leaving it NULL is both safe and truthful.

    It does not write an `ai_summary` either: `call_intelligence` derives
    one from the transcript at the moment of transition in the router, and
    a summary asserted by a sweeper hours later would look identical to
    one the system actually produced.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=settings.stale_call_timeout_minutes)

    stale = (
        db.query(models.CallLog)
        .filter(
            models.CallLog.status == "in_progress",
            # `started_at` is UTC-naive (app/timezones.py), as is the
            # cutoff. Rows with no started_at fall back to occurred_at.
            models.CallLog.started_at.isnot(None),
            models.CallLog.started_at < cutoff,
        )
        .all()
    )
    if not stale:
        return None

    ended_at = datetime.utcnow()
    completed = failed = 0

    for call in stale:
        spoke = (
            db.query(models.TranscriptMessage.id)
            .filter(models.TranscriptMessage.call_id == call.id)
            .first()
            is not None
        )
        call.status = "completed" if spoke else "failed"
        call.ended_at = ended_at

        if spoke:
            completed += 1
        else:
            failed += 1
            # Same rule the router applies on a missed terminal status,
            # so a reaped dead call reaches the callback queue exactly as
            # a live one would have.
            if call.callback_status is None:
                call.callback_status = "pending"
                call.callback_updated_at = ended_at

    db.commit()

    # Tell any dashboard that is watching, so the phantom "live" call
    # disappears without a refresh. Non-fatal by construction: broadcast
    # hands off to the bound loop and swallows its own failures.
    for call in stale:
        db.refresh(call)
        manager.broadcast(call.org_id, "call.ended", call_summary(call))

    return (
        f"reaped {len(stale)} stalled call(s): "
        f"{completed} completed (had transcript), {failed} failed (silent)"
    )


def evaluate_recent_calls(db: Session) -> Optional[str]:
    """
    Judge a bounded sample of each clinic's unevaluated calls.

    AI-7 built the whole judging harness — `evaluation_service`, the
    rubric, `needs_review`, the review queue — and then left it entirely
    manual: `POST /quality/run` only fires when an admin clicks it. A
    quality signal nobody remembers to collect is not a quality signal.

    THIS IS OFF BY DEFAULT, ON PURPOSE.

    Every judged call is a paid model request, and the AI-7 design note
    says outright that judging every call doubles the per-call LLM cost
    for a signal nobody reads live. That is a decision the owner already
    made and wrote down; a scheduled job that silently reverses it would
    be spending their money to overrule them. `QUALITY_AUTO_EVAL_ENABLED`
    is the opt-in.

    Two bounds even once enabled:

      * `quality_auto_eval_per_org_limit` caps each clinic **per run**, so
        a sudden surge in call volume cannot turn into a surge in spend;
      * suspended clinics are skipped — a suspended org should not be
        consuming paid model calls on the platform's behalf.
    """
    if not settings.quality_auto_eval_enabled:
        return None

    # No judge configured is the normal state when GROQ_API_KEY is unset.
    # Say so once rather than looping over every org to discover it.
    if not llm_service.is_enabled():
        return "skipped: no LLM configured (GROQ_API_KEY unset)"

    orgs = _active_orgs(db)
    if not orgs:
        return None

    limit = max(1, settings.quality_auto_eval_per_org_limit)
    judged = 0
    for org in orgs:
        # run_batch_task opens and closes its own Session and swallows its
        # own failures, so one clinic's bad transcript cannot abort the
        # sweep for the rest.
        judged += evaluation_service.run_batch_task(org.id, limit)

    if not judged:
        return None
    return f"judged {judged} call(s) across {len(orgs)} clinic(s)"


def _active_orgs(db: Session) -> list[models.Organization]:
    """
    Every clinic a scheduled job should act for.

    `suspended` may be NULL on rows that predate the column —
    `sync_missing_columns` only ever adds nullable columns and never
    backfills — and NULL means "not suspended" here, not "unknown, skip".
    """
    return (
        db.query(models.Organization)
        .filter(
            or_(
                models.Organization.suspended == False,  # noqa: E712 - SQL Server BIT
                models.Organization.suspended.is_(None),
            )
        )
        .all()
    )


def send_weekly_digests(db: Session) -> Optional[str]:
    """
    Email each clinic's admins a summary of their own last seven days.

    The numbers already existed — `analytics_service` has computed all of
    them since HV-9 — but only for someone who opened the Analytics page
    and picked a range. A clinic that never opens the dashboard is
    precisely the one that cannot tell whether the product is working,
    which is the same clinic that churns.

    Nothing here is estimated: call counts, resolution rate, AI-handled
    minutes and the no-show rate are all counted from real rows, and a
    rate with nothing to measure renders as an em dash rather than 0%.

    Silent when SMTP is unconfigured, which is the current state — see
    `weekly_digest_enabled` in config.py for why this defaults on anyway.
    """
    if not settings.weekly_digest_enabled:
        return None
    if not notification_service._email_configured():
        return "skipped: no SMTP configured"

    sent = 0
    clinics = 0
    for org in _active_orgs(db):
        try:
            count = notification_service.send_weekly_digest(
                db, org, settings.weekly_digest_days
            )
        except Exception:  # noqa: BLE001 - one clinic's bad data must not stop the rest
            logger.exception("Weekly digest failed for org=%s", org.id)
            continue
        if count:
            sent += count
            clinics += 1

    if not sent:
        return None
    return f"sent {sent} digest email(s) to {clinics} clinic(s)"


def send_appointment_reminders(db: Session) -> Optional[str]:
    """
    Remind patients about appointments coming up shortly.

    The single most requested clinic feature, and structurally impossible
    before F2: `notification_service` could already compose and send the
    message, but nothing could fire it at a time rather than in response
    to a request.

    THE TIMEZONE IS THE WHOLE JOB

    `Appointment.start_at` is clinic **wall-clock** naive, not UTC (see
    app/timezones.py). "Within the next 24 hours" therefore has to be
    computed per clinic, against that clinic's own current wall clock —
    `timezones.now_local(tz)`, never `datetime.utcnow()`. Getting this
    wrong would not error; it would quietly remind a UTC+5:30 clinic's
    patients five and a half hours off, which for a 24-hour lead means
    some are reminded twice and some not at all.

    That is also why the sweep runs hourly instead of at a fixed hour: an
    interval-based scheduler has no notion of "9am", and 9am is a
    different instant for every clinic. An hourly pass catches each one's
    window without the job needing to know.

    WHY THE WINDOW STARTS AT NOW

    Only appointments between now and now+lead are eligible. Without the
    lower bound, an appointment that has already started — or one from
    last week whose status nobody updated — would be "within 24 hours" of
    the cutoff and get a reminder for a slot that has passed.

    `reminder_sent_at` stops a second send. The scheduler's run_key
    already prevents two workers racing one tick; this prevents the same
    patient being reminded again on the *next* tick, after a restart, or
    if the lead time is widened.
    **Why `no_consent` is reported.** `Patient.contact_consent` is
    tri-state and NULL means nobody has ever asked;
    `CONTACT_UNASKED_IS_CONSENT` decides how NULL reads and defaults to
    the cautious answer. A clinic that has just wired up messaging and
    never recorded consent therefore gets zero sends -- and with no
    figure for it, cannot tell that from a broken transport. Same
    reasoning as registering `evaluate_recent_calls` even while it is
    switched off: a run row has to distinguish "correctly doing nothing"
    from "failing". Counted with `notification_service.may_contact`, the
    same predicate the send wrappers enforce with, so the tally cannot
    drift from the decision -- and the gate itself stays inside those
    wrappers, where a new caller cannot forget it.
    """
    if not settings.appointment_reminders_enabled:
        return None

    # No channel configured is the normal state here. Say so once rather
    # than walking every clinic's calendar to discover it.
    if not (
        notification_service._email_configured()
        or notification_service._whatsapp_configured()
    ):
        return "skipped: no email or WhatsApp configured"

    lead = max(1, settings.appointment_reminder_lead_hours)
    sent = 0
    considered = 0
    no_consent = 0

    for org in _active_orgs(db):
        tz = timezones.org_timezone(org)
        now_local = timezones.now_local(tz)
        until_local = now_local + timedelta(hours=lead)

        due = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.org_id == org.id,
                models.Appointment.status == "upcoming",
                models.Appointment.reminder_sent_at.is_(None),
                models.Appointment.start_at.isnot(None),
                models.Appointment.start_at >= now_local,
                models.Appointment.start_at < until_local,
            )
            .all()
        )

        for appt in due:
            considered += 1
            # One extra lookup per due appointment, deliberately: the
            # wrapper resolves the patient itself, but it returns a bare
            # bool because `reminder_sent_at` depends on that contract.
            # Counting here keeps the contract intact.
            reminder_patient = (
                db.query(models.Patient)
                .filter(models.Patient.id == appt.patient_id)
                .first()
                if appt.patient_id else None
            )
            if reminder_patient is not None and not notification_service.may_contact(
                reminder_patient
            ):
                no_consent += 1
            try:
                delivered = notification_service.send_appointment_reminder(
                    db, appt, clinic_name=org.name
                )
            except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
                logger.exception("Reminder failed for appointment=%s", appt.id)
                continue

            if delivered:
                # Marked once dispatched, not once confirmed delivered:
                # the transports swallow their own failures by design, so
                # "it arrived" is not knowable here, and retrying forever
                # against an address that will never accept mail is the
                # worse failure. A patient with no contact details at all
                # returns False and is left unmarked — there is nothing to
                # retry, and nothing was claimed.
                appt.reminder_sent_at = datetime.utcnow()
                sent += 1

        if due:
            db.commit()

    if not considered:
        return None
    return (
        f"reminded {sent} of {considered} upcoming appointment(s)"
        f" no_consent={no_consent}"
    )


def contact_missed_callers(db: Session) -> Optional[str]:
    """
    Reach out to patients whose call did not get through (PF-12).

    A missed call to a clinic is usually a lost booking. The callback
    queue has existed since HV-7 and the reaper fills it, but nothing ever
    acted on it -- a patient was contacted only if a staff member noticed
    the queue and worked it. Nothing aged it, prompted anyone, or
    escalated.

    FOUR RULES, AND EACH OF THEM MATTERS

      * **It does not touch `callback_status`.** That column is the human
        queue -- pending, contacted, resolved. A machine sending "sorry we
        missed you" is not a person having called back, and writing
        "contacted" there would quietly empty the queue staff are meant to
        work. The marker is `callback_message_sent_at`, which is a
        different fact.

      * **A human gets the first chance.** Nothing is sent until the call
        has been in the queue for `MISSED_CALL_OUTREACH_DELAY_MINUTES`.
        This exists to catch what staff did not get to, not to race them
        to the patient.

      * **Only the recent past.** A patient who rang four days ago and
        gave up has moved on; an apology then is noise rather than
        service.

      * **Once, ever, per call.** The marker is written when a message is
        *dispatched*, not when delivery is confirmed -- the transports
        swallow their own failures by design, so arrival is not knowable,
        and retrying forever against an address that will never accept
        mail is the worse failure. A patient with no contact details, or
        who has not consented, is left unmarked: nothing was claimed, so
        there is nothing to suppress.

    Suspended clinics are skipped, so a suspended org cannot spend on the
    platform's behalf -- the same rule the evaluation job follows.
    **Why `no_consent` is reported.** `Patient.contact_consent` is
    tri-state and NULL means nobody has ever asked;
    `CONTACT_UNASKED_IS_CONSENT` decides how NULL reads and defaults to
    the cautious answer. A clinic that has just wired up messaging and
    never recorded consent therefore gets zero sends -- and with no
    figure for it, cannot tell that from a broken transport. Same
    reasoning as registering `evaluate_recent_calls` even while it is
    switched off: a run row has to distinguish "correctly doing nothing"
    from "failing". Counted with `notification_service.may_contact`, the
    same predicate the send wrappers enforce with, so the tally cannot
    drift from the decision -- and the gate itself stays inside those
    wrappers, where a new caller cannot forget it.
    """
    if not settings.missed_call_outreach_enabled:
        return "disabled"

    now = datetime.utcnow()
    ready_before = now - timedelta(minutes=settings.missed_call_outreach_delay_minutes)
    not_older_than = now - timedelta(hours=settings.missed_call_outreach_max_age_hours)

    sent = 0
    considered = 0
    no_consent = 0
    for org in _active_orgs(db):
        rows = (
            db.query(models.CallLog)
            .filter(
                models.CallLog.org_id == org.id,
                models.CallLog.callback_status == "pending",
                models.CallLog.callback_message_sent_at.is_(None),
                models.CallLog.patient_id.isnot(None),
                models.CallLog.occurred_at <= ready_before,
                models.CallLog.occurred_at >= not_older_than,
            )
            .all()
        )
        for call in rows:
            considered += 1
            patient = (
                db.query(models.Patient)
                .filter(
                    models.Patient.id == call.patient_id,
                    # Org-scoped even though the call already is: the
                    # parent being in this org does not prove the child
                    # is, which is the exact shape of the one cross-org
                    # IDOR this codebase has had.
                    models.Patient.org_id == org.id,
                )
                .first()
            )
            if patient is None:
                continue
            if not notification_service.may_contact(patient):
                no_consent += 1
            if notification_service.notify_missed_call(patient, org.name or "our clinic"):
                call.callback_message_sent_at = now
                sent += 1

    if sent:
        db.commit()
    return f"contacted={sent} considered={considered} no_consent={no_consent}"


def request_call_feedback(db: Session) -> Optional[str]:
    """
    Ask recent callers what they thought (PF-10).

    The product has always been thorough about its own opinion of itself:
    every call is scored by a language model, flagged for hallucination
    and policy breaches, and queued for review. None of that ever asked
    the patient. This closes an asymmetry, not a feature gap.

    FOUR THINGS IT WILL NOT DO

      * **Ask twice about the same call.** `feedback_requested_at` is the
        marker, written on dispatch for the same reason
        `reminder_sent_at` is.

      * **Ask a frequent caller every time.** The cooldown is per
        *patient*, not per call. Being surveyed after every contact is how
        a clinic teaches people to ignore its messages.

      * **Ask about a call that failed.** Someone whose call did not
        connect is already getting an apology from
        `contact_missed_callers`; asking them to rate the experience on
        top of that is close to rude.

      * **Ask without consent.** It goes through the same gate as every
        other patient message, so a declined patient is never surveyed.

    Off by default. Every other message this product sends is something
    the patient needs; this is something the clinic wants, and that is a
    decision a clinic should make rather than inherit.
    **Why `no_consent` is reported.** `Patient.contact_consent` is
    tri-state and NULL means nobody has ever asked;
    `CONTACT_UNASKED_IS_CONSENT` decides how NULL reads and defaults to
    the cautious answer. A clinic that has just wired up messaging and
    never recorded consent therefore gets zero sends -- and with no
    figure for it, cannot tell that from a broken transport. Same
    reasoning as registering `evaluate_recent_calls` even while it is
    switched off: a run row has to distinguish "correctly doing nothing"
    from "failing". Counted with `notification_service.may_contact`, the
    same predicate the send wrappers enforce with, so the tally cannot
    drift from the decision -- and the gate itself stays inside those
    wrappers, where a new caller cannot forget it.
    """
    if not settings.feedback_requests_enabled:
        return "disabled"

    now = datetime.utcnow()
    ready_before = now - timedelta(minutes=settings.feedback_request_delay_minutes)
    not_older_than = now - timedelta(hours=settings.feedback_request_max_age_hours)
    cooldown_start = now - timedelta(days=settings.feedback_request_cooldown_days)

    asked = 0
    considered = 0
    no_consent = 0
    for org in _active_orgs(db):
        calls = (
            db.query(models.CallLog)
            .filter(
                models.CallLog.org_id == org.id,
                # Completed only. A failed call is already receiving an
                # apology; asking it to be rated as well is close to rude.
                models.CallLog.status == "completed",
                models.CallLog.feedback_requested_at.is_(None),
                models.CallLog.patient_id.isnot(None),
                models.CallLog.occurred_at <= ready_before,
                models.CallLog.occurred_at >= not_older_than,
            )
            .all()
        )
        for call in calls:
            patient = (
                db.query(models.Patient)
                .filter(
                    models.Patient.id == call.patient_id,
                    models.Patient.org_id == org.id,
                )
                .first()
            )
            if patient is None:
                continue

            # Per-patient cooldown, counted from the last time we ASKED
            # rather than the last time they answered -- someone who
            # ignored the question has told us something too.
            recently_asked = (
                db.query(models.CallLog)
                .filter(
                    models.CallLog.org_id == org.id,
                    models.CallLog.patient_id == patient.id,
                    models.CallLog.feedback_requested_at.isnot(None),
                    models.CallLog.feedback_requested_at >= cooldown_start,
                )
                .first()
            )
            if recently_asked is not None:
                continue

            considered += 1
            if not notification_service.may_contact(patient):
                no_consent += 1
            if notification_service.request_call_feedback(patient, org.name or "our clinic"):
                call.feedback_requested_at = now
                asked += 1
                # Committed inside the loop so the cooldown query above
                # sees this patient on the very next iteration. Without
                # it, two calls from one patient in the same sweep would
                # both be asked.
                db.commit()

    return f"asked={asked} considered={considered} no_consent={no_consent}"


def offer_waiting_list_slots(db: Session) -> Optional[str]:
    """
    Match freed appointment slots to patients waiting for one (PF-15).

    A cancellation is pure lost capacity: the slot reopens and whoever
    wanted it has already been told there was nothing and gone away.

    WHY THIS IS A SWEEP RATHER THAN A CANCELLATION HOOK

    Cancelling happens in three places -- the staff/agent endpoint, the
    self-service link, and an inbound reply -- so a hook would have to be
    remembered in all three, and the third one did not exist when the
    first two were written. A sweep has one call site, and it also catches
    capacity freed by everything that is not a cancellation: a doctor's
    hours widened, a no-show written off, an appointment moved earlier.

    FAIRNESS IS OLDEST-FIRST. Whoever has waited longest is offered first,
    and that is a business rule rather than an ordering convenience.

    ONE PATIENT AT A TIME. An offer is exclusive for
    `WAITING_LIST_OFFER_TTL_MINUTES`, then returns to the pool. Offering
    one slot to five people would mean four disappointments per booking.

    THE SLOT IS NOT HELD. See `models.WaitingListEntry`: a walk-in can
    still take it first, and the acceptance then fails the ordinary
    conflict check. That race is accepted knowingly rather than solved
    with a placeholder appointment that would leak into the calendar,
    availability, analytics and the reaper.
    **Why `no_consent` is reported.** `Patient.contact_consent` is
    tri-state and NULL means nobody has ever asked;
    `CONTACT_UNASKED_IS_CONSENT` decides how NULL reads and defaults to
    the cautious answer. A clinic that has just wired up messaging and
    never recorded consent therefore gets zero sends -- and with no
    figure for it, cannot tell that from a broken transport. Same
    reasoning as registering `evaluate_recent_calls` even while it is
    switched off: a run row has to distinguish "correctly doing nothing"
    from "failing". Counted with `notification_service.may_contact`, the
    same predicate the send wrappers enforce with, so the tally cannot
    drift from the decision -- and the gate itself stays inside those
    wrappers, where a new caller cannot forget it.
    """
    if not settings.waiting_list_enabled:
        return "disabled"

    now = datetime.utcnow()
    expired = 0
    offered = 0
    no_consent = 0

    # Expire stale offers first, so a slot parked by someone who never
    # replied is available to the next person in this same sweep.
    stale = (
        db.query(models.WaitingListEntry)
        .filter(
            models.WaitingListEntry.status == "offered",
            models.WaitingListEntry.offer_expires_at.isnot(None),
            models.WaitingListEntry.offer_expires_at < now,
        )
        .all()
    )
    for entry in stale:
        entry.status = "waiting"
        entry.offered_start_at = None
        entry.offer_expires_at = None
        expired += 1
    if expired:
        db.commit()

    for org in _active_orgs(db):
        if offered >= settings.waiting_list_max_offers_per_run:
            break

        tz = timezones.org_timezone(org)
        today = timezones.now_local(tz).date()
        horizon = today + timedelta(days=settings.waiting_list_horizon_days)

        entries = (
            db.query(models.WaitingListEntry)
            .filter(
                models.WaitingListEntry.org_id == org.id,
                models.WaitingListEntry.status == "waiting",
            )
            .order_by(models.WaitingListEntry.created_at.asc())  # oldest first
            .all()
        )

        # Slots already offered to somebody else in this org must not be
        # offered again while those offers are live.
        taken = {
            e.offered_start_at
            for e in db.query(models.WaitingListEntry)
            .filter(
                models.WaitingListEntry.org_id == org.id,
                models.WaitingListEntry.status == "offered",
                models.WaitingListEntry.offered_start_at.isnot(None),
            )
            .all()
        }

        for entry in entries:
            if offered >= settings.waiting_list_max_offers_per_run:
                break

            patient = (
                db.query(models.Patient)
                .filter(
                    models.Patient.id == entry.patient_id,
                    models.Patient.org_id == org.id,
                )
                .first()
            )
            if patient is None:
                continue

            slot, doctor = _first_free_slot(db, org, entry, today, horizon, taken)
            if slot is None:
                continue

            if not notification_service.may_contact(patient):
                no_consent += 1
            if not notification_service.offer_waiting_list_slot(
                patient,
                f"{slot.strftime('%a %d %b')} at {_spoken_time(slot)}",
                doctor.name if doctor else None,
                org.name or "our clinic",
            ):
                # Unreachable or not consented. Left waiting rather than
                # marked offered, so a staff member can still place them
                # by hand.
                continue

            entry.status = "offered"
            entry.offered_start_at = slot
            entry.offered_at = now
            entry.offer_expires_at = now + timedelta(
                minutes=settings.waiting_list_offer_ttl_minutes
            )
            entry.offers_made = (entry.offers_made or 0) + 1
            taken.add(slot)
            offered += 1
            db.commit()

    return f"offered={offered} expired={expired} no_consent={no_consent}"


def _spoken_time(dt: datetime) -> str:
    """
    %-I is glibc-only and raises on Windows, so the hour is built by hand.
    Same workaround as `_format_time_label` in routers/appointments.py.
    """
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _first_free_slot(db: Session, org, entry, today, horizon, taken):
    """
    The earliest slot inside this entry's window that is genuinely free.

    Reuses the same availability computation staff and the voice agent
    use, one day at a time, rather than reimplementing the slot grid --
    which is where a second, subtly different notion of "free" would
    otherwise be born.
    """
    from .routers.appointments import get_availability

    doctors = (
        db.query(models.Doctor)
        .filter(
            models.Doctor.org_id == org.id,
            models.Doctor.active == True,  # noqa: E712 - SQL Server BIT compatibility
        )
        .all()
    )
    if entry.doctor_id:
        doctors = [d for d in doctors if d.id == entry.doctor_id]

    lower = entry.wanted_from.date() if entry.wanted_from else today
    upper = entry.wanted_until.date() if entry.wanted_until else horizon
    lower = max(lower, today)
    upper = min(upper, horizon)
    if lower > upper:
        return None, None

    class _Ctx:
        org_id = org.id
        actor = "system"
        user = None

    day = lower
    while day <= upper:
        for doctor in doctors:
            try:
                result = get_availability(
                    doctor_id=doctor.id, date=day.strftime("%Y-%m-%d"),
                    db=db, ctx=_Ctx(),
                )
            except Exception:  # noqa: BLE001 - one bad doctor must not stop the sweep
                continue
            for s in result.slots:
                if s.start_at in taken:
                    continue
                if entry.wanted_from and s.start_at < entry.wanted_from:
                    continue
                if entry.wanted_until and s.start_at > entry.wanted_until:
                    continue
                return s.start_at, doctor
        day += timedelta(days=1)
    return None, None


def send_post_visit_follow_ups(db: Session) -> Optional[str]:
    """
    Follow up after a completed appointment, in the clinic's own words
    (PF-14).

    Nothing happened after a visit. Aftercare depended entirely on what
    the patient remembered, and a follow-up booking depended on them
    thinking of it.

    **THE CLINIC WRITES THE WORDS, AND IF IT HAS NOT, NOTHING IS SENT.**

    That is the safety design, not a limitation. Aftercare wording is a
    clinical statement -- what to expect, when to worry, when to come back
    -- and shipping a default would mean a software vendor composing
    clinical text that goes out over a clinic's name. So this job reads
    `ClinicInfo.follow_up_message`, and a clinic that has written nothing
    is skipped entirely. Same reasoning as `EMERGENCY_INSTRUCTION`.

    The rest is the pattern the other outreach jobs already set: once per
    appointment, marked on dispatch rather than on delivery, consent
    respected through the usual wrappers, suspended clinics skipped, and a
    bounded look-back so switching the job on does not chase every
    appointment from last month.

    THE DELAY IS THE CLINIC'S TOO, and it is counted in days from the
    appointment's own wall-clock start -- not from when this job happens
    to run.
    """
    if not settings.post_visit_follow_up_enabled:
        return "disabled"

    now = datetime.utcnow()
    sent = 0
    skipped_no_template = 0

    for org in _active_orgs(db):
        info = (
            db.query(models.ClinicInfo)
            .filter(models.ClinicInfo.org_id == org.id)
            .first()
        )
        template = (info.follow_up_message or "").strip() if info else ""
        if not template:
            # No content, no feature. Counted so the job's run row can say
            # why it did nothing, which distinguishes "not configured"
            # from "broken".
            skipped_no_template += 1
            continue

        delay_days = (info.follow_up_delay_days or 3) if info else 3
        tz = timezones.org_timezone(org)
        local_now = timezones.now_local(tz)
        # `Appointment.start_at` is clinic wall-clock, so the window is
        # computed against the clinic's own clock -- see app/timezones.py.
        due_before = local_now - timedelta(days=delay_days)
        not_older_than = local_now - timedelta(
            days=delay_days + settings.post_visit_follow_up_max_age_days
        )

        appts = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.org_id == org.id,
                models.Appointment.status == "done",
                models.Appointment.follow_up_sent_at.is_(None),
                models.Appointment.patient_id.isnot(None),
                models.Appointment.start_at.isnot(None),
                models.Appointment.start_at <= due_before,
                models.Appointment.start_at >= not_older_than,
            )
            .all()
        )

        for appt in appts:
            patient = (
                db.query(models.Patient)
                .filter(
                    models.Patient.id == appt.patient_id,
                    models.Patient.org_id == org.id,
                )
                .first()
            )
            if patient is None:
                continue
            if notification_service.send_post_visit_follow_up(
                patient, template, org.name or "our clinic"
            ):
                appt.follow_up_sent_at = now
                sent += 1

        if sent:
            db.commit()

    return f"sent={sent} clinics_without_wording={skipped_no_template}"


def retry_webhook_deliveries(db: Session) -> Optional[str]:
    """
    Retry webhook deliveries that failed, with bounded backoff (GAP-T4).

    F14 shipped one attempt and said so plainly: retrying needs somewhere
    durable to hold the attempt, and a per-event delivery table would
    collect a row for every call a busy clinic takes. It also named the
    fix -- "if retries are wanted, the scheduler is the place, its claim
    row already solves the double-send problem". The scheduler now exists.

    WHAT ANSWERS THE ORIGINAL OBJECTION

    Only *failures* are queued. A delivery that succeeds first time writes
    nothing to `webhook_deliveries`, so the table is empty for a clinic
    whose endpoint is healthy, and holds a short backlog for one that is
    briefly down. Settled rows are pruned.

    WHAT MAKES A RETRY A RETRY

    The stored body is resent byte for byte. It carries its own
    `delivery_id`, so a receiver can tell the third attempt from a third
    event and deduplicate. Minting a fresh payload would make that
    impossible, and re-serialising the dict would break the signature,
    which is an HMAC over the exact bytes.

    WHERE IT GIVES UP

    After `WEBHOOK_MAX_ATTEMPTS` in total, the row is marked failed and
    left alone. Beyond roughly half an hour a clinic's endpoint is down
    rather than flapping, and retrying forever against something that will
    never accept the payload is the worse failure -- the same reasoning
    behind `reminder_sent_at` marking dispatch rather than delivery.
    """
    if not settings.webhook_retry_enabled:
        return "disabled"

    now = datetime.utcnow()
    schedule = webhook_service.backoff_minutes()
    delivered = failed = 0

    due = (
        db.query(models.WebhookDelivery)
        .filter(
            models.WebhookDelivery.status == "pending",
            models.WebhookDelivery.next_attempt_at.isnot(None),
            models.WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(models.WebhookDelivery.next_attempt_at.asc())
        .limit(settings.webhook_retry_max_per_run)
        .all()
    )

    for row in due:
        hook = (
            db.query(models.Webhook)
            .filter(
                models.Webhook.id == row.webhook_id,
                models.Webhook.org_id == row.org_id,
            )
            .first()
        )
        # The clinic removed the webhook, or emptied its URL, between the
        # failure and now. There is nowhere left to deliver, so stop
        # rather than retrying into the void.
        if hook is None or not (hook.endpoint_url or "").strip() or not hook.secret:
            row.status = "failed"
            row.last_status = "no endpoint"
            row.completed_at = now
            failed += 1
            continue

        ok, status = webhook_service.post_body(
            hook, row.event, row.delivery_id or "", row.body.encode("utf-8")
        )
        row.attempts = (row.attempts or 0) + 1
        row.last_status = status[:120]
        hook.last_delivery_at = now
        hook.last_delivery_status = status[:120]

        if ok:
            row.status = "delivered"
            row.next_attempt_at = None
            row.completed_at = now
            delivered += 1
        elif row.attempts >= settings.webhook_max_attempts:
            row.status = "failed"
            row.next_attempt_at = None
            row.completed_at = now
            failed += 1
        else:
            # attempts is 1-based and the first entry was already used
            # when the row was created, so index from there.
            idx = min(row.attempts - 1, len(schedule) - 1)
            row.next_attempt_at = now + timedelta(minutes=schedule[idx])

    # Prune settled rows so the table stays bounded without losing the
    # recent record staff might ask about.
    cutoff = now - timedelta(days=settings.webhook_delivery_retention_days)
    pruned = (
        db.query(models.WebhookDelivery)
        .filter(
            models.WebhookDelivery.status.in_(("delivered", "failed")),
            models.WebhookDelivery.completed_at.isnot(None),
            models.WebhookDelivery.completed_at < cutoff,
        )
        .delete(synchronize_session=False)
    )

    if due or pruned:
        db.commit()
    return f"delivered={delivered} failed={failed} pruned={pruned}"


def register_all() -> None:
    """
    Wire the jobs up. Called once from application startup.

    Separate from the job bodies so a test can register a fake job
    without the real ones firing.
    """
    scheduler.register(
        "reap_stale_calls",
        settings.stale_call_sweep_interval_seconds,
        reap_stale_calls,
    )
    # Registered unconditionally; the job itself returns immediately when
    # QUALITY_AUTO_EVAL_ENABLED is false. Registering it either way means
    # flipping the flag needs only a restart, and `scheduled_job_runs`
    # shows the job ticking (and doing nothing) rather than being absent,
    # which is the difference between "off" and "broken".
    scheduler.register(
        "evaluate_recent_calls",
        settings.quality_auto_eval_interval_seconds,
        evaluate_recent_calls,
    )
    scheduler.register(
        "send_weekly_digests",
        settings.weekly_digest_interval_seconds,
        send_weekly_digests,
    )
    scheduler.register(
        "send_appointment_reminders",
        settings.appointment_reminder_interval_seconds,
        send_appointment_reminders,
    )
    # Registered unconditionally, like the evaluation job: the body
    # returns immediately when the flag is off, so `scheduled_job_runs`
    # shows it ticking and doing nothing -- which distinguishes "switched
    # off" from "broken".
    scheduler.register(
        "contact_missed_callers",
        settings.missed_call_outreach_interval_seconds,
        contact_missed_callers,
    )
    scheduler.register(
        "request_call_feedback",
        settings.feedback_request_interval_seconds,
        request_call_feedback,
    )
    scheduler.register(
        "offer_waiting_list_slots",
        settings.waiting_list_interval_seconds,
        offer_waiting_list_slots,
    )
    scheduler.register(
        "send_post_visit_follow_ups",
        settings.post_visit_follow_up_interval_seconds,
        send_post_visit_follow_ups,
    )
    scheduler.register(
        "retry_webhook_deliveries",
        settings.webhook_retry_interval_seconds,
        retry_webhook_deliveries,
    )
