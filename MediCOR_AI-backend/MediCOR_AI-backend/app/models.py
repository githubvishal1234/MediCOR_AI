import enum
import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import relationship

from .database import Base

# SQLAlchemy's generic types map to SQL Server's *deprecated* LOB types:
# UnicodeText -> NTEXT, LargeBinary -> IMAGE, Text -> TEXT. Microsoft
# deprecated all three in 2005 in favour of the (MAX) forms. These
# variants pin the modern types on SQL Server while staying portable to
# the SQLite used by the test suite.
#
# Every free-text column here uses UNICODE_TEXT. It used to be plain
# `Text`, which compiles to TEXT — non-Unicode under this deployment's
# SQL_Latin1_General_CP1 collation, so anything outside that codepage was
# silently stored as "?". That destroyed Hindi and Telugu patient speech
# in `transcript_messages.text` and mangled any non-Latin greeting an
# agent was configured with, while the voice agent advertised all three
# languages. Migration 0012 widened the 21 affected columns; use
# UNICODE_TEXT (or Unicode) for any new column that can hold text a
# human typed or spoke.
UNICODE_TEXT = UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")
BINARY_BLOB = LargeBinary().with_variant(mssql.VARBINARY(None), "mssql")


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


class UserRole(str, enum.Enum):
    admin = "admin"
    medical_staff = "medical_staff"
    ai_agent = "ai_agent"


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(64), primary_key=True, default=gen_id)
    name = Column(String, nullable=False)

    # IANA timezone name, e.g. "Asia/Kolkata". Everything the clinic sees
    # or schedules is interpreted in this zone — doctor working hours,
    # slot grids, "cannot book in the past", and the dashboard's notion of
    # "today". Defaults to UTC, which reproduces the pre-Phase-1 behaviour
    # exactly, so existing installs are unaffected until a clinic sets its
    # real zone. See app/timezones.py for the storage convention.
    timezone = Column(String(64), default="UTC")

    plan = Column(String, default="Professional Plan")
    voice_minutes_used = Column(Integer, default=0)
    voice_minutes_limit = Column(Integer, default=5000)
    next_billing_date = Column(DateTime, nullable=True)
    payment_label = Column(String, default="Visa ending in 4242")
    payment_expires = Column(String, default="12/25")
    hipaa_last_audit = Column(String, default="")
    data_retention_years = Column(Integer, default=7)
    # HV-1: was `default="Signed by Admin · v2.4"`, so every clinic that
    # registered was recorded as having a signed Business Associate
    # Agreement it had never seen. That is a fabricated compliance record
    # on the page a clinic would show an auditor. New orgs now start
    # unsigned; **existing rows still hold the old string** and only the
    # owner can correct them.
    baa_status = Column(String, default="Not signed")
    created_at = Column(DateTime, default=datetime.utcnow)

    # --- Super Admin: platform-level suspension, independent of any
    # per-org billing/plan state above. A suspended org's users can no
    # longer authenticate (see deps.get_current_user /
    # deps.get_org_context) or reach the voice agent's API-key path.
    suspended = Column(Boolean, default=False)
    suspended_reason = Column(String, nullable=True)

    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    patients = relationship("Patient", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)

    # NOTE: globally unique, not unique-per-org. One consequence worth
    # knowing before changing it: the same person cannot hold accounts at
    # two clinics. Moving to UniqueConstraint(org_id, email) would change
    # login lookup semantics (POST /auth/login resolves a user by email
    # alone, with no org in hand), so it is deliberately left as-is.
    email = Column(String(255), unique=True, index=True, nullable=False)

    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.admin)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # --- Staff invitations (HV-2) ---
    # An invited user exists as a real, inactive `users` row from the
    # moment the invite is sent, rather than living in a separate
    # Invitation table. That choice buys three things: the existing unique
    # constraint on `email` prevents a duplicate invite racing a duplicate
    # signup; `get_current_user` already refuses `is_active=False`, so an
    # unaccepted invite cannot authenticate without any new check; and
    # there are no orphan rows to reconcile if an invite is never accepted.
    #
    # Only the SHA-256 of the invite token is stored — same reasoning as
    # APIKey.hashed_key in security.py (high-entropy value, needs O(1)
    # lookup, must not be recoverable from the database).
    invite_token_hash = Column(String(64), nullable=True, index=True)
    invited_at = Column(DateTime, nullable=True)
    invite_expires_at = Column(DateTime, nullable=True)
    invite_accepted_at = Column(DateTime, nullable=True)

    # --- Password reset (GAP-U1) ---
    #
    # Same construction as the invite token above, and stored the same
    # way: only the SHA-256 is kept, so a database read cannot mint a
    # working reset link.
    #
    # Unlike an invite this one is deliberately short-lived and strictly
    # single-use. An invitation is a welcome; a reset token is a bearer
    # credential for an existing account, and the two deserve different
    # lifetimes.
    #
    # Cleared on use, so a link that has already been spent is
    # indistinguishable from one that never existed.
    reset_token_hash = Column(String(64), nullable=True, index=True)
    reset_expires_at = Column(DateTime, nullable=True)
    reset_requested_at = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="users")

    @property
    def invite_pending(self) -> bool:
        """True while an invite has been issued but not yet accepted."""
        return self.invite_token_hash is not None and self.invite_accepted_at is None

    @property
    def org_name(self) -> str:
        return self.organization.name if self.organization else ""


class Patient(Base):
    __tablename__ = "patients"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    mrn = Column(String, nullable=False)
    name = Column(String, nullable=False)
    dob = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    doctor = Column(String, nullable=True)
    status = Column(String, default="Active")
    initials = Column(String, nullable=True)

    # The language this patient last actually spoke on a call, as an
    # IANA-ish BCP-47 tag ("en-IN" | "hi-IN" | "te-IN").
    #
    # The voice agent already detects the caller's language per turn and
    # switches Sarvam TTS to match — but it threw that away when the call
    # ended, so a patient who had called ten times in Telugu was still
    # greeted in English on the eleventh. This is where that detection is
    # remembered.
    #
    # Last-spoken wins, deliberately. If a relative calls in English on
    # someone's behalf the preference flips, and it flips back the next
    # time the patient speaks Telugu themselves. A sticky first-seen value
    # would never recover from one wrong guess; a majority-vote scheme
    # would need call history this column exists to avoid re-deriving.
    preferred_language = Column(String(8), nullable=True)

    # The caller asked to be spoken to more slowly, and it stuck (PF-16).
    #
    # "slow" or NULL, never a number. The agent could just as easily report
    # a pace float, but that value is fed straight back into Sarvam TTS on
    # the next call, and an out-of-range one would either be rejected mid
    # call or produce something unintelligible. A closed vocabulary here
    # and the actual number in configuration keeps the two concerns apart
    # -- exactly the reasoning behind `preferred_language` holding a
    # language tag rather than a voice id.
    #
    # Only ever set because the patient asked. There is no inference from
    # age, no guess from how long their calls run: a patient who has not
    # asked is not marked, because being slowed down unbidden is its own
    # small insult.
    speech_pace_preference = Column(String(16), nullable=True)

    # Which channel this patient can actually receive (PF-04).
    #
    # NULL means nobody has asked, and falls back to the deployment
    # default -- deliberately NOT the same shape as `contact_consent`,
    # where an unanswered question has to mean "do not send". Here the
    # question is only *how* to reach someone we already have permission
    # to reach, so a default is safe where it would not be there.
    message_channel = Column(String(16), nullable=True)  # whatsapp | sms | email

    # --- Contact consent (F17 / PF-05) ---
    #
    # Whether this patient has agreed to be messaged, and a way for them
    # to stop. Nothing here existed before, which was harmless only
    # because no transport was configured and `notification_service` sent
    # nothing. It stops being harmless the moment mail or WhatsApp
    # credentials are set, which is precisely why this lands *with*
    # messaging rather than after it.
    #
    # Tri-state on purpose, and the third state is the important one:
    #
    #   "granted"  — the patient said yes. Send.
    #   "declined" — the patient said no, or replied STOP. Never send.
    #   NULL       — nobody has ever asked.
    #
    # NULL is not "no". Every patient row that existed before this column
    # holds it, and treating that as a refusal would silently mute a
    # clinic's entire roster; treating it as consent would message people
    # who never agreed. So the decision is deliberately NOT encoded here —
    # `notification_service.may_contact` reads a per-clinic setting to
    # decide what an unasked patient means, and the default is the
    # cautious one. See CONTACT_CONSENT_STATES in schemas.
    contact_consent = Column(String(16), nullable=True)
    # When the answer was recorded, and how it reached us ("voice" |
    # "staff" | "reply"). Kept because "did we ever ask, and how" is the
    # question a complaint actually turns on, and an audit entry records
    # only that the field changed, never its value.
    contact_consent_at = Column(DateTime, nullable=True)
    contact_consent_source = Column(String(16), nullable=True)

    # latest vitals snapshot
    vitals_bp = Column(String, nullable=True)
    vitals_bp_trend = Column(String, nullable=True)  # up | down | flat
    vitals_hr = Column(String, nullable=True)
    vitals_weight = Column(String, nullable=True)
    vitals_recorded = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="patients")
    prescriptions = relationship("Prescription", back_populates="patient", cascade="all, delete-orphan")
    interactions = relationship("Interaction", back_populates="patient", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="patient", cascade="all, delete-orphan")
    call_logs = relationship("CallLog", back_populates="patient")


class Prescription(Base):
    __tablename__ = "prescriptions"

    id = Column(String(64), primary_key=True, default=gen_id)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=False)
    name = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    status = Column(String, default="Active")  # Active | Refill Soon
    note = Column(String, nullable=True)

    patient = relationship("Patient", back_populates="prescriptions")


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(String(64), primary_key=True, default=gen_id)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=False)
    title = Column(String, nullable=False)
    date_label = Column(String, nullable=True)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    detail = Column(UNICODE_TEXT, nullable=True)
    has_audio = Column(Boolean, default=False)
    duration = Column(String, nullable=True)
    audio_url = Column(String, nullable=True)

    patient = relationship("Patient", back_populates="interactions")


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    specialty = Column(String, nullable=True)
    slot_minutes = Column(Integer, default=30)
    work_start_hour = Column(Integer, default=9)  # 24h clinic-local clock
    work_end_hour = Column(Integer, default=17)
    work_days_csv = Column(String, default="0,1,2,3,4")  # Mon=0 .. Sun=6
    active = Column(Boolean, default=True)
    consultation_fee_label = Column(String, nullable=True)  # e.g. "$120" or "Free for follow-ups"

    appointments = relationship("Appointment", back_populates="doctor")


class Appointment(Base):
    __tablename__ = "appointments"

    # The calendar orders by start_at within an org, and _validate_slot's
    # conflict check scans the same (org_id, start_at) shape on every
    # booking. Covers org_id-alone lookups too. Migration: 0005.
    __table_args__ = (Index("ix_appointments_org_start", "org_id", "start_at"),)

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=True)
    doctor_id = Column(String(64), ForeignKey("doctors.id"), nullable=True)
    title = Column(String, nullable=False)
    patient_name = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    day_label = Column(String, nullable=True)
    time_label = Column(String, nullable=True)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    location = Column(String, nullable=True)
    status = Column(String, default="upcoming")  # upcoming | done | cancelled | no_show
    ai_generated = Column(Boolean, default=False)

    # When the pre-appointment reminder was dispatched (UTC), or NULL if it
    # has not been. This is the idempotency guard for the scheduled
    # reminder job: the scheduler's own run_key already stops two workers
    # racing the same tick, but this is what stops a patient being
    # reminded twice across *different* ticks, restarts, or a widened
    # lead time.
    #
    # Set once the pass has run for the appointment, not only on a
    # confirmed delivery. `_send_email` and `_send_whatsapp` swallow their
    # own failures by design, so "it definitely arrived" is not knowable
    # here — and retrying forever on an address that will never accept
    # mail is the worse of the two failure modes.
    reminder_sent_at = Column(DateTime, nullable=True)

    # --- Patient self-service link (PF-06) ---
    #
    # One live link per appointment, following the staff-invitation
    # precedent exactly: only the SHA-256 of the token is stored, the
    # plaintext exists solely inside the link we send, and lookup is an
    # indexed exact match on the hash.
    #
    # It differs from an invite in one deliberate way: it is NOT
    # single-use. An invite is accepted once; an appointment link has to
    # keep working until the appointment happens, because a patient may
    # open it, think, and come back. Expiry does the bounding instead.
    #
    # Scoped to ONE appointment on purpose. A token that resolved to a
    # patient would, if forwarded or leaked, expose their whole record and
    # every appointment they have. This exposes one booking and nothing
    # else -- see routers/appointment_link.py for what it may return.
    link_token_hash = Column(String(64), nullable=True, index=True)
    link_expires_at = Column(DateTime, nullable=True)

    # --- Attendance confirmation (PF-08) ---
    #
    # NULL until the patient says something. There is deliberately no
    # "declined" member: declining to attend IS cancelling, and the
    # appointment status already records that. A second field expressing
    # the same fact would let the two disagree.
    #
    # This column was deliberately not added earlier, because at the time
    # nothing could have written it -- a column nothing writes is the
    # `call_logs.recording_url` mistake. PF-06 is its writer.
    confirmation_status = Column(String(16), nullable=True)  # None | "confirmed"
    confirmed_at = Column(DateTime, nullable=True)
    confirmation_source = Column(String(16), nullable=True)  # link | voice | staff
    # When the post-visit follow-up went out (PF-14). Written on dispatch,
    # like `reminder_sent_at`, for the same reason: the transports swallow
    # their own failures, so arrival is not knowable, and sending twice is
    # worse than not knowing.
    follow_up_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")


class PendingBooking(Base):
    """
    An AI-proposed booking awaiting staff confirmation.

    `patient_name` / `type_label` / `when_label` are display strings only.
    Verifying a pending booking has to produce a REAL appointment, and an
    appointment is only real if it has a concrete `start_at` and a doctor —
    the calendar places appointments by `start_at`, and conflict detection
    ignores rows where it is null. The three structured columns below carry
    that information so `verify_pending_booking` can build a complete,
    schedulable appointment instead of a label-only shell.

    They are nullable because rows created before Phase 0 (and the seeded
    demo rows) predate them; verify refuses those rather than silently
    creating an appointment that renders nowhere.
    """

    __tablename__ = "pending_bookings"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    patient_name = Column(String, nullable=False)
    type_label = Column(String, nullable=True)
    when_label = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | verified | declined

    # --- Structured booking intent (Phase 0) ---
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=True)
    doctor_id = Column(String(64), ForeignKey("doctors.id"), nullable=True)
    start_at = Column(DateTime, nullable=True)
    reason = Column(String, nullable=True)


class CallLog(Base):
    __tablename__ = "call_logs"

    # Dashboard stats and call-volume both filter by org AND a
    # clinic-local day range (see timezones.local_day_bounds_utc), so the
    # composite serves those directly. It also covers a lookup on org_id
    # alone, which is why there is no separate index on that column.
    # Migration: 0005_org_id_indexes.
    __table_args__ = (Index("ix_call_logs_org_occurred", "org_id", "occurred_at"),)

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=True)
    appointment_id = Column(String(64), ForeignKey("appointments.id"), nullable=True)
    patient_name = Column(String, default="Unknown Caller")
    timestamp_label = Column(String, nullable=True)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String, nullable=True)
    duration = Column(String, nullable=True)  # human-readable label, e.g. "4m 12s"
    duration_seconds = Column(Integer, nullable=True)
    outcome = Column(String, nullable=True)  # Booked | FAQ Answered | Transferred to Nurse | ...
    sentiment = Column(String, nullable=True)  # Positive | Neutral | Concerned
    ai_summary = Column(UNICODE_TEXT, nullable=True)
    actions_taken = Column(UNICODE_TEXT, nullable=True)  # newline separated
    # RESERVED, AND NOT WIRED TO ANYTHING. Call recording is not built.
    #
    # This column has never been written by any code path. The call-detail
    # panel nonetheless carried a "Play Recording" control for it, and the
    # API returned the field. The control was gated on the value being
    # truthy, so it never actually rendered — no user saw a broken button.
    # What existed was dead playback code and an API field advertising a
    # capability the product does not have. Both are gone; the column is
    # kept so the option survives, but it is deliberately absent from
    # every response schema.
    #
    # If recording is built later, do NOT put this value in an API
    # response and play it with `new Audio(url)`. A recording is a full
    # PHI conversation; served as a bare URL it is reachable by anyone
    # holding the link, with no authentication, no org check and no audit
    # entry. Playback belongs behind an authenticated, org-scoped endpoint
    # that writes an audit row, the way `GET /calls/{id}` already does for
    # transcripts.
    recording_url = Column(String, nullable=True)
    direction = Column(String, default="inbound")  # inbound | outbound
    status = Column(String, default="in_progress")  # in_progress | completed | failed | no_answer

    # Which telephony stack carried this call, and the carrier's own id
    # for it. Both nullable and both written only by the voice gateway --
    # every row that predates the Twilio + Deepgram migration holds NULL,
    # which is honest: the LiveKit/SIP path never recorded a carrier id,
    # and backfilling "livekit" would assert something nobody checked.
    #
    # `provider_call_sid` is the join key into the carrier's own console,
    # logs and billing (a Twilio CallSid). Without it, correlating a
    # complaint about one call with Twilio's view of that call means
    # guessing from timestamps.
    #
    # Bounded because they are identifiers: a bare String is VARCHAR(MAX)
    # on SQL Server and cannot be indexed, and "find the call for CallSid
    # X" is the whole point of the column. Not indexed today -- nothing
    # queries by it yet, and an index with no reader is speculation.
    # Migration 0033.
    provider = Column(String(32), nullable=True)  # twilio | livekit
    provider_call_sid = Column(String(64), nullable=True)
    caller_phone = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    # What the caller actually spoke on THIS call ("en-IN" | "hi-IN" |
    # "te-IN"), as detected by Sarvam STT. Kept per call as well as on the
    # patient: the patient row holds only the latest, and "which languages
    # do our callers use" is a question about calls, not people. It is
    # also the evidence for why a patient's preference changed.
    detected_language = Column(String(8), nullable=True)
    # Set only when the caller actually asked during THIS call (PF-16).
    # Per-call evidence, in the same relationship to
    # `patients.speech_pace_preference` as `detected_language` is to
    # `patients.preferred_language`.
    speech_pace_requested = Column(String(16), nullable=True)

    # --- LLM call analysis (AI-2) ---
    # `ai_summary`, `outcome` and `sentiment` above are still the fields
    # the dashboard reads — the LLM overwrites them in place, so nothing
    # in the UI had to change. These four record what the model added and
    # where the values came from.
    #
    # `summary_model` / `summary_generated_at` being null means the row
    # holds the rule-based fallback from call_intelligence.py, which is
    # the normal state when GROQ_API_KEY is unset or a request failed.
    intent = Column(String(120), nullable=True)
    extracted_json = Column(UNICODE_TEXT, nullable=True)
    summary_model = Column(String(120), nullable=True)
    summary_generated_at = Column(DateTime, nullable=True)

    # --- Missed-call callback queue (HV-7) ---
    # `status` already supported "failed" and "no_answer", and update_call
    # already fired a warning notification for them — but there was no
    # queue and no follow-up state, so a missed call surfaced as a
    # transient toast and then vanished. At a clinic a missed call is a
    # lost patient, so these three columns turn it into a work item.
    #
    # Null means "not applicable": the column is only populated once a
    # call actually ends in a missed state (see _sync_callback_state in
    # routers/calls.py), so existing rows and successful calls are
    # untouched.
    callback_status = Column(String(16), nullable=True)  # pending | contacted | resolved
    callback_note = Column(UNICODE_TEXT, nullable=True)
    callback_updated_at = Column(DateTime, nullable=True)
    # When an automatic apology went out for this missed call (PF-12).
    #
    # Deliberately NOT `callback_status = "contacted"`. That column is the
    # *human* queue -- pending, contacted, resolved -- and a machine
    # sending "sorry we missed you" is not a person having called the
    # patient back. Writing it there would quietly empty the queue staff
    # are meant to work, which is the opposite of the intent.
    callback_message_sent_at = Column(DateTime, nullable=True)
    # When we asked this patient what they thought of the call (PF-10).
    # One ask per call, ever -- see jobs.request_call_feedback for the
    # cooldown that stops a frequent caller being asked every time.
    feedback_requested_at = Column(DateTime, nullable=True)

    patient = relationship("Patient", back_populates="call_logs")
    transcript_messages = relationship(
        "TranscriptMessage", back_populates="call", cascade="all, delete-orphan", order_by="TranscriptMessage.id"
    )

    @property
    def extracted(self) -> dict:
        """
        `extracted_json` decoded, for the API response (AI-2).

        Written only by llm_service, so unparseable content means a bug
        there rather than bad input — return an empty dict so one broken
        row cannot fail the whole call-detail response.
        """
        if not self.extracted_json:
            return {}
        try:
            value = json.loads(self.extracted_json)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


class TranscriptMessage(Base):
    __tablename__ = "transcript_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String(64), ForeignKey("call_logs.id"), nullable=False)
    who = Column(String, nullable=False)  # ai | patient
    text = Column(UNICODE_TEXT, nullable=False)
    time_label = Column(String, nullable=True)

    call = relationship("CallLog", back_populates="transcript_messages")



class PhoneNumber(Base):
    """
    A telephone number owned by one clinic — the link that makes a single
    voice-agent deployment serve many tenants (Phase 2).

    Before this table there was nothing anywhere mapping a dialled number
    to an organization, so the agent worker had to be pinned to one clinic
    by a single BACKEND_API_KEY in its .env. Every caller on every number
    wrote into that one org.

    `e164` is globally unique, not unique-per-org: a phone number can only
    belong to one clinic in the world, and resolution has to be able to
    answer "who owns this number" without an org in hand.
    """

    __tablename__ = "phone_numbers"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)

    # Bounded + unique: SQL Server cannot index a VARCHAR(MAX) column, and
    # this one is looked up on every inbound call.
    e164 = Column(String(32), unique=True, index=True, nullable=False)

    label = Column(String(120), nullable=True)  # e.g. "Main reception"
    # The LiveKit SIP trunk this number arrives on. Display/ops only today;
    # resolution keys on the number itself.
    trunk_id = Column(String(120), nullable=True)
    direction = Column(String(16), default="inbound")  # inbound | outbound | both
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization")


class KBDocument(Base):
    __tablename__ = "kb_documents"

    # Removing a document must remove what it taught the AI. An orphan
    # chunk would keep answering questions from a file the clinic
    # believes it deleted.
    chunks = relationship(
        "KBChunk", cascade="all, delete-orphan", passive_deletes=False
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    size_bytes = Column(Integer, default=0)
    status = Column(String, default="Indexing")  # Indexing | Indexed | Failed
    file_path = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class KBChunk(Base):
    """
    One embedded passage of an uploaded document (AI-3).

    Before this table there was no RAG at all: an uploaded file was
    written to disk and immediately flipped to "Indexed" without ever
    being opened, and `/knowledge-base/search` matched on **filename
    only**. Every document a clinic uploaded was dead weight.

    `embedding` holds raw float32 bytes (384 dims -> 1536 bytes). SQL
    Server 2022 has no native VECTOR type — that arrived in 2025 — so
    similarity is computed in numpy over the org's chunks rather than in
    the database. At clinic scale that is faster than a round trip, and
    it avoids operating a second datastore holding PHI-adjacent text.

    Deleted with its parent document via cascade: an un-indexed orphan
    chunk would keep answering questions from a file the clinic thinks
    it removed.
    """

    __tablename__ = "kb_chunks"

    # Retrieval always scans one org's chunks; nothing ever reads across
    # orgs. Document id is in the index so re-indexing one file can
    # delete its old chunks without touching the rest.
    __table_args__ = (Index("ix_kb_chunks_org_document", "org_id", "document_id"),)

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    document_id = Column(String(64), ForeignKey("kb_documents.id"), nullable=True)
    chunk_index = Column(Integer, default=0)

    # UnicodeText, not Text: this holds clinic document content, and
    # `Text` compiles to non-Unicode TEXT on SQL Server (see CLAUDE.md).
    # A handbook with a rupee sign or a patient name in Devanagari must
    # survive being stored.
    text = Column(UNICODE_TEXT, nullable=False)

    embedding = Column(BINARY_BLOB, nullable=True)
    char_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class KBSource(Base):
    __tablename__ = "kb_sources"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    url = Column(String, nullable=False)
    status = Column(String, default="Indexing")
    updated_at = Column(DateTime, default=datetime.utcnow)


class FAQ(Base):
    __tablename__ = "faqs"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    question = Column(String, nullable=False)
    answer = Column(UNICODE_TEXT, nullable=False)


class ClinicInfo(Base):
    """
    Org-scoped singleton (one row per org, like AgentSettings) holding the
    structured clinic facts the voice agent needs to answer instantly
    without a fuzzy search: timings, contact details, and general
    "about us" info.

    Insurance is deliberately absent. This product does not handle it, so
    there is nothing for the agent to read out and nothing for a clinic
    to maintain -- see migration 0034.
    """

    __tablename__ = "clinic_info"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), unique=True, nullable=False)
    timings_text = Column(UNICODE_TEXT, nullable=True)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    website = Column(String, nullable=True)
    general_info = Column(UNICODE_TEXT, nullable=True)

    # --- Post-visit follow-up (PF-14) ---
    #
    # What to send a patient after their appointment, and how long to
    # wait. **Written by the clinic, never shipped with a default.**
    #
    # That is the whole safety design. Aftercare wording is a clinical
    # statement: what to expect, when to worry, when to come back. It is
    # not something a software vendor should compose on a clinic's behalf,
    # and it is the same reasoning that made `EMERGENCY_INSTRUCTION`
    # configuration rather than code.
    #
    # NULL or blank means the clinic has not written one, and the job then
    # sends nothing at all. No content, no feature -- rather than a
    # generic message going out over a clinic's name.
    #
    # `{name}` is substituted with the patient's first name. Nothing else
    # is interpolated: a template language here would be a way for a
    # settings screen to raise exceptions inside a scheduled job.
    follow_up_message = Column(UNICODE_TEXT, nullable=True)
    follow_up_delay_days = Column(Integer, default=3)

    updated_at = Column(DateTime, default=datetime.utcnow)


class Service(Base):
    """Org-scoped list of clinic services (name, description, fee)."""

    __tablename__ = "services"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(UNICODE_TEXT, nullable=True)
    fee_label = Column(String, nullable=True)  # e.g. "$150" or "Included with consultation"
    active = Column(Boolean, default=True)


class AgentSettings(Base):
    __tablename__ = "agent_settings"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), unique=True, nullable=False)
    voice_profile = Column(String, default="Dr. Sarah (Calm, Professional)")
    greeting_script = Column(UNICODE_TEXT, default="Thank you for calling. How can I help you today?")


class RoutingRule(Base):
    """
    When a call should stop being handled by the AI and reach a person.

    Until 0032 this was a title and a sentence of prose, both rendered
    into the agent's system prompt. Whether a rule fired was whether a
    language model recognised it on that particular call -- which is not
    nothing (it matches a paraphrase, and it matches across languages),
    but it is advisory. It could not be tested, could not be audited, and
    two identical calls could resolve differently.

    `condition_type` splits that into two honest halves rather than
    replacing one with the other, because neither is sufficient:

      * **`model`** is the original behaviour and stays first-class. Some
        conditions genuinely need judgement -- "if the caller sounds
        distressed" is not a keyword list -- and forcing those into
        structure would produce bad keyword lists, not determinism.

      * **`keyword`, `unknown_caller`, `outside_hours`** are evaluated by
        `app/escalation_service.py` on the server, before the model sees
        the turn. Same input, same verdict, every time, and the rule that
        fired is named in the result.

    The two coexist deliberately: a clinic's English keyword list will
    not catch a caller who says the Telugu for "chest pain", so the
    prose rule and the EMERGENCY block are still doing work behind it.
    Escalation is layered, not switched.

    `destination` is the number this particular rule transfers to, so
    "billing questions" and "chest pain" can reach different people --
    previously every escalation landed on one configured default.
    Nullable still means that default.

    `priority` orders evaluation, lowest first, so the winning rule on a
    call that matches three of them is a defined one rather than
    whichever row came back first.
    """

    __tablename__ = "routing_rules"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)

    # See escalation_service.CONDITION_TYPES. NULL is read as "model",
    # which is what every row written before 0032 was.
    condition_type = Column(String(24), nullable=True, default="model")

    # Comma-separated, and UNICODE_TEXT rather than String because the
    # entire objection to keyword matching was that a caller saying the
    # Hindi or Telugu for "chest pain" must be able to match -- which
    # means a clinic types Devanagari and Telugu into this column.
    keywords = Column(UNICODE_TEXT, nullable=True)

    destination = Column(String(64), nullable=True)
    priority = Column(Integer, nullable=True, default=100)


class EHRIntegration(Base):
    __tablename__ = "ehr_integrations"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    status = Column(String, default="Not Connected")
    connected = Column(Boolean, default=False)
    detail = Column(UNICODE_TEXT, nullable=True)
    note = Column(UNICODE_TEXT, nullable=True)
    meta1_label = Column(String, nullable=True)
    meta1_value = Column(String, nullable=True)
    meta2_label = Column(String, nullable=True)
    meta2_value = Column(String, nullable=True)
    api_credentials_json = Column(UNICODE_TEXT, nullable=True)


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    label = Column(String, nullable=False)
    key_prefix = Column(String, nullable=False)
    hashed_key = Column(String, nullable=False)
    environment = Column(String, default="production")  # production | staging
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Boolean, default=False)


class Webhook(Base):
    """
    Where a clinic wants its events POSTed, and which ones.

    This row, its API and its config screen all existed before anything
    ever delivered to it. `endpoint_url` was only ever read back out in
    the GET/PUT response, so a clinic could follow the landing page's
    "Webhooks for custom infrastructure" claim, save an endpoint, watch it
    persist — and receive nothing. The three default events were fictional
    too: no part of the system has ever emitted `transcript.completed`,
    `intent.requires_action` or `agent.error`.

    F14 made it real. `app/webhook_service.py` is the sender, and
    `schemas.WEBHOOK_EVENTS` is the vocabulary, which is now the set of
    things that actually happen.
    """

    __tablename__ = "webhooks"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), unique=True, nullable=False)
    endpoint_url = Column(String, nullable=True)
    events_csv = Column(String, default="call.completed,appointment.booked,appointment.cancelled")

    # Shared secret for the HMAC-SHA256 signature on every delivery, so a
    # receiver can tell our POST from anyone who guessed the URL.
    #
    # Stored recoverable, unlike APIKey.hashed_key — signing requires the
    # plaintext on this side, and the clinic needs the same value on
    # theirs. That is what a shared secret is; hashing it would make
    # signing impossible rather than safer.
    secret = Column(String(64), nullable=True)

    # Last delivery outcome, kept on the row rather than in a log table.
    # One line of truth for the config screen ("is this actually
    # working?") without a per-event row for every call a clinic takes,
    # which is the volume that would bury the audit log.
    last_delivery_at = Column(DateTime, nullable=True)          # UTC
    last_delivery_status = Column(String(120), nullable=True)   # "204" | "timeout" | ...


class AuditLogEntry(Base):
    """
    The clinic's own audit trail (HV-1) — who did what, to which record.

    Distinct from `SuperAdminAuditLog`, which records platform actions and
    is only visible to us. This one is org-scoped and rendered on the
    clinic's Security page, which is what makes it usable as the PHI
    access log a BAA expects. Write to it through `app/audit_service.py`,
    never directly: the helper owns the closed action vocabulary and the
    actor labelling, including the impersonation case.

    Columns beyond `status` were added by HV-1 (migration 0011) to mirror
    the context `SuperAdminAuditLog` already carried — without them an
    entry could say "a patient record was viewed" but not which record,
    by which account, from where.

    `action` is deliberately left as an unbounded `String`. It is
    filtered on but not indexed, and narrowing it would only apply on the
    Alembic path — `database.sync_missing_columns()` adds columns but
    never alters them, so the two schema paths would silently disagree
    about its length. See the note in CLAUDE.md.
    """

    __tablename__ = "audit_log"

    # GET /security/audit-log orders by occurred_at desc within an org.
    # Covers org_id-alone lookups too. Migration: 0005.
    __table_args__ = (Index("ix_audit_log_org_occurred", "org_id", "occurred_at"),)

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    action = Column(String, nullable=False)
    # Display label for the actor, resolved at write time. Denormalised on
    # purpose: it has to survive the account being deleted, which is
    # exactly when an investigator needs to read it.
    who = Column(String, nullable=True)
    status = Column(String, default="Success")  # Success | Blocked | Logged

    # --- HV-1 context ---
    # "user" | "service" (the voice agent) | "platform" (a super admin
    # acting inside this org) | "anonymous" (a failed login).
    actor_type = Column(String(16), nullable=True)
    # Null for service and anonymous actors, and for a staff account that
    # has since been deleted — `who` still names them either way.
    actor_user_id = Column(String(64), ForeignKey("users.id"), nullable=True)
    # What was touched, e.g. ("patient", "<id>"). Lets an investigator ask
    # "everything that happened to this record" rather than reading prose.
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(64), nullable=True)
    # Free text: which fields changed, which plan, which file name.
    # Unicode because it can quote patient-supplied content (a document
    # name, an appointment reason).
    detail = Column(UNICODE_TEXT, nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(400), nullable=True)


class CallEvaluation(Base):
    """
    An automated quality score for one call (AI-7).

    There was previously no way to tell whether a change to the agent made
    it better or worse. `agent.py` carries a history of behavioural fixes
    — `_resolve_start_at` recovering from the model returning bare times,
    `min_delay` tuned against observed transcript latency, `max_tool_steps=1`
    for cost — all of which were presumably found in production, because
    there is no offline evaluation.

    Transcripts are already persisted and `CallLog.actions_taken` already
    records which tools succeeded, so the corpus for this existed all
    along; only the scoring did not.

    Deliberately one row per (call, judge_model): re-running the judge
    after changing the rubric should produce a comparable second opinion,
    not silently overwrite the first.
    """

    __tablename__ = "call_evaluations"

    # The review queue lists an org's flagged calls newest-first, and the
    # aggregate score filters the same way.
    __table_args__ = (
        Index("ix_call_evaluations_org_evaluated", "org_id", "evaluated_at"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    call_id = Column(String(64), ForeignKey("call_logs.id"), nullable=False, index=True)

    # 0-100. The headline number; the booleans below are what make it
    # actionable, so both are stored rather than deriving one from the other.
    score = Column(Integer, nullable=True)
    goal_achieved = Column(Boolean, nullable=True)

    # The two that matter clinically. A hallucination on a live medical
    # call is not a quality issue, and neither is unsolicited medical
    # advice — both are flagged for human review regardless of score.
    hallucination_detected = Column(Boolean, nullable=True)
    policy_violations_json = Column(UNICODE_TEXT, nullable=True)

    conciseness = Column(Integer, nullable=True)  # 1-5
    reasoning = Column(UNICODE_TEXT, nullable=True)

    judge_model = Column(String(120), nullable=True)
    evaluated_at = Column(DateTime, default=datetime.utcnow)

    # Set when a human has looked at a flagged call, so the review queue
    # is a work list rather than an ever-growing pile.
    reviewed_at = Column(DateTime, nullable=True)

    @property
    def policy_violations(self) -> list:
        """`policy_violations_json` decoded, for the API response."""
        if not self.policy_violations_json:
            return []
        try:
            value = json.loads(self.policy_violations_json)
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

    @property
    def needs_review(self) -> bool:
        """
        Whether a human should look at this call.

        A hallucination or a policy violation flags the call whatever the
        score says — a call can be efficient, concise, and still have told
        a patient something untrue.
        """
        if self.reviewed_at is not None:
            return False
        if self.hallucination_detected or self.policy_violations:
            return True
        return self.score is not None and self.score < 60


class PatientFeedback(Base):
    """
    What the patient thought (PF-10).

    The product has always been thorough about its opinion of itself:
    `CallEvaluation` scores every call with a language model as judge,
    flags hallucinations and policy breaches, and queues them for review.
    All of that is the system marking its own homework. Nothing anywhere
    asked the patient.

    This is the other half, and it is deliberately one number. A rating
    scale a patient answers with a single keystroke gets answered; a form
    does not. Free text is accepted when they volunteer it, never
    required.

    Its own table rather than columns on `call_logs` for the same reason
    refill requests got one: this is a fact contributed by a different
    party at a different time, and a call is not amended by it.
    """

    __tablename__ = "patient_feedback"
    __table_args__ = (
        Index("ix_patient_feedback_org_created", "org_id", "created_at"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    # Nullable: feedback can outlive the call it is about, and a patient
    # who replies twice should not lose the first answer.
    call_id = Column(String(64), ForeignKey("call_logs.id"), nullable=True, index=True)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=True, index=True)
    # 1-5. Validated at the edge, because it arrives from an
    # unauthenticated inbound message.
    rating = Column(Integer, nullable=False)
    # Whatever else they typed. Recorded for a person to read, never
    # parsed and never acted on -- the same rule the inbound webhook
    # follows for everything that is not an exact keyword.
    comment = Column(UNICODE_TEXT, nullable=True)
    source = Column(String(16), nullable=True)  # reply | link
    created_at = Column(DateTime, default=datetime.utcnow)


class WaitingListEntry(Base):
    """
    A patient who wanted an appointment the clinic could not offer (PF-15).

    Cancellations are pure lost capacity: a slot frees up and the person
    who wanted it has already been told there was nothing and has gone
    away. Nothing recorded unmet demand, so nothing could match the two.

    HOW AN OFFER IS MADE, AND THE RACE IT DOES NOT SOLVE

    A freed slot is offered to **one** patient at a time, exclusively,
    with an expiry. It is deliberately NOT held by creating a placeholder
    appointment: a "held" status would ripple into the calendar, the
    availability grid, conflict detection, analytics and the reaper, and
    every one of those is a place for a phantom booking to leak out.

    So a walk-in can still take an offered slot before the patient
    replies. That is a real race and it is accepted knowingly: the window
    is short, and when it happens the acceptance simply fails the ordinary
    `_validate_slot` conflict check and the entry returns to waiting.
    Losing a slot you were offered is disappointing; a phantom
    appointment nobody can see is worse.

    FAIRNESS IS OLDEST-FIRST, and that is a business rule rather than an
    implementation detail: whoever has waited longest is offered first.
    """

    __tablename__ = "waiting_list_entries"
    __table_args__ = (
        Index("ix_waiting_list_org_status", "org_id", "status"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=False, index=True)
    # Null means "any clinician". A patient who will see anyone is easier
    # to place, and saying so should not require naming a doctor.
    doctor_id = Column(String(64), ForeignKey("doctors.id"), nullable=True)

    # The window they would accept, as clinic wall-clock dates. Naive and
    # local for the same reason `Appointment.start_at` is -- see
    # app/timezones.py. Null `until` means "no later bound".
    wanted_from = Column(DateTime, nullable=True)
    wanted_until = Column(DateTime, nullable=True)
    reason = Column(String(300), nullable=True)

    # waiting | offered | booked | cancelled | expired
    status = Column(String(16), default="waiting", nullable=False)

    # The live offer, if any. Cleared when it expires or is taken.
    offered_start_at = Column(DateTime, nullable=True)
    offered_at = Column(DateTime, nullable=True)
    offer_expires_at = Column(DateTime, nullable=True)
    # How many times this entry has been offered something and not
    # replied. Surfaced to staff rather than acted on automatically:
    # deciding someone has stopped caring is a judgement, not a threshold.
    offers_made = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    resulting_appointment_id = Column(
        String(64), ForeignKey("appointments.id"), nullable=True
    )


class PreVisitSubmission(Base):
    """
    What the patient told us ahead of an appointment (PF-13).

    Everything reception would otherwise collect at the desk, while other
    patients wait: what they are coming about, what they are taking, what
    they react to, and whether their cover or contact details have
    changed.

    **IT IS A SUBMISSION, NOT AN EDIT.** Nothing here is written to the
    patient record, and that is the central decision of the feature. Two
    independent reasons, either sufficient:

      * **Security.** It is reached with the appointment-link token, which
        is unauthenticated by design. If that token could change a
        patient's phone number, a link forwarded to a family group or
        lifted off a handset would become an account-takeover path:
        redirect the messages, then use inbound replies (PF-07) to cancel
        or book at will. A submission cannot be weaponised that way.

      * **Clinical correctness.** A patient-reported medication list is
        not a verified one. Writing "metformin, lisinopril" into a medical
        record because somebody typed it into a web form would put
        unverified clinical text where verified text is expected. A nurse
        reading it before the visit is the value; the record is updated by
        the person who checks it.

    So the fields below are what was *said*, held beside the appointment
    for staff to read, and applied by a human or not at all.

    Contact details are stored under strict containment: bounded lengths,
    never pushed to the EHR, never handed to the language model, and never
    written into an audit detail.
    """

    __tablename__ = "pre_visit_submissions"
    __table_args__ = (
        Index("ix_pre_visit_org_appointment", "org_id", "appointment_id"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    appointment_id = Column(
        String(64), ForeignKey("appointments.id"), nullable=False, index=True
    )
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=True, index=True)

    # Free text, all of it. Unicode because a patient may well write in
    # Hindi or Telugu -- the same reason the transcript columns are.
    visit_reason = Column(UNICODE_TEXT, nullable=True)
    medications = Column(UNICODE_TEXT, nullable=True)
    allergies = Column(UNICODE_TEXT, nullable=True)
    notes = Column(UNICODE_TEXT, nullable=True)

    # Proposed, never applied. Named `proposed_` so no future reader
    # mistakes these for the patient's actual details.
    proposed_phone = Column(String(30), nullable=True)
    proposed_email = Column(String(200), nullable=True)

    submitted_at = Column(DateTime, default=datetime.utcnow)
    # When a staff member read it. Not "applied": what they choose to
    # carry across to the record is their judgement, and recording that
    # they looked is the useful fact.
    reviewed_at = Column(DateTime, nullable=True)


class WebhookDelivery(Base):
    """
    A webhook delivery that failed and is waiting to be retried (GAP-T4).

    **Only failures live here.** A delivery that succeeds first time
    writes nothing to this table -- the outcome still goes on
    `Webhook.last_delivery_status` as before. That is the direct answer to
    the objection that kept retries out of F14: a per-event delivery table
    would collect a row for every call a busy clinic takes, whereas this
    one holds the backlog, which is empty when a clinic's endpoint is
    healthy.

    THE BODY IS STORED, NOT REBUILT

    `body` is the exact JSON that was sent, kept verbatim. Two reasons:

      * the signature is an HMAC over the bytes on the wire, so a
        re-serialised dict with different key order would fail
        verification (see `webhook_service.sign`);

      * the payload carries its own `delivery_id`, and a retry has to
        reuse it. Minting a new one would make a retry indistinguishable
        from a second event, and a receiver could not deduplicate.

    The signature itself is recomputed at send time rather than stored, so
    a clinic that rotates its secret between attempts gets a delivery it
    can actually verify.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    webhook_id = Column(String(64), ForeignKey("webhooks.id"), nullable=True)

    event = Column(String(64), nullable=False)
    # The id inside `body`, duplicated out so it can be logged and queried
    # without parsing the payload.
    delivery_id = Column(String(64), nullable=True)
    body = Column(UNICODE_TEXT, nullable=False)

    # pending | delivered | failed
    status = Column(String(16), default="pending", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, nullable=True)
    last_status = Column(String(120), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class Notification(Base):
    """
    A staff-facing notification, persisted (HV-6).

    Before this table, `realtime.notify()` pushed over WebSocket only and
    nothing was stored: a page refresh wiped the list, and anything that
    happened while nobody had the dashboard open was lost permanently. A
    transfer-to-nurse request at 3am simply did not exist by morning.

    `data_json` holds the same small payload the WebSocket event carries
    (e.g. `{"call_id": ...}`), stored as JSON text rather than as columns
    because it differs per event type and is only ever read back whole to
    build a link. It is written by the backend, never by a client.
    """

    __tablename__ = "notifications"

    # The inbox lists newest-first within an org, and the unread badge
    # counts within an org — both are served by this composite.
    __table_args__ = (Index("ix_notifications_org_created", "org_id", "created_at"),)

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False)
    level = Column(String(16), default="info")  # info | success | warning
    title = Column(String(200), nullable=False)
    message = Column(UNICODE_TEXT, nullable=True)
    data_json = Column(UNICODE_TEXT, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    invoice_number = Column(String, nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow)
    amount_cents = Column(Integer, default=0)
    status = Column(String, default="Paid")
    # The payment that produced this invoice, when one did (HV-5).
    #
    # **NULL is the meaningful case.** Every row written before payments
    # existed came from `app/seed.py` and describes a charge this
    # application never took, which is why the invoice document says on
    # its face that charges are not processed here. A row WITH a payment
    # is different in kind: money actually moved, through Razorpay, and
    # the document may honestly call itself a receipt.
    #
    # So this column is what lets one endpoint serve both without either
    # lying. Do not backfill it.
    payment_id = Column(String(64), ForeignKey("payments.id"), nullable=True)


class Payment(Base):
    """
    One attempt to pay for a plan, and the record that proves it happened.

    **The amount is decided here, never by the browser.** It is copied
    from `Plan.monthly_price_cents` when the order is created, and the
    verification step compares what the provider says was paid against
    this row. A client that could name its own price is the oldest
    mistake in online payments.

    `provider_order_id` is UNIQUE and is what makes fulfilment idempotent.
    Razorpay delivers a webhook at least once and the browser callback
    arrives as well, so the same payment reaches us at least twice by
    design; both paths look the row up by this id and both find the same
    one. Bounded (not a bare `String`) precisely because it is unique --
    SQL Server cannot put a UNIQUE constraint on VARCHAR(MAX).

    `status` is the provider's view, narrowed: created -> captured, or
    created -> failed. Nothing here tracks refunds; a refund is a
    conversation, and inventing a state for it that no code writes would
    be the `callback_status` mistake.
    """

    __tablename__ = "payments"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    # Nullable so a payment survives the plan being deleted from the
    # catalogue: the money moved, and the record of it must not vanish
    # because somebody tidied up the price list.
    plan_id = Column(String(64), ForeignKey("plans.id"), nullable=True)
    plan_name = Column(String(120), nullable=True)

    provider = Column(String(32), default="razorpay")
    provider_order_id = Column(String(64), unique=True, nullable=False)
    provider_payment_id = Column(String(64), nullable=True)

    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(8), default="INR")
    status = Column(String(20), default="created", index=True)

    # Who clicked Pay. Nullable because the row must outlive the account.
    created_by = Column(String(64), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    captured_at = Column(DateTime, nullable=True)
    # Which path fulfilled it -- "checkout" or "webhook". Not decoration:
    # if this is always "checkout", the webhook is not reaching us and the
    # only thing holding the system up is the browser staying open.
    fulfilled_via = Column(String(20), nullable=True)


class SuperAdmin(Base):
    """
    Platform-level identity, deliberately NOT a row in `users`/scoped to
    any `org_id`. Kept as its own table (rather than adding a nullable
    org_id + a new UserRole value) so nothing about existing org-scoped
    query patterns (`filter(Model.org_id == user.org_id)`) has to change
    anywhere in the codebase. Authenticates via its own JWT, issued with
    a distinct `typ` claim (see security.create_super_admin_token) so a
    super-admin token can never be replayed against ordinary
    get_current_user/get_org_context endpoints, and vice versa.
    """

    __tablename__ = "super_admins"

    id = Column(String(64), primary_key=True, default=gen_id)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SuperAdminAuditLog(Base):
    """
    Separate from AuditLogEntry (which is org-scoped and user-facing on
    each clinic's own Security page). This table records every
    cross-org / platform action a super admin takes, independent of
    which org (if any) it touched.
    """

    __tablename__ = "super_admin_audit_log"

    id = Column(String(64), primary_key=True, default=gen_id)
    super_admin_id = Column(String(64), ForeignKey("super_admins.id"), nullable=False)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    action = Column(String, nullable=False)  # e.g. "org.suspend", "org.impersonate", "user.deactivate"
    target_org_id = Column(String(64), ForeignKey("organizations.id"), nullable=True)
    target_user_id = Column(String(64), ForeignKey("users.id"), nullable=True)
    detail = Column(UNICODE_TEXT, nullable=True)
    # Phase 7: request metadata + outcome. Additive/nullable columns on
    # the SAME table (picked up automatically by database.sync_missing_
    # columns()) — deliberately not a new/duplicate audit table.
    status = Column(String, default="success")  # success | failed
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


class Plan(Base):
    """
    Super Admin-managed subscription plan catalog (Phase 5). Independent
    of `Organization.plan` (a free-text label the clinic-side /billing/plan
    endpoint has always displayed) — that column is left untouched so
    existing clinic billing behaviour does not change. When a super admin
    assigns one of these plans to a clinic (see Subscription below), the
    org's `plan` / `voice_minutes_limit` fields are kept in sync so the
    clinic-facing Billing page continues to read correct values without
    any change to routers/billing.py itself.
    """

    __tablename__ = "plans"

    id = Column(String(64), primary_key=True, default=gen_id)
    # Bounded (not plain String/VARCHAR(MAX)) because this column carries a
    # UNIQUE constraint — SQL Server rejects UNIQUE/index constraints on
    # VARCHAR(MAX) columns. 120 matches the existing PlanIn/PlanUpdateIn
    # Pydantic validation (max_length=120) in app/schemas.py, so nothing
    # the API already accepts can be truncated or rejected at the DB level.
    name = Column(String(120), unique=True, nullable=False)
    description = Column(UNICODE_TEXT, nullable=True)
    monthly_price_cents = Column(Integer, default=0)
    voice_minutes_limit = Column(Integer, default=0)
    # Null = unlimited.
    user_limit = Column(Integer, nullable=True)
    patient_limit = Column(Integer, nullable=True)
    ehr_access = Column(Boolean, default=False)
    knowledge_base_access = Column(Boolean, default=False)
    # Comma-separated feature keys (same lightweight pattern as
    # Webhook.events_csv) — avoids a separate features table for what is,
    # today, just a small set of on/off flags shown in the admin UI.
    features_csv = Column(String, default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscriptions = relationship("Subscription", back_populates="plan")


class Subscription(Base):
    """
    One row per organization: which Plan it currently has and the status
    of that assignment. Separate from Plan itself so plan definitions can
    be edited/deactivated without rewriting every org's history, and
    separate from Organization so the existing tenant table/queries never
    need an `if` for "does this org have a subscription yet".
    """

    __tablename__ = "subscriptions"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), unique=True, nullable=False)
    plan_id = Column(String(64), ForeignKey("plans.id"), nullable=False)
    status = Column(String, default="active")  # active | canceled | past_due
    started_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    organization = relationship("Organization")
    plan = relationship("Plan", back_populates="subscriptions")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=True)
    subject = Column(String, default="Technical Issue")
    priority = Column(String, default="Normal")
    message = Column(UNICODE_TEXT, nullable=False)
    status = Column(String, default="Open")
    created_at = Column(DateTime, default=datetime.utcnow)

class ContactEnquiry(Base):
    """
    Somebody who is not a customer yet, asking to be contacted.

    **The only table in this schema with no `org_id`, and that is the
    point** -- a visitor filling in the public contact form has no
    tenant, so there is nothing to scope it to. It is platform-level
    correspondence, read by super admins beside `support_tickets` (which
    *are* org-scoped, and come from clinics who have already signed in).
    Do not "fix" the missing column by attaching an org: guessing which
    clinic an anonymous enquiry belongs to is the kind of guess this
    codebase refuses everywhere else.

    Everything here is typed by a stranger, so nothing is trusted or
    parsed. `email` is not verified, `clinic_name` is not matched against
    `organizations`, and the message is stored verbatim and never read for
    keywords -- the same rule as the inbound-message webhook.

    `source_ip` is kept for abuse triage only: the form is public, and
    without it a flood cannot be told from a busy week. It is the
    resolved client IP (`rate_limit.client_ip`), so it honours the
    trusted-proxy rules rather than whatever a header claimed.
    """

    __tablename__ = "contact_enquiries"

    id = Column(String(64), primary_key=True, default=gen_id)
    name = Column(String(160), nullable=False)
    email = Column(String(255), nullable=False)
    phone = Column(String(40), nullable=True)
    clinic_name = Column(String(200), nullable=True)
    message = Column(UNICODE_TEXT, nullable=False)
    # "new" -> "handled". Deliberately two states: this is an inbox, not
    # a workflow, and a status nobody advances is the `callback_status`
    # mistake again.
    status = Column(String(20), default="new", index=True)
    source_ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    handled_at = Column(DateTime, nullable=True)
    handled_by = Column(String(64), ForeignKey("super_admins.id"), nullable=True)


class ScheduledJobRun(Base):
    """
    One attempt at one scheduled job — and the lock that makes scheduling
    safe on a deployment that cannot run a second worker safely.

    This table is the leader election. The scheduler does not coordinate
    in memory: before running anything it INSERTs a row for
    (job_name, run_key) and lets the UNIQUE constraint decide. Two
    processes racing the same tick both try; exactly one commits, the
    other takes an IntegrityError and skips. No Redis, no advisory lock,
    no second service.

    That matters more than it looks. An in-process scheduler library
    would NOT have fixed this: each worker gets its own timer and both
    fire, so a second uvicorn worker means every clinic gets two of every
    reminder. The constraint below is the thing that prevents it, which
    is why the scheduler was built around this table rather than around a
    scheduling package.

    `run_key` is the time bucket a run belongs to (e.g. "2026-09-06T17"),
    so re-ticking within the same bucket — after a restart, a slow tick,
    or a clock nudge — is a no-op rather than a double send.

    Rows are kept, not deleted: "did the reminder job run at 09:00" is a
    question worth being able to answer after the fact.
    """

    __tablename__ = "scheduled_job_runs"

    # The lock itself. Both columns are bounded because SQL Server cannot
    # put a UNIQUE constraint on VARCHAR(MAX) — see
    # tests/test_indexable_columns_are_bounded.py.
    __table_args__ = (
        UniqueConstraint("job_name", "run_key", name="uq_scheduled_job_runs_job_key"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    job_name = Column(String(120), nullable=False, index=True)
    run_key = Column(String(120), nullable=False)

    status = Column(String(16), nullable=False, default="running")  # running | succeeded | failed
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # UTC
    finished_at = Column(DateTime, nullable=True)  # UTC
    detail = Column(UNICODE_TEXT, nullable=True)

    # Which process won this claim (GAP-T1, migration 0031). Nullable
    # because rows written before 0031 have no answer, and because a
    # NULL here must never stop a job from running.
    #
    # This turns the losing side of the race into evidence. Losing a
    # claim on its own means nothing -- a process restarting inside the
    # same time bucket races itself, which is what `run_key` bucketing
    # exists to absorb. Losing it to a *different* instance id is proof
    # of a second process, which this deployment cannot run safely and
    # otherwise fails silently. See app/instance.py.
    instance_id = Column(String(64), nullable=True)


class RefillRequest(Base):
    """
    A patient asking, by phone, for a repeat prescription.

    WHAT THIS IS AND IS NOT

    It is a **request**, recorded. It is not an approval, a dispense, or a
    clinical decision. The voice agent may create one and may confirm back
    what it wrote down; it may never tell a caller the refill is approved,
    ready, or safe to take, and it must not advise on dose or timing.
    Those are prescriber decisions and the AI has neither the authority
    nor the information to make them.

    That separation is the whole reason this is its own table rather than
    a status flip on `Prescription`. A refill request that lived on the
    prescription row would make "the patient asked" and "the clinic
    agreed" the same fact, and the second one is the one with clinical
    weight.

    `medication` is free text because it is what the caller SAID, not a
    validated drug code. `prescription_id` is filled in only when the
    backend can match that text to something already on this patient's
    record — a link when we have one, and honestly absent when we do not,
    rather than a guess presented as a match.

    `patient_id` is not nullable in practice (the router refuses an
    unidentified caller) but is nullable in the column so a patient record
    can be archived without destroying the audit of what was asked for.
    """

    __tablename__ = "refill_requests"

    __table_args__ = (
        # The queue is read as "this clinic's outstanding requests, oldest
        # first", which is exactly this index.
        Index("ix_refill_requests_org_status", "org_id", "status", "requested_at"),
    )

    id = Column(String(64), primary_key=True, default=gen_id)
    org_id = Column(String(64), ForeignKey("organizations.id"), nullable=False, index=True)
    patient_id = Column(String(64), ForeignKey("patients.id"), nullable=True)

    # Set only when `medication` matched something already prescribed to
    # this patient. Null means "they named something we have no record of",
    # which is itself worth knowing at review time.
    prescription_id = Column(String(64), ForeignKey("prescriptions.id"), nullable=True)

    # Which call this came from, so a reviewer can hear/read the context
    # rather than trusting a one-line summary of a medication request.
    call_id = Column(String(64), ForeignKey("call_logs.id"), nullable=True)

    medication = Column(String(200), nullable=False)
    note = Column(UNICODE_TEXT, nullable=True)

    # pending -> approved | declined. Bounded: it is indexed above, and
    # SQL Server cannot index VARCHAR(MAX).
    status = Column(String(16), nullable=False, default="pending")

    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # UTC
    handled_at = Column(DateTime, nullable=True)  # UTC
    handled_by_user_id = Column(String(64), ForeignKey("users.id"), nullable=True)

    patient = relationship("Patient")
    prescription = relationship("Prescription")
