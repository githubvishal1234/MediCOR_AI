/**
 * Missed-call callback queue — HV-7.
 *
 * `CallLog.status` has always supported "failed" and "no_answer", and the
 * backend already raised a warning notification for them — but there was
 * no queue, so a missed call surfaced as a toast and then existed only as
 * a row buried in the call log. At a clinic a missed call is a lost
 * patient, so this turns each one into a work item with a state.
 *
 * Collapsed by default and hidden entirely when the queue is empty: this
 * sits above the call log, and a permanently-visible empty panel would
 * just push the main content down.
 */

import { useCallback, useEffect, useState } from "react";
import {
  PhoneMissed, ChevronDown, ChevronRight, Check, PhoneOutgoing, Loader2,
} from "lucide-react";
import { Card, Chip, Button } from "../components/ui";
import * as api from "../lib/api";
import { callStatusLabel, callStatusTone } from "../lib/format";

const STATUS_TONE = { pending: "warning", contacted: "info", resolved: "success" };
const STATUS_LABEL = { pending: "Needs callback", contacted: "Contacted", resolved: "Resolved" };

function whenLabel(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function CallbackQueue({ onSelectCall }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .listCallbackQueue()
      .then((data) => {
        setItems(data);
        setError("");
      })
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function advance(call, status) {
    setBusyId(call.id);
    setError("");
    try {
      await api.updateCallback(call.id, { callback_status: status });
      // Resolved items drop out of the default queue server-side, so
      // refetch rather than patching in place.
      load();
    } catch (err) {
      setError(err?.message || "Couldn't update that callback.");
    } finally {
      setBusyId(null);
    }
  }

  // Nothing outstanding is the good case — say nothing at all.
  if (loading || items.length === 0) return null;

  const pendingCount = items.filter((i) => i.callback_status === "pending").length;

  return (
    <Card className="mb-6 overflow-hidden">
      {/* The heading wraps the toggle, as in RefillQueue: a heading inside
          a <button> is invalid and drops out of the heading list. */}
      <h2>
        <button
          onClick={() => setOpen((o) => !o)}
          className="focus-ring flex min-h-14 w-full flex-wrap items-center gap-2 px-5 py-3 text-left hover:bg-surface-low"
          aria-expanded={open}
        >
          {open ? <ChevronDown size={16} className="text-on-surface-variant" aria-hidden="true" />
                : <ChevronRight size={16} className="text-on-surface-variant" aria-hidden="true" />}
          <PhoneMissed size={16} className="text-warning" aria-hidden="true" />
          <span className="font-display text-base font-bold text-on-surface">Missed calls</span>
          {pendingCount > 0 && <Chip tone="warning">{pendingCount} to call back</Chip>}
          <span className="ml-auto text-xs font-normal tabular-nums text-on-surface-variant">
            {items.length} in the queue
          </span>
        </button>
      </h2>

      {open && (
        <div className="border-t border-outline-variant">
          {error && (
            <p className="border-b border-outline-variant bg-error-bg px-5 py-2 text-sm text-error" role="alert">
              {error}
            </p>
          )}
          <div className="divide-y divide-outline-variant">
            {items.map((c) => {
              const busy = busyId === c.id;
              return (
                <div key={c.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {/* min-h-10: on a phone this name is the only way to
                          open the missed call, and it was a 20px text line. */}
                      <button
                        onClick={() => onSelectCall?.(c.id)}
                        className="focus-ring inline-flex min-h-10 min-w-0 items-center truncate rounded text-sm font-semibold text-on-surface hover:underline"
                      >
                        {c.patient_name || c.caller_phone || "Unknown caller"}
                      </button>
                      <Chip tone={STATUS_TONE[c.callback_status] || "neutral"}>
                        {STATUS_LABEL[c.callback_status] || c.callback_status}
                      </Chip>
                      {/* Same tone as the call list, so "Failed" is one colour app-wide. */}
                      <Chip tone={callStatusTone(c.status)}>{callStatusLabel(c.status)}</Chip>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-on-surface-variant">
                      {c.caller_phone && <span className="font-mono">{c.caller_phone}</span>}
                      {c.caller_phone && " · "}
                      {whenLabel(c.occurred_at)}
                      {c.callback_note && ` · ${c.callback_note}`}
                    </p>
                  </div>

                  <div className="flex items-center gap-2">
                    {c.callback_status === "pending" && (
                      <Button variant="outline" disabled={busy} onClick={() => advance(c, "contacted")}>
                        {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <PhoneOutgoing size={14} aria-hidden="true" />}
                        Mark contacted
                      </Button>
                    )}
                    <Button variant="outline" disabled={busy} onClick={() => advance(c, "resolved")}>
                      <Check size={14} aria-hidden="true" /> Resolve
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}
