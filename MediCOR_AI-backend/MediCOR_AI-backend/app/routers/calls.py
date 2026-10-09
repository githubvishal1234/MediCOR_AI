import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import (
    audit_service,
    call_intelligence,
    llm_service,
    models,
    schemas,
    timezones,
    usage_service,
    webhook_service,
)
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context
from ..realtime import call_summary, manager, notify

router = APIRouter(prefix="/calls", tags=["calls"])

_TERMINAL_STATUSES = {"completed", "failed", "no_answer"}

# Outcomes that should surface as an actionable (warning-level) notification
# for clinic staff rather than a routine info-level one.
_URGENT_OUTCOMES = {"Transferred to Nurse"}

# HV-7: terminal statuses that mean nobody spoke to the caller. A missed
# call at a clinic is a lost patient, so these enter a callback queue
# rather than just disappearing into the call log.
_MISSED_STATUSES = {"failed", "no_answer"}

# The callback workflow, in order. `pending` is set automatically when a
# call ends missed; the rest are staff actions.
_CALLBACK_STATUSES = {"pending", "contacted", "resolved"}


_MAX_EXPORT_ROWS = 5000

# Column order is the contract with the dashboard's existing CSV. It used
# to be built in the browser, so changing this changes a file people
# already have saved copies of.
_EXPORT_HEADER = [
    "Patient", "Phone", "Direction", "Time", "Reason",
    "Duration", "Status", "Outcome", "Transcript",
]


def _get_call_or_404(db: Session, call_id: str, org_id: str) -> models.CallLog:
    call = (
        db.query(models.CallLog)
        .filter(models.CallLog.id == call_id, models.CallLog.org_id == org_id)
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.get("", response_model=list[schemas.CallLogListOut])
def list_calls(
    outcome: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    `limit`/`offset` are optional (Phase 8, opt-in pagination) — omitting
    `limit` preserves the previous behaviour of returning every matching
    call log.
    """
    q = db.query(models.CallLog).filter(models.CallLog.org_id == ctx.org_id)
    if outcome:
        q = q.filter(models.CallLog.outcome == outcome)
    q = q.order_by(models.CallLog.occurred_at.desc())
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(min(limit, 500))
    return q.all()


@router.post("", response_model=schemas.CallLogDetailOut)
def create_call(
    payload: schemas.CallLogCreate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Creates a call log. The voice agent calls this once, right at the
    start of a call (inbound or outbound), with whatever it already
    knows (direction, caller_phone, started_at). Everything else —
    patient/appointment association, transcript, final metadata, and the
    AI summary — is filled in later via the transcript/bulk and PATCH
    endpoints as the call progresses and ends.
    """
    data = payload.model_dump(exclude_none=True)
    if payload.patient_id:
        patient = (
            db.query(models.Patient)
            .filter(models.Patient.id == payload.patient_id, models.Patient.org_id == ctx.org_id)
            .first()
        )
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
    if payload.appointment_id:
        appt = (
            db.query(models.Appointment)
            .filter(models.Appointment.id == payload.appointment_id, models.Appointment.org_id == ctx.org_id)
            .first()
        )
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")

    call = models.CallLog(org_id=ctx.org_id, **data)
    db.add(call)
    db.commit()
    db.refresh(call)

    manager.broadcast(ctx.org_id, "call.started", call_summary(call))
    notify(
        ctx.org_id,
        level="info",
        title="Call in progress" if call.direction == "outbound" else "Incoming call",
        message=f"{call.patient_name or 'Unknown Caller'} · {call.reason or ('Outbound call' if call.direction == 'outbound' else 'Inbound call')}",
        data={"call_id": call.id},
    )
    return call


def _csv_safe(value) -> str:
    """
    One export cell, defused against spreadsheet formula injection.

    Caller names and numbers can come from the phone line, so a cell like
    `=HYPERLINK(...)` would run when a clinic opens the export in Excel.
    A leading quote makes the spreadsheet treat it as text. A plain signed
    number (`+15551110000`) is left alone -- it is not a formula.
    """
    s = str(value or "")
    if s[:1] in ("=", "@", "\t", "\r") or (s[:1] in ("+", "-") and not s[1:].replace(" ", "").isdigit()):
        return "'" + s
    return s


@router.post("/export")
def export_calls(
    payload: schemas.CallLogExportIn,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Call logs + transcripts as CSV, for the dashboard's Export button.

    This used to be built entirely in the browser. The rows were still
    traceable — every `GET /calls/{id}` the export made wrote a
    `call.transcript_viewed` entry — but nothing recorded that 200 of them
    were one bulk pull rather than a day of ordinary lookups. That is the
    distinction an investigator is looking for, so the export now happens
    here and audits itself once, the way `/security/audit-log/export`
    does.

    Deliberately `get_current_user`, not `require_admin`: medical staff
    could already export from this page, and quietly making it
    admin-only would be a regression dressed up as a fix. Whether bulk
    PHI export should be admin-gated is a policy call, not this one.

    Ids come from the client because the table filters client-side, but
    they are resolved org-scoped here — asking for another clinic's call
    id simply returns nothing for that row.
    """
    ids = list(dict.fromkeys(payload.call_ids))[:_MAX_EXPORT_ROWS]

    calls = (
        db.query(models.CallLog)
        .filter(models.CallLog.id.in_(ids), models.CallLog.org_id == user.org_id)
        .all()
    )
    by_id = {c.id: c for c in calls}

    org = db.query(models.Organization).filter(models.Organization.id == user.org_id).first()
    tz = timezones.org_timezone(org)

    transcripts: dict[str, list[models.TranscriptMessage]] = {}
    if by_id:
        rows = (
            db.query(models.TranscriptMessage)
            .filter(models.TranscriptMessage.call_id.in_(list(by_id)))
            .order_by(models.TranscriptMessage.id)
            .all()
        )
        for msg in rows:
            transcripts.setdefault(msg.call_id, []).append(msg)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_EXPORT_HEADER)

    exported = 0
    for call_id in ids:
        call = by_id.get(call_id)
        if call is None:
            continue
        exported += 1

        # `started_at` is UTC-naive (see app/timezones.py). The browser
        # used to render it in the *viewer's* locale, which made two
        # people's exports of the same call disagree. A compliance
        # artifact should not depend on who clicked the button, so this
        # is the clinic's own wall clock, stated explicitly.
        when = ""
        if call.started_at:
            when = timezones.to_local_naive(call.started_at, tz).strftime("%Y-%m-%d %H:%M")
        elif call.timestamp_label:
            when = call.timestamp_label

        messages = transcripts.get(call.id) or []
        if messages:
            transcript = "\n".join(
                f"{'Patient' if m.who == 'patient' else 'AI'}"
                f"{f' [{m.time_label}]' if m.time_label else ''}: {m.text}"
                for m in messages
            )
        else:
            transcript = "No transcript available"

        writer.writerow([_csv_safe(v) for v in (
            call.patient_name,
            call.caller_phone,
            call.direction,
            when,
            call.reason,
            call.duration,
            call.status,
            call.outcome,
            transcript,
        )])

    audit_service.record(
        db, user.org_id, "call_log.exported", user=user,
        target_type="call_log",
        detail=f"{exported} call{'' if exported == 1 else 's'} exported as CSV",
        status=audit_service.STATUS_LOGGED,
        request=request, commit=True,
    )

    filename = f"call-logs-{timezones.now_local(tz).date().isoformat()}.csv"
    return Response(
        # UTF-8 BOM so Excel on Windows doesn't mangle a patient name with
        # an accent — same reason as the audit-log export.
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{call_id}", response_model=schemas.CallLogDetailOut)
def get_call(
    call_id: str,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    HV-1: a staff member opening this is reading a patient conversation
    verbatim, so it is logged.

    Only for `actor == "user"`. The voice agent reaching this endpoint is
    reading back the call it is itself conducting — that is not a
    disclosure to anyone, and logging it would put a row on the Security
    page for every poll during every call, burying the staff reads that
    a breach investigation is actually looking for.
    """
    call = _get_call_or_404(db, call_id, ctx.org_id)
    if ctx.actor == "user":
        audit_service.record(
            db, ctx.org_id, "call.transcript_viewed", ctx=ctx,
            target_type="call", target_id=call.id,
            detail=call.patient_name or "Unknown caller",
            status=audit_service.STATUS_LOGGED,
            request=request, commit=True,
        )
    return call


@router.patch("/{call_id}", response_model=schemas.CallLogDetailOut)
def update_call(
    call_id: str,
    payload: schemas.CallLogUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Updates call metadata (duration, status, timestamps, caller/patient/
    appointment association, etc). Used by the voice agent to finalize a
    call once it ends. If the call is being moved into a terminal status
    (completed | failed | no_answer) and no explicit ai_summary/sentiment
    was supplied, the backend generates them from the stored transcript
    and actions_taken — the voice agent never has to compute these
    itself.
    """
    call = _get_call_or_404(db, call_id, ctx.org_id)
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    was_in_progress = call.status not in _TERMINAL_STATUSES

    if "patient_id" in data:
        patient = (
            db.query(models.Patient)
            .filter(models.Patient.id == data["patient_id"], models.Patient.org_id == ctx.org_id)
            .first()
        )
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
    if "appointment_id" in data:
        appt = (
            db.query(models.Appointment)
            .filter(models.Appointment.id == data["appointment_id"], models.Appointment.org_id == ctx.org_id)
            .first()
        )
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")

    for field, value in data.items():
        setattr(call, field, value)

    if "duration_seconds" in data and "duration" not in data:
        call.duration = call_intelligence.format_duration_label(data["duration_seconds"])

    # F5: remember which language this caller actually spoke, so the next
    # call can open in it instead of re-detecting from scratch.
    #
    # The rule lives here rather than in the agent on purpose — the agent
    # holds no business logic, it just reports what Sarvam detected. Which
    # signal becomes a patient's stored preference is a product decision
    # and belongs on this side.
    #
    # Deliberately not audited: this is a derived attribute of a call, not
    # a staff edit to a medical record or a disclosure of one, and a row
    # per call would bury the patient reads on the Security page that a
    # breach investigation is actually looking for.
    if call.detected_language and call.patient_id:
        patient = (
            db.query(models.Patient)
            .filter(
                models.Patient.id == call.patient_id,
                # Org-scoped even though the call already is: the parent
                # being in this org does not prove the child is, which is
                # the exact shape of the one cross-org IDOR this codebase
                # has had (tests/test_phase8_tenant_isolation.py).
                models.Patient.org_id == ctx.org_id,
            )
            .first()
        )
        if patient is not None and patient.preferred_language != call.detected_language:
            patient.preferred_language = call.detected_language

    # PF-16, same shape and same reasoning: the agent reports that the
    # caller asked to be spoken to more slowly, the backend decides that
    # this becomes a standing preference. Sticky rather than last-wins,
    # unlike language -- someone who needed it slower once is unlikely to
    # need it faster later, and there is a way to clear it on the patient
    # record if that turns out to be wrong.
    if call.speech_pace_requested and call.patient_id:
        patient = (
            db.query(models.Patient)
            .filter(
                models.Patient.id == call.patient_id,
                models.Patient.org_id == ctx.org_id,
            )
            .first()
        )
        if patient is not None and not patient.speech_pace_preference:
            patient.speech_pace_preference = call.speech_pace_requested

    just_ended = was_in_progress and call.status in _TERMINAL_STATUSES
    if call.status in _TERMINAL_STATUSES:
        if "sentiment" not in data:
            call.sentiment = call_intelligence.infer_sentiment(call)
        if "outcome" not in data:
            call.outcome = call_intelligence.infer_outcome(call)
        if "ai_summary" not in data:
            call.ai_summary = call_intelligence.generate_summary(call)

    # HV-7: a call that ends failed/no_answer joins the callback queue.
    # Only on the transition, and only if the caller hasn't already set a
    # callback_status explicitly — re-running this must never reset a
    # call someone has already marked contacted.
    if just_ended and call.status in _MISSED_STATUSES and "callback_status" not in data:
        if call.callback_status is None:
            call.callback_status = "pending"
            call.callback_updated_at = datetime.utcnow()

    db.commit()
    db.refresh(call)

    manager.broadcast(ctx.org_id, "call.updated", call_summary(call))
    if just_ended:
        manager.broadcast(ctx.org_id, "call.ended", call_summary(call))
        level = "warning" if call.outcome in _URGENT_OUTCOMES or call.status in {"failed", "no_answer"} else "success"
        notify(
            ctx.org_id,
            level=level,
            title="Call ended",
            message=f"{call.patient_name or 'Unknown Caller'} · {call.outcome or call.status} ({call.duration or '—'})",
            data={"call_id": call.id},
        )

        # AI-2: upgrade the rule-based summary written above with a real
        # LLM pass, in the background.
        #
        # Ordering is deliberate. The rule-based values are committed
        # first, so a call always has *a* summary the instant it ends,
        # even if the model is slow, down, or unconfigured. The task then
        # overwrites them in place — which is why no dashboard code had
        # to change. A live call never waits on this.
        llm_service.schedule_analysis(background_tasks, call.id, ctx.org_id)

        # F14: tell the clinic's own systems. Ids and plain values only —
        # the session this row came from is closed by the time the task
        # runs, so an ORM instance would raise on a lazy attribute.
        webhook_service.schedule(
            background_tasks, ctx.org_id, "call.completed",
            {
                "call_id": call.id,
                "direction": call.direction,
                "status": call.status,
                "outcome": call.outcome,
                "duration_seconds": call.duration_seconds,
                "patient_id": call.patient_id,
                "appointment_id": call.appointment_id,
            },
        )

        # HV-4: warn the clinic when this call pushed it past 80% or 100%
        # of its included minutes. Fires on the crossing only, so it does
        # not nag once per call for the rest of the month.
        #
        # It warns; it does not block. Whether an over-limit clinic is cut
        # off or billed for overage is a commercial decision, and
        # hard-blocking inbound calls touches the voice path.
        org = (
            db.query(models.Organization)
            .filter(models.Organization.id == ctx.org_id)
            .first()
        )
        if org is not None:
            usage_service.notify_if_threshold_crossed(db, org, call.id)

    return call


@router.post("/{call_id}/transcript", response_model=schemas.TranscriptMessageOut)
def add_transcript_message(
    call_id: str,
    payload: schemas.TranscriptMessageCreate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """Appends a single transcript message (e.g. for live/streaming use)."""
    _get_call_or_404(db, call_id, ctx.org_id)
    msg = models.TranscriptMessage(call_id=call_id, **payload.model_dump())
    db.add(msg)
    db.commit()
    db.refresh(msg)
    manager.broadcast(
        ctx.org_id,
        "call.transcript_message",
        {"call_id": call_id, "who": msg.who, "text": msg.text, "time_label": msg.time_label},
    )
    return msg


@router.put("/{call_id}/transcript/bulk", response_model=schemas.CallLogDetailOut)
def save_transcript_bulk(
    call_id: str,
    payload: schemas.TranscriptBulkCreate,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    Replaces the call's full transcript in one request. The voice agent
    calls this once at call end with the complete conversation history
    (works the same for inbound and outbound calls) — far cheaper than
    one request per turn, and idempotent if the agent retries on
    failure.
    """
    call = _get_call_or_404(db, call_id, ctx.org_id)
    db.query(models.TranscriptMessage).filter(models.TranscriptMessage.call_id == call.id).delete()
    for msg in payload.messages:
        db.add(models.TranscriptMessage(call_id=call.id, **msg.model_dump()))
    db.commit()
    db.refresh(call)
    return call

# ---------- Missed-call callback queue (HV-7) ----------
#
# Pure read/annotate side over data the voice agent already writes. It
# requires nothing from the agent and touches no part of the call path:
# `CallLog.status` has always supported "failed" and "no_answer", and
# update_call already fired a warning notification for them — but there
# was no queue, so a missed call surfaced as a transient toast and was
# then invisible. These endpoints turn it into a work item with a state.
#
# Staff-only (`get_current_user`), deliberately: this is a front-desk
# workflow. The agent has no reason to read or advance the queue, so it
# is not exposed through `get_org_context`.


@router.get("/callbacks/queue", response_model=list[schemas.CallbackOut])
def list_callback_queue(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Calls nobody answered, oldest-missed first.

    Defaults to everything still outstanding (`pending` + `contacted`)
    rather than all history — the queue is a to-do list, and a resolved
    callback has no reason to sit at the top of it. Pass `status` for a
    specific bucket.
    """
    q = db.query(models.CallLog).filter(
        models.CallLog.org_id == user.org_id,
        models.CallLog.callback_status.isnot(None),
    )
    if status:
        if status not in _CALLBACK_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of {sorted(_CALLBACK_STATUSES)}",
            )
        q = q.filter(models.CallLog.callback_status == status)
    else:
        q = q.filter(models.CallLog.callback_status.in_(("pending", "contacted")))

    return (
        q.order_by(models.CallLog.occurred_at.asc())
        .limit(min(max(limit, 1), 500))
        .all()
    )


@router.patch("/{call_id}/callback", response_model=schemas.CallbackOut)
def update_callback(
    call_id: str,
    payload: schemas.CallbackUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Advances a callback ("we rang them back", "this is dealt with") and/or
    attaches a note.

    Only calls that actually ended missed can be queued — refusing here
    rather than silently accepting keeps the queue meaningful, since a
    completed call has nothing to call back about.
    """
    call = _get_call_or_404(db, call_id, user.org_id)
    if call.status not in _MISSED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Only missed calls (failed or no_answer) have a callback state.",
        )

    data = payload.model_dump(exclude_unset=True)
    if "callback_status" in data and data["callback_status"] is not None:
        if data["callback_status"] not in _CALLBACK_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"callback_status must be one of {sorted(_CALLBACK_STATUSES)}",
            )
        call.callback_status = data["callback_status"]
    if "callback_note" in data:
        call.callback_note = data["callback_note"]

    call.callback_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(call)

    manager.broadcast(user.org_id, "call.updated", call_summary(call))
    return call
