// Super Admin API client — deliberately separate from lib/api.js.
// Uses its own token storage key so a super admin session and a clinic
// user session can coexist in the same browser without clobbering each
// other (e.g. an admin opens the app in one tab, a clinic in another).

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const TOKEN_KEY = "medvoice_admin_token";

export function getAdminToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setAdminToken(token) {
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

async function request(path, { method = "GET", body, headers = {}, isForm = false } = {}) {
  const token = getAdminToken();
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
    if (res.status === 401) setAdminToken(null);
    throw new ApiError(typeof message === "string" ? message : JSON.stringify(message), res.status, data);
  }

  return data;
}

const get = (path) => request(path);
const post = (path, body) => request(path, { method: "POST", body });
const patch = (path, body) => request(path, { method: "PATCH", body });
const del = (path) => request(path, { method: "DELETE" });

// ---------- auth ----------
export async function adminLogin(email, password) {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const data = await request("/admin/auth/login", {
    method: "POST",
    body: form,
    isForm: true,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  setAdminToken(data.access_token);
  return data;
}

export function adminLogout() {
  setAdminToken(null);
}

export const adminMe = () => get("/admin/me");

// ---------- platform ----------
export const getPlatformStats = () => get("/admin/stats");
export const getRecentActivity = (limit = 15) => get(`/admin/recent-activity?limit=${limit}`);

// ---------- organizations ----------
export const listOrganizations = () => get("/admin/organizations");
export const getOrganization = (id) => get(`/admin/organizations/${id}`);
export const updateOrganization = (id, payload) => patch(`/admin/organizations/${id}`, payload);
export const updateOrgUser = (orgId, userId, payload) =>
  patch(`/admin/organizations/${orgId}/users/${userId}`, payload);
export const impersonateOrganization = (id) => post(`/admin/organizations/${id}/impersonate`);

// ---------- users (cross-org) ----------
export const listAllUsers = () => get("/admin/users");

// ---------- audit (Phase 7) ----------
export function getAdminAuditLog({
  q,
  action,
  orgId,
  status,
  startDate,
  endDate,
  page = 1,
  pageSize = 25,
} = {}) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (action) params.set("action", action);
  if (orgId) params.set("org_id", orgId);
  if (status) params.set("status", status);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  params.set("page", page);
  params.set("page_size", pageSize);
  return get(`/admin/audit-log?${params.toString()}`);
}
export const getAdminAuditLogActions = () => get("/admin/audit-log/actions");

// ---------- plans ----------
export const listPlans = () => get("/admin/plans");
export const getPlan = (id) => get(`/admin/plans/${id}`);
export const createPlan = (payload) => post("/admin/plans", payload);
export const updatePlan = (id, payload) => patch(`/admin/plans/${id}`, payload);
export const activatePlan = (id) => post(`/admin/plans/${id}/activate`);
export const deactivatePlan = (id) => post(`/admin/plans/${id}/deactivate`);

// ---------- subscriptions ----------
export const listSubscriptions = () => get("/admin/subscriptions");
export const getSubscription = (orgId) => get(`/admin/subscriptions/${orgId}`);
export const assignSubscription = (payload) => post("/admin/subscriptions", payload);
export const updateSubscriptionStatus = (orgId, payload) => patch(`/admin/subscriptions/${orgId}`, payload);

// ---------- usage (Phase 6) ----------
export function getUsage({ startDate, endDate, orgId } = {}) {
  const params = new URLSearchParams();
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (orgId) params.set("org_id", orgId);
  const qs = params.toString();
  return get(`/admin/usage${qs ? `?${qs}` : ""}`);
}

export { ApiError };

// ---------- phone numbers (tenant routing) ----------
// The mapping that lets one voice-agent deployment answer for many
// clinics. Platform-level on purpose: a number belongs to exactly one
// clinic, so only a super admin can allocate it.
export const listPhoneNumbers = (orgId) =>
  get(`/admin/phone-numbers${orgId ? `?org_id=${encodeURIComponent(orgId)}` : ""}`);
export const createPhoneNumber = (payload) => post("/admin/phone-numbers", payload);
export const updatePhoneNumber = (id, payload) => patch(`/admin/phone-numbers/${id}`, payload);
export const releasePhoneNumber = (id) => del(`/admin/phone-numbers/${id}`);

// ---------- support inbox (QW-8) ----------
// `GET /support/tickets` is org-scoped, so before this a submitted ticket
// could only be read with a direct database query. This is the other end:
// one inbox across every clinic, filterable down to one.
export function listSupportTickets({ orgId, status, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (orgId) params.set("org_id", orgId);
  if (status) params.set("status", status);
  if (limit) params.set("limit", String(limit));
  if (offset) params.set("offset", String(offset));
  const qs = params.toString();
  return get(`/admin/support/tickets${qs ? `?${qs}` : ""}`);
}

export const updateSupportTicket = (id, payload) =>
  patch(`/admin/support/tickets/${id}`, payload);

// Enquiries from the PUBLIC contact form. Unlike a support ticket these
// carry no org_id -- whoever filled the form is not a customer yet,
// which is the whole reason they are writing -- so there is nothing to
// filter them by an organization with.
export function listContactEnquiries({ status, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (limit) params.set("limit", String(limit));
  if (offset) params.set("offset", String(offset));
  const qs = params.toString();
  return get(`/admin/support/enquiries${qs ? `?${qs}` : ""}`);
}

export const updateContactEnquiry = (id, payload) =>
  patch(`/admin/support/enquiries/${id}`, payload);
