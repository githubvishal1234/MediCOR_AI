// Lightweight date helpers for the Appointment Manager calendar.
//
// This project has no date library installed (see package.json), so these
// helpers wrap the native `Date` API. All calculations use the local Date
// getters/setters (getDate, getDay, getMonth, setDate, ...), which operate
// in the browser's local timezone by default — there is no UTC conversion
// anywhere in this file, so the calendar always reflects the viewer's own
// clock.

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const MONTH_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export function startOfDay(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

export function addDays(date, amount) {
  const d = new Date(date);
  d.setDate(d.getDate() + amount);
  return d;
}

export function addMonths(date, amount) {
  const d = new Date(date);
  d.setMonth(d.getMonth() + amount);
  return d;
}

export function isSameDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

// Monday-based start of week (matches the clinic's Mon–Fri schedule view).
export function startOfWeek(date) {
  const d = startOfDay(date);
  const day = d.getDay(); // 0 = Sun ... 6 = Sat
  const diffToMonday = day === 0 ? -6 : 1 - day;
  return addDays(d, diffToMonday);
}

// Mon–Fri workdays for the week containing `date`.
export function getWeekdays(date) {
  const monday = startOfWeek(date);
  return [0, 1, 2, 3, 4].map((i) => addDays(monday, i));
}

// Full calendar-month grid (array of 7-day weeks, Mon–Sun), including the
// leading/trailing days needed to fill out the first and last week.
export function getMonthGrid(date) {
  const firstOfMonth = new Date(date.getFullYear(), date.getMonth(), 1);
  const lastOfMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0);
  const gridStart = startOfWeek(firstOfMonth);
  const gridEnd = addDays(startOfWeek(lastOfMonth), 6);

  const weeks = [];
  let cursor = gridStart;
  while (cursor <= gridEnd) {
    const week = [];
    for (let i = 0; i < 7; i++) {
      week.push(cursor);
      cursor = addDays(cursor, 1);
    }
    weeks.push(week);
  }
  return weeks;
}

// Matching key used to line appointments up with a calendar cell, e.g. "Mon 12".
export function dayKey(date) {
  return `${WEEKDAY_SHORT[date.getDay()]} ${date.getDate()}`;
}

// Hour-row key used to line an appointment's `start_at` up with the
// calendar's HOURS row labels, e.g. "8:00 AM" / "10:00 AM" (no leading
// zero, matches the literal strings in Appointments.jsx's HOURS array).
export function hourLabel(date) {
  let hours = date.getHours();
  const minutes = date.getMinutes();
  const period = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  return `${hours}:${String(minutes).padStart(2, "0")} ${period}`;
}

// Display label: shows "Today" / "Tomorrow" when applicable, otherwise the
// weekday + day-of-month (e.g. "Mon 12"). `today` is injectable for testing.
export function relativeDayLabel(date, today = new Date()) {
  if (isSameDay(date, today)) return "Today";
  if (isSameDay(date, addDays(today, 1))) return "Tomorrow";
  return dayKey(date);
}

export function formatWeekRangeHeader(date) {
  const days = getWeekdays(date);
  const start = days[0];
  const end = days[days.length - 1];
  if (start.getMonth() === end.getMonth()) {
    return `${MONTH_LONG[start.getMonth()]} ${start.getDate()} – ${end.getDate()}, ${end.getFullYear()}`;
  }
  return `${MONTH_SHORT[start.getMonth()]} ${start.getDate()} – ${MONTH_SHORT[end.getMonth()]} ${end.getDate()}, ${end.getFullYear()}`;
}

export function formatDayHeader(date) {
  return `${MONTH_LONG[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
}

export function formatMonthHeader(date) {
  return `${MONTH_LONG[date.getMonth()]} ${date.getFullYear()}`;
}

// Human-readable call-start label for real call records, e.g.
// "Called today at 5:18 PM" / "Called yesterday at 9:02 AM" / "Called Mon 12 at 3:40 PM".
// `isoString` is whatever the backend/LiveKit call record sent for started_at
// (or occurred_at) — an ISO datetime string. `Date` parses it and renders in
// the viewer's local timezone automatically, so no manual UTC math here.
// Returns null (not a placeholder) when there's no real timestamp to show,
// so callers can fall back appropriately instead of displaying a fake time.
export function formatCallTimeLabel(isoString, now = new Date()) {
  if (!isoString) return null;
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return null;

  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (isSameDay(date, now)) return `Called today at ${time}`;
  if (isSameDay(date, addDays(now, -1))) return `Called yesterday at ${time}`;
  return `Called ${dayKey(date)} at ${time}`;
}