/**
 * Call analytics — HV-9.
 *
 * The dashboard could answer "how many calls today" and nothing else.
 * This answers the questions a clinic actually asks at review time: are
 * we busier on Mondays, what drives transfers, which doctor absorbs the
 * bookings, is the AI getting better or worse.
 *
 * The time charts use the shared `BarChart` (components/chart.jsx), which
 * this page predated: its own div-bars had no axis, only a mouse-hover
 * `title`, and nothing a screen reader could reach. `BarChart` carries a
 * y-axis, keyboard tooltips and a visually-hidden table of the numbers.
 * Still no charting library — that decision stands.
 *
 * One rendering rule worth keeping: `resolution_rate_pct` is null when no
 * call connected in the range, which is NOT the same as 0%. It renders as
 * an em dash. Showing a zero would tell a clinic that had a quiet week
 * that its AI failed every call.
 */

import { useCallback, useEffect, useState } from "react";
import {
  TrendingUp, PhoneCall, PhoneMissed, PhoneForwarded, Clock,
  CalendarCheck, Loader2, UserX, Bot, CalendarX,
} from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, SectionHeader, StatCell, Skeleton, EmptyState, ErrorState,
  Table, THead, TH, TBody, TR, TD, BarChart, ChartEmpty, ChartSkeleton,
} from "../../components/ui";
import * as api from "../../lib/api";

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

/** Null-safe percentage. Null means "nothing connected", not zero. */
function pct(value) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function duration(seconds) {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m}m ${s}s` : `${s}s`;
}

/**
 * "2026-09-25" from the API is a calendar day in the clinic's timezone.
 * Parsed from its parts: `new Date("2026-09-25")` reads it as UTC
 * midnight, which is the previous day anywhere west of Greenwich.
 */
function dayLabel(iso, withYear = false) {
  const [y, m, d] = String(iso).split("-").map(Number);
  const date = new Date(y, m - 1, d);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

const hourText = (h) => `${String(h).padStart(2, "0")}:00`;
const callsText = (v) => `${v} call${v === 1 ? "" : "s"}`;

/** The largest row, or null when every row is zero — a quiet week has no peak. */
function peakOf(rows) {
  const top = rows.reduce((best, r) => (r.value > (best?.value ?? 0) ? r : best), null);
  return top && top.value > 0 ? top : null;
}

function ChartCard({ title, detail, className = "", children }) {
  return (
    <Card className={`min-w-0 p-5 sm:p-6 ${className}`}>
      <h3 className="font-display text-base font-bold text-on-surface">{title}</h3>
      {detail && <p className="mt-0.5 text-xs text-on-surface-variant">{detail}</p>}
      <div className="mt-4">{children}</div>
    </Card>
  );
}

/** A time chart, or an honest empty state: a row of zero bars reads as broken. */
function CallsChart({ rows, caption, xEvery, empty = "No calls in this period" }) {
  if (rows.every((r) => !r.value)) return <ChartEmpty message={empty} />;
  return <BarChart rows={rows} unit="calls" caption={caption} format={callsText} xEvery={xEvery} />;
}

/**
 * Horizontal bars for a categorical breakdown. The count is printed beside
 * every bar, so the bar itself is decoration and hidden from screen
 * readers; widths are relative to the largest value, not to a total.
 */
function Breakdown({ title, rows, empty }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <ChartCard title={title}>
      {rows.length === 0 ? (
        <ChartEmpty message={empty} />
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => (
            <li key={row.label}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="truncate text-on-surface">{row.label}</span>
                <span className="shrink-0 font-semibold tabular-nums text-on-surface">{row.count}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-container" aria-hidden="true">
                <div className="h-full rounded-full bg-accent" style={{ width: `${(row.count / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </ChartCard>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // `cancelled` guards against switching range mid-request: without it a
  // slow 90-day response could land after the 7-day one and overwrite it,
  // leaving the 7-day button selected over 90 days of numbers.
  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.all([
      api.getAnalyticsOverview(days),
      api.getAnalyticsDaily(days),
      api.getAnalyticsHourly(days),
      api.getAnalyticsWeekday(days),
      api.getAnalyticsBreakdowns(days),
      api.getAnalyticsDoctors(days),
    ])
      .then(([overview, daily, hourly, weekday, breakdowns, doctors]) => {
        if (!cancelled) setData({ overview, daily, hourly, weekday, breakdowns, doctors });
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  useEffect(() => load(), [load]);

  const subtitle = "How your AI receptionist is actually performing.";

  if (error) {
    return (
      <AppShell title="Analytics" subtitle={subtitle}>
        <ErrorState detail="We couldn't load your analytics." onRetry={load} className="py-24" />
      </AppShell>
    );
  }

  const first = loading && !data;
  const o = data?.overview;

  const dailyRows = (data?.daily ?? []).map((r) => ({ label: dayLabel(r.date), value: r.total }));
  const hourlyRows = (data?.hourly ?? []).map((r) => ({ label: hourText(r.hour), value: r.calls }));
  const weekdayRows = (data?.weekday ?? []).map((r) => ({ label: r.label, value: r.calls }));
  const peakHour = peakOf(hourlyRows);
  const peakDay = peakOf(weekdayRows);

  return (
    <AppShell title="Analytics" subtitle={subtitle}>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        {/* A filter, like the Patients one. These were rounded pills with a
            navy fill -- the same colour as the dark-mode page, so in dark
            mode the chosen range lost its outline -- and no aria-pressed,
            so the choice was only ever visible. */}
        <div className="flex gap-0.5 rounded-lg border border-outline-variant bg-surface-lowest p-0.5" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r.days}
              type="button"
              onClick={() => setDays(r.days)}
              aria-pressed={days === r.days}
              className={`focus-ring min-h-9 rounded-md px-3 text-sm font-semibold transition-colors ${
                days === r.days
                  ? "bg-surface-container text-on-surface"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
        {loading && data && (
          <span role="status" className="flex items-center gap-1.5 text-xs text-on-surface-variant">
            <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Updating
          </span>
        )}
        {o && (
          <span className="ml-auto text-sm tabular-nums text-on-surface-variant">
            {dayLabel(o.start_date)} – {dayLabel(o.end_date, true)}
          </span>
        )}
      </div>

      {/* Two sections because the page answers two questions, and each
          opens with its own strip of figures rather than seven cards of
          equal weight competing across the top. */}
      <SectionHeader title="Calls" className="mb-3" />
      <Card as="section" aria-label="Calls at a glance" className="overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-outline-variant lg:grid-cols-5">
          {/* No threshold colours: the old tiles turned the resolution rate
              green at 70% and the no-show rate amber at 10%, targets nobody
              at the clinic had set. Missed calls are coloured because each
              one is a patient waiting on a callback. */}
          <StatCell icon={PhoneCall} label="Calls handled" loading={first} value={o?.total_calls}
            hint={o && `${o.connected_calls} connected`} />
          <StatCell icon={TrendingUp} label="Resolved by AI" loading={first} value={pct(o?.resolution_rate_pct)}
            hint="Of calls that connected" />
          <StatCell icon={PhoneForwarded} label="Transferred" loading={first} value={pct(o?.transfer_rate_pct)}
            hint={o && `${o.transferred_calls} reached a person`} />
          <StatCell icon={PhoneMissed} label="Missed calls" loading={first} value={o?.missed_calls}
            hint="Never connected" tone={o?.missed_calls > 0 ? "warning" : undefined} />
          {/* Spans both columns on a phone so five cells leave no hole. */}
          <StatCell icon={Clock} label="AI call time" loading={first} value={o && `${o.ai_handled_minutes} min`}
            hint={o && (o.average_handle_seconds ? `Average ${duration(o.average_handle_seconds)} per call` : "No calls in this period")} className="col-span-2 lg:col-span-1" />
        </div>
      </Card>

      <ChartCard title="Calls per day" className="mt-6">
        {first ? (
          <ChartSkeleton bars={30} />
        ) : (
          <CallsChart
            rows={dailyRows}
            caption="Calls per day"
            // About four to six labels whatever the range, so they never collide.
            xEvery={dailyRows.length > 45 ? 14 : dailyRows.length > 14 ? 7 : 1}
          />
        )}
      </ChartCard>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Busiest hours"
          detail={`Your clinic's local time, across the whole period.${peakHour ? ` Most calls at ${peakHour.label}.` : ""}`}
        >
          {first ? <ChartSkeleton bars={24} /> : <CallsChart rows={hourlyRows} caption="Calls by hour of day" xEvery={6} />}
        </ChartCard>
        <ChartCard title="Busiest days" detail={peakDay ? `Most calls on ${peakDay.label}.` : undefined}>
          {first ? <ChartSkeleton bars={7} /> : <CallsChart rows={weekdayRows} caption="Calls by day of the week" xEvery={1} />}
        </ChartCard>
      </div>

      {first ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-48 w-full rounded-xl" />)}
        </div>
      ) : (
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          <Breakdown
            title="Why people called"
            rows={data.breakdowns.by_intent.length ? data.breakdowns.by_intent : data.breakdowns.by_reason}
            empty="No reasons recorded yet"
          />
          <Breakdown title="Call outcomes" rows={data.breakdowns.by_outcome} empty="No outcomes recorded yet" />
          <Breakdown title="Caller sentiment" rows={data.breakdowns.by_sentiment} empty="No sentiment recorded yet" />
        </div>
      )}

      <SectionHeader title="Appointments" className="mb-3 mt-10" />
      <Card as="section" aria-label="Appointments at a glance" className="overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-outline-variant">
          <StatCell icon={CalendarCheck} label="Booked" loading={first} value={o?.appointments_booked}
            hint="In this period" />
          {/* Same null rule as `resolution_rate_pct`: an em dash until
              something has concluded. A zero here would read as "nobody
              missed an appointment", which is a claim, not missing data.
              Denominator is completed + no-show — cancellations are
              excluded, because the patient told the clinic. */}
          <StatCell icon={UserX} label="No-show rate" loading={first} value={pct(o?.no_show_rate_pct)}
            hint={
              !o || o.no_show_rate_pct === null || o.no_show_rate_pct === undefined
                ? "No appointments concluded yet"
                : `${o.appointments_no_show} of ${o.appointments_completed + o.appointments_no_show} concluded`
            } />
        </div>
      </Card>

      <Card className="mt-6 overflow-hidden">
        <div className="border-b border-outline-variant px-5 py-4 sm:px-6">
          <h3 className="font-display text-base font-bold text-on-surface">Bookings by doctor</h3>
        </div>
        {first ? (
          <div className="space-y-2 p-5">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
          </div>
        ) : data.doctors.length === 0 ? (
          <EmptyState icon={CalendarX} title="No appointments booked in this period" className="py-10" />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Doctor</TH>
                <TH className="text-right">Booked</TH>
                <TH className="text-right">By AI</TH>
                <TH className="text-right">Cancelled</TH>
              </tr>
            </THead>
            <TBody>
              {data.doctors.map((row) => (
                <TR key={row.doctor}>
                  <TD className="text-on-surface">{row.doctor}</TD>
                  {/* tabular-nums so the columns line up as the figures
                      change width. */}
                  <TD className="text-right font-semibold tabular-nums text-on-surface">{row.booked}</TD>
                  <TD className="text-right tabular-nums text-on-surface-variant">
                    {/* Teal and the Bot icon mean "the AI did this" across
                        the app. This was a green success chip, which said
                        "good" about a number that is only a count. */}
                    {row.ai_booked > 0 ? (
                      <span className="inline-flex items-center gap-1 font-semibold text-accent">
                        <Bot size={13} aria-hidden="true" />
                        {row.ai_booked}
                      </span>
                    ) : (
                      row.ai_booked
                    )}
                  </TD>
                  <TD className="text-right tabular-nums text-on-surface-variant">{row.cancelled}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
    </AppShell>
  );
}
