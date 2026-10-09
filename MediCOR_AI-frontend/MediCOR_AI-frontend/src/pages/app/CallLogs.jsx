import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, Bot, CheckCircle2, PhoneOff, PhoneIncoming, PhoneOutgoing, ArrowLeft, User, Wrench } from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, Skeleton, SkeletonList, EmptyState, ErrorState, InlineAlert,
  Select, Table, THead, TH, TBody, TR, TD, Tabs, TabPanel,
} from "../../components/ui";
import CallbackQueue from "../../components/CallbackQueue";
import * as api from "../../lib/api";
import CallQuality from "./CallQuality";
import { useRealtime } from "../../lib/realtime";
import { formatCallTimeLabel, isSameDay, addDays } from "../../lib/date";
import { outcomeTone, callStatusTone, callStatusLabel } from "../../lib/format";

// Real call records carry `started_at` (from the LiveKit/SIP call record);
// older/seeded rows may only have the legacy `timestamp_label` string.
// Prefer the real timestamp whenever it's present instead of showing a
// stale or missing label.
function callTimeLabel(call) {
  return formatCallTimeLabel(call.started_at) || call.timestamp_label || "—";
}

const SENTIMENT_TONE = {
  Positive: "success",
  Neutral: "neutral",
  Concerned: "warning",
};

const OUTCOME_OPTIONS = ["Booked", "FAQ Answered", "Transferred to Nurse"];
// Display strings double as filter keys. The outcome options are the
// backend's exact vocabulary and must not be re-cased; these are ours.
const DATE_OPTIONS = ["All dates", "Today", "Yesterday", "Last 7 days"];
const ALL_OUTCOMES = "All outcomes";

// Which telephony stack carried the call. Unrecognised values fall
// through to the raw string rather than being hidden — a carrier this
// list has not been taught about is still worth showing.
const PROVIDER_LABEL = {
  twilio: "Twilio",
  livekit: "LiveKit / SIP",
};

// Client-side date filter over each call's real `started_at` timestamp.
// The backend doesn't expose a date-range query param, and there's no
// other real timestamp to filter on, so this filters the already-loaded
// list rather than fabricating a server-side filter.
function matchesDateFilter(call, filter, now) {
  if (filter === "All dates") return true;
  if (!call.started_at) return false;
  const d = new Date(call.started_at);
  if (Number.isNaN(d.getTime())) return false;
  if (filter === "Today") return isSameDay(d, now);
  if (filter === "Yesterday") return isSameDay(d, addDays(now, -1));
  if (filter === "Last 7 days") return d >= addDays(now, -7) && d <= now;
  return true;
}

function todayStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// NOTE: `csvEscape` and `transcriptText` used to live here, building the
// export in the browser. Both moved to POST /calls/export so the export
// writes one `call_log.exported` audit entry — see api.downloadCallLogsCsv.

// CallLogs' actual content lives in CallLogsBody, rendered as a *child* of
// AppShell (see default export below). AppShell mounts RealtimeProvider,
// so useRealtime() must be called from a component underneath it, not from
// the component that renders AppShell itself.
function CallLogsBody() {
  const { subscribe } = useRealtime();
  // A call is addressable by URL, the same way `?tab=quality` already is.
  // The dashboard's live-call band links straight to a call in progress,
  // and a shared link now opens the call it names rather than the list.
  const [params, setParams] = useSearchParams();
  const linkedCallId = params.get("call");
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [panelLoading, setPanelLoading] = useState(false);
  const [panelError, setPanelError] = useState(false);
  const [dateFilter, setDateFilter] = useState(DATE_OPTIONS[0]);
  const [outcomeFilter, setOutcomeFilter] = useState(ALL_OUTCOMES);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  // Below `lg` the detail panel is a full-screen sheet rather than a
  // column, and it opens only on an explicit tap. It cannot key off
  // `selectedId`, which is pre-set to the first row so the desktop
  // panel is never empty — on a phone that would slam a detail view
  // over the list the moment the page loaded.
  const [sheetOpen, setSheetOpen] = useState(false);

  // Read through a ref, not as a dependency of loadList. As a dependency,
  // every row click (which writes ?call=) rebuilt loadList and refetched
  // the whole list, flashing the skeleton -- and a missed call opened from
  // the queue but outside the current filter then fell back to row 0
  // while the URL still named the missed call.
  const linkedRef = useRef(linkedCallId);
  useEffect(() => {
    linkedRef.current = linkedCallId;
  }, [linkedCallId]);

  const loadList = useCallback(() => {
    setLoading(true);
    setError(false);
    return api
      .listCalls(outcomeFilter === ALL_OUTCOMES ? undefined : outcomeFilter)
      .then((rows) => {
        setCalls(rows);
        setLoading(false);
        setSelectedId((prev) => {
          const linked = linkedRef.current;
          // Keep what is open if it is still listed, or if the URL names
          // it (a missed call can sit outside the filter).
          if (prev && (prev === linked || rows.some((r) => r.id === prev))) return prev;
          // Only honour ?call= when the id is really in this clinic's
          // list — a stale or foreign id should fall back to the first
          // row, not leave the panel stuck on nothing.
          if (linked && rows.some((r) => r.id === linked)) return linked;
          return rows[0]?.id ?? null;
        });
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, [outcomeFilter]);

  useEffect(() => {
    loadList();
  }, [loadList]);


  // Live-updates: refresh the list on start/update/end so in-progress calls,
  // durations, and outcomes stay current without a manual reload.
  useEffect(() => {
    const unsubs = [
      subscribe("call.started", loadList),
      subscribe("call.updated", loadList),
      subscribe("call.ended", loadList),
    ];
    return () => unsubs.forEach((fn) => fn());
  }, [subscribe, loadList]);

  // Load the transcript/summary panel whenever selection changes.
  const loadPanel = useCallback((id) => {
    if (!id) {
      setSelected(null);
      return undefined;
    }
    let cancelled = false;
    setPanelLoading(true);
    setPanelError(false);
    api
      .getCall(id)
      .then((detail) => {
        if (cancelled) return;
        setSelected(detail);
        setPanelLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setPanelError(true);
        setPanelLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const cleanup = loadPanel(selectedId);
    return cleanup;
  }, [selectedId, loadPanel]);

  // While a call is in progress and selected, append live transcript turns
  // and refresh call metadata (status/duration) as they arrive.
  useEffect(() => {
    if (!selected || selected.status !== "in_progress") return undefined;
    const offMessage = subscribe("call.transcript_message", (msg) => {
      if (msg.call_id !== selected.id) return;
      setSelected((prev) =>
        prev && prev.id === msg.call_id
          ? { ...prev, transcript_messages: [...prev.transcript_messages, { id: `live-${Date.now()}`, who: msg.who, text: msg.text, time_label: msg.time_label }] }
          : prev
      );
    });
    const offUpdate = subscribe("call.updated", (call) => {
      if (call.id !== selected.id) return;
      setSelected((prev) => (prev ? { ...prev, ...call } : prev));
    });
    return () => {
      offMessage();
      offUpdate();
    };
  }, [selected, subscribe]);

  // Takes an id so the missed-calls queue can use it too. It used to be
  // handed the bare `setSelectedId`, so on a phone tapping a missed call
  // changed a panel nobody could see, and the URL never named the call.
  function selectCall(id) {
    // Always open the sheet, even when re-tapping the already-selected
    // row: on a phone that row IS how you get back into the detail.
    setSheetOpen(true);
    if (id === selectedId) return;
    setSelectedId(id);
    // `replace` so browsing the list does not fill the back button with
    // one entry per row inspected.
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("call", id);
      return next;
    }, { replace: true });
  }

  // The sheet is `fixed inset-0`, so without this the list underneath
  // still scrolls behind it under a finger. Phones only: from `lg` the
  // panel is an ordinary column, `sheetOpen` is still set by every click,
  // and the page scrolls on <body> -- so this used to freeze the whole
  // desktop page the moment a call was clicked.
  useEffect(() => {
    if (!sheetOpen || window.matchMedia("(min-width: 1024px)").matches) return undefined;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [sheetOpen]);

  const now = useMemo(() => new Date(), []);
  const filteredCalls = useMemo(
    () => calls.filter((c) => matchesDateFilter(c, dateFilter, now)),
    [calls, dateFilter, now]
  );

  // Exports exactly the currently filtered rows. The list endpoint
  // (GET /calls) intentionally omits transcript_messages for list
  // performance (see CallLogListOut in schemas.py), so the real
  // transcript for each row is pulled from the existing call-detail
  // endpoint (GET /calls/{id} — the same one the side panel already
  // uses) rather than fabricated. Any call whose detail fetch fails
  // just falls back to "No transcript available" for that row instead
  // of failing the whole export.
  async function exportCsv() {
    if (exporting || filteredCalls.length === 0) return;
    setExporting(true);
    setExportError("");
    try {
      const blob = await api.downloadCallLogsCsv(filteredCalls.map((c) => c.id));
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `call-logs-${todayStamp()}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      // A `try/finally` with no `catch` used to sit here, so a rejected
      // export cleared the spinner and showed nothing at all.
      setExportError(err?.message || "Could not export call logs. Please try again.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      {/* HV-7 — outstanding missed calls, above the log. Renders nothing
          when the queue is empty, so a clinic with no missed calls sees
          exactly what it saw before. */}
      <CallbackQueue onSelectCall={selectCall} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* Sized by a wrapper, not by `w-auto` on the control. Both these
            Selects carried `w-auto`, which never took effect: the shared
            CONTROL class also sets `w-full`, and Tailwind emits `w-full`
            later in the stylesheet, so it won on every render and the two
            filters stacked full-width down the page. Constraining the
            parent sidesteps class-order entirely — and full-width on a
            phone, fixed above `sm`, is the behaviour you want anyway. */}
        <div className="w-full sm:w-44">
          <Select
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value)}
            aria-label="Filter by date"
            className="py-2"
          >
            {DATE_OPTIONS.map((o) => <option key={o}>{o}</option>)}
          </Select>
        </div>
        <div className="w-full sm:w-52">
          <Select
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value)}
            aria-label="Filter by outcome"
            className="py-2"
          >
            <option>{ALL_OUTCOMES}</option>
            {OUTCOME_OPTIONS.map((o) => <option key={o}>{o}</option>)}
          </Select>
        </div>
        <Button
          variant="outline"
          onClick={exportCsv}
          disabled={filteredCalls.length === 0}
          loading={exporting}
          className="ml-auto"
        >
          {!exporting && <Download size={15} aria-hidden="true" />}
          {exporting ? "Exporting…" : "Export"}
        </Button>
      </div>

      {/* Was `bg-error-container` / `text-on-error-container` — neither is
          a token in index.css, so this banner compiled to no background
          and inherited text, i.e. an error message that was almost
          invisible. InlineAlert uses the tokens that do exist. */}
      {exportError ? <InlineAlert className="mb-4">{exportError}</InlineAlert> : null}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="overflow-hidden lg:col-span-2">
          {loading ? (
            <SkeletonList rows={5} avatar={false} label="Loading calls…" />
          ) : error ? (
            <ErrorState detail="We couldn't load your call logs." onRetry={loadList} className="py-20" />
          ) : filteredCalls.length === 0 ? (
            <EmptyState
              icon={PhoneOff}
              title="No calls yet"
              detail={
                calls.length === 0
                  ? "Once your AI receptionist takes a call, it will show up here with a transcript and summary."
                  : "No calls match the current filters."
              }
            />
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Patient</TH>
                  <TH>Outcome</TH>
                </tr>
              </THead>
              {/* TR carries tabIndex + Enter/Space handling. These rows
                  were clickable <tr>s with neither, so the whole list was
                  mouse-only. */}
              <TBody>
                {filteredCalls.map((c) => (
                  <TR key={c.id} onClick={() => selectCall(c.id)} selected={selectedId === c.id}>
                    <TD>
                      <p className="flex items-center gap-1.5 font-semibold text-on-surface">
                        {c.direction === "outbound" ? (
                          <PhoneOutgoing size={12} className="shrink-0 text-on-surface-variant" aria-hidden="true" />
                        ) : (
                          <PhoneIncoming size={12} className="shrink-0 text-on-surface-variant" aria-hidden="true" />
                        )}
                        {c.patient_name || "Unknown caller"}
                      </p>
                      {c.caller_phone && (
                        <p className="truncate text-xs tabular-nums text-on-surface-variant">{c.caller_phone}</p>
                      )}
                      {/* Only the parts the record has. "10:02 AM · — · —"
                          printed two placeholders for every call with no
                          reason and no duration yet. */}
                      <p className="text-xs tabular-nums text-on-surface-variant">
                        {[callTimeLabel(c), c.reason, c.duration].filter(Boolean).join(" · ")}
                      </p>
                    </TD>
                    <TD>
                      {/* Outcome where there is one, status otherwise -- the
                          same rule as the Dashboard. A live call has no
                          outcome yet; a failed one never will, and a "—"
                          chip read as missing data rather than a failure. */}
                      {c.outcome && c.status !== "in_progress" ? (
                        <Chip tone={outcomeTone(c.outcome)}>{c.outcome}</Chip>
                      ) : (
                        <Chip tone={callStatusTone(c.status)}>{callStatusLabel(c.status)}</Chip>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Card>

        {/* One panel, presented two ways. Below `lg` it is a sheet over
            the list; at `lg` and up it is the right-hand column it has
            always been. Rendering it twice would mean two copies of a
            live-updating transcript. */}
        <div
          // From `lg` the panel sticks below the header and scrolls on its
          // own, so a call picked far down the list is still in view --
          // it used to stay at the top of the page, off-screen.
          className={`lg:sticky lg:top-20 lg:col-span-3 lg:self-start ${
            sheetOpen
              ? "fixed inset-0 z-40 overflow-y-auto bg-surface p-4 lg:right-auto lg:bottom-auto lg:left-auto lg:z-auto lg:overflow-visible lg:bg-transparent lg:p-0"
              : "hidden lg:block"
          }`}
        >
          <button
            onClick={() => setSheetOpen(false)}
            className="focus-ring mb-3 flex min-h-10 items-center gap-1.5 rounded px-1 text-sm font-semibold text-on-surface-variant hover:text-on-surface lg:hidden"
          >
            <ArrowLeft size={16} aria-hidden="true" /> All calls
          </button>
        {/* Error and loading are checked before "nothing selected": the
            first panel load has no `selected` yet, so a failure there used
            to read "Select a call" and a load showed the same. */}
        <Card className="lg:max-h-[calc(100dvh-6rem)] lg:overflow-y-auto">
          {panelError ? (
            <ErrorState
              detail="We couldn't load this call's details."
              onRetry={() => loadPanel(selectedId)}
              className="py-24"
            />
          ) : panelLoading ? (
            <div className="space-y-6 px-6 py-5">
              <div className="flex items-start justify-between border-b border-outline-variant pb-4">
                <div className="space-y-2">
                  <Skeleton className="h-5 w-36" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="h-6 w-20 rounded-full" />
              </div>
              <div className="space-y-2">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-20 w-full rounded-xl" />
              </div>
              <div className="space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-10 w-3/4 rounded-xl" />
                <Skeleton className="ml-auto h-10 w-2/3 rounded-xl" />
              </div>
            </div>
          ) : !selected ? (
            <EmptyState
              icon={PhoneOff}
              title="Select a call"
              detail="Choose a call from the list to see its transcript and AI summary."
              className="py-24"
            />
          ) : (
            <>
              <div className="flex items-start justify-between border-b border-outline-variant px-6 py-4">
                <div>
                  <h2 className="font-display text-lg font-bold text-on-surface">
                    {selected.patient_name || "Unknown caller"}
                  </h2>
                  {selected.caller_phone && (
                    <p className="text-xs tabular-nums text-on-surface-variant">{selected.caller_phone}</p>
                  )}
                  <p className="text-xs tabular-nums text-on-surface-variant">
                    {callTimeLabel(selected)} {selected.duration ? `(${selected.duration})` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {/* Was an `info` "Live" chip here beside a `live` "In
                      progress" chip for the same call in the list. */}
                  {selected.status === "in_progress" ? (
                    <Chip tone="live">{callStatusLabel("in_progress")}</Chip>
                  ) : (
                    selected.sentiment && <Chip tone={SENTIMENT_TONE[selected.sentiment] || "neutral"}>{selected.sentiment}</Chip>
                  )}
                </div>
              </div>

              <div className="space-y-6 px-6 py-5">
                {/* The call's facts at a glance, all from the record. A row
                    renders only when the record carries it -- so an old call
                    marked "Booked" with no linked appointment shows no
                    appointment row rather than implying one exists. */}
                <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                  <div>
                    <dt className="text-xs text-on-surface-variant">Direction</dt>
                    <dd className="text-sm font-medium text-on-surface">
                      {selected.direction === "outbound" ? "Outbound" : "Inbound"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-on-surface-variant">Outcome</dt>
                    <dd className="mt-0.5">
                      {selected.outcome && selected.status !== "in_progress" ? (
                        <Chip tone={outcomeTone(selected.outcome)}>{selected.outcome}</Chip>
                      ) : (
                        <Chip tone={callStatusTone(selected.status)}>{callStatusLabel(selected.status)}</Chip>
                      )}
                    </dd>
                  </div>
                  {selected.intent && (
                    <div>
                      <dt className="text-xs text-on-surface-variant">Intent</dt>
                      <dd className="text-sm font-medium text-on-surface">{selected.intent}</dd>
                    </div>
                  )}
                  {selected.patient_id && (
                    <div>
                      <dt className="text-xs text-on-surface-variant">Patient</dt>
                      <dd>
                        <Link
                          to={`/app/patients/${encodeURIComponent(selected.patient_id)}`}
                          className="focus-ring inline-flex min-h-10 items-center rounded text-sm font-semibold text-accent hover:underline"
                        >
                          Open record
                        </Link>
                      </dd>
                    </div>
                  )}
                  {selected.appointment_id && (
                    <div>
                      <dt className="text-xs text-on-surface-variant">Appointment</dt>
                      <dd>
                        <Link
                          to="/app/appointments"
                          className="focus-ring inline-flex min-h-10 items-center rounded text-sm font-semibold text-accent hover:underline"
                        >
                          View in calendar
                        </Link>
                      </dd>
                    </div>
                  )}
                </dl>

                <div>
                  <div className="mb-2 flex items-center gap-2">
                    <Bot size={16} className="text-accent" aria-hidden="true" />
                    <h3 className="text-sm font-bold text-on-surface">AI summary</h3>
                  </div>
                  {selected.ai_summary ? (
                    <p className="rounded-xl bg-surface-low p-4 text-sm leading-relaxed text-on-surface-variant">
                      {selected.ai_summary}
                    </p>
                  ) : (
                    <p className="rounded-xl bg-surface-low p-4 text-sm leading-relaxed text-on-surface-variant">
                      {selected.status === "in_progress" ? "Call in progress — summary will appear once it ends." : "No summary available."}
                    </p>
                  )}
                </div>

                {/* What the agent DID, as its own region rather than a
                    footnote to the summary — the two answer different
                    questions, and a booking that happened is a harder
                    fact than a sentence describing the call.

                    Deliberately NOT interleaved into the transcript
                    below. That is a data fact, not a layout choice:
                    `actions_taken` is a newline-delimited summary
                    carrying no timestamps, so there is no honest place
                    to slot each one between two turns. Rendering them
                    as a third speaker would invent an ordering nobody
                    recorded. */}
                {selected.actions_taken && (
                  <div>
                    <div className="mb-2 flex items-center gap-2">
                      <Wrench size={16} className="text-on-surface-variant" aria-hidden="true" />
                      <h3 className="text-sm font-bold text-on-surface">Actions taken</h3>
                    </div>
                    <ul className="space-y-1.5 rounded-xl border border-outline-variant p-4">
                      {selected.actions_taken.split("\n").filter(Boolean).map((t) => (
                        <li key={t} className="flex items-center gap-2 text-sm text-on-surface">
                          {/* Ink, not green. These lines are the agent's own
                              account of what it did; green would present
                              them as verified. The outcome chip is what the
                              backend checked against the appointment book. */}
                          <CheckCircle2 size={15} className="shrink-0 text-on-surface-variant" aria-hidden="true" />
                          {t}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div>
                  <div className="mb-2 flex items-center justify-between">
                    {/* A "Play Recording" button used to sit here, gated on
                        `recording_url`. Call recording is not built and that
                        column has never been written, so the control could
                        never do anything — it advertised a capability the
                        product does not have. Removed rather than left
                        dormant. See models.CallLog.recording_url. */}
                    <h3 className="text-sm font-bold text-on-surface">Transcript</h3>
                  </div>
                  {!selected.transcript_messages || selected.transcript_messages.length === 0 ? (
                    <p className="text-sm text-on-surface-variant">No transcript yet.</p>
                  ) : (
                    <div className="space-y-4">
                      {selected.transcript_messages.map((t) => {
                        // `who` is constrained server-side to exactly
                        // "ai" | "patient" (schemas.py enforces that
                        // pattern), so these are the only two speakers
                        // a transcript can ever contain.
                        const isPatient = t.who === "patient";
                        const Icon = isPatient ? User : Bot;
                        return (
                          <div
                            key={t.id}
                            className={`flex flex-col gap-1 ${isPatient ? "items-end" : "items-start"}`}
                          >
                            {/* Named, not merely placed. Side and colour
                                alone are the "conveyed by colour" failure,
                                and on a transcript you have to know who
                                said something to read it at all. */}
                            <span className="flex items-center gap-1.5 px-1 text-xs font-semibold text-on-surface-variant">
                              <Icon size={12} aria-hidden="true" />
                              {isPatient ? "Caller" : "AI receptionist"}
                              {t.time_label && (
                                <span className="font-normal tabular-nums text-on-surface-variant/70">
                                  {t.time_label}
                                </span>
                              )}
                            </span>
                            {/* Teal means the AI at work everywhere else in
                                the app, so the AI's turns carry the tint and
                                the caller's are plain. This used to be the
                                other way round: the caller in solid teal. */}
                            <div
                              className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm text-on-surface ${
                                isPatient
                                  ? "border border-outline-variant bg-surface-lowest"
                                  : "bg-accent-subtle"
                              }`}
                            >
                              {t.text}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Where the call came from, for the support question that
                    starts here and finishes in the carrier's console.
                    Rendered only when the record actually carries it:
                    both columns are nullable and every call made before
                    the Twilio migration holds NULL, which means "this
                    predates the field" rather than "unknown carrier" —
                    so there is nothing honest to print for those. */}
                {(selected.provider || selected.provider_call_sid) && (
                  <div className="border-t border-outline-variant pt-4">
                    <h3 className="mb-2 text-sm font-bold text-on-surface">
                      Call origin
                    </h3>
                    <dl className="space-y-1.5 text-sm">
                      {selected.provider && (
                        <div className="flex gap-3">
                          <dt className="w-24 shrink-0 text-on-surface-variant">Carrier</dt>
                          <dd className="text-on-surface">
                            {PROVIDER_LABEL[selected.provider] || selected.provider}
                          </dd>
                        </div>
                      )}
                      {selected.provider_call_sid && (
                        <div className="flex gap-3">
                          <dt className="w-24 shrink-0 text-on-surface-variant">Call ID</dt>
                          {/* `select-all` so one click grabs the whole id
                              to paste into the carrier's console, and
                              `break-all` because a 34-character SID would
                              otherwise widen the panel. */}
                          <dd className="min-w-0 select-all break-all font-mono text-xs text-on-surface">
                            {selected.provider_call_sid}
                          </dd>
                        </div>
                      )}
                    </dl>
                  </div>
                )}
              </div>
            </>
          )}
        </Card>
        </div>
      </div>
    </>
  );
}

/**
 * Two views of the same calls, as a tab rather than a thirteenth sidebar
 * entry (AI-7). The active tab lives in the query string so a flagged
 * call can be linked to — `?tab=quality` — which a `useState` tab could
 * not do.
 */
const TABS = [
  { id: "calls", label: "Transcripts" },
  { id: "quality", label: "Quality" },
];

export default function CallLogs() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "quality" ? "quality" : "calls";

  return (
    <AppShell title="Call logs" subtitle="Review transcripts, outcomes, and AI summaries from every call.">
      <Tabs
        items={TABS}
        value={tab}
        onChange={(id) => setParams(id === "calls" ? {} : { tab: id }, { replace: true })}
        label="Call views"
        className="mb-5"
      />
      {/* The tabs point at `panel-{id}` via aria-controls; this is that
          panel. It did not exist, so the tablist referenced nothing. */}
      <TabPanel id={tab} value={tab}>
        {tab === "quality" ? <CallQuality /> : <CallLogsBody />}
      </TabPanel>
    </AppShell>
  );
}