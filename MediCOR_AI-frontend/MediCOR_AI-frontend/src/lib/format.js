/**
 * Small display helpers shared across pages.
 *
 * Pure functions only — no React — so importing them cannot cost a
 * component file its Fast Refresh.
 */

/**
 * How a clinic role is spelled in the UI.
 *
 * DISPLAY ONLY, and the one definition of it. The keys are the real
 * `user.role` values the backend issues and enforces RBAC on — `admin`
 * still means admin to `require_admin`, to the JWT, to the database and
 * to every `user.role === "admin"` check in this codebase. This maps a
 * role to a word on screen and must never be read back into a permission
 * decision.
 *
 * It lives here because four places were labelling the same three roles
 * independently and had already drifted: the team page said "Medical
 * Staff", the platform console said "Medical staff", and the org-detail
 * table printed the raw enum. That is the drift this project spent a
 * design-system pass removing everywhere else.
 */
const CLINIC_ROLE_LABEL = {
  admin: "User",
  medical_staff: "Medical staff",
  ai_agent: "AI agent",
};

/** Falls back to a humanised form so an unknown role still reads. */
export function clinicRoleLabel(role) {
  if (!role) return null;
  return CLINIC_ROLE_LABEL[role] ?? String(role).replace(/_/g, " ");
}

/**
 * Chip tone for a call outcome.
 *
 * One definition. Call Logs and Patient Profile each carried an identical
 * copy of this map, and the Dashboard had none -- it coloured every outcome
 * green, so "Cancelled" and "Transferred to Nurse" read as successes there.
 * Keys are `llm_service.ALLOWED_OUTCOMES`; outcomes not listed stay neutral.
 */
const OUTCOME_TONE = {
  Booked: "success",
  Rescheduled: "success",
  "FAQ Answered": "info",
  "Transferred to Nurse": "warning",
};

export function outcomeTone(outcome) {
  return OUTCOME_TONE[outcome] ?? "neutral";
}

/**
 * Chip tone and word for a call's status, for when it has no outcome yet
 * (in progress) or never will (failed, no answer). A blank chip there
 * reads as missing data rather than as a failure.
 *
 * `live` for in progress, never `info`: one colour means "happening now"
 * across the app, the same one the Dashboard's live band uses.
 */
// Completed is the normal end of a call, so it stays neutral; colour is
// for the live one and the failed one.
const CALL_STATUS_TONE = {
  in_progress: "live",
  completed: "neutral",
  failed: "error",
  no_answer: "neutral",
};

const CALL_STATUS_LABEL = {
  in_progress: "In progress",
  completed: "Completed",
  failed: "Failed",
  no_answer: "No answer",
};

export function callStatusTone(status) {
  return CALL_STATUS_TONE[status] ?? "neutral";
}

export function callStatusLabel(status) {
  return CALL_STATUS_LABEL[status] ?? status;
}

/**
 * Chip tone and word for an appointment status.
 *
 * One definition. The Dashboard and Appointments each carried a map and
 * had drifted: one showed a finished visit grey, the other green like an
 * upcoming one. A no-show is "warning" because a missed slot is what the
 * clinic is trying to reduce; a cancellation is not a failure.
 */
// Upcoming is every appointment's normal state -- neutral, not green.
const APPOINTMENT_STATUS_TONE = {
  upcoming: "neutral",
  done: "neutral",
  cancelled: "neutral",
  no_show: "warning",
};

const APPOINTMENT_STATUS_LABEL = {
  upcoming: "Upcoming",
  done: "Done",
  cancelled: "Cancelled",
  no_show: "No-show",
};

export function appointmentStatusTone(status) {
  return APPOINTMENT_STATUS_TONE[status] ?? "neutral";
}

export function appointmentStatusLabel(status) {
  return APPOINTMENT_STATUS_LABEL[status] ?? status;
}

/**
 * Initials for an avatar.
 *
 * `Patient.initials` is nullable and in practice often null: the seeded
 * records carry it, but every patient the voice agent registers comes back
 * with `initials: null`, which rendered as an empty coloured circle. Three
 * of the five patients in this deployment look like that.
 *
 * Deriving them here invents nothing — the name is printed immediately
 * beside the avatar, so this is a restatement of visible data rather than
 * a guess about missing data. That is the line: a *name* would never be
 * derived, initials of a shown name are only formatting.
 *
 * Falls back to "?" so the circle is never blank.
 */
export function initialsOf(name, provided) {
  if (provided) return provided;
  const words = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  return words
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}
