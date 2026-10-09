import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, PhoneOff, PhoneIncoming, PhoneOutgoing, CalendarDays, Bot } from "lucide-react";
import { useNavigate } from "react-router-dom";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, StatCell, SkeletonList, EmptyState, ErrorState,
  BarChart, ChartEmpty, ChartSkeleton,
} from "../../components/ui";
import OnboardingChecklist from "../../components/OnboardingChecklist";
import LiveCallBanner from "../../components/LiveCallBanner";
import * as api from "../../lib/api";
import { useRealtime } from "../../lib/realtime";
import { formatCallTimeLabel, dayKey } from "../../lib/date";
import {
  initialsOf, outcomeTone, callStatusTone, callStatusLabel, appointmentStatusTone, appointmentStatusLabel,
} from "../../lib/format";

// Every figure here comes from GET /dashboard/stats. No trend or
// comparison is shown, because that endpoint returns today only — a
// "vs last week" arrow would have to be invented. Analytics has a real
// range and is where a trend belongs.
const STAT_CARDS = [
  {
    key: "calls_handled_today",
    label: "Calls handled",
    hint: "today",
    format: (v) => `${v}`,
  },
  {
    key: "appointments_booked_today",
    // Short label so all four fit on one line at laptop widths; the hint
    // carries the rest.
    label: "Appointments",
    hint: "booked today",
    format: (v) => `${v}`,
  },
  {
    key: "resolution_rate_pct",
    label: "Resolved by AI",
    hint: "without a transfer",
    format: (v) => `${v}%`,
    // The backend coerces this to 0.0 when nothing connected
    // (`resolution_rate(...) or 0.0`), and "0%" reads as "the AI failed
    // every call" rather than "there was nothing to measure". With no
    // calls today there is no rate, so show an em dash instead.
    blankWhen: (stats) => !stats.calls_handled_today,
  },
  {
    key: "staff_time_saved_hrs",
    label: "Staff time saved",
    hint: "AI-handled call time",
    format: (v) => `${v} hrs`,
  },
];

// Fill of the duration trace. Teal is the AI at work; a failed call is an
// error; anything else (no answer) stays neutral.
const TRACE_FILL = {
  completed: "bg-accent",
  failed: "bg-error",
};

/**
 * Label the 24 call-volume buckets with the clock hour each one covers.
 *
 * `/dashboard/call-volume` returns buckets indexed by *hours ago*, not by
 * clock hour — `hours[23]` is the hour happening now and `hours[0]` is 23
 * hours back (see routers/dashboard.py, `buckets[23 - hours_ago] += 1`).
 *
 * This page used to draw a fixed "12AM · 6AM · 12PM · 6PM" axis under
 * them, which is simply not what the bars mean: at 3 PM the leftmost bar
 * is 4 PM *yesterday*, not midnight. The endpoint already returns
 * `current_hour` in the clinic's own timezone to make this resolvable,
 * and the page was fetching it and throwing it away.
 */
function hourLabels(currentHour) {
  const base = Number.isInteger(currentHour) ? currentHour : new Date().getHours();
  return Array.from({ length: 24 }, (_, i) => {
    const hour = (((base - (23 - i)) % 24) + 24) % 24;
    const suffix = hour < 12 ? "AM" : "PM";
    const twelve = hour % 12 === 0 ? 12 : hour % 12;
    return `${twelve} ${suffix}`;
  });
}

// Dashboard's actual content lives in DashboardBody, which is rendered as a
// *child* of AppShell (see the default export below). AppShell is what
// mounts RealtimeProvider, so useRealtime() must be called from a component
// that renders underneath it — not from the component that renders AppShell
// itself, since at that point RealtimeProvider isn't in the tree yet.
function DashboardBody() {
  const navigate = useNavigate();
  const { liveCalls, seedLiveCalls, subscribe } = useRealtime();

  const [stats, setStats] = useState(null);
  const [volume, setVolume] = useState(null);
  const [recentCalls, setRecentCalls] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Initial load: dashboard stats, hourly call volume, and any calls already
  // in progress (so the live panel isn't empty until the next WS event).
  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    // `listAppointments` is the one request this page did not already make.
    // It is an existing endpoint the Appointments page also calls — no new
    // contract — and it is what lets the schedule panel below show the day
    // itself rather than only the count `/dashboard/stats` returns.
    Promise.all([
      api.getDashboardStats(),
      api.getCallVolume(),
      api.listCalls(),
      api.listAppointments(),
    ])
      .then(([statsRes, volumeRes, callsRes, apptsRes]) => {
        if (cancelled) return;
        setStats(statsRes);
        setVolume(volumeRes);
        setAppointments(apptsRes);
        // Already fetched for the live-call seed below; keeping it costs
        // nothing and gives the recent-activity panel real rows instead
        // of a second request.
        setRecentCalls(callsRes);
        seedLiveCalls(callsRes.filter((c) => c.status === "in_progress" && !c.ended_at));
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [seedLiveCalls]);

  useEffect(() => {
    return load();
  }, [load]);

  // Keep stats/volume fresh as calls finish and appointments change, without
  // requiring a page refresh — the live-call panel itself is driven directly
  // by RealtimeProvider's liveCalls state.
  useEffect(() => {
    const refresh = () => {
      api.getDashboardStats().then(setStats);
      api.getCallVolume().then(setVolume);
      api.listCalls().then(setRecentCalls).catch(() => {});
      // Added alongside the existing three so the schedule panel reacts to
      // the same events the counters already do — a booking that changes
      // "appointments booked today" must also appear in today's list.
      api.listAppointments().then(setAppointments).catch(() => {});
    };
    const unsubs = [
      subscribe("call.ended", refresh),
      subscribe("appointment.booked", refresh),
      subscribe("appointment.cancelled", refresh),
    ];
    return () => unsubs.forEach((fn) => fn());
  }, [subscribe]);

  // Safety-net reconciliation for "Live Active Calls": call.ended is pushed
  // over the dashboard WebSocket, but if that single message is missed
  // (a reconnect gap, a dropped frame, the tab regaining focus after
  // sleep, etc.) there's no replay — the panel would otherwise show a
  // call as "In Progress" forever after it actually ended. Periodically
  // re-syncing from the same REST source used on initial load (listCalls)
  // via seedLiveCalls — which already drops any call in a terminal status
  // (completed | failed | no_answer) — self-heals that drift without
  // touching the WS-driven fast path above.
  useEffect(() => {
    const reconcile = () => {
      api
        .listCalls()
        .then((rows) => {
          setRecentCalls(rows);
          seedLiveCalls(rows);
        })
        .catch(() => {});
    };
    const interval = setInterval(reconcile, 10000);
    return () => clearInterval(interval);
  }, [seedLiveCalls]);

  // Calls that have finished. The live ones have their own band above, so
  // repeating them here would show the same call twice on one screen.
  const finishedCalls = recentCalls.filter((c) => c.status !== "in_progress").slice(0, 5);
  // The trace is relative to the longest of the calls on screen, so the
  // bars compare these calls with each other rather than with a made-up
  // "typical" length.
  const longestCall = Math.max(0, ...finishedCalls.map((c) => c.duration_seconds || 0));

  const volumeRows = useMemo(() => {
    if (!volume?.hours) return [];
    const labels = hourLabels(volume.current_hour);
    return volume.hours.map((value, i) => ({ label: labels[i], value }));
  }, [volume]);

  // Nothing at all in 24 hours is a real, meaningful state — and a row of
  // zero-height bars under an axis looks like a rendering failure rather
  // than a quiet day.
  const volumeIsEmpty = volumeRows.length > 0 && volumeRows.every((r) => !r.value);

  /**
   * Today's schedule.
   *
   * `start_at` is clinic wall-clock naive (see app/timezones.py), which is
   * why it is compared with `dayKey` against a plain `new Date()` rather
   * than converted — exactly what Appointments.jsx does with the same
   * column. Cancelled ones are dropped: the panel answers "who is coming
   * in today", and a cancellation is the answer "nobody".
   */
  const todaysAppointments = useMemo(() => {
    const today = dayKey(new Date());
    return appointments
      .filter((a) => a.start_at && a.status !== "cancelled" && dayKey(new Date(a.start_at)) === today)
      .sort((a, b) => new Date(a.start_at) - new Date(b.start_at));
  }, [appointments]);

  if (error) {
    return <ErrorState detail="We couldn't load your dashboard." onRetry={load} className="py-24" />;
  }

  return (
    <>
      {/* Hides itself once setup is complete, so established clinics
          never see it. */}
      <OnboardingChecklist />

      {/* Above the KPIs on purpose. "A call is happening right now" is the
          single most time-sensitive thing this page can say, and it used
          to sit below the fold-line of four static tiles. */}
      <LiveCallBanner calls={liveCalls} />

      {/* One instrument strip rather than four floating cards: the four
          figures are one reading of today, so they share one surface. */}
      {/* The 1px gap over a divider-coloured ground draws the rules
          between cells, in the 2x2 phone layout and the single row alike. */}
      <Card as="section" aria-label="Today at a glance" className="overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-outline-variant lg:grid-cols-4">
          {/* The hint says what the number counts. Four bare integers with
              no period attached is the shape of a demo, not a dashboard. */}
          {STAT_CARDS.map((s) => (
            <StatCell
              key={s.key}
              label={s.label}
              hint={s.hint}
              loading={loading || !stats}
              value={stats && (s.blankWhen?.(stats) ? "—" : s.format(stats[s.key]))}
            />
          ))}
        </div>
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="min-w-0 lg:col-span-2">
          <div className="flex items-center justify-between gap-3 border-b border-outline-variant px-6 py-4">
            <div className="min-w-0">
              <h2 className="font-display text-lg font-bold text-on-surface">Recent calls</h2>
              <p className="text-xs text-on-surface-variant">
                The last few calls your AI receptionist handled
              </p>
            </div>
            <button
              onClick={() => navigate("/app/calls")}
              className="focus-ring inline-flex min-h-10 shrink-0 items-center rounded px-1 text-sm font-semibold text-accent hover:underline"
            >
              View all
            </button>
          </div>
          {loading ? (
            <SkeletonList rows={4} label="Loading recent calls…" />
          ) : finishedCalls.length === 0 ? (
            <EmptyState
              icon={PhoneOff}
              title="No calls yet"
              detail="Your AI receptionist is ready. Once it takes a call, it will appear here with its outcome."
            />
          ) : (
            <ul className="divide-y divide-outline-variant">
              {finishedCalls.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => navigate(`/app/calls?call=${encodeURIComponent(c.id)}`)}
                    className="focus-ring flex w-full items-center gap-4 px-6 py-3.5 text-left transition-colors hover:bg-surface-low"
                  >
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-container text-sm font-semibold text-on-surface-variant">
                      {initialsOf(c.patient_name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 truncate text-sm font-semibold text-on-surface">
                        {c.direction === "outbound" ? (
                          <PhoneOutgoing size={12} className="text-on-surface-variant" aria-hidden="true" />
                        ) : (
                          <PhoneIncoming size={12} className="text-on-surface-variant" aria-hidden="true" />
                        )}
                        {c.patient_name || "Unknown caller"}
                      </span>
                      <span className="block truncate text-xs text-on-surface-variant">
                        {c.reason || (c.direction === "outbound" ? "Outbound call" : "Inbound call")}
                        {formatCallTimeLabel(c.started_at) && ` · ${formatCallTimeLabel(c.started_at)}`}
                        {c.duration && ` · ${c.duration}`}
                      </span>
                    </span>
                    {/* The call trace: how long this call ran, against the
                        longest one listed. Decorative -- the duration is
                        already in the text line above -- so hidden from
                        screen readers, and shown only from `xl` up, where
                        the call's own details are not squeezed by it. */}
                    <span className="hidden w-24 shrink-0 xl:block" aria-hidden="true">
                      <span className="block h-1.5 overflow-hidden rounded-full bg-surface-container">
                        {c.duration_seconds > 0 && (
                          <span
                            className={`block h-full rounded-full ${TRACE_FILL[c.status] || "bg-outline"}`}
                            style={{ width: `${Math.max(6, (c.duration_seconds / longestCall) * 100)}%` }}
                          />
                        )}
                      </span>
                    </span>
                    {/* Outcome where there is one, status otherwise: a
                        failed call has no outcome, and a blank cell there
                        reads as missing data rather than as a failure. */}
                    {/* Fixed-width slot so every trace starts at the same
                        x, whatever the chip's word length. */}
                    <span className="flex shrink-0 justify-end xl:w-28">
                      <Chip tone={c.outcome ? outcomeTone(c.outcome) : callStatusTone(c.status)}>
                        {c.outcome || callStatusLabel(c.status)}
                      </Chip>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Today's schedule. The KPI above says how many were booked; this
            says who is actually coming in, which is the question a manager
            opens the dashboard to answer. */}
        <Card className="min-w-0 overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-outline-variant px-6 py-4">
            <div className="min-w-0">
              <h2 className="font-display text-lg font-bold text-on-surface">Today's schedule</h2>
              <p className="text-xs text-on-surface-variant">
                {loading
                  ? " "
                  : `${todaysAppointments.length} appointment${todaysAppointments.length === 1 ? "" : "s"}`}
              </p>
            </div>
            <button
              onClick={() => navigate("/app/appointments")}
              className="focus-ring inline-flex min-h-10 shrink-0 items-center rounded px-1 text-sm font-semibold text-accent hover:underline"
            >
              View all
            </button>
          </div>
          {loading ? (
            <SkeletonList rows={3} avatar={false} label="Loading today's schedule…" />
          ) : todaysAppointments.length === 0 ? (
            <EmptyState
              icon={CalendarDays}
              title="Nothing booked today"
              detail="Appointments your team or the AI receptionist books will appear here."
            />
          ) : (
            <ul className="max-h-80 divide-y divide-outline-variant overflow-y-auto">
              {todaysAppointments.map((a) => (
                <li key={a.id} className="flex items-start gap-3 px-6 py-3">
                  <span className="w-16 shrink-0 pt-0.5 text-xs font-semibold tabular-nums text-on-surface-variant">
                    {a.time_label || "—"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      {a.confirmation_status === "confirmed" && (
                        <CheckCircle2 size={12} className="shrink-0 text-success" aria-label="Patient confirmed" />
                      )}
                      {a.ai_generated && (
                        <Bot size={12} className="shrink-0 text-accent" aria-label="Booked by the AI receptionist" />
                      )}
                      <span className="truncate text-sm font-semibold text-on-surface">
                        {a.patient_name || a.title}
                      </span>
                    </span>
                    <span className="block truncate text-xs text-on-surface-variant">{a.title}</span>
                  </span>
                  {a.status !== "upcoming" && (
                    <Chip tone={appointmentStatusTone(a.status)}>
                      {appointmentStatusLabel(a.status)}
                    </Chip>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Full width, where it was previously squeezed into a third of the
          row. Twenty-four bars need the room to be readable at all. */}
      <Card className="mt-6 p-6">
        <h2 className="font-display text-lg font-bold text-on-surface">Call volume</h2>
        <p className="mb-5 text-xs text-on-surface-variant">
          Calls per hour over the last 24 hours, in your clinic's timezone
        </p>
        {loading || !volume ? (
          <ChartSkeleton bars={24} />
        ) : volumeIsEmpty ? (
          <ChartEmpty message="No calls in the last 24 hours" />
        ) : (
          <BarChart
            rows={volumeRows}
            unit="calls"
            caption="Calls per hour over the last 24 hours"
            format={(v) => `${v} call${v === 1 ? "" : "s"}`}
            // Every 4th of 24 bars — six labels, which fits without
            // colliding even on a narrow viewport.
            xEvery={4}
          />
        )}
      </Card>
    </>
  );
}

export default function Dashboard() {
  return (
    <AppShell title="Today" subtitle="Calls, bookings and your schedule, updated as they happen.">
      <DashboardBody />
    </AppShell>
  );
}