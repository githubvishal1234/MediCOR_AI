/**
 * Repeat-prescription queue — F9.
 *
 * The voice agent can now take a refill request over the phone, but it
 * only ever records one: `POST /refills` writes `status="pending"` and
 * nothing else, and approving is a staff-only route the agent's
 * credential cannot reach. This screen is the other half of that design —
 * without somewhere for a person to decide, requests would queue up
 * unseen, which would be worse than not taking them at all.
 *
 * Mirrors CallbackQueue deliberately: same collapse behaviour, same
 * hidden-when-empty rule, so the two work-item panels on this page read
 * as one idea rather than two.
 *
 * One thing this component must never imply: that the AI approved
 * anything. Every request arrives "Awaiting review" and only a click
 * here changes that.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Pill, ChevronDown, ChevronRight, Check, X, Loader2, Link2Off,
} from "lucide-react";
import { Card, Chip, Button } from "../components/ui";
import * as api from "../lib/api";

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

export default function RefillQueue({ onSelectPatient }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .listRefillRequests()
      .then((data) => {
        setItems(data);
        setError("");
      })
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function decide(item, status) {
    setBusyId(item.id);
    setError("");
    try {
      await api.updateRefillRequest(item.id, { status });
      // A handled request drops out of the default queue server-side, so
      // refetch rather than patching it out here.
      load();
    } catch (err) {
      setError(err?.message || "Couldn't update that request.");
    } finally {
      setBusyId(null);
    }
  }

  // An empty queue is the good case — say nothing rather than pushing the
  // page down with a permanently visible empty panel.
  if (loading || items.length === 0) return null;

  return (
    <Card className="mb-6 overflow-hidden">
      {/* The heading wraps the toggle, not the other way round: a heading
          inside a <button> is invalid and screen readers drop it from the
          page's heading list. */}
      <h2>
        <button
          onClick={() => setOpen((o) => !o)}
          className="focus-ring flex min-h-14 w-full flex-wrap items-center gap-2 px-5 py-3 text-left hover:bg-surface-low"
          aria-expanded={open}
        >
          {open ? <ChevronDown size={16} className="text-on-surface-variant" aria-hidden="true" />
                : <ChevronRight size={16} className="text-on-surface-variant" aria-hidden="true" />}
          <Pill size={16} className="text-warning" aria-hidden="true" />
          <span className="font-display text-base font-bold text-on-surface">Refill requests</span>
          <Chip tone="warning">{items.length} awaiting review</Chip>
          <span className="ml-auto text-xs font-normal text-on-surface-variant">
            Taken by phone · not approved
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
            {items.map((r) => {
              const busy = busyId === r.id;
              return (
                <div key={r.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-on-surface">
                        {r.medication}
                      </span>
                      <Chip tone="warning">Awaiting review</Chip>
                      {/* A null prescription_id means the caller named
                          something not on their record. Worth seeing at
                          review time, so it is shown rather than hidden. */}
                      {!r.prescription_id && (
                        <Chip tone="neutral">
                          <Link2Off size={12} /> Not on file
                        </Chip>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-on-surface-variant">
                      {r.patient_id ? (
                        <button
                          onClick={() => onSelectPatient?.(r.patient_id)}
                          className="focus-ring inline-flex min-h-10 items-center rounded font-semibold hover:underline"
                        >
                          {r.patient_name || "Patient"}
                        </button>
                      ) : (
                        <span>{r.patient_name || "Unknown patient"}</span>
                      )}
                      {" · "}
                      {whenLabel(r.requested_at)}
                      {r.note && ` · ${r.note}`}
                    </p>
                  </div>

                  <div className="flex items-center gap-2">
                    {/* Outline, not teal fill -- the page's one primary
                        action is "Add patient", and CallbackQueue's
                        buttons are outline too. */}
                    <Button variant="ghost" disabled={busy} onClick={() => decide(r, "declined")}>
                      <X size={14} aria-hidden="true" /> Decline
                    </Button>
                    <Button variant="secondary" disabled={busy} onClick={() => decide(r, "approved")}>
                      {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}
                      Approve
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
