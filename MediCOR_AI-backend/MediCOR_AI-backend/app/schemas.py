from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .security import validate_password_strength


# ---------- Auth ----------
class RegisterIn(BaseModel):
    org_name: str = Field(..., min_length=1, max_length=200)
    full_name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)
    # IANA name, e.g. "Asia/Kolkata". Optional — omitting it leaves the
    # clinic on UTC, which the profile page can change later. Asked for at
    # signup because scheduling is wrong until it is right.
    timezone: Optional[str] = Field(None, max_length=64)

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        error = validate_password_strength(v)
        if error:
            raise ValueError(error)
        return v

    @field_validator("org_name", "full_name")
    @classmethod
    def _strip_and_require_nonblank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field cannot be blank.")
        return v


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str
    role: str
    org_id: str
    org_name: str


# ---------- Staff management (HV-2) ----------
class TeamMemberOut(BaseModel):
    """A user as seen by their own clinic's admin on /app/team."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    invite_pending: bool
    invited_at: Optional[datetime] = None
    invite_expires_at: Optional[datetime] = None


class InviteIn(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=200)
    # Deliberately not defaulted: inviting someone is exactly the moment to
    # decide what they may do. `admin` is accepted but must be chosen.
    role: Literal["admin", "medical_staff"]


class InviteOut(BaseModel):
    """
    The created (inactive) user, plus the one-time invite token.

    `invite_token` and `invite_url` are returned **once** and never stored
    in recoverable form — same contract as POST /ehr/api-keys. They are
    returned even when SMTP is configured, so an admin can always hand the
    link over directly if the email doesn't arrive.
    """

    user: TeamMemberOut
    invite_token: str
    invite_url: Optional[str] = None
    email_sent: bool = False


class TeamMemberUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=200)
    role: Optional[Literal["admin", "medical_staff"]] = None
    is_active: Optional[bool] = None


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str = Field(..., min_length=8, max_length=200)
    # Same bound and same strength rule as accepting an invitation: the
    # 72-byte cap is bcrypt's, and exceeding it silently truncates rather
    # than erroring, so it is rejected up front.
    password: str = Field(..., min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        error = validate_password_strength(v)
        if error:
            raise ValueError(error)
        return v


class PasswordResetAck(BaseModel):
    """
    Returned for every reset request, whether the address is registered or
    not. See routers/auth.py: distinguishing them would turn this into a
    way to enumerate a clinic's staff.
    """
    detail: str


class AcceptInviteIn(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        error = validate_password_strength(v)
        if error:
            raise ValueError(error)
        return v


# ---------- Org ----------
class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    plan: str


# ---------- Vitals (embedded) ----------
class VitalsIn(BaseModel):
    bp: Optional[str] = None
    bp_trend: Optional[str] = None
    hr: Optional[str] = None
    weight: Optional[str] = None
    recorded: Optional[str] = None


# ---------- Prescription ----------
class PrescriptionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    detail: Optional[str] = Field(None, max_length=2000)
    status: str = "Active"
    note: Optional[str] = Field(None, max_length=2000)


class PrescriptionCreate(PrescriptionBase):
    pass


class PrescriptionOut(PrescriptionBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


# ---------- Interaction ----------
class InteractionBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    date_label: Optional[str] = Field(None, max_length=100)
    detail: Optional[str] = Field(None, max_length=5000)
    has_audio: bool = False
    duration: Optional[str] = None
    audio_url: Optional[str] = None


class InteractionCreate(InteractionBase):
    pass


class InteractionOut(InteractionBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    occurred_at: datetime


# ---------- Appointment (nested, on patient) ----------
class PatientAppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    day_label: Optional[str] = None
    time_label: Optional[str] = None
    location: Optional[str] = None
    status: str


# ---------- Patient ----------
class PatientBase(BaseModel):
    mrn: Optional[str] = Field(None, max_length=50)  # auto-generated server-side if omitted (e.g. AI registration over the phone)
    name: str = Field(..., min_length=1, max_length=200)
    dob: Optional[str] = Field(None, max_length=50)
    age: Optional[int] = Field(None, ge=0, le=150)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[EmailStr] = None
    doctor: Optional[str] = Field(None, max_length=200)
    status: str = "Active"
    initials: Optional[str] = Field(None, max_length=10)

    # --- Contact consent (PF-05) ---
    # Omitted entirely means "not asked", which is not the same as
    # "declined". Only staff and the voice agent set this; a patient's own
    # STOP reply is handled by notification_service, not by this schema.
    contact_consent: Optional[str] = None
    contact_consent_source: Optional[str] = None
    message_channel: Optional[str] = None

    @field_validator("message_channel")
    @classmethod
    def _channel_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in MESSAGE_CHANNELS:
            raise ValueError(
                "Unsupported message channel. Expected one of: " + ", ".join(MESSAGE_CHANNELS)
            )
        return v

    @field_validator("contact_consent")
    @classmethod
    def _consent_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CONTACT_CONSENT_STATES:
            raise ValueError(
                "Unsupported consent state. Expected one of: "
                + ", ".join(CONTACT_CONSENT_STATES)
            )
        return v

    @field_validator("contact_consent_source")
    @classmethod
    def _consent_source_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CONTACT_CONSENT_SOURCES:
            raise ValueError(
                "Unsupported consent source. Expected one of: "
                + ", ".join(CONTACT_CONSENT_SOURCES)
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_nonblank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Patient name cannot be blank.")
        return v


class PatientCreate(PatientBase):
    vitals: Optional[VitalsIn] = None


class PatientUpdate(BaseModel):
    mrn: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    dob: Optional[str] = Field(None, max_length=50)
    age: Optional[int] = Field(None, ge=0, le=150)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[EmailStr] = None
    doctor: Optional[str] = Field(None, max_length=200)
    status: Optional[str] = None
    initials: Optional[str] = Field(None, max_length=10)
    vitals: Optional[VitalsIn] = None

    # --- Contact consent (PF-05) ---
    # Omitted entirely means "not asked", which is not the same as
    # "declined". Only staff and the voice agent set this; a patient's own
    # STOP reply is handled by notification_service, not by this schema.
    contact_consent: Optional[str] = None
    contact_consent_source: Optional[str] = None
    message_channel: Optional[str] = None

    @field_validator("message_channel")
    @classmethod
    def _channel_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in MESSAGE_CHANNELS:
            raise ValueError(
                "Unsupported message channel. Expected one of: " + ", ".join(MESSAGE_CHANNELS)
            )
        return v

    @field_validator("contact_consent")
    @classmethod
    def _consent_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CONTACT_CONSENT_STATES:
            raise ValueError(
                "Unsupported consent state. Expected one of: "
                + ", ".join(CONTACT_CONSENT_STATES)
            )
        return v

    @field_validator("contact_consent_source")
    @classmethod
    def _consent_source_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CONTACT_CONSENT_SOURCES:
            raise ValueError(
                "Unsupported consent source. Expected one of: "
                + ", ".join(CONTACT_CONSENT_SOURCES)
            )
        return v



# The languages the voice pipeline actually supports. Sarvam STT detects
# the caller's language and Sarvam TTS speaks it back, but only these
# three are allowed to reach TTS — agent.py clamps to them explicitly.
# Anything else stored here would be spoken in the wrong voice or fail.
SPOKEN_LANGUAGES = ("en-IN", "hi-IN", "te-IN")


# Whether this patient may be messaged. A closed vocabulary for the same
# reason `SPOKEN_LANGUAGES` is one: the value is read back out by
# `notification_service` on every send, and a typo would fail open or
# closed silently rather than loudly.
#
# There is deliberately no "unknown" member. Never having asked is
# represented by NULL, not by a value — see models.Patient.contact_consent
# for why that distinction carries weight.
# What a caller may ask for about how the agent speaks (PF-16). A closed
# vocabulary rather than a pace number for the same reason
# SPOKEN_LANGUAGES is closed: the value is handed back to Sarvam TTS on
# the next call, and the mapping from "slow" to an actual figure is
# configuration, not something a client gets to assert.
SPEECH_PACE_PREFERENCES = ("slow",)

# How a patient is reached. Closed because the value selects a transport
# at send time; an unrecognised one would silently mean "no channel".
MESSAGE_CHANNELS = ("whatsapp", "sms", "email")

CONTACT_CONSENT_STATES = ("granted", "declined")

# How the answer reached us. Recorded because "did we ever ask, and how"
# is the question a complaint turns on, and the audit log stores field
# names rather than values so it cannot answer that on its own.
CONTACT_CONSENT_SOURCES = ("voice", "staff", "reply")


class PatientListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mrn: str
    name: str
    doctor: Optional[str] = None
    status: str
    initials: Optional[str] = None
    # Present so the voice agent can greet a returning caller in the
    # language they actually speak. The agent strips this field before
    # handing the result to the LLM — this shape goes straight into a
    # live prompt and every field costs tokens on the Groq TPM budget.
    preferred_language: Optional[str] = None
    # Same justification as `preferred_language`: the agent needs it to set
    # up TTS the moment a returning caller is identified, and it is
    # stripped before the list is handed to the model.
    speech_pace_preference: Optional[str] = None


class PatientDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mrn: str
    name: str
    dob: Optional[str] = None
    age: Optional[int] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    doctor: Optional[str] = None
    status: str
    initials: Optional[str] = None
    # The language this patient last spoke on a call. Read-only from the
    # dashboard's point of view — it is written by the voice agent's
    # detection, not typed by staff.
    preferred_language: Optional[str] = None
    # Deliberately absent from PatientListOut: that shape is handed
    # straight into a live prompt, and whether a patient may be messaged
    # is not something the model needs or should reason about.
    speech_pace_preference: Optional[str] = None
    message_channel: Optional[str] = None
    contact_consent: Optional[str] = None
    contact_consent_at: Optional[datetime] = None
    contact_consent_source: Optional[str] = None
    vitals_bp: Optional[str] = None
    vitals_bp_trend: Optional[str] = None
    vitals_hr: Optional[str] = None
    vitals_weight: Optional[str] = None
    vitals_recorded: Optional[str] = None
    prescriptions: list[PrescriptionOut] = []
    interactions: list[InteractionOut] = []
    appointments: list[PatientAppointmentOut] = []


# ---------- Pre-visit information (PF-13) ----------

class PreVisitIn(BaseModel):
    """
    What a patient may submit through their appointment link.

    Bounded even though the columns are unbounded text: this arrives from
    an unauthenticated endpoint, and "a patient typed too much" should be
    a validation error rather than a very large row.
    """
    visit_reason: Optional[str] = Field(None, max_length=2000)
    medications: Optional[str] = Field(None, max_length=2000)
    allergies: Optional[str] = Field(None, max_length=2000)
    notes: Optional[str] = Field(None, max_length=2000)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[str] = Field(None, max_length=200)


class PreVisitOut(BaseModel):
    """
    Read back to the patient so they can see and correct what they sent.

    Carries only their own submission -- never anything from the patient
    record, which the link has no business exposing.
    """
    model_config = ConfigDict(from_attributes=True)
    visit_reason: Optional[str] = None
    medications: Optional[str] = None
    allergies: Optional[str] = None
    notes: Optional[str] = None
    submitted_at: Optional[datetime] = None


class PreVisitStaffOut(BaseModel):
    """
    The full submission, for staff.

    `proposed_*` are named that way deliberately: they are what the
    patient said, not what the record holds, and nothing applies them
    automatically.
    """
    model_config = ConfigDict(from_attributes=True)
    id: str
    appointment_id: str
    patient_id: Optional[str] = None
    visit_reason: Optional[str] = None
    medications: Optional[str] = None
    allergies: Optional[str] = None
    notes: Optional[str] = None
    proposed_phone: Optional[str] = None
    proposed_email: Optional[str] = None
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None


# ---------- Waiting list (PF-15) ----------

WAITING_LIST_STATUSES = ("waiting", "offered", "booked", "cancelled", "expired")


class WaitingListCreate(BaseModel):
    patient_id: str = Field(..., min_length=1)
    doctor_id: Optional[str] = None
    wanted_from: Optional[datetime] = None
    wanted_until: Optional[datetime] = None
    reason: Optional[str] = Field(None, max_length=300)


class WaitingListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    patient_name: Optional[str] = None
    doctor_id: Optional[str] = None
    doctor_name: Optional[str] = None
    wanted_from: Optional[datetime] = None
    wanted_until: Optional[datetime] = None
    reason: Optional[str] = None
    status: str
    offered_start_at: Optional[datetime] = None
    offer_expires_at: Optional[datetime] = None
    offers_made: int = 0
    created_at: Optional[datetime] = None


# ---------- Patient feedback (PF-10) ----------

# The scale. Closed and small on purpose: a patient answers a rating with
# one keystroke, and anything they have to think about, they do not
# answer at all.
FEEDBACK_RATING_MIN = 1
FEEDBACK_RATING_MAX = 5


class PatientFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    call_id: Optional[str] = None
    patient_id: Optional[str] = None
    rating: int
    comment: Optional[str] = None
    source: Optional[str] = None
    created_at: datetime


class FeedbackSummaryOut(BaseModel):
    """
    What the clinic sees. Deliberately alongside the AI's own quality
    score rather than instead of it -- the interesting number is the gap
    between what the judge thought and what the patient thought.
    """
    responses: int
    # None, not 0, when nobody has answered: an average of nothing is not
    # zero out of five. Same rule as `resolution_rate_pct`.
    average_rating: Optional[float] = None
    # How many of each score, so a bimodal split is visible rather than
    # averaged into a comfortable middle.
    distribution: dict[str, int] = {}
    asked: int


# ---------- Patient self-service link (PF-06 / PF-08) ----------

class LinkTokenIn(BaseModel):
    """A bare token, for the actions that need nothing else."""
    token: str = Field(..., min_length=8, max_length=200)


class LinkRescheduleIn(LinkTokenIn):
    start_at: datetime


class LinkAppointmentOut(BaseModel):
    """
    Everything a self-service link holder may ever see -- and nothing else.

    This shape is the security boundary of the whole feature, which is why
    it is written out explicitly rather than derived from the ORM model
    with `from_attributes`. If it were derived, adding a column to
    `Appointment` would silently widen what an unauthenticated caller
    receives.

    Absent on purpose: patient id, medical record number, phone, email,
    date of birth, any other appointment, and any clinical
    history. `patient_first_name` is the first name only -- enough for the
    patient to recognise the link as theirs, not enough for a forwarded
    link to confirm to a stranger that a specific named person is a
    patient of this clinic.
    """
    clinic_name: str
    patient_first_name: Optional[str] = None
    doctor_name: Optional[str] = None
    title: str
    day_label: Optional[str] = None
    time_label: Optional[str] = None
    location: Optional[str] = None
    status: str
    confirmation_status: Optional[str] = None
    # False when the appointment has no doctor, which is the one case the
    # slot grid cannot be computed for.
    can_reschedule: bool = False
    # Whether they have already told us about this visit (PF-13). A
    # boolean, not the content: the content is fetched separately so the
    # summary view stays as small as it is.
    pre_visit_submitted: bool = False


# ---------- Appointment (top-level) ----------
class AppointmentBase(BaseModel):
    title: str
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    doctor_id: Optional[str] = None
    reason: Optional[str] = None
    day_label: Optional[str] = None
    time_label: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    status: str = "upcoming"
    ai_generated: bool = False


class AppointmentCreate(AppointmentBase):
    pass


# The appointment lifecycle, previously implicit. `no_show` is new: until
# it existed a patient who simply did not arrive was indistinguishable
# from an appointment nobody got round to updating, so the no-show rate —
# the number a clinic buys this product to reduce — could not be computed
# at all.
#
# `cancelled` and `no_show` are deliberately separate. A cancellation is
# the patient telling you; a no-show is the patient not telling you. They
# cost the clinic different amounts and only one is a service failure.
APPOINTMENT_STATUSES = ("upcoming", "done", "cancelled", "no_show")


class AppointmentUpdate(BaseModel):
    title: Optional[str] = None
    day_label: Optional[str] = None
    time_label: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    status: Optional[str] = None
    ai_generated: Optional[bool] = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: Optional[str]) -> Optional[str]:
        """
        Until this existed the field took any string, so a typo became a
        status: an appointment PATCHed to "canceled" (one l) stayed
        visible in the calendar and kept blocking its slot, because every
        conflict query tests `status != "cancelled"`.
        """
        if v is not None and v not in APPOINTMENT_STATUSES:
            raise ValueError(
                "Invalid status. Expected one of: " + ", ".join(APPOINTMENT_STATUSES)
            )
        return v


class AppointmentOut(AppointmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    # PF-08. Read-only from the dashboard's point of view: it is written
    # by the patient through their link, not typed by staff.
    confirmation_status: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    confirmation_source: Optional[str] = None


# ---------- Doctors & Scheduling ----------
class DoctorOut(BaseModel):
    """
    Lean shape returned by GET /doctors.

    Deliberately NOT widened with working-hours fields: the voice agent
    calls this on a live call and hands the result straight to the LLM,
    where every extra field costs prompt tokens. Management screens use
    DoctorAdminOut (GET /doctors/all) instead.
    """

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    specialty: Optional[str] = None
    slot_minutes: int
    consultation_fee_label: Optional[str] = None


def _work_days_to_list(csv: Optional[str]) -> list[int]:
    if not csv:
        return []
    out: list[int] = []
    for part in csv.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.append(int(part))
    return sorted(set(out))


class DoctorAdminOut(BaseModel):
    """Full doctor record for the clinic's own management screens."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    specialty: Optional[str] = None
    slot_minutes: int
    work_start_hour: int
    work_end_hour: int
    work_days: list[int] = []
    active: bool
    consultation_fee_label: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _expand_work_days(cls, data):
        # Presents work_days_csv ("0,1,2,3,4") as a list, which is far
        # easier for a day-picker UI than parsing a string client-side.
        if hasattr(data, "work_days_csv"):
            values = {
                "id": data.id,
                "name": data.name,
                "specialty": data.specialty,
                "slot_minutes": data.slot_minutes,
                "work_start_hour": data.work_start_hour,
                "work_end_hour": data.work_end_hour,
                "work_days": _work_days_to_list(data.work_days_csv),
                "active": bool(data.active),
                "consultation_fee_label": data.consultation_fee_label,
            }
            return values
        return data


class _DoctorWritable(BaseModel):
    """Shared validation for doctor create/update."""

    specialty: Optional[str] = Field(None, max_length=200)
    slot_minutes: int = Field(30, ge=5, le=240)
    work_start_hour: int = Field(9, ge=0, le=23)
    work_end_hour: int = Field(17, ge=1, le=24)
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    active: bool = True
    consultation_fee_label: Optional[str] = Field(None, max_length=100)

    @field_validator("work_days")
    @classmethod
    def _validate_days(cls, v):
        if not v:
            raise ValueError("Select at least one working day.")
        for day in v:
            if day < 0 or day > 6:
                raise ValueError("Working days must be 0 (Monday) through 6 (Sunday).")
        return sorted(set(v))

    @model_validator(mode="after")
    def _validate_hours(self):
        if self.work_end_hour <= self.work_start_hour:
            raise ValueError("The end of the working day must be after the start.")
        return self

    @property
    def work_days_csv(self) -> str:
        return ",".join(str(d) for d in self.work_days)


class DoctorCreate(_DoctorWritable):
    name: str = Field(..., min_length=1, max_length=200)


class DoctorUpdate(BaseModel):
    """All-optional patch. Hour/day validation runs only on what is sent."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    specialty: Optional[str] = Field(None, max_length=200)
    slot_minutes: Optional[int] = Field(None, ge=5, le=240)
    work_start_hour: Optional[int] = Field(None, ge=0, le=23)
    work_end_hour: Optional[int] = Field(None, ge=1, le=24)
    work_days: Optional[list[int]] = None
    active: Optional[bool] = None
    consultation_fee_label: Optional[str] = Field(None, max_length=100)

    @field_validator("work_days")
    @classmethod
    def _validate_days(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("Select at least one working day.")
        for day in v:
            if day < 0 or day > 6:
                raise ValueError("Working days must be 0 (Monday) through 6 (Sunday).")
        return sorted(set(v))


class AvailabilitySlotOut(BaseModel):
    start_at: datetime
    label: str  # e.g. "10:00 AM"


class AvailabilityOut(BaseModel):
    doctor_id: str
    doctor_name: str
    date: str  # YYYY-MM-DD
    slots: list[AvailabilitySlotOut]


class AppointmentBookIn(BaseModel):
    patient_id: str = Field(..., min_length=1)
    doctor_id: str = Field(..., min_length=1)
    start_at: datetime
    reason: Optional[str] = Field(None, max_length=500)


class AppointmentRescheduleIn(BaseModel):
    start_at: datetime
    doctor_id: Optional[str] = None  # omit to keep the same doctor


class PendingBookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_name: str
    type_label: Optional[str] = None
    when_label: Optional[str] = None
    status: str
    # Structured booking intent (Phase 0). Null on rows created before
    # these fields existed; such rows cannot be verified into a real
    # appointment. Additive — existing clients that ignore them are
    # unaffected.
    patient_id: Optional[str] = None
    doctor_id: Optional[str] = None
    start_at: Optional[datetime] = None
    reason: Optional[str] = None


class PendingBookingCreate(BaseModel):
    patient_name: str
    type_label: Optional[str] = None
    when_label: Optional[str] = None
    # Supply these to make the pending booking verifiable into a real,
    # schedulable appointment. All optional so existing callers keep
    # working unchanged.
    patient_id: Optional[str] = None
    doctor_id: Optional[str] = None
    start_at: Optional[datetime] = None
    reason: Optional[str] = None


# ---------- Calls ----------
class TranscriptMessageBase(BaseModel):
    who: str = Field(..., pattern="^(ai|patient)$")
    text: str = Field(..., min_length=1, max_length=10000)
    time_label: Optional[str] = Field(None, max_length=50)


class TranscriptMessageCreate(TranscriptMessageBase):
    pass


class TranscriptMessageOut(TranscriptMessageBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class CallLogBase(BaseModel):
    patient_id: Optional[str] = None
    appointment_id: Optional[str] = None
    patient_name: str = "Unknown Caller"
    timestamp_label: Optional[str] = None
    reason: Optional[str] = None
    duration: Optional[str] = None
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    sentiment: Optional[str] = None
    ai_summary: Optional[str] = None
    actions_taken: Optional[str] = None
    direction: Optional[str] = None  # inbound | outbound
    status: Optional[str] = None  # in_progress | completed | failed | no_answer
    caller_phone: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    # Which telephony stack carried the call and the carrier's own id for
    # it. Written by the voice gateway at call start; see the note on
    # models.CallLog.provider. Bounded here as well as in the database so
    # a malformed value is a 422 rather than a truncation at insert.
    provider: Optional[str] = Field(default=None, max_length=32)
    provider_call_sid: Optional[str] = Field(default=None, max_length=64)


class CallLogCreate(CallLogBase):
    pass


class CallLogExportIn(BaseModel):
    """
    The call ids the user is exporting, in the order the table showed them.

    The dashboard filters client-side, so it sends the resulting ids
    rather than the server re-deriving the filter. Ids are still resolved
    org-scoped on the server, so this cannot reach another clinic's calls
    no matter what it asks for.
    """

    call_ids: list[str] = Field(..., min_length=1, max_length=5000)


class CallLogUpdate(BaseModel):
    """Partial update used to finalize a call (metadata, associations, summary).

    `detected_language` is what makes a returning caller be greeted in
    their own language: the backend copies it onto the patient record, so
    the next call can start in the right language instead of re-detecting
    from scratch. Validated against `SPOKEN_LANGUAGES` because the value
    is fed back to Sarvam TTS — an unsupported tag would either be spoken
    in the wrong voice or rejected mid-call.
    """

    patient_id: Optional[str] = None
    appointment_id: Optional[str] = None
    patient_name: Optional[str] = None
    caller_phone: Optional[str] = None
    reason: Optional[str] = None
    duration: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: Optional[str] = None
    outcome: Optional[str] = None
    sentiment: Optional[str] = None
    ai_summary: Optional[str] = None
    actions_taken: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    detected_language: Optional[str] = None
    # PF-16. Validated on the way in because it is fed back to Sarvam TTS
    # on this patient's next call.
    speech_pace_requested: Optional[str] = None
    # Patchable because an outbound call log is created before the carrier
    # has issued a SID, exactly as caller_phone is backfilled on inbound.
    provider: Optional[str] = Field(default=None, max_length=32)
    provider_call_sid: Optional[str] = Field(default=None, max_length=64)

    @field_validator("speech_pace_requested")
    @classmethod
    def _pace_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in SPEECH_PACE_PREFERENCES:
            raise ValueError(
                "Unsupported speech pace. Expected one of: "
                + ", ".join(SPEECH_PACE_PREFERENCES)
            )
        return v

    @field_validator("detected_language")
    @classmethod
    def _supported_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in SPOKEN_LANGUAGES:
            raise ValueError(
                "Unsupported language. Expected one of: " + ", ".join(SPOKEN_LANGUAGES)
            )
        return v


class CallLogListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: Optional[str] = None
    appointment_id: Optional[str] = None
    patient_name: str
    timestamp_label: Optional[str] = None
    reason: Optional[str] = None
    duration: Optional[str] = None
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    sentiment: Optional[str] = None
    direction: Optional[str] = None
    status: Optional[str] = None
    caller_phone: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class CallLogDetailOut(CallLogListOut):
    # NOTE: no `recording_url`. Call recording is not built — the column
    # exists but nothing writes it, and it used to be returned here and
    # played with `new Audio(url)`, which advertised playback the product
    # cannot do and would have served a full PHI conversation as an
    # unauthenticated link. See the note on models.CallLog.recording_url
    # before adding it back.
    ai_summary: Optional[str] = None
    actions_taken: Optional[str] = None
    # What the caller actually spoke on this call, as detected by Sarvam
    # STT. Deliberately on the detail shape and not on CallLogListOut:
    # the list shape is what `GET /calls` returns for the dashboard table
    # and what the agent's own lookups pull, and neither needs it.
    detected_language: Optional[str] = None
    speech_pace_requested: Optional[str] = None
    # Detail-only, for the same reason the two fields above are: the list
    # shape is what the dashboard table and the agent's own lookups pull,
    # and a carrier id is of use to neither. Staff investigating one call
    # need it; a table of eighty rows does not.
    provider: Optional[str] = None
    provider_call_sid: Optional[str] = None
    transcript_messages: list[TranscriptMessageOut] = []

    # --- LLM analysis (AI-2) ---
    # `summary_model` null means `ai_summary` is the rule-based fallback
    # from call_intelligence.py rather than a model-generated summary.
    # The UI can use that to label provenance honestly instead of calling
    # a template an "AI Summary".
    intent: Optional[str] = None
    # Populated from CallLog.extracted — a property that decodes the
    # stored `extracted_json` text (see app/models.py).
    extracted: dict = Field(default_factory=dict)
    summary_model: Optional[str] = None
    summary_generated_at: Optional[datetime] = None


class TranscriptBulkCreate(BaseModel):
    """Full/partial transcript submitted in one shot at call end. Replaces
    any transcript messages already stored for the call (idempotent on retry)."""

    messages: list[TranscriptMessageCreate]


# ---------- Dashboard ----------
class DashboardStatsOut(BaseModel):
    calls_handled_today: int
    appointments_booked_today: int
    resolution_rate_pct: float
    staff_time_saved_hrs: float



class KBDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    size_bytes: int
    status: str
    updated_at: datetime


class KBSourceBase(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)


class KBSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    status: str
    updated_at: datetime


class FAQBase(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=5000)


class FAQOut(FAQBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


# ---------- Clinic info & services (Knowledge Base) ----------
class ClinicInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # Lives on Organization, not ClinicInfo, but belongs to the same
    # "clinic profile" form — surfaced here so there is one screen to edit.
    timezone: Optional[str] = None
    timezone_label: Optional[str] = None  # e.g. "UTC+05:30", display only
    timings_text: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    general_info: Optional[str] = None
    # PF-14. What this clinic sends after a visit, and how long it waits.
    # Blank means nothing is sent -- the wording is a clinical statement
    # and belongs to the clinic, so nothing ships with a default.
    follow_up_message: Optional[str] = None
    follow_up_delay_days: Optional[int] = None


class ClinicInfoUpdate(BaseModel):
    timezone: Optional[str] = Field(None, max_length=64)
    timings_text: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    general_info: Optional[str] = None
    follow_up_message: Optional[str] = Field(None, max_length=1000)
    # Bounded so a typo cannot schedule a message years out, and at least
    # one so nothing is sent while the patient is still in the waiting room.
    follow_up_delay_days: Optional[int] = Field(None, ge=1, le=90)


class ServiceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    fee_label: Optional[str] = Field(None, max_length=50)
    active: bool = True


class ServiceOut(ServiceBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class ServiceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    fee_label: Optional[str] = Field(None, max_length=50)
    active: Optional[bool] = None


class KBAnswerOut(BaseModel):
    answer: str
    # For a document hit this is the source document's name; for an FAQ
    # hit, the matched question. Null when nothing matched.
    matched_question: Optional[str] = None
    source: str  # "document" | "faq" | "none"

    # AI-3. Cosine similarity of the best match, 0.0 when nothing cleared
    # `kb_min_similarity`. Additive and optional, so the voice agent's
    # existing handling is unaffected — it keys only on `source == "none"`.
    # Surfaced so the QW-6 test console can show *why* an answer was given,
    # which is what makes the retrieval quality inspectable by the clinic.
    confidence: float = 0.0




# ---------- Onboarding ----------
class OnboardingStepOut(BaseModel):
    key: str
    label: str
    done: bool
    detail: str


class OnboardingStatusOut(BaseModel):
    complete: bool
    steps: list[OnboardingStepOut]

# ---------- Voice agent runtime (Phase 2) ----------
class AgentResolveIn(BaseModel):
    """The number the caller dialled, in whatever shape SIP handed it over."""

    dialed_number: str = Field(..., min_length=1, max_length=64)


class AgentTenantOut(BaseModel):
    """
    Everything the voice agent needs to answer as one specific clinic:
    who it is, what clock it keeps, how it should introduce itself, and a
    short-lived org-scoped token for the rest of the call.
    """

    org_id: str
    org_name: str
    timezone: str
    phone_number: str
    access_token: str
    expires_in_seconds: int
    voice_profile: Optional[str] = None
    greeting_script: Optional[str] = None
    # This clinic's enabled routing rules, already rendered one line
    # each. The backend decides which rules apply and how they read; the
    # agent only drops them into its prompt. Empty means the clinic has
    # none, and the agent then adds no escalation section at all.
    escalation_rules: list[str] = Field(default_factory=list)
    # Whether this clinic has any rule the *server* evaluates (GAP-T9).
    # The agent asks `/agent/escalation-check` once per caller turn, and
    # a clinic with none must not pay a round trip per turn for an answer
    # that is always "no". False means: do not ask.
    escalation_deterministic: bool = False


class PhoneNumberBase(BaseModel):
    e164: str = Field(..., min_length=2, max_length=32)
    label: Optional[str] = Field(None, max_length=120)
    trunk_id: Optional[str] = Field(None, max_length=120)
    direction: str = Field("inbound", pattern="^(inbound|outbound|both)$")
    active: bool = True


class PhoneNumberCreate(PhoneNumberBase):
    org_id: str = Field(..., min_length=1)


class PhoneNumberUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=120)
    trunk_id: Optional[str] = Field(None, max_length=120)
    direction: Optional[str] = Field(None, pattern="^(inbound|outbound|both)$")
    active: Optional[bool] = None


class PhoneNumberOut(PhoneNumberBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    org_id: str
    org_name: Optional[str] = None

# ---------- Agent settings ----------
class AgentSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    voice_profile: str
    greeting_script: str


class AgentSettingsUpdate(BaseModel):
    voice_profile: Optional[str] = None
    greeting_script: Optional[str] = None


#: Served to the settings screen rather than hardcoded there -- the rule
#: F14 arrived at for webhook events, after a UI ended up offering three
#: events that no part of the codebase emitted. The authority is
#: `escalation_service.CONDITION_TYPES`; this is the same tuple, imported
#: at the bottom of this module to avoid a circular import.
ESCALATION_CONDITIONS = (
    "model",
    "keyword",
    "unknown_caller",
    "outside_hours",
)


class RoutingRuleBase(BaseModel):
    """
    An escalation rule, structured since GAP-T9.

    `condition_type` defaults to `model`, which is what every rule was
    before: prose in the agent's prompt, matched by the model. Choosing
    anything else moves the decision to `app/escalation_service.py`,
    where it is evaluated the same way every time.
    """

    title: str
    detail: Optional[str] = None
    enabled: bool = True

    condition_type: str = "model"
    #: Comma-separated. Only read when `condition_type == "keyword"`.
    keywords: Optional[str] = None
    #: Where this particular rule transfers to. None means the agent's
    #: configured default, which is what every escalation used to hit.
    destination: Optional[str] = Field(None, max_length=64)
    #: Lowest first. Decides which rule wins when a call matches several.
    priority: int = 100

    @field_validator("condition_type")
    @classmethod
    def _known_condition(cls, v: str) -> str:
        value = (v or "model").strip().lower()
        if value not in ESCALATION_CONDITIONS:
            raise ValueError(
                f"Unknown condition type {v!r}. Expected any of: "
                + ", ".join(ESCALATION_CONDITIONS)
            )
        return value

    @model_validator(mode="after")
    def _keywords_are_required_by_the_type_that_uses_them(self):
        """
        A `keyword` rule with no keywords never fires. Silently, and
        forever -- which is precisely the shape of defect this codebase
        keeps finding (a control over a capability that does not exist).
        Refuse it at the point somebody is looking at the screen.
        """
        if self.condition_type == "keyword":
            terms = [t for t in (self.keywords or "").split(",") if t.strip()]
            if not terms:
                raise ValueError(
                    "A keyword rule needs at least one keyword, or it can "
                    "never match. Use the 'model' condition for a rule "
                    "written as a sentence."
                )
        return self


class RoutingRuleOut(RoutingRuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: str

    @field_validator("condition_type", mode="before")
    @classmethod
    def _null_reads_as_model(cls, v):
        # Rows written before 0032 hold NULL, and `sync_missing_columns`
        # never backfills. Same reading as escalation_service.condition_of.
        return v or "model"

    @field_validator("priority", mode="before")
    @classmethod
    def _null_priority_reads_as_default(cls, v):
        return 100 if v is None else v


class RoutingRuleUpdate(BaseModel):
    """
    PATCH: every field optional, so validation cannot be a model
    validator over a half-populated object. The keyword/condition
    coherence check therefore lives in the router, which is the only
    place that can see the merged result.
    """

    title: Optional[str] = None
    detail: Optional[str] = None
    enabled: Optional[bool] = None
    condition_type: Optional[str] = None
    keywords: Optional[str] = None
    destination: Optional[str] = Field(None, max_length=64)
    priority: Optional[int] = None

    @field_validator("condition_type")
    @classmethod
    def _known_condition(cls, v):
        if v is None:
            return v
        value = v.strip().lower()
        if value not in ESCALATION_CONDITIONS:
            raise ValueError(
                f"Unknown condition type {v!r}. Expected any of: "
                + ", ".join(ESCALATION_CONDITIONS)
            )
        return value


class EscalationCheckIn(BaseModel):
    """
    What the agent knows at the moment a caller finished speaking.

    Everything is optional because the agent does not always know it, and
    a missing input must mean "this condition does not match" rather than
    a guess. `caller_is_known=None` in particular means nobody looked --
    which is not the same as the caller being unknown.
    """

    utterance: Optional[str] = Field(None, max_length=2000)
    caller_is_known: Optional[bool] = None


class EscalationCheckOut(BaseModel):
    """
    The verdict, plus which rule produced it.

    `rule_title` and `matched_on` exist so the answer is explicable after
    the fact -- the thing the model-only path could never provide. They
    are written into the call's action list, not spoken.
    """

    escalate: bool
    #: Whether this clinic has ANY server-evaluated rule.
    #:
    #: False tells the agent to stop asking for the rest of the call.
    #: `resolve_tenant` already says the same thing up front, but the
    #: single-tenant fallback never resolves a tenant -- and without this
    #: that deployment would either ask on every turn forever or, if it
    #: never asked, silently lose every rule a clinic converted from
    #: prose to a keyword (those no longer go into the prompt).
    has_rules: bool = False
    rule_id: Optional[str] = None
    rule_title: Optional[str] = None
    condition_type: Optional[str] = None
    destination: Optional[str] = None
    matched_on: Optional[str] = None


# ---------- EHR ----------
class EHRIntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    status: str
    connected: bool
    detail: Optional[str] = None
    note: Optional[str] = None
    meta1_label: Optional[str] = None
    meta1_value: Optional[str] = None
    meta2_label: Optional[str] = None
    meta2_value: Optional[str] = None


class EHRIntegrationUpdate(BaseModel):
    status: Optional[str] = None
    connected: Optional[bool] = None
    api_key: Optional[str] = None


class APIKeyCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    environment: str = Field("production", pattern="^(production|staging)$")


class APIKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    label: str
    key_prefix: str
    environment: str
    created_at: datetime
    revoked: bool


class APIKeyCreatedOut(APIKeyOut):
    plaintext_key: str  # shown once, on creation only


# Events a clinic's own systems can subscribe to (F14).
#
# These are the things that actually happen. The three this replaced —
# "transcript.completed", "intent.requires_action", "agent.error" — were
# offered by the config screen and emitted by nothing, for as long as the
# webhook feature had existed.
#
# Closed on purpose, for the same reason `audit_service.ACTIONS` is: a
# typo in a sender would simply never match a subscription, and would
# therefore fail silently and permanently.
WEBHOOK_EVENTS = (
    "call.completed",
    "appointment.booked",
    "appointment.cancelled",
    "appointment.rescheduled",
    "refill.requested",
)


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    endpoint_url: Optional[str] = None
    events: list[str]
    # Served rather than hardcoded in the UI, which is how the screen came
    # to offer three events that did not exist.
    available_events: list[str] = list(WEBHOOK_EVENTS)
    # The HMAC signing secret. Shown so the clinic can verify our
    # signature on their side — it is a *shared* secret, useless to them
    # if we hide it, and it is already only readable by an authenticated
    # member of the org that owns it.
    secret: Optional[str] = None
    last_delivery_at: Optional[datetime] = None
    last_delivery_status: Optional[str] = None


class WebhookUpdate(BaseModel):
    endpoint_url: Optional[str] = None
    events: Optional[list[str]] = None

    @field_validator("events")
    @classmethod
    def _known_events(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        unknown = [e for e in v if e not in WEBHOOK_EVENTS]
        if unknown:
            raise ValueError(
                "Unknown event(s): " + ", ".join(unknown)
                + ". Expected any of: " + ", ".join(WEBHOOK_EVENTS)
            )
        return v


# ---------- EHR sync & clinical history (Phase 7) ----------
class EHRStatusOut(BaseModel):
    connected: bool
    provider: Optional[str] = None
    configured: bool = False
    detail: Optional[str] = None
    # HV-4. True when the org's plan excludes `ehr_access`, as opposed to
    # nobody having connected an EHR yet. The dashboard needs the
    # difference: one is an upgrade prompt, the other is a Connect button.
    plan_restricted: bool = False


class EHRSyncResultOut(BaseModel):
    status: str  # synced | not_configured | unavailable | local_only
    provider: Optional[str] = None
    detail: Optional[str] = None
    synced_at: Optional[datetime] = None


class EHRPrescriptionOut(BaseModel):
    id: str
    name: str
    detail: Optional[str] = None
    status: str
    note: Optional[str] = None


class EHRVisitOut(BaseModel):
    id: str
    title: str
    date_label: Optional[str] = None
    occurred_at: Optional[datetime] = None
    detail: Optional[str] = None


class EHRAppointmentOut(BaseModel):
    id: str
    title: str
    day_label: Optional[str] = None
    time_label: Optional[str] = None
    status: str
    start_at: Optional[datetime] = None


class EHRSourceOut(BaseModel):
    connected: bool
    provider: Optional[str] = None


class PatientHistoryOut(BaseModel):
    patient_id: str
    mrn: str
    name: str
    prescriptions: list[EHRPrescriptionOut] = []
    visits: list[EHRVisitOut] = []
    appointments: list[EHRAppointmentOut] = []
    ehr_source: EHRSourceOut


# ---------- Security ----------
class ComplianceOut(BaseModel):
    """
    What is actually known about this clinic's compliance posture.

    `hipaa_verified` used to default to True here and was never set by
    the router, so the API asserted that every clinic was verified —
    including one that registered thirty seconds ago. It is now derived
    from whether a security audit date has actually been recorded, which
    is the only evidence the system holds.

    `audit_log_entries` is the number this page exists to make true: HV-1
    shipped the write path, so the Security page can state how many real
    events are behind it rather than rendering seeded demo rows.
    """

    hipaa_verified: bool = False
    last_security_audit: str
    data_retention_years: int
    baa_status: str
    audit_log_entries: int = 0
    encryption_at_rest: str = "AES-256"
    encryption_in_transit: str = "TLS 1.3"


class RoleSummaryOut(BaseModel):
    name: str
    count: int
    detail: str


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    occurred_at: datetime
    action: str
    who: Optional[str] = None
    status: str
    # HV-1 context. All optional: entries written before HV-1 (by
    # `seed.py` or the old `ehr_service._log_audit`) legitimately have
    # none of it, and backfilling a guess would put invented attribution
    # into an audit log.
    actor_type: Optional[str] = None
    actor_user_id: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    detail: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuditLogPageOut(BaseModel):
    """
    Paged, so the Security page stops silently truncating at 50 rows.

    Same shape as `AdminAuditLogPageOut` on purpose — two audit views
    with different pagination contracts is one contract too many.
    """

    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------- Billing ----------
class PlanOut(BaseModel):
    plan: str
    status: str = "Active"
    # Float since HV-4: usage is computed from actual call durations
    # rather than being a whole-number counter someone incremented.
    minutes_used: float
    minutes_limit: int
    usage_pct: float
    next_billing_date: Optional[str] = None
    payment_label: str
    payment_expires: str

    # HV-4 additions. All defaulted, so nothing that reads the older
    # shape breaks. `over_limit` is reported, not enforced — see
    # `usage_service` for why cutting off calls needs a human decision.
    over_limit: bool = False
    # The billing month in the clinic's own timezone, e.g.
    # "Sep 1 - Sep 30, 2026". Not the same thing as `next_billing_date`,
    # which is a stored column a super admin sets.
    period_label: Optional[str] = None
    # What the plan actually grants, so the page can stop describing
    # every plan as including "premium EHR integrations".
    subscription_status: Optional[str] = None
    ehr_access: bool = True
    knowledge_base_access: bool = True
    user_limit: Optional[int] = None
    patient_limit: Optional[int] = None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    invoice_number: str
    issued_at: datetime
    amount_cents: int
    status: str


# ---------- Support ----------
# ---------- Analytics (HV-9) ----------
class AnalyticsOverviewOut(BaseModel):
    range_days: int
    start_date: date
    end_date: date
    total_calls: int
    connected_calls: int
    # Calls that never connected. Broken out rather than folded into the
    # resolution rate, where they used to be counted as successes.
    missed_calls: int
    transferred_calls: int
    # None, not 0.0, when nothing connected — "we answered nothing" and
    # "we answered badly" are different facts and a zero reads as the second.
    resolution_rate_pct: Optional[float] = None
    transfer_rate_pct: Optional[float] = None
    # Replaces `staff_time_saved_hrs`, which was `calls * 5 / 60` — a
    # constant dressed as a measurement. This is real call duration the
    # AI handled without transferring.
    ai_handled_minutes: float = 0.0
    average_handle_seconds: Optional[int] = None
    # Booked during the range (by created_at).
    appointments_booked: int = 0
    # Scheduled FOR the range (by start_at, clinic wall-clock). A
    # different question from the line above, so both are reported.
    appointments_scheduled: int = 0
    appointments_completed: int = 0
    appointments_no_show: int = 0
    appointments_cancelled: int = 0
    # None until something has concluded. Denominator is done + no_show:
    # a cancellation is the patient telling you, which is not a no-show,
    # and folding it in would dilute the rate toward zero.
    no_show_rate_pct: Optional[float] = None


class AnalyticsDailyPointOut(BaseModel):
    date: str
    total: int
    missed: int
    transferred: int


class AnalyticsHourPointOut(BaseModel):
    hour: int
    calls: int


class AnalyticsWeekdayPointOut(BaseModel):
    weekday: int
    label: str
    calls: int


class AnalyticsCountOut(BaseModel):
    label: str
    count: int


class AnalyticsBreakdownsOut(BaseModel):
    by_outcome: list[AnalyticsCountOut] = Field(default_factory=list)
    by_sentiment: list[AnalyticsCountOut] = Field(default_factory=list)
    by_reason: list[AnalyticsCountOut] = Field(default_factory=list)
    # From AI-2's structured extraction — empty when LLM analysis is off.
    by_intent: list[AnalyticsCountOut] = Field(default_factory=list)
    by_status: list[AnalyticsCountOut] = Field(default_factory=list)


class AnalyticsDoctorRowOut(BaseModel):
    doctor: str
    booked: int
    ai_booked: int
    cancelled: int


# ---------- Call quality evaluation (AI-7) ----------
class CallEvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    call_id: str
    score: Optional[int] = None
    goal_achieved: Optional[bool] = None
    hallucination_detected: Optional[bool] = None
    # Decoded from `policy_violations_json` by the model property.
    policy_violations: list[str] = Field(default_factory=list)
    conciseness: Optional[int] = None
    reasoning: Optional[str] = None
    judge_model: Optional[str] = None
    evaluated_at: datetime
    reviewed_at: Optional[datetime] = None
    # True when a human should look at this call — a hallucination or a
    # policy violation flags it regardless of score.
    needs_review: bool = False


class CallEvaluationWithCallOut(CallEvaluationOut):
    """An evaluation plus enough of the call to triage it without a second fetch."""

    patient_name: Optional[str] = None
    occurred_at: Optional[datetime] = None
    outcome: Optional[str] = None


class EvaluationSummaryOut(BaseModel):
    evaluated_calls: int
    average_score: Optional[float] = None
    goal_achieved_pct: Optional[float] = None
    # Counted separately from the average on purpose: a run of concise,
    # efficient calls that each stated one wrong fact would show a healthy
    # average and still be the most urgent thing on the page.
    hallucination_count: int = 0
    policy_violation_count: int = 0
    needs_review_count: int = 0
    violations_by_code: dict = Field(default_factory=dict)


class EvaluationRunOut(BaseModel):
    queued: bool
    limit: int
    detail: str


# ---------- Notifications (HV-6) ----------
class NotificationOut(BaseModel):
    id: str
    level: str
    title: str
    message: Optional[str] = None
    # Decoded from the stored `data_json`; shape varies by event type
    # (e.g. {"call_id": ...} or {"appointment_id": ...}) so it stays an
    # open dict rather than a union the client would have to narrow.
    data: dict = Field(default_factory=dict)
    read_at: Optional[datetime] = None
    created_at: datetime


class NotificationPageOut(BaseModel):
    items: list[NotificationOut]
    # Returned with every page so the bell badge never needs a second
    # request. Counts unread across the whole org, not just this page.
    unread_count: int


class NotificationReadAllOut(BaseModel):
    marked_read: int


# ---------- Missed-call callback queue (HV-7) ----------
class CallbackOut(BaseModel):
    """A missed call as a work item, rather than a log line."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    caller_phone: Optional[str] = None
    direction: str
    status: str
    reason: Optional[str] = None
    occurred_at: datetime
    timestamp_label: Optional[str] = None
    ai_summary: Optional[str] = None
    callback_status: Optional[str] = None
    callback_note: Optional[str] = None
    callback_updated_at: Optional[datetime] = None


class CallbackUpdate(BaseModel):
    callback_status: Optional[Literal["pending", "contacted", "resolved"]] = None
    callback_note: Optional[str] = Field(None, max_length=2000)


class SupportTicketCreate(BaseModel):
    subject: str = Field("Technical Issue", max_length=300)
    priority: str = Field("Normal", max_length=50)
    message: str = Field(..., min_length=1, max_length=5000)


class SupportTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subject: str
    priority: str
    message: str
    status: str
    created_at: datetime


# ---------- Super Admin ----------
class SuperAdminLoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SuperAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str


class AdminOrgListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    plan: str
    suspended: bool
    voice_minutes_used: int
    voice_minutes_limit: int
    user_count: int
    patient_count: int
    created_at: datetime

    @field_validator("suspended", mode="before")
    @classmethod
    def _null_suspended_is_false(cls, v):
        # Organizations created before the `suspended` column existed have
        # NULL there (the additive schema-sync ALTER TABLE doesn't backfill
        # existing rows) — treat "never suspended" (NULL) the same as
        # explicitly-not-suspended (False), never as a validation error.
        return bool(v) if v is not None else False


class AdminOrgUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class AdminUserListOut(BaseModel):
    """
    Cross-org user row for the platform-wide /admin/users page. Same
    underlying `User` row as AdminOrgUserOut (nothing new is stored) —
    just annotated with which org it belongs to, since this list spans
    every organization instead of being scoped to one.
    """

    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    org_id: str
    org_name: str


class AdminOrgDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    plan: str
    suspended: bool
    suspended_reason: Optional[str] = None
    voice_minutes_used: int
    voice_minutes_limit: int
    patient_count: int
    call_count: int
    appointment_count: int
    created_at: datetime
    users: list[AdminOrgUserOut]
    # Contact info — reuses the org-scoped ClinicInfo table (the same
    # record the clinic's own Clinic Info settings page reads/writes),
    # not a new field on Organization.
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_address: Optional[str] = None
    contact_website: Optional[str] = None

    @field_validator("suspended", mode="before")
    @classmethod
    def _null_suspended_is_false(cls, v):
        # Same NULL-safety as AdminOrgListOut.suspended above — pre-existing
        # organizations have NULL here, not False.
        return bool(v) if v is not None else False
    # Admin/owner — the org's oldest active admin user, same selection
    # logic already used by POST /admin/organizations/{id}/impersonate.
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None


class AdminOrgUpdateIn(BaseModel):
    plan: Optional[str] = None
    voice_minutes_limit: Optional[int] = Field(None, ge=0)
    suspended: Optional[bool] = None
    suspended_reason: Optional[str] = Field(None, max_length=500)


class AdminUserUpdateIn(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("admin", "medical_staff", "ai_agent"):
            raise ValueError("Invalid role.")
        return v


class AdminImpersonateOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    org_id: str
    org_name: str
    acting_as_user_id: str
    acting_as_email: str


class AdminPlanBreakdownOut(BaseModel):
    plan: str
    org_count: int
    voice_minutes_used: int
    voice_minutes_limit: int


class AdminPlatformStatsOut(BaseModel):
    org_count: int
    active_org_count: int
    suspended_org_count: int
    user_count: int
    active_user_count: int
    patient_count: int
    total_calls: int
    calls_today: int
    total_appointments: int
    appointments_today: int
    total_voice_minutes_used: int
    total_voice_minutes_limit: int
    plan_breakdown: list[AdminPlanBreakdownOut]


class AdminRecentActivityItemOut(BaseModel):
    type: str  # org_created | org_suspended | super_admin_action | call_completed
    title: str
    detail: Optional[str] = None
    occurred_at: datetime
    org_id: Optional[str] = None
    org_name: Optional[str] = None


class AdminAuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    occurred_at: datetime
    action: str
    status: str = "success"
    target_org_id: Optional[str] = None
    target_org_name: Optional[str] = None
    target_user_id: Optional[str] = None
    target_user_email: Optional[str] = None
    detail: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    # Who performed the action — resolved from SuperAdmin, never a raw id
    # on its own in the UI.
    super_admin_id: str
    super_admin_name: Optional[str] = None
    super_admin_email: Optional[str] = None


class AdminAuditLogPageOut(BaseModel):
    """Paginated envelope for GET /admin/audit-log (Phase 7)."""

    items: list[AdminAuditLogOut]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------- Admin: Plans & Subscriptions (Phase 5) ----------
class PlanIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    monthly_price_cents: int = Field(0, ge=0)
    voice_minutes_limit: int = Field(0, ge=0)
    user_limit: Optional[int] = Field(None, ge=0)
    patient_limit: Optional[int] = Field(None, ge=0)
    ehr_access: bool = False
    knowledge_base_access: bool = False
    features: list[str] = Field(default_factory=list)
    is_active: bool = True


class PlanUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    monthly_price_cents: Optional[int] = Field(None, ge=0)
    voice_minutes_limit: Optional[int] = Field(None, ge=0)
    user_limit: Optional[int] = Field(None, ge=0)
    patient_limit: Optional[int] = Field(None, ge=0)
    ehr_access: Optional[bool] = None
    knowledge_base_access: Optional[bool] = None
    features: Optional[list[str]] = None
    is_active: Optional[bool] = None


class CheckoutIn(BaseModel):
    """
    Which plan the clinic wants. **The price is not in here, and must
    never be**: the amount is looked up from the catalogue server-side.
    A client that can name its own price is the oldest mistake in online
    payments.
    """

    plan_name: str = Field(..., min_length=1, max_length=120)


class CheckoutOut(BaseModel):
    """
    Everything the browser needs to open Razorpay Checkout, and nothing
    more. `key_id` is publishable by design -- it identifies the account
    and Razorpay's own integration puts it in page source. The key
    SECRET never leaves the server, which is why the order is created
    here rather than in the browser.
    """

    key_id: str
    order_id: str
    amount_cents: int
    currency: str
    plan_name: str
    org_name: str
    prefill_email: Optional[str] = None
    # True when RAZORPAY_MOCK_MODE is on, so the page can say plainly
    # that no money will move rather than looking like a real checkout.
    mock: bool = False


class CheckoutVerifyIn(BaseModel):
    """The three values Razorpay Checkout hands back on success."""

    razorpay_order_id: str = Field(..., max_length=64)
    razorpay_payment_id: str = Field(..., max_length=64)
    razorpay_signature: str = Field(..., max_length=256)


class CheckoutVerifyOut(BaseModel):
    paid: bool
    plan_name: Optional[str] = None
    # False when this call found the payment already fulfilled -- the
    # webhook got there first. Still a success for the caller.
    newly_fulfilled: bool = True


class PaymentOut(BaseModel):
    """One payment, as the clinic's own Billing page may see it."""

    id: str
    plan_name: Optional[str] = None
    amount_cents: int
    currency: str
    status: str
    created_at: datetime
    captured_at: Optional[datetime] = None


class PublicContactInfoOut(BaseModel):
    """
    How to reach a person, as configured -- never as invented.

    Every field is optional and blank by default, and the pages that read
    this are written to show the form alone when nothing is set. A
    placeholder number on a public page is worse than no number: somebody
    dials it.
    """

    phone: Optional[str] = None
    email: Optional[str] = None
    hours: Optional[str] = None


class ContactEnquiryIn(BaseModel):
    """
    What a stranger may send. Bounded on the way in, because this is an
    unauthenticated write and the bounds are the only thing between the
    table and whatever somebody pastes.
    """

    name: str = Field(..., min_length=1, max_length=160)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=40)
    clinic_name: Optional[str] = Field(None, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)


class ContactEnquiryAck(BaseModel):
    """
    Deliberately says only that it was received.

    No id, no reference number, no "we will reply within X". An id would
    invite a lookup endpoint over other people's correspondence, and a
    response time is a promise this application cannot keep -- the same
    reason the missed-call message never says anyone will ring back.
    """

    received: bool = True


class AdminContactEnquiryOut(BaseModel):
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    clinic_name: Optional[str] = None
    message: str
    status: str
    created_at: datetime
    handled_at: Optional[datetime] = None


class AdminContactEnquiryUpdateIn(BaseModel):
    status: str = Field(..., pattern="^(new|handled)$")


class PublicPlanOut(BaseModel):
    """
    One plan as the public pricing page may state it.

    Deliberately an explicit field list rather than `from_attributes`
    over `models.Plan` -- this is served to anyone on the internet, so a
    new column must not start being published the day it is added. The
    same reasoning, and the same shape, as `LinkAppointmentOut`.

    Omitted on purpose: `id` (nothing public addresses a plan by id --
    `POST /billing/checkout` takes a plan NAME, so an id would be a
    second public handle on the same row for no gain),
    `features_csv` (internal keys), `is_active` (inactive plans are not
    returned at all), and the timestamps.
    """

    name: str
    description: Optional[str] = None
    monthly_price_cents: int
    # ISO 4217. Served so a page renders the price in the currency the
    # platform actually bills in -- the pricing page and the in-app
    # checkout both hardcoded a dollar sign while BILLING_CURRENCY
    # defaulted to INR.
    currency: str
    voice_minutes_limit: int
    user_limit: Optional[int] = None
    patient_limit: Optional[int] = None
    ehr_access: bool
    knowledge_base_access: bool


class AdminPlanOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    monthly_price_cents: int
    voice_minutes_limit: int
    user_limit: Optional[int] = None
    patient_limit: Optional[int] = None
    ehr_access: bool
    knowledge_base_access: bool
    features: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Denormalized for the Plans list — how many clinics currently have an
    # active subscription to this plan. Not a stored column.
    subscribed_org_count: int = 0


class AdminSubscriptionOut(BaseModel):
    id: str
    org_id: str
    org_name: str
    plan_id: str
    plan_name: str
    status: str
    started_at: datetime
    updated_at: datetime
    voice_minutes_used: int
    voice_minutes_limit: int


# ---------- Admin: Usage Management (Phase 6) ----------
class AdminUsageClinicOut(BaseModel):
    """
    One clinic's row in the Super Admin usage table. Every count/sum here
    is read straight from existing tables (CallLog, Appointment, Patient,
    KBDocument/KBSource/FAQ, EHRIntegration, APIKey, Organization,
    Subscription/Plan) for the requested date range — nothing here is
    synthesized. Fields the platform genuinely does not track yet
    (e.g. a persisted EHR sync count) are left `None` so the frontend can
    show "Not available" instead of a fabricated number.
    """

    org_id: str
    org_name: str
    plan_name: str
    has_subscription: bool  # False = no Plan(Phase 5) row assigned; legacy Organization.plan is used instead

    # Calls (from CallLog, filtered to the requested date range by occurred_at)
    total_calls: int
    voice_minutes_used_period: float  # sum(duration_seconds)/60 for calls in range; only calls with a recorded duration are counted

    # Appointments (from Appointment, filtered by created_at)
    appointments_total: int
    appointments_ai_booked: int

    # Patients — current roster size (Patient count), not date-filtered,
    # since "how many patients does this clinic have" isn't a period metric.
    patient_count: int
    user_count: int

    # Knowledge base (KBDocument / KBSource / FAQ) — current totals
    kb_document_count: int
    kb_source_count: int
    faq_count: int

    # EHR / API — only what's actually tracked today
    ehr_integrations_connected: int
    ehr_integrations_total: int
    ehr_sync_count: Optional[int] = None  # not persisted anywhere in the system today
    api_keys_active: int

    # Plan limit vs. current (all-time) usage — Organization's running
    # counter, same field the clinic's own Billing page reads.
    plan_voice_minutes_limit: int
    plan_voice_minutes_used_alltime: int
    plan_voice_minutes_usage_pct: float
    plan_user_limit: Optional[int] = None  # None = unlimited (subscribed) or unknown (no subscription)
    plan_patient_limit: Optional[int] = None


class AdminUsageSummaryOut(BaseModel):
    clinic_count: int
    total_calls: int
    total_voice_minutes_used_period: float
    total_appointments: int
    total_ai_appointments: int
    total_patients: int
    total_users: int
    total_kb_documents: int
    total_kb_sources: int
    total_faqs: int
    total_ehr_integrations_connected: int
    total_api_keys_active: int


class AdminUsageOut(BaseModel):
    start_date: date
    end_date: date
    org_id: Optional[str] = None
    summary: AdminUsageSummaryOut
    clinics: list[AdminUsageClinicOut]


class SubscriptionAssignIn(BaseModel):
    org_id: str
    plan_id: str
    status: str = "active"

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in ("active", "canceled", "past_due"):
            raise ValueError("Invalid status.")
        return v


class SubscriptionStatusUpdateIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in ("active", "canceled", "past_due"):
            raise ValueError("Invalid status.")
        return v

# ---------- Super-admin support inbox (QW-8) ----------

# A closed set, and the same three the clinic-facing chip already knows
# how to colour. Kept here rather than on the model because there is no
# migration behind it — `SupportTicket.status` is a plain string column
# that defaulted to "Open" and was never written again by anything.
SUPPORT_TICKET_STATUSES = ("Open", "In Progress", "Resolved")


class AdminSupportTicketOut(BaseModel):
    """
    A ticket with enough context for somebody who does not know the org.

    The clinic's own `SupportTicketOut` deliberately omits `org_id` and
    the submitter — a clinic already knows who it is. A platform inbox
    needs both, or every row reads "Technical Issue" with no idea whose.
    """

    model_config = ConfigDict(from_attributes=True)
    id: str
    org_id: str
    org_name: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_by_email: Optional[str] = None
    subject: str
    priority: str
    message: str
    status: str
    created_at: datetime


class AdminSupportTicketStatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in SUPPORT_TICKET_STATUSES:
            raise ValueError(
                "Invalid status. Expected one of: " + ", ".join(SUPPORT_TICKET_STATUSES)
            )
        return v


# ---------- Refill requests (F9) ----------
# A patient asking by phone for a repeat prescription. A REQUEST, never an
# approval — see models.RefillRequest for why that distinction has its own
# table rather than being a status on Prescription.

REFILL_STATUSES = ("pending", "approved", "declined")


class RefillRequestCreate(BaseModel):
    """
    What the voice agent records. Deliberately small.

    `patient_id` is required: an anonymous medication request is not
    actionable and not safe to queue. The agent must identify the caller
    with `patient_lookup` first, which the router enforces again.
    """

    patient_id: str = Field(..., min_length=1, max_length=64)
    medication: str = Field(..., min_length=1, max_length=200)
    note: Optional[str] = Field(None, max_length=2000)
    call_id: Optional[str] = Field(None, max_length=64)

    @field_validator("medication")
    @classmethod
    def _medication_nonblank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Medication cannot be blank.")
        return v


class RefillRequestUpdate(BaseModel):
    status: str
    note: Optional[str] = Field(None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        if v not in REFILL_STATUSES:
            raise ValueError(
                "Invalid status. Expected one of: " + ", ".join(REFILL_STATUSES)
            )
        return v


class RefillRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    # Null means the caller named something not on their record, which is
    # itself worth seeing at review time — so it is surfaced, not hidden.
    prescription_id: Optional[str] = None
    call_id: Optional[str] = None
    medication: str
    note: Optional[str] = None
    status: str
    requested_at: datetime
    handled_at: Optional[datetime] = None
