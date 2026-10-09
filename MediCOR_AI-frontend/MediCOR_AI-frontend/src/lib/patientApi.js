/**
 * API calls for the patient self-service page (PF-06).
 *
 * Its own module, deliberately, for the same reason `adminApi.js` is
 * separate from `api.js`: these are different callers with different
 * credentials, and sharing a request helper is how one ends up leaking
 * into the other.
 *
 * The important difference here is what this one does NOT do — it never
 * attaches an Authorization header. A clinic admin may well open a
 * patient link in the same browser where they are signed in, and sending
 * their staff token to a public endpoint would be sloppy at best and, the
 * day someone adds a "use the session if present" convenience, a genuine
 * privilege confusion. The token in the URL is the only credential this
 * page has, and it is scoped to one appointment.
 */

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export class PatientApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", body } = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }

  if (!res.ok) {
    // The backend returns one identical message for every failure mode so
    // this endpoint cannot be used to tell a real token from a guess.
    // Show it as-is rather than inventing a more specific reason.
    throw new PatientApiError(
      payload?.detail || "This link is no longer valid. Please call the clinic.",
      res.status
    );
  }
  return payload;
}

const q = (token) => `?token=${encodeURIComponent(token)}`;

export const getAppointment = (token) => request(`/appointment-link${q(token)}`);

export const confirmAppointment = (token) =>
  request("/appointment-link/confirm", { method: "POST", body: { token } });

export const cancelAppointment = (token) =>
  request("/appointment-link/cancel", { method: "POST", body: { token } });

export const getAvailability = (token, date) =>
  request(`/appointment-link/availability${q(token)}&date=${encodeURIComponent(date)}`);

export const rescheduleAppointment = (token, startAt) =>
  request("/appointment-link/reschedule", {
    method: "POST",
    body: { token, start_at: startAt },
  });

export const getPreVisit = (token) => request(`/appointment-link/pre-visit${q(token)}`);

export const submitPreVisit = (token, fields) =>
  request(`/appointment-link/pre-visit${q(token)}`, { method: "POST", body: fields });
