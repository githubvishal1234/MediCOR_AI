/**
 * Billing & usage.
 *
 * HV-4 changed what this page is allowed to claim. Three things it used
 * to state without knowing them:
 *
 *   * "Includes premium EHR integrations and dedicated support" was
 *     hardcoded copy shown to every clinic, including the seeded Starter
 *     plan that grants neither. It now renders the real plan flags, which
 *     matter more since those flags started being enforced — a clinic
 *     reading this needs to know what it will actually be allowed to do.
 *   * The status chip was hardcoded green "Active", including for a
 *     clinic whose subscription had been cancelled.
 *   * "Upgrade Plan" called an endpoint that used to grant Enterprise for
 *     free and now returns 501 by design. A button whose only outcome is
 *     an error is worse than no button, so plan changes point at support
 *     until HV-5 wires a payment provider.
 *
 * The usage bar itself was already real — `minutes_used` is computed from
 * CallLog, not the stale `voice_minutes_used` column.
 */

import { useCallback, useEffect, useState } from "react";
import {
  CreditCard, Calendar, Receipt,
  Check, Minus, AlertTriangle,
} from "lucide-react";
import { Link } from "react-router-dom";
import AppShell from "../../components/AppShell";
import { Card, Chip, Skeleton, EmptyState, ErrorState, Table, THead, TH, TBody, TD } from "../../components/ui";
import PlanCheckout from "../../components/PlanCheckout";
import * as api from "../../lib/api";

/**
 * An amount in minor units, in the currency it was actually charged in.
 *
 * `Intl.NumberFormat` rather than a symbol table: it throws on an
 * unknown ISO code instead of guessing, and guessing is how a rupee
 * charge came to be printed with a dollar sign.
 */
function formatMoney(cents, currency) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "INR",
      maximumFractionDigits: 2,
    }).format((cents || 0) / 100);
  } catch {
    return `${currency || ""} ${((cents || 0) / 100).toFixed(2)}`.trim();
  }
}

/** Green when a subscription entitles the plan, amber/red when it doesn't. */
const STATUS_TONE = {
  active: "success",
  past_due: "warning",
  canceled: "error",
};

/**
 * One granted-or-not line. Renders what the plan says rather than what
 * we would like it to say — a missing feature is stated plainly, because
 * the alternative is a clinic discovering it at a 403.
 */
function FeatureRow({ granted, children }) {
  const Icon = granted ? Check : Minus;
  return (
    <li className={`flex items-center gap-2 ${granted ? "text-on-surface" : "text-on-surface-variant"}`}>
      <Icon size={14} className={granted ? "text-success" : "text-on-surface-variant"} />
      <span className={granted ? "" : "line-through decoration-on-surface-variant/40"}>{children}</span>
    </li>
  );
}

function formatCents(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export default function Billing() {
  const [plan, setPlan] = useState(null);
  const [invoices, setInvoices] = useState([]);
  // The most recent payment attempt, for the card that used to assert a
  // stored card. A failed fetch leaves it null and the card says there
  // have been no payments, which is the honest reading of "we do not know".
  const [lastPayment, setLastPayment] = useState(null);
  const [invoiceError, setInvoiceError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    api
      .listPayments()
      .then((rows) => setLastPayment(rows[0] || null))
      .catch(() => setLastPayment(null));

    Promise.all([api.getPlan(), api.listInvoices()])
      .then(([planData, invoicesData]) => {
        if (cancelled) return;
        setPlan(planData);
        setInvoices(invoicesData);
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
  }, []);

  useEffect(() => {
    return load();
  }, [load]);

  const pct = plan ? Math.round(plan.usage_pct) : 0;
  // Three states, not two: under, approaching, and over. The bar used to
  // be green at every value including 140%.
  const usageTone = plan?.over_limit ? "error" : pct >= 80 ? "warning" : "ok";
  // No subscription row is the ordinary healthy state for most clinics —
  // they predate the plan catalogue — so it reads green, not grey.
  const statusTone = plan?.subscription_status
    ? STATUS_TONE[plan.subscription_status] || "neutral"
    : "success";
  // var() rather than a hex, so the bar re-themes in dark mode with
  // everything else instead of staying a fixed light-mode colour.
  const BAR_COLOR = {
    ok: "var(--color-accent)",
    warning: "var(--color-warning)",
    error: "var(--color-error)",
  }[usageTone];

  if (error) {
    return (
      <AppShell title="Billing & Usage" subtitle="Manage your current plan, monitor AI voice minutes usage, and view your billing history.">
        <ErrorState detail="We couldn't load your billing information." onRetry={load} className="py-24" />
      </AppShell>
    );
  }

  /**
   * Open one invoice as a printable document.
   *
   * A blob URL rather than a direct link, because the endpoint needs the
   * bearer token — the same reason the call-log export is fetched. The
   * URL is revoked on a delay: revoking it immediately races the new
   * tab's own load and yields a blank page.
   */
  async function openInvoice(invoice) {
    setInvoiceError("");
    try {
      const blob = await api.openInvoiceDocument(invoice.id);
      const url = URL.createObjectURL(blob);
      const opened = window.open(url, "_blank", "noopener");
      if (!opened) {
        // Pop-up blocked. Say so — silently doing nothing is how a
        // control ends up looking broken.
        setInvoiceError("Allow pop-ups for this site to view invoices.");
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      setInvoiceError(err?.message || "Could not open that invoice.");
    }
  }

  return (
    <AppShell title="Billing & Usage" subtitle="Manage your current plan, monitor AI voice minutes usage, and view your billing history.">
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          {loading ? (
            <div className="space-y-4">
              <Skeleton className="h-6 w-40" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-10 w-56" />
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="font-display text-lg font-bold text-on-surface">{plan?.plan}</h2>
                    <Chip tone={statusTone}>{plan?.status}</Chip>
                  </div>
                  <ul className="mt-2.5 space-y-1.5 text-sm">
                    <FeatureRow granted>
                      {plan?.minutes_limit?.toLocaleString()} AI voice minutes per month
                    </FeatureRow>
                    <FeatureRow granted={plan?.ehr_access}>EHR integration &amp; sync</FeatureRow>
                    <FeatureRow granted={plan?.knowledge_base_access}>
                      Document knowledge base for the AI
                    </FeatureRow>
                    {plan?.user_limit ? (
                      <FeatureRow granted>Up to {plan.user_limit} team members</FeatureRow>
                    ) : null}
                    {plan?.patient_limit ? (
                      <FeatureRow granted>Up to {plan.patient_limit.toLocaleString()} patient records</FeatureRow>
                    ) : null}
                  </ul>
                </div>
              </div>

              {plan?.subscription_status === "canceled" && (
                <p className="mt-4 flex items-start gap-2 rounded-lg bg-error-bg px-3 py-2.5 text-sm text-error">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                  <span>
                    This subscription is cancelled. Calls are still answered, but EHR sync
                    and the knowledge base are switched off.
                  </span>
                </p>
              )}

              {/* HV-5: self-serve checkout exists now (Razorpay), so the
                  page no longer sends every plan change to support. The
                  link stays for what the catalogue cannot express -- a
                  bespoke plan, or a clinic that would rather be invoiced
                  than pay by card. */}
              <PlanCheckout currentPlan={plan?.plan} onPaid={load} />

              <p className="mt-4 text-sm text-on-surface-variant">
                Need something the plans above don&apos;t cover?{" "}
                <Link to="/app/support" className="font-semibold text-success hover:underline">
                  Contact us
                </Link>
                .
              </p>

              <div className="mt-7 border-t border-outline-variant pt-6">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold text-on-surface">Voice Minutes Usage</h3>
                  <span className="text-sm font-semibold text-on-surface">
                    {plan?.minutes_used?.toLocaleString(undefined, { maximumFractionDigits: 0 })} / {plan?.minutes_limit?.toLocaleString()} min
                  </span>
                </div>
                <div className="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-surface-container">
                  {/* Capped at 100% so an over-limit clinic gets a full bar
                      rather than one that overflows its container. */}
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: BAR_COLOR }}
                  />
                </div>
                <div className="mt-2 flex items-center justify-between gap-3 text-xs text-on-surface-variant">
                  <span>{plan?.period_label ? `Billing period ${plan.period_label}` : ""}</span>
                  {usageTone === "error" ? (
                    <Chip tone="error">{pct}% used — over your included minutes</Chip>
                  ) : usageTone === "warning" ? (
                    <Chip tone="warning">{pct}% used — approaching limit</Chip>
                  ) : null}
                </div>
                {plan?.over_limit && (
                  <p className="mt-2 text-xs text-on-surface-variant">
                    Calls are still being answered — we don&apos;t cut off your phone line.
                    We&apos;ll be in touch about the overage.
                  </p>
                )}
              </div>
            </>
          )}
        </Card>

        <div className="space-y-6">
          <Card className="p-5">
            <div className="flex items-center gap-2">
              <Calendar size={16} className="text-accent" aria-hidden="true" />
              <h3 className="text-sm font-bold text-on-surface">Next Billing Date</h3>
            </div>
            {loading ? (
              <Skeleton className="mt-2 h-6 w-28" />
            ) : (
              <p className="mt-2 text-lg font-semibold text-on-surface">{plan?.next_billing_date || "—"}</p>
            )}
          </Card>

          <Card className="p-5">
            {/* WAS "Visa ending in 4242 / Expires 12/25", read straight
                off seeded demo columns. No card was ever on file and
                nothing in this application has ever accepted one: the
                Razorpay flow redirects to the provider precisely so a
                card never reaches this server. A page asserting a stored
                card, beside a real next-billing date, told a clinic they
                were about to be charged on it.

                It now shows the last payment actually recorded, or says
                plainly that there has not been one. `payment_label` and
                `payment_expires` are left on the API for compatibility
                and deliberately not read here. */}
            <h3 className="text-sm font-bold text-on-surface">Last payment</h3>
            {loading ? (
              <Skeleton className="mt-3 h-14 w-full rounded-xl" />
            ) : lastPayment ? (
              <div className="mt-3 flex items-center gap-3 rounded-xl border border-outline-variant p-3.5">
                <CreditCard size={20} className="text-on-surface-variant" />
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-on-surface">
                    {formatMoney(lastPayment.amount_cents, lastPayment.currency)}
                    {lastPayment.plan_name ? ` · ${lastPayment.plan_name}` : ""}
                  </p>
                  <p className="truncate text-xs text-on-surface-variant">
                    {lastPayment.captured_at
                      ? new Date(lastPayment.captured_at).toLocaleDateString()
                      : "Not completed"}
                  </p>
                </div>
              </div>
            ) : (
              <p className="mt-3 rounded-xl border border-outline-variant p-3.5 text-sm text-on-surface-variant">
                No payments yet. Cards are handled by Razorpay and never stored here.
              </p>
            )}
          </Card>
        </div>
      </div>

      <Card className="mt-6">
        {/* A "Download All" button used to sit here with no `onClick` and
            no endpoint behind it. Individual invoices are served now
            (GAP-B2), so each row links to its own document — but there is
            still no bulk endpoint, and a button that zips nothing would
            be the same defect again. */}
        <div className="border-b border-outline-variant px-6 py-4">
          <h2 className="font-display text-base font-bold text-on-surface">Billing history</h2>
          {invoiceError && (
            <p role="alert" className="mt-1 text-xs font-semibold text-error">{invoiceError}</p>
          )}
        </div>
        {loading ? (
          <div className="space-y-2 p-4">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-9 w-full" />)}
          </div>
        ) : invoices.length === 0 ? (
          <EmptyState
            icon={Receipt}
            title="No invoices yet"
            detail="Your billing history will appear here once your first invoice is issued."
            className="py-16"
          />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Invoice ID</TH>
                <TH>Date</TH>
                <TH>Amount</TH>
                <TH>Status</TH>
                <TH><span className="sr-only">Document</span></TH>
              </tr>
            </THead>
            <TBody>
              {invoices.map((inv) => (
                <tr key={inv.id}>
                  <TD className="font-medium text-on-surface">{inv.invoice_number}</TD>
                  <TD className="text-on-surface-variant">{formatDate(inv.issued_at)}</TD>
                  <TD className="tabular-nums text-on-surface-variant">{formatCents(inv.amount_cents)}</TD>
                  <TD>
                    <Chip tone={inv.status === "Paid" ? "success" : "neutral"}>{inv.status}</Chip>
                  </TD>
                  <TD className="text-right">
                    <button
                      type="button"
                      onClick={() => openInvoice(inv)}
                      className="focus-ring inline-flex min-h-10 items-center rounded-md px-3 text-sm font-semibold text-accent hover:bg-surface-container"
                    >
                      View
                    </button>
                  </TD>
                </tr>
              ))}
            </TBody>
          </Table>
        )}
      </Card>
    </AppShell>
  );
}
