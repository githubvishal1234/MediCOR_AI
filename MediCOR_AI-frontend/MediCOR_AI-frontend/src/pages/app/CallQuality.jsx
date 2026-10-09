/**
 * Call quality review — AI-7.
 *
 * The scoring backend (`app/evaluation_service.py`, `/quality/*`) shipped
 * with no UI at all, so the scores were unreachable. This is that UI. It
 * renders inside the Call Logs shell as a tab rather than taking a
 * thirteenth sidebar entry — quality is a way of reading calls, not a
 * separate part of the product.
 *
 * Three rules this page follows deliberately, each from a finding in the
 * evaluation work:
 *
 *   * **The review queue is driven by `needs_review`, never by score.** A
 *     call where the AI told a patient to take 400mg of ibuprofen scored
 *     70 with `goal_achieved: true` — any "flag below 60" rule passes it.
 *     Score colour is decoration; the flags are the signal.
 *   * **Violation counts sit next to the average, not folded into it.** A
 *     run of concise, efficient calls that each stated one wrong fact
 *     shows a healthy average and is the most urgent thing here.
 *   * **Nothing evaluates on mount.** Each judged call is a paid model
 *     request, so running a batch is an explicit admin action.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ShieldCheck, AlertTriangle, Gauge, Target, ClipboardCheck, Play,
  Check, RefreshCw, Bot,
} from "lucide-react";
import { Card, Chip, Button, StatCell, SkeletonList, EmptyState, ErrorState, InlineAlert } from "../../components/ui";
import * as api from "../../lib/api";
import { useAuth } from "../../lib/auth";

/** Human labels for the closed policy vocabulary the judge may return. */
const VIOLATION_LABEL = {
  medical_advice: "Gave medical advice",
  unverified_disclosure: "Disclosed without verifying identity",
  invented_information: "Invented information",
  missed_escalation: "Missed an escalation",
  unprofessional_tone: "Unprofessional tone",
};

// There used to be a `scoreTone` here colouring the score green / amber /
// red, while the comment beside it said that a green 88 and an amber 70
// say nothing about whether a call needs review. Colour here means
// something, so the score is plain ink and only the flags carry colour.

function whenLabel(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function EvaluationRow({ row, onReviewed, busy }) {
  const [open, setOpen] = useState(false);
  const violations = row.policy_violations || [];
  // Worth its own chip only when nothing else explains the flag: a call
  // with a hallucination already tells that story.
  const goalMissedOnly =
    row.goal_achieved === false && !row.hallucination_detected && violations.length === 0;

  return (
    <div className="border-b border-outline-variant last:border-b-0">
      <div className="flex flex-wrap items-center gap-3 px-5 py-3.5">
        <span
          className="w-10 shrink-0 text-center font-display text-lg font-semibold tabular-nums text-on-surface"
          aria-label={row.score == null ? "No score" : `Score ${row.score} out of 100`}
        >
          {row.score ?? "—"}
        </span>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-on-surface">
            {row.patient_name || "Unknown caller"}
          </p>
          <p className="text-xs text-on-surface-variant">
            {whenLabel(row.occurred_at)}
            {row.outcome ? ` · ${row.outcome}` : ""}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {row.hallucination_detected && (
            <Chip tone="error"><AlertTriangle size={12} aria-hidden="true" /> Hallucination</Chip>
          )}
          {violations.map((code) => (
            <Chip key={code} tone="warning">{VIOLATION_LABEL[code] || code}</Chip>
          ))}
          {goalMissedOnly && <Chip tone="neutral">Goal not achieved</Chip>}
          {row.reviewed_at && <Chip tone="success"><Check size={12} aria-hidden="true" /> Signed off</Chip>}
        </div>

        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="focus-ring min-h-10 rounded-lg px-3 text-xs font-semibold text-on-surface-variant hover:bg-surface-container"
        >
          {open ? "Hide" : "Why"}
        </button>

        {row.needs_review && !row.reviewed_at && (
          <Button variant="outline" size="sm" className="min-h-10" onClick={() => onReviewed(row.id)} loading={busy}>
            Mark reviewed
          </Button>
        )}
      </div>

      {open && (
        <div className="bg-surface-low px-5 pb-4 pt-1 text-sm text-on-surface-variant">
          <p className="leading-relaxed">{row.reasoning || "The judge gave no reasoning."}</p>
          <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span>Conciseness {row.conciseness ?? "—"}/100</span>
            <span>Goal achieved: {row.goal_achieved ? "yes" : "no"}</span>
            {row.judge_model && (
              <span className="flex items-center gap-1"><Bot size={12} aria-hidden="true" /> {row.judge_model}</span>
            )}
            <span>Judged {whenLabel(row.evaluated_at)}</span>
          </p>
        </div>
      )}
    </div>
  );
}

export default function CallQuality() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState([]);
  const [onlyNeedsReview, setOnlyNeedsReview] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [running, setRunning] = useState(false);
  const [runMessage, setRunMessage] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.all([
      api.getQualitySummary(),
      api.listEvaluations({ needsReview: onlyNeedsReview }),
    ])
      .then(([summaryData, rowsData]) => {
        if (cancelled) return;
        setSummary(summaryData);
        setRows(rowsData);
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
  }, [onlyNeedsReview]);

  useEffect(() => load(), [load]);

  async function runBatch() {
    setRunning(true);
    setRunMessage(null);
    try {
      const result = await api.runQualityEvaluation(20);
      // `queued: false` is a normal answer, not an error — it means no
      // LLM is configured, and the endpoint says so in `detail`.
      setRunMessage({ tone: result.queued ? "info" : "warning", text: result.detail });
    } catch (err) {
      setRunMessage({ tone: "error", text: err?.message || "Couldn't start the evaluation." });
    } finally {
      setRunning(false);
    }
  }

  async function markReviewed(evaluationId) {
    setBusyId(evaluationId);
    try {
      const updated = await api.markEvaluationReviewed(evaluationId);
      setRows((prev) =>
        onlyNeedsReview
          // Signing off removes it from the queue being viewed.
          ? prev.filter((r) => r.id !== updated.id)
          : prev.map((r) => (r.id === updated.id ? { ...r, ...updated } : r)),
      );
      setSummary((prev) =>
        prev ? { ...prev, needs_review_count: Math.max(0, prev.needs_review_count - 1) } : prev,
      );
    } catch (err) {
      setRunMessage({ tone: "error", text: err?.message || "Couldn't sign that off." });
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return <ErrorState detail="We couldn't load call quality." onRetry={load} className="py-20" />;
  }

  const violations = Object.entries(summary?.violations_by_code || {}).sort((a, b) => b[1] - a[1]);
  const nothingEvaluated = !loading && summary?.evaluated_calls === 0;

  return (
    <>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-on-surface-variant">
          Finished calls scored by an LLM judge against the transcript <em>and</em> the tools
          that actually ran — which is what makes &ldquo;I&apos;ve booked you in&rdquo; with no
          booking detectable. Scoring every call is off by default, because each judged call
          is a paid model request.
        </p>
        {/* "Nothing runs automatically" used to be stated here as fact. The
            backend has an automatic hourly job (QUALITY_AUTO_EVAL_ENABLED);
            it is off by default, which is all this page can truthfully say. */}
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="min-h-10" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} aria-hidden="true" /> Refresh
          </Button>
          {isAdmin && (
            <Button size="sm" className="min-h-10" onClick={runBatch} loading={running}>
              {!running && <Play size={14} aria-hidden="true" />}
              Evaluate 20 recent calls
            </Button>
          )}
        </div>
      </div>

      {runMessage && (
        <InlineAlert tone={runMessage.tone} className="mb-5">
          {runMessage.text}
        </InlineAlert>
      )}

      {/* One strip, as on the Dashboard: four readings of the same thing
          share one surface rather than floating as four cards. */}
      <Card as="section" aria-label="Quality at a glance" className="overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-outline-variant lg:grid-cols-4">
          {loading || !summary ? (
            ["Calls evaluated", "Average score", "Goal achieved", "Needs review"].map((label) => (
              <StatCell key={label} label={label} loading />
            ))
          ) : (
            <>
              <StatCell
                icon={ClipboardCheck}
                label="Calls evaluated"
                value={summary.evaluated_calls.toLocaleString()}
              />
              <StatCell
                icon={Gauge}
                label="Average score"
                value={summary.average_score === null ? "—" : summary.average_score}
                hint="Out of 100"
              />
              <StatCell
                icon={Target}
                label="Goal achieved"
                value={summary.goal_achieved_pct === null ? "—" : `${summary.goal_achieved_pct}%`}
                hint="The caller got what they rang for"
              />
              <StatCell
                icon={AlertTriangle}
                label="Needs review"
                value={summary.needs_review_count.toLocaleString()}
                hint={
                  summary.hallucination_count || summary.policy_violation_count
                    ? `${summary.hallucination_count} hallucination${summary.hallucination_count === 1 ? "" : "s"} · ${summary.policy_violation_count} policy`
                    : "Flagged regardless of score"
                }
                tone={summary.needs_review_count > 0 ? "error" : undefined}
              />
            </>
          )}
        </div>
      </Card>

      {violations.length > 0 && (
        <Card className="mt-6 p-5">
          <h2 className="font-display text-base font-bold text-on-surface">Policy violations</h2>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Counted separately from the average on purpose — one wrong fact per call still
            averages well.
          </p>
          <div className="mt-4 space-y-2.5">
            {violations.map(([code, count]) => (
              <div key={code} className="flex items-center justify-between gap-4 text-sm">
                <span className="text-on-surface">{VIOLATION_LABEL[code] || code}</span>
                <span className="font-semibold tabular-nums text-on-surface-variant">{count}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mt-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-outline-variant px-5 py-3.5">
          <h2 className="font-display text-base font-bold text-on-surface">
            {onlyNeedsReview ? "Review queue" : "All evaluated calls"}
          </h2>
          {/* A filter, like the Patients one: `aria-pressed`, which these
              two buttons were missing, so the chosen one was only visible. */}
          <div className="flex gap-0.5 rounded-lg border border-outline-variant p-0.5 text-sm font-semibold" role="group" aria-label="Filter evaluated calls">
            {[
              { value: true, label: "Needs review" },
              { value: false, label: "All" },
            ].map((opt) => (
              <button
                key={opt.label}
                onClick={() => setOnlyNeedsReview(opt.value)}
                aria-pressed={onlyNeedsReview === opt.value}
                className={`focus-ring min-h-9 rounded-md px-3 transition ${
                  onlyNeedsReview === opt.value
                    ? "bg-surface-container text-on-surface"
                    : "text-on-surface-variant hover:text-on-surface"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <SkeletonList rows={3} avatar={false} label="Loading evaluations…" />
        ) : nothingEvaluated ? (
          <EmptyState
            icon={ShieldCheck}
            title="No calls have been evaluated yet"
            detail={
              isAdmin
                ? "Run an evaluation to score your recent calls. Each judged call costs one model request, so automatic scoring is off by default."
                : "Ask a clinic admin to run an evaluation — each judged call costs a model request, so automatic scoring is off by default."
            }
            className="py-16"
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Check}
            title="Nothing needs review"
            detail="No evaluated call was flagged for a hallucination, a policy violation, or a low score."
            className="py-16"
          />
        ) : (
          <div>
            {rows.map((row) => (
              <EvaluationRow
                key={row.id}
                row={row}
                onReviewed={markReviewed}
                busy={busyId === row.id}
              />
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
