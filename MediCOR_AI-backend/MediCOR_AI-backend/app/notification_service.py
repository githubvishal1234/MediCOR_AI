"""
Patient-facing appointment notifications (email + WhatsApp).

Mirrors ehr_service.py's philosophy: this is a side effect of booking, not
part of it. A misconfigured or down notification channel must never turn
a successful booking into an error for the caller/staff. Each channel is
independently optional — skip silently (with a log line) if its settings
aren't filled in, rather than failing.

Routers should call `notify_appointment_booked(...)` from inside a
BackgroundTasks callback (see routers/appointments.py) so the HTTP
response for the booking itself never waits on an SMTP/Twilio round trip.
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

import httpx
from fastapi import BackgroundTasks

from . import models
from .config import settings
from .database import SessionLocal

logger = logging.getLogger("notification-service")


def _format_when(appt: models.Appointment) -> str:
    # Cross-platform formatting: "%-d"/"%-I" are glibc/Linux-only strftime
    # directives and raise ValueError("Invalid format string") on Windows.
    # Build the day/hour without a platform-specific directive instead —
    # same approach already used by billing.py's _format_billing_date and
    # appointments.py's _format_time_label for this exact reason.
    day = appt.day_label
    if not day and appt.start_at:
        day = f"{appt.start_at.strftime('%a, %b')} {appt.start_at.day}"
    time = appt.time_label
    if not time and appt.start_at:
        time = appt.start_at.strftime("%I:%M %p").lstrip("0")
    return f"{day or ''} at {time or ''}".strip(" at")


def _email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def _twilio_configured() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token)


def _whatsapp_configured() -> bool:
    return bool(_twilio_configured() and settings.whatsapp_from_number)


def _sms_configured() -> bool:
    return bool(_twilio_configured() and settings.sms_from_number)


def _channel_for(patient: models.Patient) -> str:
    """
    Which channel to use for this patient, in preference order.

    Their own recorded channel wins. Failing that the deployment default,
    and failing *that* whichever of the two is actually configured -- a
    clinic that has only set up SMS should not silently send nothing
    because the default still says WhatsApp.
    """
    choice = (patient.message_channel or "").strip().lower()
    if choice in ("whatsapp", "sms"):
        return choice
    default = (settings.default_message_channel or "whatsapp").strip().lower()
    if default == "sms" and _sms_configured():
        return "sms"
    if default == "whatsapp" and _whatsapp_configured():
        return "whatsapp"
    if _whatsapp_configured():
        return "whatsapp"
    if _sms_configured():
        return "sms"
    return default


def _send_email(to_email: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.notification_http_timeout_seconds) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        logger.info(f"Sent appointment email to {to_email}")
    except Exception as e:
        # Never propagate — a failed confirmation email must not affect
        # the booking that already succeeded.
        logger.warning(f"Failed to send appointment email to {to_email}: {e}")


def _send_twilio_message(to_phone: str, body: str, channel: str = "whatsapp") -> None:
    """
    Send one message over Twilio, as WhatsApp or as SMS.

    These are the same API, the same credentials and the same payload --
    the only difference is that WhatsApp numbers carry a "whatsapp:"
    channel prefix on both ends and SMS numbers do not. That is why SMS
    arrived as a parameter rather than as a second integration.

    Never raises: a failed message must not affect the booking that
    triggered it. Same posture as the rest of this module.
    """
    channel = channel if channel in ("whatsapp", "sms") else "whatsapp"

    if channel == "whatsapp":
        sender = settings.whatsapp_from_number
        to = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"
    else:
        sender = settings.sms_from_number
        # A "whatsapp:" prefix on an SMS recipient is a silent delivery
        # failure at Twilio, so strip one if a record carries it.
        to = to_phone[len("whatsapp:"):] if to_phone.startswith("whatsapp:") else to_phone

    if not sender:
        logger.info("No %s sender configured - skipping message to %s", channel, to)
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    try:
        resp = httpx.post(
            url,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={"From": sender, "To": to, "Body": body},
            timeout=settings.notification_http_timeout_seconds,
        )
        if resp.status_code >= 400:
            logger.warning("Twilio %s send to %s failed: %s %s", channel, to, resp.status_code, resp.text)
        else:
            logger.info("Sent %s message to %s", channel, to)
    except Exception as e:
        logger.warning("Failed to send %s message to %s: %s", channel, to, e)


def _send_whatsapp(to_phone: str, body: str) -> None:
    """Kept as the WhatsApp-specific entry point the tests already stub."""
    _send_twilio_message(to_phone, body, channel="whatsapp")


def _send_sms(to_phone: str, body: str) -> None:
    _send_twilio_message(to_phone, body, channel="sms")



# --------------------------------------------------------------------
# Contact consent (PF-05)
# --------------------------------------------------------------------
#
# The gate lives HERE, in the two functions every patient-facing message
# must pass through, rather than at the top of each notify_* function.
# That is the same reasoning as the plan gate inside
# `ehr_service.get_active_integration`: a gate a new caller has to
# remember to add is a gate that will eventually be forgotten, and the
# failure mode — messaging somebody who asked not to be — is not one that
# shows up in testing.
#
# Staff mail (invitations, the weekly digest) deliberately does NOT route
# through these. A receptionist is not a patient, and their invitation is
# not marketing.


def appointment_link_url(token: Optional[str]) -> Optional[str]:
    """
    Build the patient-facing link, or None.

    None when there is no token, or when no patient site is configured.
    Returning None rather than inventing a host is the point: the messages
    fall back to asking the patient to telephone, which is true, whereas a
    link to a host that does not exist is worse than no link at all.
    """
    base = (settings.patient_link_base_url or "").strip().rstrip("/")
    if not token or not base:
        return None
    return f"{base}/appointment?token={token}"


def may_contact(patient: models.Patient) -> bool:
    """
    Whether this patient may be sent a message.

    Three cases, and the third is the one that matters:

    - "granted"  -> yes.
    - "declined" -> no. Never, on any channel.
    - NULL       -> nobody ever asked. What that means is a business
      decision, not a technical one, so it is read from configuration
      rather than assumed here. The default is the cautious reading.

    NULL is the state every patient row created before this feature holds,
    which is exactly why it cannot be silently treated as either answer.
    """
    consent = (patient.contact_consent or "").strip().lower()
    if consent == "granted":
        return True
    if consent == "declined":
        return False
    return settings.contact_unasked_is_consent


def _send_patient_email(patient: models.Patient, subject: str, body: str) -> bool:
    """
    Consent-gated email to a patient. Never used for staff mail.

    Returns whether a message was actually handed to the transport.
    The reminder job marks an appointment as reminded on the strength
    of this, so a patient who has not consented must read as False --
    exactly like a patient with no email address. Nothing was claimed,
    so there is nothing to suppress next time.
    """
    if not patient.email:
        return False
    if not may_contact(patient):
        logger.info(
            "Skipping patient email: no contact consent recorded for patient=%s", patient.id
        )
        return False
    if not _email_configured():
        logger.info("Patient has email but SMTP isn't configured — skipping")
        return False
    _send_email(patient.email, subject, body)
    return True


def _send_patient_whatsapp(patient: models.Patient, body: str) -> bool:
    """
    Consent-gated message to a patient's phone. Returns whether it sent.

    Named for WhatsApp for continuity -- it is the single phone-channel
    wrapper, and PF-04 made *which* channel a property of the patient
    rather than of the call site. Every caller still gets the consent
    gate, which is the whole reason this function exists.
    """
    if not patient.phone:
        return False
    if not may_contact(patient):
        logger.info(
            "Skipping patient message: no contact consent recorded for patient=%s", patient.id
        )
        return False

    channel = _channel_for(patient)
    if channel == "sms" and not _sms_configured():
        logger.info("Patient prefers SMS but no SMS sender is configured - skipping")
        return False
    if channel == "whatsapp" and not _whatsapp_configured():
        logger.info("Patient has phone but WhatsApp/Twilio isn't configured - skipping")
        return False

    _send_twilio_message(patient.phone, body, channel=channel)
    return True


def notify_appointment_booked(
    patient: models.Patient,
    appt: models.Appointment,
    doctor: Optional[models.Doctor],
    clinic_name: str = "our clinic",
    link_token: Optional[str] = None,
) -> None:
    """
    Fire-and-forget: sends an appointment-confirmed message over whichever
    of email/WhatsApp are configured AND the patient has contact info for.
    Call this from a BackgroundTasks callback, not inline in the request.
    """
    when = _format_when(appt)
    doctor_line = f" with {doctor.name}" if doctor else ""
    link = appointment_link_url(link_token)
    # Two wordings, because only one of them is true at a time. With a
    # link the patient can act on it; without one, the honest instruction
    # is to telephone. Neither ever invites a reply -- no inbound webhook
    # exists, so a reply would go nowhere silently (PF-07 is not built).
    action_email = (
        f"Confirm, change or cancel your appointment here:\n{link}\n\n"
        if link
        else "If you need to reschedule or cancel, just call us back.\n\n"
    )
    action_sms = (
        f" Confirm or change it here: {link}"
        if link
        else " Please call us if you need to reschedule."
    )

    _send_patient_email(
        patient,
        f"Appointment confirmed — {when}",
        (
            f"Hi {patient.name},\n\n"
            f"Your appointment{doctor_line} at {clinic_name} is confirmed for {when}.\n\n"
            f"{action_email}"
            f"— {clinic_name}"
        ),
    )

    # "Reply" is deliberately NOT offered here. Nothing in this
    # deployment receives an inbound message, so inviting one was a
    # promise the product could not keep: the reply went nowhere, and
    # the patient was given no sign of that. Restore the invitation
    # the day an inbound webhook exists (PF-07).
    _send_patient_whatsapp(
        patient,
        (
            f"Hi {patient.name}, your appointment{doctor_line} at {clinic_name} "
            f"is confirmed for {when}.{action_sms}"
        ),
    )


def notify_appointment_cancelled(
    patient: models.Patient,
    appt: models.Appointment,
    doctor: Optional[models.Doctor],
    clinic_name: str = "our clinic",
) -> None:
    when = _format_when(appt)
    doctor_line = f" with {doctor.name}" if doctor else ""

    _send_patient_email(
        patient,
        "Appointment cancelled",
        f"Hi {patient.name},\n\nYour appointment{doctor_line} for {when} at {clinic_name} has been cancelled.\n\n— {clinic_name}",
    )
    _send_patient_whatsapp(
        patient,
        f"Hi {patient.name}, your appointment{doctor_line} for {when} at {clinic_name} has been cancelled.",
    )


def notify_appointment_rescheduled(
    patient: models.Patient,
    appt: models.Appointment,
    doctor: Optional[models.Doctor],
    clinic_name: str = "our clinic",
    previous_when: Optional[str] = None,
) -> None:
    """
    Booking and cancellation both notified the patient; rescheduling did
    not — so the one change most likely to make someone miss their
    appointment was the one nobody was told about.

    `previous_when` is the old day/time label, captured before the move,
    so the message can say what changed rather than just asserting a new
    time the patient may not realise is different.
    """
    when = _format_when(appt)
    doctor_line = f" with {doctor.name}" if doctor else ""
    moved_from = f" (moved from {previous_when})" if previous_when else ""

    _send_patient_email(
        patient,
        f"Appointment rescheduled — {when}",
        (
            f"Hi {patient.name},\n\n"
            f"Your appointment{doctor_line} at {clinic_name} has been moved to {when}{moved_from}.\n\n"
            f"If that time doesn't work, just call us back.\n\n"
            f"— {clinic_name}"
        ),
    )
    _send_patient_whatsapp(
        patient,
        (
            f"Hi {patient.name}, your appointment{doctor_line} at {clinic_name} "
            f"has been moved to {when}{moved_from}. Call us if that time doesn't work."
        ),
    )


# ---------- Background (fire-and-forget) dispatch ----------
#
# The three functions above take live ORM instances, which is correct for
# a caller that still holds an open Session — but NOT for a BackgroundTasks
# callback, and passing them into one was a real bug:
#
#   background_tasks.add_task(notify_appointment_booked, patient, appt, ...)
#
# By the time the task ran, the router had committed (SQLAlchemy's default
# expire_on_commit=True expires every attribute) and `get_db` had closed the
# session. The instances were detached, so the very first attribute read —
# `if patient.email` — tried to refresh from a dead session and raised
# DetachedInstanceError. The booking itself had already returned 200, so the
# failure was invisible to the caller: no confirmation was ever sent, and
# only a traceback in the server log said so.
#
# The fix is the pattern ehr_service.py already uses for exactly this
# reason: pass ids, open a fresh Session inside the task, and re-query.
# Nothing crosses the request boundary except strings.

_NOTIFY_KINDS = ("booked", "rescheduled", "cancelled")


def _run_appointment_notification(
    kind: str,
    appointment_id: str,
    org_id: str,
    previous_when: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        appt = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.id == appointment_id,
                models.Appointment.org_id == org_id,
            )
            .first()
        )
        if appt is None:
            return

        # No patient record means nowhere to send to. This is normal for
        # appointments created by staff against a walk-in name only.
        if not appt.patient_id:
            return
        patient = (
            db.query(models.Patient)
            .filter(
                models.Patient.id == appt.patient_id,
                models.Patient.org_id == org_id,
            )
            .first()
        )
        if patient is None:
            return

        doctor = (
            db.query(models.Doctor)
            .filter(
                models.Doctor.id == appt.doctor_id,
                models.Doctor.org_id == org_id,
            )
            .first()
            if appt.doctor_id
            else None
        )
        org = (
            db.query(models.Organization)
            .filter(models.Organization.id == org_id)
            .first()
        )
        clinic_name = org.name if org else "our clinic"

        if kind == "booked":
            # Mint the self-service token here rather than in the router.
            #
            # This background task is the single path every booking
            # notification takes, and it already holds the appointment and
            # a session -- so there is one place to maintain, and no
            # booking route can ship a message without a link by
            # forgetting to mint one.
            #
            # Only minted when a patient site is actually configured. A
            # token nobody can ever use is a live credential with no
            # purpose, and this module's whole posture is to avoid
            # creating things that look like capabilities and are not.
            link_token = None
            if (settings.patient_link_base_url or "").strip():
                try:
                    from .routers.appointment_link import issue_link_token

                    link_token = issue_link_token(appt)
                    db.commit()
                except Exception:
                    # A link is a convenience; the confirmation is not.
                    # Send the message without one rather than not at all.
                    db.rollback()
                    link_token = None
                    logger.warning(
                        "Could not issue a self-service link for appointment=%s", appt.id,
                        exc_info=True,
                    )
            notify_appointment_booked(patient, appt, doctor, clinic_name, link_token=link_token)
        elif kind == "rescheduled":
            notify_appointment_rescheduled(
                patient, appt, doctor, clinic_name, previous_when=previous_when
            )
        elif kind == "cancelled":
            notify_appointment_cancelled(patient, appt, doctor, clinic_name)
        else:  # pragma: no cover - guarded by schedule_appointment_notification
            logger.warning("Unknown appointment notification kind %r", kind)
    except Exception:
        # Same posture as the rest of this module: a notification failure
        # must never surface anywhere near the booking that triggered it.
        logger.exception("Background appointment notification (%s) failed", kind)
    finally:
        db.close()


def _run_invite_email(
    to_email: str,
    full_name: str,
    clinic_name: str,
    invited_by: str,
    invite_url: Optional[str],
) -> None:
    subject = f"You've been invited to join {clinic_name} on MediCOR"
    opening = f"Hi {full_name},\n\n{invited_by} has invited you to join {clinic_name} on MediCOR."
    if invite_url:
        body = (
            f"{opening}\n\n"
            f"Set your password and get started here:\n{invite_url}\n\n"
            f"This link expires in {settings.invite_expire_days} days. If it has "
            f"expired, ask {invited_by} to send a new one.\n\n"
            f"— {clinic_name}"
        )
    else:
        # No frontend URL is configured, so there is no link to send. Say
        # so plainly rather than emailing a dead or invented address —
        # the admin still has the token from the API response.
        body = (
            f"{opening}\n\n"
            f"{invited_by} will send you a sign-up link separately.\n\n"
            f"— {clinic_name}"
        )
    _send_email(to_email, subject, body)


def send_password_reset_email(to_email: str, reset_url: str, clinic_name: str) -> None:
    """
    Send a password reset link.

    Staff mail, so it calls `_send_email` directly rather than the
    consent-gated patient wrapper -- a receptionist locked out of their
    own account is not a patient, and their reset is not marketing.

    If no link could be built (no frontend URL configured) the message
    says so plainly instead of sending a dead one. A reset email with a
    broken link is worse than none: it burns the token and teaches the
    user the feature does not work.
    """
    if reset_url:
        body = (
            f"Someone asked to reset the password for this account at {clinic_name}.\n\n"
            f"Set a new password here:\n{reset_url}\n\n"
            f"The link expires shortly. If this wasn't you, you can ignore this "
            f"email - nothing has changed.\n\n"
            f"\u2014 {clinic_name}"
        )
    else:
        body = (
            f"Someone asked to reset the password for this account at {clinic_name}.\n\n"
            f"Please contact your clinic administrator to have it reset.\n\n"
            f"\u2014 {clinic_name}"
        )
    _send_email(to_email, f"Reset your {clinic_name} password", body)


def schedule_password_reset_email(
    background_tasks: BackgroundTasks, to_email: str, reset_url: str, clinic_name: str
) -> None:
    background_tasks.add_task(
        send_password_reset_email, to_email, reset_url, clinic_name
    )


def schedule_invite_email(
    background_tasks: BackgroundTasks,
    to_email: str,
    full_name: str,
    clinic_name: str,
    invited_by: str,
    invite_url: Optional[str],
) -> bool:
    """
    Queues the staff invitation email.

    Returns whether it was queued at all: when SMTP isn't configured this
    is a no-op returning False, and the caller surfaces the invite link in
    the UI instead. Same "an unconfigured channel is skipped, never an
    error" posture as the rest of this module — a clinic with no SMTP can
    still invite staff by handing over the link.
    """
    if not _email_configured():
        logger.info("SMTP isn't configured — skipping invite email to %s", to_email)
        return False
    background_tasks.add_task(
        _run_invite_email, to_email, full_name, clinic_name, invited_by, invite_url
    )
    return True


def schedule_appointment_notification(
    background_tasks: BackgroundTasks,
    kind: str,
    appointment_id: str,
    org_id: str,
    previous_when: Optional[str] = None,
) -> None:
    """
    Queues a patient notification for after the response is sent.

    `kind` is one of "booked" | "rescheduled" | "cancelled". Only ids and
    plain strings are captured — see the note above on why ORM instances
    must not be.
    """
    if kind not in _NOTIFY_KINDS:
        raise ValueError(f"kind must be one of {_NOTIFY_KINDS}, got {kind!r}")
    background_tasks.add_task(
        _run_appointment_notification, kind, appointment_id, org_id, previous_when
    )


# ---------------------------------------------------------------------
# Weekly performance digest (F13)
#
# Everything above notifies a PATIENT about their own appointment. This
# notifies the CLINIC about itself. It lives here because this module owns
# the email transport and the "a side effect must never break the caller"
# posture that a scheduled job needs just as much as a request path does.
# ---------------------------------------------------------------------

def _pct(value: Optional[float]) -> str:
    """
    An em dash for "nothing to measure", never 0%.

    Same rule the Analytics page follows. A zero resolution rate reads as
    "the AI resolved nothing"; a zero no-show rate reads as "nobody missed
    an appointment". Both are claims, and neither is what a null means.
    """
    return "—" if value is None else f"{value}%"


def build_weekly_digest(db, org: models.Organization, days: int = 7) -> tuple[str, str]:
    """
    Compose the digest for one clinic. Returns (subject, body).

    Split out from the sending so the wording is testable without SMTP,
    and so a clinic's numbers can be inspected without emailing anyone.
    """
    from . import analytics_service

    data = analytics_service.overview(db, org, days)

    concluded = data["appointments_completed"] + data["appointments_no_show"]
    subject = f"{org.name} — your last {data['range_days']} days with MediCOR"

    lines = [
        f"{org.name}",
        f"{data['start_date']} to {data['end_date']}",
        "",
        "CALLS",
        f"  Handled            {data['total_calls']}",
        f"  Connected          {data['connected_calls']}",
        f"  Missed             {data['missed_calls']}",
        f"  Resolved by AI     {_pct(data['resolution_rate_pct'])}  (of calls that connected)",
        f"  Transferred        {_pct(data['transfer_rate_pct'])}  ({data['transferred_calls']} reached a person)",
        f"  AI call time       {data['ai_handled_minutes']} min",
        "",
        "APPOINTMENTS",
        f"  Booked             {data['appointments_booked']}",
        f"  Scheduled          {data['appointments_scheduled']}",
        f"  Completed          {data['appointments_completed']}",
        f"  No-shows           {data['appointments_no_show']}",
        f"  No-show rate       {_pct(data['no_show_rate_pct'])}"
        + (f"  ({data['appointments_no_show']} of {concluded} concluded)" if concluded else ""),
        "",
        "Figures are for your clinic's own timezone. Nothing here is",
        "estimated — every number is counted from real calls and",
        "appointments.",
    ]
    return subject, "\n".join(lines)


def _digest_recipients(db, org_id: str) -> list[models.User]:
    """
    Active admins of the clinic.

    Medical staff are excluded deliberately: this is an operational
    summary about the practice, not a clinical one, and a receptionist
    receiving a weekly performance report is noise rather than signal.
    Users with an unaccepted invite are excluded too — mailing a
    performance report to someone who has never logged in is a small
    disclosure to a possibly-wrong address.
    """
    return [
        u
        for u in db.query(models.User).filter(
            models.User.org_id == org_id,
            models.User.role == models.UserRole.admin,
            models.User.is_active == True,  # noqa: E712 - SQL Server BIT compatibility
        ).all()
        if u.email and not u.invite_pending
    ]


def send_weekly_digest(db, org: models.Organization, days: int = 7) -> int:
    """
    Email the digest to a clinic's admins. Returns how many were sent.

    Returns 0 rather than raising when email is unconfigured, which is
    the normal state — `_send_email` is itself non-throwing, so a dead
    SMTP host degrades to a log line instead of failing the scheduled run
    and marking the whole sweep failed for every other clinic.
    """
    if not _email_configured():
        return 0

    recipients = _digest_recipients(db, org.id)
    if not recipients:
        return 0

    subject, body = build_weekly_digest(db, org, days)
    for user in recipients:
        _send_email(user.email, subject, body)
    return len(recipients)


# ---------------------------------------------------------------------
# Pre-appointment reminders (F3)
#
# The confirmation messages above fire the moment a booking is made. A
# reminder is the other, more valuable half: it goes out shortly *before*
# the appointment, which is when a patient can still act on it. Until the
# scheduler existed (F2) there was no way to send one at all.
# ---------------------------------------------------------------------

def send_post_visit_follow_up(
    patient: models.Patient,
    message: str,
    clinic_name: str = "our clinic",
) -> bool:
    """
    Send the clinic's own post-visit wording (PF-14).

    **The words are the clinic's, not this system's.** Aftercare text is a
    clinical statement -- what to expect, when to worry, when to come back
    -- and composing it on a clinic's behalf is not a software vendor's
    job. The same reasoning made `EMERGENCY_INSTRUCTION` configuration
    rather than code.

    So this function formats and delivers; it never supplies content. A
    clinic that has written nothing gets nothing sent (the caller checks),
    rather than a generic message going out over its name.

    `{name}` is the only substitution. A template language here would be a
    way for a settings screen to raise exceptions inside a scheduled job.
    """
    first_name = (patient.name or "").strip().split()[0] if patient.name else ""
    body = (message or "").replace("{name}", first_name).strip()
    if not body:
        return False

    sent_email = _send_patient_email(patient, f"Following up - {clinic_name}", body)
    sent_message = _send_patient_whatsapp(patient, body)
    return sent_email or sent_message


def offer_waiting_list_slot(
    patient: models.Patient,
    when_label: str,
    doctor_name: Optional[str],
    clinic_name: str = "our clinic",
) -> bool:
    """
    Offer a freed slot to someone on the waiting list (PF-15).

    Says plainly that the offer is time-limited and how to take it. It
    does **not** say the slot is held for them, because it is not: a
    walk-in can still take it first (see `models.WaitingListEntry` for why
    holding it was rejected). Promising a hold this system does not
    implement is the sort of small dishonesty that is discovered at the
    worst possible moment.
    """
    with_doctor = f" with {doctor_name}" if doctor_name else ""
    return _send_patient_whatsapp(
        patient,
        f"Hi {patient.name}, an appointment{with_doctor} at {clinic_name} has come "
        f"free on {when_label}. Reply BOOK to take it - first to reply gets it. "
        f"Reply STOP to opt out of messages.",
    )


def request_call_feedback(patient: models.Patient, clinic_name: str = "our clinic") -> bool:
    """
    Ask the patient what they thought of their call (PF-10).

    One question, answered with one keystroke. Anything a patient has to
    compose a sentence for, most will not answer at all -- so the ask is a
    number, and free text is invited but never required.

    Sent only over the phone channel. An email asking for a reply-with-a-
    number would be answered by almost nobody, and adding a link would
    turn a two-second favour into a task.
    """
    return _send_patient_whatsapp(
        patient,
        f"Hi {patient.name}, how was your recent call with {clinic_name}? "
        f"Reply with a number from 1 (poor) to 5 (excellent). "
        f"Reply STOP to opt out of messages.",
    )


def notify_missed_call(patient: models.Patient, clinic_name: str = "our clinic") -> bool:
    """
    Apologise for a call that failed, and invite the patient to try again.

    Deliberately does **not** claim anyone will call them back. The
    clinic's callback queue is worked by people, on their own schedule,
    and promising a return call this message cannot guarantee is the kind
    of small dishonesty that costs trust the first time it is not kept.

    Returns whether anything was dispatched, so the caller can record it.
    """
    line = (
        f"Hi {patient.name}, we're sorry \u2014 your call to {clinic_name} didn't get "
        f"through. Please call us again when it suits you and we'll help."
    )
    sent_email = _send_patient_email(patient, f"We missed your call \u2014 {clinic_name}", line)
    sent_message = _send_patient_whatsapp(patient, line)
    return sent_email or sent_message


def notify_refill_recorded(
    patient: models.Patient,
    medication: str,
    clinic_name: str = "our clinic",
) -> None:
    """
    Confirm in writing that a repeat-prescription request was taken down
    (PF-09).

    A spoken conversation leaves nothing behind. The patient is told on
    the call that their request has been passed to the clinic, and then
    holds nothing until a decision arrives -- which may be days. This is
    the receipt for the gap in between.

    **It must not imply approval, and that is the whole safety argument
    of F9 restated in a different medium.** The agent records a request
    and can never approve one; the tool result it sees is deliberately
    narrow for exactly that reason. A message saying "your prescription is
    being prepared" would undo that from the other end -- so this says
    what was recorded, says a person will review it, and stops.

    Nothing here is a clinical statement: no dose, no timing, no advice on
    whether to keep taking anything.
    """
    med = (medication or "your repeat prescription").strip()
    line = (
        f"We've recorded your request for {med}. "
        f"Someone at {clinic_name} will review it and let you know. "
        f"This is not an approval - please keep following your current "
        f"prescription until you hear from us."
    )
    _send_patient_email(
        patient,
        "We've recorded your prescription request",
        f"Hi {patient.name},\n\n{line}\n\n\u2014 {clinic_name}",
    )
    _send_patient_whatsapp(patient, f"Hi {patient.name}, {line}")


def schedule_refill_recorded_notification(
    background_tasks: BackgroundTasks, refill_id: str, org_id: str
) -> None:
    background_tasks.add_task(_send_refill_recorded, refill_id, org_id)


def _send_refill_recorded(refill_id: str, org_id: str) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(models.RefillRequest)
            .filter(
                models.RefillRequest.id == refill_id,
                models.RefillRequest.org_id == org_id,
            )
            .first()
        )
        if row is None or not row.patient_id:
            return
        patient = (
            db.query(models.Patient)
            .filter(
                models.Patient.id == row.patient_id,
                models.Patient.org_id == org_id,
            )
            .first()
        )
        if patient is None:
            return
        org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
        notify_refill_recorded(patient, row.medication, org.name if org else "our clinic")
    except Exception:
        logger.warning("Could not send a refill receipt", exc_info=True)
    finally:
        db.close()


def notify_refill_decision(
    patient: models.Patient,
    medication: str,
    status: str,
    clinic_name: str = "our clinic",
) -> None:
    """
    Tell the patient the clinic has decided on their repeat-prescription
    request (PF-11).

    The agent already tells them, truthfully, that the request has been
    passed to the clinic. Then nothing happened -- so the patient
    telephoned to find out, which is precisely the call the feature was
    meant to remove. This closes that loop.

    **It carries the outcome and nothing clinical.** An approval says
    where to collect; a decline says the clinic will be in touch and stops
    there. It does not say why, does not name a reason, and does not
    advise on the medicine -- the same boundary the voice agent works
    under. Whoever declined it knows the reason and can give it properly.
    """
    approved = status == "approved"
    med = (medication or "your repeat prescription").strip()

    if approved:
        subject = "Your repeat prescription is ready"
        line = (
            f"Your request for {med} has been approved. "
            f"Please contact {clinic_name} to arrange collection."
        )
    else:
        subject = "About your repeat prescription request"
        line = (
            f"Your request for {med} could not be approved. "
            f"{clinic_name} will be in touch."
        )

    _send_patient_email(
        patient,
        subject,
        f"Hi {patient.name},\n\n{line}\n\n\u2014 {clinic_name}",
    )
    _send_patient_whatsapp(patient, f"Hi {patient.name}, {line}")


def schedule_refill_decision_notification(
    background_tasks: BackgroundTasks, refill_id: str, org_id: str
) -> None:
    """
    Queue it after the response, in the module's usual posture: a
    clinician working through the queue must never see a request fail
    because a message could not be sent.
    """
    background_tasks.add_task(_send_refill_decision, refill_id, org_id)


def _send_refill_decision(refill_id: str, org_id: str) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(models.RefillRequest)
            .filter(
                models.RefillRequest.id == refill_id,
                models.RefillRequest.org_id == org_id,
            )
            .first()
        )
        if row is None or not row.patient_id:
            return
        patient = (
            db.query(models.Patient)
            .filter(
                models.Patient.id == row.patient_id,
                # Org-scoped even though the parent already is: the parent
                # being in this org does not prove the child is.
                models.Patient.org_id == org_id,
            )
            .first()
        )
        if patient is None:
            return
        org = (
            db.query(models.Organization).filter(models.Organization.id == org_id).first()
        )
        notify_refill_decision(
            patient, row.medication, row.status, org.name if org else "our clinic"
        )
    except Exception:
        logger.warning("Could not send a refill decision notification", exc_info=True)
    finally:
        db.close()


def send_appointment_reminder(
    db, appt: models.Appointment, clinic_name: str = "our clinic"
) -> bool:
    """
    Remind one patient about one upcoming appointment.

    Returns True when at least one channel was dispatched — meaning the
    caller should mark the appointment reminded — and False when the
    patient has no contact details at all, or the appointment has no
    patient attached (a staff-entered slot with only a name on it).

    Deliberately mirrors `notify_appointment_booked`: same `_format_when`
    rendering, same "use whichever channel is configured AND the patient
    has details for" rule, same non-raising transport. A reminder that
    throws would fail the scheduled run for every other clinic in the
    sweep.

    The wording asks the patient to call back to change anything, rather
    than inviting a reply. Replies would land in a WhatsApp inbox nothing
    reads yet — two-way confirmation needs inbound Twilio, which this
    deployment does not have. Promising a channel that is not listening
    is worse than not offering one.
    """
    if not appt.patient_id:
        return False

    patient = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == appt.patient_id,
            # Org-scoped: the appointment being in this org does not prove
            # the patient row is.
            models.Patient.org_id == appt.org_id,
        )
        .first()
    )
    if patient is None:
        return False

    doctor = (
        db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first()
        if appt.doctor_id
        else None
    )

    when = _format_when(appt)
    doctor_line = f" with {doctor.name}" if doctor else ""
    where = f" at {appt.location}" if appt.location else ""

    # `sent` drives `reminder_sent_at`, so consent has to be able to
    # make this False. A patient who has not agreed to be contacted is
    # treated exactly like one with no contact details: unmarked,
    # because nothing was claimed on their behalf.
    # PF-13/PF-06: the reminder is the message a patient actually acts
    # on, and both the self-service controls and the pre-visit form live
    # behind the same link. Only minted when one is not already live, so
    # a patient who kept the confirmation keeps a working link.
    link = None
    if (settings.patient_link_base_url or "").strip():
        try:
            if not appt.link_token_hash:
                from .routers.appointment_link import issue_link_token

                link = appointment_link_url(issue_link_token(appt))
                db.commit()
        except Exception:
            db.rollback()
            link = None
            logger.warning("Could not issue a link for the reminder", exc_info=True)
    link_line = (
        "\n\nConfirm, change or tell us about your visit here:\n" + link
        if link else ""
    )

    sent_email = _send_patient_email(
        patient,
        f"Reminder — your appointment {when}",
        (
            f"Hi {patient.name},\n\n"
            f"This is a reminder of your appointment{doctor_line} at "
            f"{clinic_name}{where}, {when}.\n\n"
            f"If you can no longer make it, please call us so we can offer "
            f"the slot to someone else.{link_line}\n\n"
            f"— {clinic_name}"
        ),
    )
    sent_whatsapp = _send_patient_whatsapp(
        patient,
        f"Hi {patient.name}, a reminder of your appointment{doctor_line} "
        f"at {clinic_name}{where}, {when}. Please call us if you can no "
        f"longer make it.",
    )
    sent = sent_email or sent_whatsapp

    return sent
