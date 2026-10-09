import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { ChevronLeft, ChevronRight, Bot, Check, CheckCircle2, ClipboardList, X, RefreshCw, ClipboardCheck, Plus, CalendarClock, Trash2, UserX, Link2Off, AlertTriangle } from "lucide-react";
import AppShell from "../../components/AppShell";
import { Card, EmptyState, Skeleton, SkeletonList, ErrorState, Button, Chip, Modal, ConfirmDialog, InlineAlert } from "../../components/ui";
import AppointmentFormModal from "../../components/AppointmentFormModal";
import * as api from "../../lib/api";
import * as dateUtil from "../../lib/date";
import { appointmentStatusTone, appointmentStatusLabel } from "../../lib/format";

// Covers both seeded doctors' working hours (8AM–5PM / 9AM–5PM, see seed.py)
// with an extra hour of buffer on each side so a booking right at the edge
// of a doctor's schedule still has a row to land in.
const HOURS = [
  "7:00 AM", "8:00 AM", "9:00 AM", "10:00 AM", "11:00 AM", "12:00 PM",
  "1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM", "5:00 PM", "6:00 PM",
];

// Colour says who booked it and whether it happened: teal is the AI at
// work (the same meaning as the Dashboard's call trace), a staff booking is
// plain ink, a no-show carries the warning rule. Never colour alone -- the
// Bot icon and the status chip say the same thing in words.
function blockClass(a) {
  if (a.status === "no_show") return "border-warning bg-warning-bg hover:bg-surface-container";
  if (a.ai_generated) return "border-accent bg-accent-subtle hover:bg-accent/20";
  return "border-outline bg-surface-low hover:bg-surface-container";
}
 
/**
 * The phone rendering of the calendar: the same appointments the grid
 * shows, listed in time order under a heading per day, with empty days
 * left out entirely rather than rendered as blank rows.
 */
function Agenda({ days, appointmentsFor, onSelect }) {
  const withAppointments = days
    .map((col) => ({ ...col, items: appointmentsFor(col.key) }))
    .filter((col) => col.items.length > 0);

  if (withAppointments.length === 0) {
    return (
      <EmptyState
        icon={CalendarClock}
        title="Nothing booked"
        detail="No appointments in this date range."
        className="py-12"
      />
    );
  }

  return (
    <div className="divide-y divide-outline-variant">
      {withAppointments.map((col) => (
        <section key={col.key}>
          <h3 className="sticky top-0 border-b border-outline-variant bg-surface-low px-4 py-2 text-sm font-semibold text-on-surface">
            {col.label}
          </h3>
          <ul className="divide-y divide-outline-variant">
            {col.items.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => onSelect(a)}
                  className="focus-ring flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-low"
                >
                  <span className="w-20 shrink-0 pt-0.5 text-xs font-semibold tabular-nums text-on-surface-variant">
                    {a.time_label}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      {a.confirmation_status === "confirmed" && (
                        <CheckCircle2
                          size={12}
                          className="shrink-0 text-success"
                          aria-label="Patient confirmed"
                        />
                      )}
                      {a.ai_generated && (
                        <Bot
                          size={12}
                          className="shrink-0 text-accent"
                          aria-label="Booked by the AI receptionist"
                        />
                      )}
                      <span className="truncate text-sm font-semibold text-on-surface">{a.title}</span>
                    </span>
                    <span className="block truncate text-xs text-on-surface-variant">
                      {a.patient_name}
                    </span>
                  </span>
                  {a.status !== "upcoming" && (
                    <Chip tone={appointmentStatusTone(a.status)}>{appointmentStatusLabel(a.status)}</Chip>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/**
 * What the EHR link is actually doing, from `/ehr/status`.
 *
 * Four states, because they call for different responses: connected,
 * configured-but-unreachable (someone should look at it), not connected
 * at all (bookings are local only — true, and not an error), and excluded
 * by the plan (a billing question, not a technical one).
 */
function EhrIndicator({ status }) {
  if (!status) return null;

  const { connected, provider, configured, plan_restricted: planRestricted } = status;
  const name = provider || "EHR";

  let tone = "text-on-surface-variant";
  let Icon = Link2Off;
  let label = "Not connected to an EHR";
  let title = "Appointments are stored in MediCOR AI only.";

  if (planRestricted) {
    label = "EHR not included in your plan";
    title = "Upgrade to sync appointments with your EHR.";
  } else if (connected && configured) {
    tone = "text-success";
    Icon = RefreshCw;
    label = `${name} connected`;
    title = "Appointments sync to your EHR.";
  } else if (connected) {
    // `ehr_service.get_status` reports connected=true for any integration
    // marked connected, and configured=false when it has no API address.
    // This used to read "connected" in green on `connected` alone -- a sync
    // claim for an integration that cannot sync anything.
    tone = "text-warning";
    Icon = AlertTriangle;
    label = `${name} not set up`;
    title = "Marked connected, but no API address is configured. Bookings are kept in MediCOR AI only.";
  }

  return (
    <span className={`flex items-center gap-2 text-sm font-medium ${tone}`} title={title}>
      <Icon size={14} aria-hidden="true" />
      {label}
    </span>
  );
}

export default function Appointments() {
  const [view, setView] = useState("Week");
  // Always starts on the real current date/time — never a fixed date.
  const [anchorDate, setAnchorDate] = useState(() => new Date());
  const [appointments, setAppointments] = useState([]);
  const [pendingBookings, setPendingBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [showNewAppointment, setShowNewAppointment] = useState(false);
  // Clicking an appointment opens it; reschedule and cancel both live there.
  // Until now neither action had any frontend wrapper at all, so an
  // appointment could not be moved or cancelled from the dashboard.
  const [selected, setSelected] = useState(null);
  const [rescheduling, setRescheduling] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [markingNoShow, setMarkingNoShow] = useState(false);
  // PF-13. Null until fetched; false when the patient told us nothing.
  const [preVisit, setPreVisit] = useState(null);
  // Was hardcoded as "Epic EHR Synced (1m ago)" — a specific vendor and a
  // freshness claim, both invented, on a page a clinic trusts to tell it
  // whether its calendar is actually reaching the EHR. `/ehr/status` is
  // what actually knows, and it reports no timestamp, so none is shown.
  const [ehrStatus, setEhrStatus] = useState(null);
 
  // The set of calendar days to render for the active view, always derived
  // from `anchorDate` (which starts at `new Date()` and moves via
  // Previous/Next) so the grid is never a hardcoded date range.
  const gridDays = useMemo(() => {
    if (view === "Day") return [anchorDate];
    if (view === "Month") return dateUtil.getMonthGrid(anchorDate).flat();
    return dateUtil.getWeekdays(anchorDate); // Week
  }, [view, anchorDate]);
 
  const dayColumns = useMemo(
    () => gridDays.map((d) => ({ date: d, key: dateUtil.dayKey(d), label: dateUtil.relativeDayLabel(d) })),
    [gridDays]
  );
 
  const headerLabel =
    view === "Day"
      ? dateUtil.formatDayHeader(anchorDate)
      : view === "Month"
      ? dateUtil.formatMonthHeader(anchorDate)
      : dateUtil.formatWeekRangeHeader(anchorDate);
 
  // Disables the Today button when it would do nothing.
  const isOnToday = dateUtil.dayKey(anchorDate) === dateUtil.dayKey(new Date());

  function goPrevious() {
    setAnchorDate((d) => {
      if (view === "Day") return dateUtil.addDays(d, -1);
      if (view === "Month") return dateUtil.addMonths(d, -1);
      return dateUtil.addDays(d, -7); // Week
    });
  }
 
  function goNext() {
    setAnchorDate((d) => {
      if (view === "Day") return dateUtil.addDays(d, 1);
      if (view === "Month") return dateUtil.addMonths(d, 1);
      return dateUtil.addDays(d, 7); // Week
    });
  }
 
  const loadAll = useCallback(() => {
    setLoading(true);
    setError(false);
    return Promise.all([api.listAppointments(), api.listPendingBookings()])
      .then(([appts, pending]) => {
        setAppointments(appts);
        setPendingBookings(pending);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    loadAll().catch(() => {});
  }, [loadAll]);

  useEffect(() => {
    let cancelled = false;
    // Non-fatal: the calendar is useful with or without an EHR, so a
    // failure here just leaves the indicator off rather than showing an
    // error over the schedule.
    api
      .getEHRStatus()
      .then((s) => !cancelled && setEhrStatus(s))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
 
  async function verify(id) {
    setPendingBookings((prev) => prev.filter((b) => b.id !== id));
    try {
      await api.verifyPendingBooking(id);
      api.listAppointments().then(setAppointments);
    } catch {
      // Roll back the optimistic removal so the item isn't silently lost.
      api.listPendingBookings().then(setPendingBookings);
    }
  }
 
  async function cancelAppointment(appt) {
    setActionError(null);
    setCancelling(true);
    try {
      const updated = await api.cancelAppointment(appt.id);
      setAppointments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      setSelected(null);
    } catch {
      setActionError("Could not cancel that appointment.");
    } finally {
      setCancelling(false);
    }
  }

  async function markNoShow(appt) {
    setActionError(null);
    setMarkingNoShow(true);
    try {
      const updated = await api.markAppointmentNoShow(appt.id);
      setAppointments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      setSelected(updated);
    } catch (err) {
      // The backend refuses a cancelled or already-marked appointment
      // with a specific reason — show it rather than a generic failure.
      setActionError(err?.message || "Could not mark that appointment as a no-show.");
    } finally {
      setMarkingNoShow(false);
    }
  }

  async function decline(id) {
    setPendingBookings((prev) => prev.filter((b) => b.id !== id));
    try {
      await api.declinePendingBooking(id);
    } catch {
      api.listPendingBookings().then(setPendingBookings);
    }
  }

  // One way in for the grid and the phone agenda. The agenda used to skip
  // the pre-visit fetch, so on a phone "Told us before the visit" never
  // appeared.
  //
  // `openedId` drops a late answer: open A, close, open B before A's fetch
  // returns, and A's medications and allergies would otherwise appear
  // under B's name.
  const openedId = useRef(null);
  function openAppointment(a) {
    openedId.current = a.id;
    setSelected(a);
    setActionError(null);
    setPreVisit(null);
    api
      .getPreVisit(a.id)
      .then((pv) => openedId.current === a.id && setPreVisit(pv))
      .catch(() => openedId.current === a.id && setPreVisit(false));
  }

  const todayKey = dateUtil.dayKey(new Date());

  // Match appointments to a grid cell by the actual `start_at` timestamp
  // rather than the backend's `day_label`/`time_label` strings — those are
  // formatted differently ("Wed, Aug 19" / "10:00 AM") than this calendar's
  // own day keys ("Wed 19"), so a string-equality match against them never
  // hits and every booking silently fails to render. Bucketed by hour only
  // (not exact minute) so appointments booked off the hour — e.g. a 20-minute
  // slot doctor's 9:20 booking — still land in the 9:00 AM row instead of
  // vanishing because no HOURS label matches their exact minute.
  const cell = (dayKey, hour) =>
    appointments.filter((a) => {
      if (!a.start_at) return false;
      if (a.status === "cancelled") return false;
      const d = new Date(a.start_at);
      const bucket = new Date(d);
      bucket.setMinutes(0, 0, 0);
      return dateUtil.dayKey(d) === dayKey && dateUtil.hourLabel(bucket) === hour;
    });
  // Everything on one day, in time order — what the agenda needs, where
  // the grid needs everything in one *hour*.
  const dayAppointments = (dayKey) =>
    appointments
      .filter(
        (a) => a.start_at && a.status !== "cancelled" && dateUtil.dayKey(new Date(a.start_at)) === dayKey
      )
      .sort((a, b) => new Date(a.start_at) - new Date(b.start_at));

  const dayCount = (dayKey) =>
    appointments.filter(
      (a) => a.start_at && a.status !== "cancelled" && dateUtil.dayKey(new Date(a.start_at)) === dayKey
    ).length;
 
  return (
    <AppShell
      title="Appointments"
      subtitle="Every booking your team and your AI receptionist have made."
      actions={
        <Button onClick={() => setShowNewAppointment(true)}>
          <Plus size={15} aria-hidden="true" /> New appointment
        </Button>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {/* Every control in this row is 40px tall, matching the header
            buttons; the old 28px chevrons were under the touch minimum. */}
        <div className="flex items-center rounded-lg border border-outline-variant bg-surface-lowest">
          <button
            onClick={goPrevious}
            aria-label={`Previous ${view.toLowerCase()}`}
            className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-l-lg text-on-surface-variant hover:bg-surface-container"
          >
            <ChevronLeft size={16} aria-hidden="true" />
          </button>
          <span className="px-2 text-sm font-semibold tabular-nums text-on-surface" aria-live="polite">
            {headerLabel}
          </span>
          <button
            onClick={goNext}
            aria-label={`Next ${view.toLowerCase()}`}
            className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-r-lg text-on-surface-variant hover:bg-surface-container"
          >
            <ChevronRight size={16} aria-hidden="true" />
          </button>
        </div>
        {/* Paging forward a few weeks left no way back to the current
            date short of reloading the page. */}
        <button
          onClick={() => setAnchorDate(new Date())}
          disabled={isOnToday}
          className="focus-ring min-h-10 rounded-lg border border-outline-variant bg-surface-lowest px-3 text-sm font-semibold text-on-surface-variant transition-colors hover:bg-surface-container disabled:pointer-events-none disabled:opacity-40"
        >
          Today
        </button>
        <div className="flex items-center gap-0.5 rounded-lg border border-outline-variant bg-surface-lowest p-0.5" role="group" aria-label="Calendar view">
          {["Day", "Week", "Month"].map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              aria-pressed={view === v}
              // Neutral, not accent. This is a view switch — navigation —
              // and it used to be filled with the brand colour right beside
              // "New Appointment", so the only actual action on the page
              // competed with three controls that just change what you are
              // looking at. One primary action per view.
              className={`focus-ring min-h-9 rounded-md px-3 text-sm font-semibold transition ${
                view === v
                  ? "bg-surface-container text-on-surface"
                  : "text-on-surface-variant hover:bg-surface-container"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <EhrIndicator status={ehrStatus} />
        </div>
      </div>
 
      <div className="grid gap-6 lg:grid-cols-4">
        <Card className="overflow-x-auto lg:col-span-3">
          {loading ? (
            <div className="space-y-2 p-4">
              {[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : error ? (
            <ErrorState detail="We couldn't load your appointments." onRetry={loadAll} className="py-20" />
          ) : (
            <>
              {/* The grid is 640px wide before it is legible, so on a phone
                  it can only be side-scrolled. Below `sm` the same
                  appointments are listed as an agenda instead — same data,
                  same click target, a shape that fits the screen. */}
              <div className="hidden sm:block">
                {view === "Month" ? (
              <div className="min-w-[640px] p-2">
                <div className="grid grid-cols-7 border-b border-outline-variant bg-surface-low">
                  {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((wd) => (
                    <div key={wd} className="px-2 py-3 text-center text-sm font-semibold text-on-surface">{wd}</div>
                  ))}
                </div>
                <div className="grid grid-cols-7">
                  {dayColumns.map((col) => {
                    const inMonth = col.date.getMonth() === anchorDate.getMonth();
                    const count = dayCount(col.key);
                    return (
                      <div
                        key={col.date.toISOString()}
                        className={`min-h-[80px] border-b border-l border-outline-variant p-2 ${inMonth ? "" : "opacity-40"}`}
                      >
                        <p
                          className={`text-xs font-semibold tabular-nums ${col.key === todayKey ? "text-accent" : "text-on-surface"}`}
                          aria-current={col.key === todayKey ? "date" : undefined}
                        >
                          {col.label === "Today" || col.label === "Tomorrow" ? col.label : col.date.getDate()}
                        </p>
                        {/* Ink, not teal: a count is not the AI doing anything. */}
                        {count > 0 && (
                          <p className="mt-1 text-xs tabular-nums text-on-surface-variant">
                            {count} booking{count === 1 ? "" : "s"}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className={view === "Day" ? "min-w-[280px]" : "min-w-[640px]"}>
                {/* minmax(0,1fr), not 1fr: a bare 1fr column will not shrink
                    below its content, so one long appointment title widened
                    its column and knocked every grid line in that row out of
                    line with the header. */}
                <div className={`grid border-b border-outline-variant bg-surface-low ${view === "Day" ? "grid-cols-[70px_minmax(0,1fr)]" : "grid-cols-[70px_repeat(5,minmax(0,1fr))]"}`}>
                  <div />
                  {dayColumns.map((col) => (
                    <div
                      key={col.key}
                      aria-current={col.key === todayKey ? "date" : undefined}
                      className={`px-2 py-3 text-center text-sm font-semibold ${
                        col.key === todayKey ? "border-b-2 border-accent text-accent" : "text-on-surface"
                      }`}
                    >
                      {col.label}
                    </div>
                  ))}
                </div>
                {HOURS.map((h) => (
                  <div key={h} className={`grid border-b border-outline-variant ${view === "Day" ? "grid-cols-[70px_minmax(0,1fr)]" : "grid-cols-[70px_repeat(5,minmax(0,1fr))]"}`}>
                    <div className="px-2 py-3 text-right text-xs tabular-nums text-on-surface-variant">{h}</div>
                    {dayColumns.map((col) => (
                      <div key={col.key} className="min-h-[64px] border-l border-outline-variant p-1">
                        {cell(col.key, h).map((a) => (
                          <button
                            key={a.id}
                            type="button"
                            onClick={() => openAppointment(a)}
                            className={`focus-ring mb-1 block w-full rounded-md border-l-2 p-1.5 text-left transition-colors last:mb-0 ${blockClass(a)}`}
                          >
                            <div className="flex items-center gap-1">
                              {/* Named, not only tinted: the warning colour
                                  alone was the grid's only no-show signal. */}
                              {a.status === "no_show" && (
                                <UserX size={11} className="shrink-0 text-warning" aria-label="No-show" />
                              )}
                              {/* A confirmed appointment has to read
                                  differently at a glance from one nobody has
                                  heard back about. That distinction is the
                                  whole point of collecting it. */}
                              {a.confirmation_status === "confirmed" && (
                                <CheckCircle2
                                  size={11}
                                  className="shrink-0 text-success"
                                  aria-label="Patient confirmed"
                                />
                              )}
                              {a.ai_generated && (
                                <Bot
                                  size={11}
                                  className="shrink-0 text-accent"
                                  aria-label="Booked by the AI receptionist"
                                />
                              )}
                              <p className="truncate text-xs font-semibold text-on-surface">{a.title}</p>
                            </div>
                            <p className="truncate text-xs text-on-surface-variant">{a.patient_name}</p>
                          </button>
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              )}
              </div>

              <div className="sm:hidden">
                <Agenda
                  days={dayColumns}
                  appointmentsFor={dayAppointments}
                  onSelect={openAppointment}
                />
              </div>
            </>
          )}
        </Card>
 
        {/* Rows inside one hairline card, like the Dashboard panels, rather
            than a stack of floating cards. */}
        <Card className="min-w-0 self-start overflow-hidden">
          <div className="border-b border-outline-variant px-5 py-4">
            <h2 className="flex items-center gap-2 font-display text-base font-bold text-on-surface">
              <Bot size={16} className="text-accent" aria-hidden="true" />
              AI bookings to verify
            </h2>
            <p className="text-xs text-on-surface-variant">
              {loading || pendingBookings.length === 0
                ? "Checked by staff before they sync"
                : `${pendingBookings.length} waiting on staff review`}
            </p>
          </div>
          {loading ? (
            <SkeletonList rows={2} avatar={false} label="Loading bookings to verify…" />
          ) : pendingBookings.length === 0 ? (
            <EmptyState
              icon={ClipboardCheck}
              title="All caught up"
              detail="No AI bookings are waiting on staff review."
              className="py-8"
            />
          ) : (
            <ul className="divide-y divide-outline-variant">
              {pendingBookings.map((b) => (
                <li key={b.id} className="px-5 py-4">
                  <p className="text-sm font-semibold text-on-surface">{b.patient_name}</p>
                  <p className="text-xs text-on-surface-variant">{b.type_label}</p>
                  <p className="mt-1 text-xs font-medium tabular-nums text-on-surface">{b.when_label}</p>
                  {/* Outline, not teal fill: "New appointment" is the one
                      primary action on this page. */}
                  <div className="mt-3 flex gap-2">
                    <Button size="sm" variant="secondary" className="min-h-10 flex-1" onClick={() => verify(b.id)}>
                      <Check size={14} aria-hidden="true" /> Verify
                    </Button>
                    <Button size="sm" variant="ghost" className="min-h-10 flex-1" onClick={() => decline(b.id)}>
                      <X size={14} aria-hidden="true" /> Decline
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <AppointmentFormModal
        open={showNewAppointment}
        onClose={() => setShowNewAppointment(false)}
        onSaved={(appt) => setAppointments((prev) => [...prev, appt])}
      />

      <AppointmentFormModal
        open={Boolean(rescheduling)}
        appointment={rescheduling}
        onClose={() => setRescheduling(null)}
        onSaved={(updated) => {
          setAppointments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
          setRescheduling(null);
          setSelected(null);
        }}
      />

      {/* Replaces the detail dialog while it is up (as rescheduling does),
          so only one dialog holds focus and Escape at a time. */}
      <ConfirmDialog
        open={confirmingCancel && Boolean(selected)}
        title="Cancel this appointment?"
        detail={selected ? `${selected.patient_name} · ${selected.day_label} · ${selected.time_label}. The time is freed up for other bookings.` : ""}
        confirmLabel="Cancel appointment"
        cancelLabel="Keep it"
        busy={cancelling}
        onConfirm={async () => {
          await cancelAppointment(selected);
          setConfirmingCancel(false);
        }}
        onCancel={() => setConfirmingCancel(false)}
      />

      <Modal
        open={Boolean(selected) && !rescheduling && !confirmingCancel}
        title={selected?.title || "Appointment"}
        onClose={() => setSelected(null)}
        className="max-w-sm"
      >
        {selected && (
          <>
            <p className="-mt-2 mb-4 text-sm text-on-surface-variant">{selected.patient_name}</p>

            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-on-surface-variant">When</dt>
                <dd className="text-right font-medium text-on-surface">
                  {selected.day_label} &middot; {selected.time_label}
                </dd>
              </div>
              {selected.location && (
                <div className="flex justify-between gap-3">
                  <dt className="text-on-surface-variant">Location</dt>
                  <dd className="text-right text-on-surface">{selected.location}</dd>
                </div>
              )}
              {selected.reason && (
                <div className="flex justify-between gap-3">
                  <dt className="text-on-surface-variant">Reason</dt>
                  <dd className="text-right text-on-surface">{selected.reason}</dd>
                </div>
              )}
              <div className="flex justify-between gap-3">
                <dt className="text-on-surface-variant">Patient confirmed</dt>
                <dd className="text-right">
                  {selected.confirmation_status === "confirmed" ? (
                    <span className="inline-flex items-center gap-1.5 font-medium text-success">
                      <CheckCircle2 size={14} aria-hidden="true" />
                      Yes
                    </span>
                  ) : (
                    /* Not "no" — the patient simply has not said, which is
                       a different fact and worth showing as one. */
                    <span className="text-on-surface-variant">Not yet</span>
                  )}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-on-surface-variant">Status</dt>
                <dd>
                  <Chip tone={appointmentStatusTone(selected.status)}>
                    {appointmentStatusLabel(selected.status)}
                  </Chip>
                </dd>
              </div>
            </dl>

            {/* PF-13. What the patient told us before the visit. These are
                what they SAID, not what their record holds -- nothing here
                has been applied, and applying it is a person's judgement. */}
            {preVisit && (
              <div className="mt-4 rounded-lg border border-outline-variant bg-surface-low p-3">
                <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-on-surface">
                  <ClipboardList size={13} aria-hidden="true" /> Told us before the visit
                </p>
                <dl className="space-y-1.5 text-sm">
                  {[
                    ["Coming about", preVisit.visit_reason],
                    ["Taking", preVisit.medications],
                    ["Reacts to", preVisit.allergies],
                    ["Also", preVisit.notes],
                  ]
                    .filter(([, v]) => v)
                    .map(([label, value]) => (
                      <div key={label}>
                        <dt className="text-xs text-on-surface-variant">{label}</dt>
                        <dd className="whitespace-pre-wrap text-on-surface">{value}</dd>
                      </div>
                    ))}
                </dl>
                {(preVisit.proposed_phone || preVisit.proposed_email) && (
                  <p className="mt-2.5 text-xs text-warning">
                    They also gave updated contact details. These have
                    <strong> not</strong> been applied — check them and update the record
                    yourself.
                  </p>
                )}
              </div>
            )}

            {actionError && <InlineAlert className="mt-4">{actionError}</InlineAlert>}

            {selected.status !== "cancelled" && selected.status !== "no_show" && (
              <div className="mt-5 flex flex-col gap-2">
                <div className="flex gap-2">
                  <Button type="button" className="flex-1" onClick={() => setRescheduling(selected)}>
                    <CalendarClock size={15} aria-hidden="true" /> Reschedule
                  </Button>
                  {/* "Cancel appointment", and confirmed first. It read just
                      "Cancel" -- the word people press to close a dialog --
                      and acted instantly, so dismissing this dialog could
                      cancel a real booking. */}
                  <Button
                    type="button"
                    variant="outline"
                    className="flex-1"
                    disabled={cancelling}
                    onClick={() => setConfirmingCancel(true)}
                  >
                    <Trash2 size={15} aria-hidden="true" /> Cancel appointment
                  </Button>
                </div>
                {/* Only offered once the slot has actually passed. A
                    no-show on a future appointment is a misclick, and
                    the status is awkward to walk back. */}
                {selected.start_at && new Date(selected.start_at) < new Date() && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={markingNoShow}
                    onClick={() => markNoShow(selected)}
                  >
                    <UserX size={15} aria-hidden="true" /> {markingNoShow ? "Marking…" : "Mark as no-show"}
                  </Button>
                )}
              </div>
            )}
          </>
        )}
      </Modal>

    </AppShell>
  );
}