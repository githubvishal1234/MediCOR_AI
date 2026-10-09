/**
 * The live-call band — the product's signature moment.
 *
 * MediCOR's whole claim is "an AI receptionist is answering your phone
 * right now". That state used to be one card among four, distinguished by
 * a 2px pulsing dot, sharing a row with three static KPI tiles. The most
 * important thing the product ever has to say was the quietest thing on
 * the page.
 *
 * This is a full-width band that only exists while a call is in progress,
 * so it is never chrome — its presence *is* the information. When no call
 * is live the component renders nothing and the dashboard closes up
 * behind it.
 *
 * Everything shown comes from the call record the realtime channel
 * already pushes (`call_summary` in app/realtime.py): direction, caller
 * name and number, reason, status and `started_at`. Nothing here is
 * invented, and no field is displayed that the backend does not send.
 */

import { useEffect, useRef, useState } from "react";
import { PhoneIncoming, PhoneOutgoing, ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { LiveDot } from "./ui";

/** mm:ss since the call started, ticking once a second. */
function useElapsed(startedAt) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    // One interval per mounted call row. Cheap, and it stops when the
    // call ends because the row unmounts with it.
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (!startedAt) return null;
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return null;

  const secs = Math.max(0, Math.floor((now - start) / 1000));
  const mins = Math.floor(secs / 60);
  const hrs = Math.floor(mins / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return hrs > 0 ? `${hrs}:${pad(mins % 60)}:${pad(secs % 60)}` : `${mins}:${pad(secs % 60)}`;
}

/**
 * Five bars on a staggered loop, so the band reads as *audio* rather than
 * as a static alert. `.waveform-bar` already existed in index.css and was
 * used only on the marketing page.
 *
 * Purely decorative: `aria-hidden`, and index.css already flattens the
 * animation under `prefers-reduced-motion`.
 */
function Waveform() {
  return (
    <span className="flex h-6 items-center gap-[3px]" aria-hidden="true">
      {[0, 0.18, 0.36, 0.12, 0.3].map((delay, i) => (
        <span
          key={i}
          className="waveform-bar w-[3px] rounded-full bg-live"
          style={{ height: "100%", animationDelay: `${delay}s` }}
        />
      ))}
    </span>
  );
}

function initials(name) {
  return (name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}

function LiveCallRow({ call, onOpen }) {
  const elapsed = useElapsed(call.started_at);
  const outbound = call.direction === "outbound";
  const Icon = outbound ? PhoneOutgoing : PhoneIncoming;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3.5 sm:px-6">
      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-live-bg text-sm font-bold text-live">
        {initials(call.patient_name)}
      </div>

      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 truncate text-sm font-bold text-on-surface">
          <Icon size={13} className="shrink-0 text-on-surface-variant" aria-hidden="true" />
          {call.patient_name || "Unknown caller"}
        </p>
        <p className="truncate text-xs text-on-surface-variant">
          {call.caller_phone && <span className="font-mono">{call.caller_phone}</span>}
          {call.caller_phone && " · "}
          {call.reason || (outbound ? "Outbound call" : "Inbound call")}
        </p>
      </div>

      <div className="flex items-center gap-4">
        <Waveform />
        {/* Tabular figures so the seconds do not jitter the layout as
            they tick. */}
        {elapsed && (
          <span className="font-mono text-sm font-semibold tabular-nums text-on-surface">
            {elapsed}
          </span>
        )}
        <button
          onClick={() => onOpen(call.id)}
          className="focus-ring hidden items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface sm:inline-flex"
        >
          Open <ArrowRight size={13} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

export default function LiveCallBanner({ calls }) {
  const navigate = useNavigate();
  const announced = useRef(0);
  const [announcement, setAnnouncement] = useState("");

  const count = calls.length;

  // A11Y: a call starting is conveyed entirely by a band appearing and a
  // waveform moving — both invisible to a screen reader. This announces
  // the change politely, and only when the count actually changes, so it
  // does not re-read on every tick of the duration timer.
  useEffect(() => {
    if (count === announced.current) return;
    announced.current = count;
    setAnnouncement(
      count === 0
        ? "No calls in progress."
        : count === 1
        ? "One call in progress with the AI receptionist."
        : `${count} calls in progress with the AI receptionist.`
    );
  }, [count]);

  return (
    <>
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {count > 0 && (
        <section
          aria-label="Calls in progress"
          className="mb-6 overflow-hidden rounded-xl border border-live/30 bg-surface-lowest shadow-(--shadow-raised)"
        >
          <div className="flex flex-wrap items-center gap-3 border-b border-live/20 bg-live-bg/60 px-4 py-3 sm:px-6">
            <LiveDot />
            <h2 className="font-display text-sm font-bold text-live">
              {count === 1 ? "Call in progress" : `${count} calls in progress`}
            </h2>
            <p className="hidden text-xs text-on-surface-variant sm:block">
              Your AI receptionist is handling {count === 1 ? "this call" : "these calls"} now.
            </p>
            <button
              onClick={() => navigate("/app/calls")}
              className="focus-ring ml-auto rounded text-xs font-semibold text-accent hover:underline"
            >
              View all calls
            </button>
          </div>

          <div className="divide-y divide-outline-variant">
            {calls.map((c) => (
              <LiveCallRow
                key={c.id}
                call={c}
                onOpen={(id) => navigate(`/app/calls?call=${encodeURIComponent(id)}`)}
              />
            ))}
          </div>
        </section>
      )}
    </>
  );
}
