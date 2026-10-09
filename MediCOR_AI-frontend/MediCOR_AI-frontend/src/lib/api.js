const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const TOKEN_KEY = "medvoice_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/**
 * Core request helper. Adds the bearer token automatically, JSON-encodes bodies
 * unless they're already FormData, and throws ApiError with a readable message
 * on non-2xx responses.
 */
async function request(path, { method = "GET", body, headers = {}, isForm = false } = {}) {
  const token = getToken();
  const finalHeaders = { ...headers };
  if (token) finalHeaders["Authorization"] = `Bearer ${token}`;
  if (!isForm && body !== undefined) finalHeaders["Content-Type"] = "application/json";

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: finalHeaders,
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });

  if (res.status === 204) return null;

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json() : await res.text();

  if (!res.ok) {
    const message = (isJson && (data.detail || data.message)) || res.statusText;
    if (res.status === 401) setToken(null);
    throw new ApiError(typeof message === "string" ? message : JSON.stringify(message), res.status, data);
  }

  return data;
}

const get = (path) => request(path);
const post = (path, body) => request(path, { method: "POST", body });
const patch = (path, body) => request(path, { method: "PATCH", body });
const put = (path, body) => request(path, { method: "PUT", body });
const del = (path) => request(path, { method: "DELETE" });

// ---------- auth ----------
export async function login(email, password) {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const data = await request("/auth/login", {
    method: "POST",
    body: form,
    isForm: true,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  setToken(data.access_token);
  return data;
}

export async function register({ orgName, fullName, email, password, timezone }) {
  const data = await post("/auth/register", {
    org_name: orgName,
    full_name: fullName,
    email,
    password,
    // Optional. Sent so a new clinic isn't silently seated on UTC —
    // every slot it offers would otherwise be wrong by its own offset.
    timezone: timezone || undefined,
  });
  setToken(data.access_token);
  return data;
}

export function logout() {
  setToken(null);
}

export const me = () => get("/auth/me");

// ---------- dashboard ----------
export const getDashboardStats = () => get("/dashboard/stats");
export const getCallVolume = () => get("/dashboard/call-volume");

// ---------- patients ----------
export const listPatients = (search, { includeArchived = false } = {}) => {
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (includeArchived) params.set("include_archived", "true");
  const qs = params.toString();
  return get(`/patients${qs ? `?${qs}` : ""}`);
};
export const getPatient = (id) => get(`/patients/${id}`);
export const createPatient = (payload) => post("/patients", payload);
export const updatePatient = (id, payload) => patch(`/patients/${id}`, payload);
/** Soft archive — the record, prescriptions and history are all retained. */
export const archivePatient = (id) => del(`/patients/${id}`);
export const restorePatient = (id) => post(`/patients/${id}/restore`);

// ---------- doctors ----------
/** Lean list of active doctors — used for pickers. */
export const listDoctors = () => get("/doctors");
/** Full records including inactive, for the management screen. */
export const listAllDoctors = () => get("/doctors/all");
export const createDoctor = (payload) => post("/doctors", payload);
export const updateDoctor = (id, payload) => patch(`/doctors/${id}`, payload);
export const deactivateDoctor = (id) => del(`/doctors/${id}`);

// ---------- calls ----------
export const listCalls = (outcome) => get(`/calls${outcome ? `?outcome=${encodeURIComponent(outcome)}` : ""}`);
export const getCall = (id) => get(`/calls/${id}`);

/**
 * Call logs + transcripts as a CSV blob.
 *
 * The CSV used to be assembled in the browser. It moved server-side so
 * the export writes a single `call_log.exported` audit entry — the
 * per-call `call.transcript_viewed` rows never said that 200 lookups
 * were one bulk pull. Same shape as downloadAuditLogCsv: fetched rather
 * than linked, because <a href> cannot carry the bearer token.
 */
export async function downloadCallLogsCsv(callIds) {
  const res = await fetch(`${BASE_URL}/calls/export`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${getToken()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ call_ids: callIds }),
  });
  if (!res.ok) {
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const data = isJson ? await res.json() : await res.text();
    throw new ApiError(
      (isJson && data.detail) || res.statusText, res.status, data,
    );
  }
  return res.blob();
}

/**
 * One invoice as a printable HTML document (GAP-B2).
 *
 * Opened in a new tab rather than saved: the browser's own Print-to-PDF
 * is what turns it into a file, and forcing a download would drop an
 * .html into Downloads that the clinic then has to find and open before
 * they can print it.
 *
 * Fetched rather than linked for the same reason as the two exports
 * above — <a href> cannot carry the bearer token — and the endpoint
 * writes an `invoice.downloaded` audit entry.
 */
export async function openInvoiceDocument(invoiceId) {
  const res = await fetch(`${BASE_URL}/billing/invoices/${invoiceId}/document`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) {
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const data = isJson ? await res.json() : await res.text();
    throw new ApiError((isJson && data.detail) || res.statusText, res.status, data);
  }
  return res.blob();
}

// ---------- appointments ----------
export const listAppointments = () => get("/appointments");
export const createAppointment = (payload) => post("/appointments", payload);
export const updateAppointment = (id, payload) => patch(`/appointments/${id}`, payload);

// Validated scheduling. These are the endpoints the voice agent uses:
// they check the doctor's working hours and reject double-bookings.
// Staff booking goes through them too, so the AI and the front desk
// can no longer disagree about what the calendar says.
export const getAvailability = (doctorId, date) =>
  get(`/appointments/availability?doctor_id=${encodeURIComponent(doctorId)}&date=${encodeURIComponent(date)}`);
export const bookAppointment = (payload) => post("/appointments/book", payload);
export const rescheduleAppointment = (id, payload) => post(`/appointments/${id}/reschedule`, payload);
export const cancelAppointment = (id) => post(`/appointments/${id}/cancel`);

/**
 * Records that the patient did not arrive.
 *
 * Distinct from cancelling: a cancellation is the patient telling the
 * clinic, a no-show is the patient not telling them. They cost different
 * amounts and only one is counted in `no_show_rate_pct`.
 */
export const markAppointmentNoShow = (id) => post(`/appointments/${id}/no-show`);
export const listPatientAppointments = (patientId, includePast = false) =>
  get(`/appointments/patient/${patientId}${includePast ? "?include_past=true" : ""}`);
// PF-13. 404 when the patient hasn't told us anything, which is the
// common case and not an error worth surfacing.
export const getPreVisit = (appointmentId) => get(`/appointments/${appointmentId}/pre-visit`);

export const listPendingBookings = () => get("/appointments/pending/bookings");
export const verifyPendingBooking = (id) => post(`/appointments/pending/bookings/${id}/verify`);
export const declinePendingBooking = (id) => post(`/appointments/pending/bookings/${id}/decline`);

// ---------- knowledge base ----------
export const listKBDocuments = () => get("/knowledge-base/documents");
export const uploadKBDocument = (file) => {
  const form = new FormData();
  form.append("file", file);
  return request("/knowledge-base/documents", { method: "POST", body: form, isForm: true });
};
export const deleteKBDocument = (id) => del(`/knowledge-base/documents/${id}`);
export const listKBSources = () => get("/knowledge-base/sources");
export const addKBSource = (url) => post("/knowledge-base/sources", { url });
export const deleteKBSource = (id) => del(`/knowledge-base/sources/${id}`);
export const listFAQs = () => get("/knowledge-base/faqs");
export const addFAQ = (payload) => post("/knowledge-base/faqs", payload);
export const deleteFAQ = (id) => del(`/knowledge-base/faqs/${id}`);

/**
 * Runs a question through the exact path the voice agent uses on a live
 * call (`clinic_action("faq")` -> GET /knowledge-base/ask), so staff can
 * see what a patient would actually be told before a patient hears it.
 *
 * Returns `{ answer, matched_question, source }`. `source` is "faq" when a
 * stored FAQ matched, "none" when nothing did and the agent falls back to
 * offering a transfer.
 */
export const askKnowledgeBase = (question) =>
  get(`/knowledge-base/ask?q=${encodeURIComponent(question)}`);

/** Filename/keyword search across documents and FAQs. */
export const searchKnowledgeBase = (query) =>
  get(`/knowledge-base/search?q=${encodeURIComponent(query)}`);

// ---------- clinic profile & services ----------
// The voice agent already reads all of this to answer callers; until now
// there was no way for a clinic to fill any of it in.
export const getClinicInfo = () => get("/knowledge-base/clinic-info");
export const updateClinicInfo = (payload) => put("/knowledge-base/clinic-info", payload);
export const listTimezones = () => get("/knowledge-base/timezones");
export const listServices = () => get("/knowledge-base/services");
export const createService = (payload) => post("/knowledge-base/services", payload);
export const updateService = (id, payload) => patch(`/knowledge-base/services/${id}`, payload);
export const deleteService = (id) => del(`/knowledge-base/services/${id}`);

// ---------- agent settings ----------
export const getAgentSettings = () => get("/agent-settings");
export const updateAgentSettings = (payload) => put("/agent-settings", payload);
export const listRoutingRules = () => get("/agent-settings/routing-rules");
// Served by the backend rather than hardcoded here: a screen offering a
// condition the server does not evaluate is exactly the defect F14 found
// in the webhook events list.
export const listRoutingRuleConditions = () =>
  get("/agent-settings/routing-rule-conditions");
export const createRoutingRule = (payload) => post("/agent-settings/routing-rules", payload);
export const updateRoutingRule = (id, payload) => patch(`/agent-settings/routing-rules/${id}`, payload);
export const deleteRoutingRule = (id) => del(`/agent-settings/routing-rules/${id}`);

// ---------- EHR ----------
export const getEHRStatus = () => get("/ehr/status");
export const listEHRIntegrations = () => get("/ehr/integrations");
export const updateEHRIntegration = (id, payload) => patch(`/ehr/integrations/${id}`, payload);
export const listAPIKeys = () => get("/ehr/api-keys");
export const createAPIKey = (payload) => post("/ehr/api-keys", payload);
export const revokeAPIKey = (id) => del(`/ehr/api-keys/${id}`);
export const getWebhook = () => get("/ehr/webhook");
export const updateWebhook = (payload) => put("/ehr/webhook", payload);

// ---------- security ----------
export const getCompliance = () => get("/security/compliance");
export const getRoles = () => get("/security/roles");
// ---------- audit trail (HV-1) ----------
// `audit_log` is now written on every request that touches PHI, so this
// endpoint returns a *page* rather than a flat list capped at 50 — a
// clinic with real activity could previously never reach anything older
// than its most recent fifty events.

function auditParams({ q, action, status, startDate, endDate, page, pageSize } = {}) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (action) params.set("action", action);
  if (status) params.set("status", status);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  return params;
}

export const getAuditLog = (opts) => {
  const qs = auditParams(opts).toString();
  return get(`/security/audit-log${qs ? `?${qs}` : ""}`);
};

export const getAuditLogActions = () => get("/security/audit-log/actions");

/**
 * The export URL, not the file. The browser has to navigate to it so the
 * Content-Disposition header does its job — and the endpoint needs the
 * bearer token, which a plain <a href> cannot carry, so the caller
 * fetches it and saves the blob. Admin-only, and the export itself is
 * audited.
 */
export async function downloadAuditLogCsv(opts) {
  const qs = auditParams(opts).toString();
  const res = await fetch(`${BASE_URL}/security/audit-log/export${qs ? `?${qs}` : ""}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) {
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const data = isJson ? await res.json() : await res.text();
    throw new ApiError(
      (isJson && data.detail) || res.statusText, res.status, data,
    );
  }
  return res.blob();
}

// ---------- billing ----------
export const getPlan = () => get("/billing/plan");
export const listInvoices = () => get("/billing/invoices");
export const upgradePlan = () => post("/billing/upgrade");

// HV-5. The amount is NOT sent -- createCheckout takes a plan name and
// the server reads the price from the catalogue. Anything here that
// looked like a price would be a client naming its own charge.
export const createCheckout = (planName) => post("/billing/checkout", { plan_name: planName });
export const verifyCheckout = (payload) => post("/billing/checkout/verify", payload);
export const listPayments = () => get("/billing/payments");

// ---------- public catalogue ----------
// Unauthenticated: the landing page reads the real plan catalogue rather
// than asserting prices of its own. The bearer token is attached if the
// visitor happens to have one; the endpoint ignores it.
export const getPublicPlans = () => get("/public/plans");
// The phone number and address, if this deployment has been given any.
// Returns nulls rather than placeholders when it has not.
export const getContactInfo = () => get("/public/contact");
export const submitContactEnquiry = (body) => post("/public/contact", body);

// ---------- support ----------
export const getSupportDocs = () => get("/support/docs");
export const createSupportTicket = (payload) => post("/support/tickets", payload);
/**
 * Tickets raised by this clinic. Admin-only server-side (`require_admin`),
 * so callers must handle a 403 for medical_staff users rather than assume
 * the request will succeed.
 */
export const listSupportTickets = () => get("/support/tickets");

// ---------- team / staff management (HV-2) ----------
// All admin-only server-side. Callers should expect a 403 for
// medical_staff users rather than assume success.

/** Everyone in the clinic, including deactivated and pending-invite users. */
export const listTeam = () => get("/users");

/**
 * Invites a colleague. Returns `{ user, invite_token, invite_url, email_sent }`
 * — the token is shown ONCE and is not recoverable, so surface `invite_url`
 * to the admin immediately, especially when `email_sent` is false.
 */
export const inviteTeamMember = (payload) => post("/users/invite", payload);

/** Issues a fresh token, invalidating the previous invite link. */
export const resendInvite = (id) => post(`/users/${id}/resend-invite`);

export const updateTeamMember = (id, payload) => patch(`/users/${id}`, payload);

/** Soft-deactivate — the user row and their history are retained. */
export const deactivateTeamMember = (id) => del(`/users/${id}`);

/** Unauthenticated: completes an invite with a chosen password. */
// GAP-U1. Both are public and unauthenticated; the request one
// answers identically whether or not the address is registered.
export const requestPasswordReset = (email) =>
  post("/auth/request-password-reset", { email });

export const resetPassword = (token, password) =>
  post("/auth/reset-password", { token, password });

export const acceptInvite = async (token, password) => {
  const data = await post("/auth/accept-invite", { token, password });
  setToken(data.access_token);
  return data;
};

export { ApiError };
// ---------- onboarding ----------
// `OnboardingChecklist` on the dashboard called this and it did not
// exist, so `api.getOnboardingStatus()` threw a TypeError synchronously
// inside a useEffect — past the component's own `.catch`, which only
// handles a rejected promise. That took the whole dashboard to the
// error boundary.

export const getOnboardingStatus = () => get("/onboarding/status");

// ---------- notifications (HV-6) ----------
// Persisted staff notifications. Before HV-6 these lived only in React
// state, so a refresh wiped them and anything raised overnight was lost.

/** Newest-first page. Returns `{ items, unread_count }` — the count covers the whole org, not just this page. */
export const listNotifications = ({ limit = 30, offset = 0, unreadOnly = false } = {}) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (unreadOnly) params.set("unread_only", "true");
  return get(`/notifications?${params.toString()}`);
};

export const markNotificationRead = (id) => patch(`/notifications/${id}/read`);
export const markAllNotificationsRead = () => post("/notifications/read-all");

// ---------- missed-call callback queue (HV-7) ----------
// A read/annotate layer over calls the agent already logged as
// failed/no_answer. Staff-only server-side.

/** Outstanding callbacks (pending + contacted), oldest-missed first. Pass a status for one bucket. */
export const listCallbackQueue = (status) =>
  get(`/calls/callbacks/queue${status ? `?status=${encodeURIComponent(status)}` : ""}`);

export const updateCallback = (callId, payload) => patch(`/calls/${callId}/callback`, payload);

// ---------- refill requests (F9) ----------
// Repeat prescriptions asked for by phone. The voice agent can create a
// request; only staff can approve or decline one — the backend enforces
// that with the route's dependency, not with a flag sent from here.

/** Outstanding refill requests, oldest first. Pass a status for one bucket. */
export const listRefillRequests = (status) =>
  get(`/refills${status ? `?status=${encodeURIComponent(status)}` : ""}`);

/** Approve or decline. `status` is "approved" | "declined". */
export const updateRefillRequest = (refillId, payload) =>
  patch(`/refills/${refillId}`, payload);

// ---------- analytics (HV-9) ----------
// Real aggregation over calls and appointments. `days` is capped
// server-side; every bucket is in the clinic's own timezone.
//
// Note `resolution_rate_pct` may be null — that means no call connected
// in the range, which is different from "0% resolved". Render it as "—",
// not as a zero.

export const getAnalyticsOverview = (days = 30) => get(`/analytics/overview?days=${days}`);
export const getAnalyticsDaily = (days = 30) => get(`/analytics/daily?days=${days}`);
export const getAnalyticsHourly = (days = 30) => get(`/analytics/hourly?days=${days}`);
export const getAnalyticsWeekday = (days = 30) => get(`/analytics/weekday?days=${days}`);
export const getAnalyticsBreakdowns = (days = 30) => get(`/analytics/breakdowns?days=${days}`);
export const getAnalyticsDoctors = (days = 30) => get(`/analytics/doctors?days=${days}`);

// ---------- call quality (AI-7) ----------
// LLM-as-judge scores over finished calls. Staff-only endpoints — the
// voice agent has no reason to read or run these.
//
// Two things to keep in mind when rendering these:
//   * `needs_review` is true for a hallucination or a policy violation
//     regardless of score. A call where the AI gave medical advice
//     measured 70 with goal_achieved=true, so a "flag below 60" rule
//     would have passed it. Don't reintroduce a score threshold in the UI.
//   * `run` is admin-only and costs a model call per judged call, so it
//     is a deliberate button, never something a page fires on mount.

export const getQualitySummary = () => get("/quality/summary");
export const listEvaluations = ({ needsReview = false, limit = 50, offset = 0 } = {}) =>
  get(`/quality/evaluations?needs_review=${needsReview}&limit=${limit}&offset=${offset}`);
export const getCallEvaluation = (callId) => get(`/quality/calls/${callId}`);
export const markEvaluationReviewed = (evaluationId) =>
  post(`/quality/evaluations/${evaluationId}/reviewed`);
export const runQualityEvaluation = (limit = 20) => post(`/quality/run?limit=${limit}`);
