/**
 * Choosing a plan and paying for it (HV-5, Razorpay).
 *
 * Before this the Billing page said "Need a different plan? Contact us",
 * because `POST /billing/upgrade` was a deliberate 501 -- it used to grant
 * Enterprise for free. This is the path that replaces it.
 *
 * Three properties are load-bearing and easy to lose:
 *
 *   - **No price is ever sent.** `createCheckout` takes a plan name; the
 *     server reads the amount from the catalogue and creates the order.
 *     Anything here that put a number on the wire would let a clinic name
 *     its own charge.
 *   - **The server is told the outcome, and decides.** Razorpay's handler
 *     hands back three values which go straight to `/billing/checkout/verify`
 *     to be checked against the key secret. This component never concludes
 *     that a payment succeeded -- it relays and waits.
 *   - **Dismissing the modal is not a failure.** Razorpay fires `ondismiss`
 *     when someone closes the sheet, which is the ordinary "changed my
 *     mind". Showing an error there would make a normal act look broken.
 *
 * The checkout script is loaded on demand rather than in index.html: a
 * clinic that never opens Billing should not fetch a payment SDK, and a
 * deployment with no Razorpay account should never load it at all.
 */

import { useCallback, useEffect, useState } from "react";
import { CreditCard, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { Card, Chip } from "./ui";
import * as api from "../lib/api";

const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

/** Load Razorpay's script once, resolving if it is already present. */
function loadCheckout() {
  if (window.Razorpay) return Promise.resolve(true);
  return new Promise((resolve) => {
    const existing = document.querySelector(`script[src="${CHECKOUT_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(Boolean(window.Razorpay)));
      existing.addEventListener("error", () => resolve(false));
      return;
    }
    const s = document.createElement("script");
    s.src = CHECKOUT_SRC;
    s.onload = () => resolve(Boolean(window.Razorpay));
    s.onerror = () => resolve(false);
    document.body.appendChild(s);
  });
}

function money(cents, currency) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "INR",
      maximumFractionDigits: 2,
    }).format((cents || 0) / 100);
  } catch {
    // An unknown ISO code throws rather than guessing a symbol; showing
    // the code beside the figure is correct and unambiguous.
    return `${currency || ""} ${((cents || 0) / 100).toFixed(2)}`.trim();
  }
}

export default function PlanCheckout({ currentPlan, onPaid }) {
  const [plans, setPlans] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .getPublicPlans()
      .then((data) => {
        if (!cancelled) setPlans(data);
      })
      .catch(() => {
        if (!cancelled) setPlans([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const buy = useCallback(
    async (planName) => {
      setBusy(planName);
      setError("");
      setDone("");
      try {
        const order = await api.createCheckout(planName);

        // Mock mode: the server made a local order and no money can move.
        // Say so rather than opening a checkout that cannot work.
        if (order.mock) {
          setError(
            "This deployment is in payment test mode, so no real payment can be taken. " +
              "Ask your administrator to configure Razorpay."
          );
          return;
        }

        const ready = await loadCheckout();
        if (!ready) {
          setError("Couldn't load the payment window. Check your connection and try again.");
          return;
        }

        const rzp = new window.Razorpay({
          key: order.key_id,
          order_id: order.order_id,
          amount: order.amount_cents,
          currency: order.currency,
          name: order.org_name || "MediCOR AI",
          description: `${order.plan_name} plan`,
          prefill: order.prefill_email ? { email: order.prefill_email } : undefined,
          handler: async (res) => {
            try {
              const out = await api.verifyCheckout({
                razorpay_order_id: res.razorpay_order_id,
                razorpay_payment_id: res.razorpay_payment_id,
                razorpay_signature: res.razorpay_signature,
              });
              setDone(out.plan_name || planName);
              if (onPaid) onPaid();
            } catch (err) {
              // The money may well have left their account -- the webhook
              // is the authoritative path and will still grant the plan.
              // Say that rather than implying the payment failed.
              setError(
                err?.message ||
                  "We couldn't confirm that payment here. If it left your account it " +
                    "will still be applied — check back shortly before trying again."
              );
            } finally {
              setBusy("");
            }
          },
          modal: {
            // Closing the sheet is an ordinary decision, not an error.
            ondismiss: () => setBusy(""),
          },
        });
        rzp.open();
        return; // `busy` is cleared by the handler or by ondismiss.
      } catch (err) {
        setError(err?.message || "Couldn't start that payment. Please try again.");
      }
      setBusy("");
    },
    [onPaid]
  );

  if (plans === null) {
    return (
      <div className="mt-4 h-24 animate-pulse rounded-xl border border-outline-variant bg-surface-container" />
    );
  }
  if (plans.length === 0) {
    // No catalogue means nothing can be bought. Point at a person rather
    // than showing an empty grid with a Pay button.
    return null;
  }

  return (
    <div className="mt-6 border-t border-outline-variant pt-6">
      <h3 className="text-sm font-bold text-on-surface">Change plan</h3>
      <p className="mt-1 text-sm text-on-surface-variant">
        Pay for a month up front. Your plan changes as soon as the payment clears.
      </p>

      {done && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-success-bg px-3 py-2.5 text-sm font-medium text-success">
          <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
          Payment received — your clinic is now on {done}.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 flex items-start gap-2 rounded-lg bg-error-bg px-3 py-2.5 text-sm text-error">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {plans.map((p) => {
          const isCurrent = currentPlan && p.name === currentPlan;
          return (
            <Card key={p.name} className="flex flex-col p-4">
              <div className="flex items-center gap-2">
                <h4 className="font-display text-sm font-bold text-on-surface">{p.name}</h4>
                {isCurrent && <Chip tone="success">Current</Chip>}
              </div>
              <p className="mt-1 font-display text-xl font-extrabold text-on-surface">
                {money(p.monthly_price_cents, p.currency)}
                <span className="text-xs font-medium text-on-surface-variant">/mo</span>
              </p>
              <p className="mt-1 flex-1 text-xs text-on-surface-variant">
                {(p.voice_minutes_limit || 0).toLocaleString()} minutes
                {p.ehr_access ? " · EHR" : ""}
                {p.knowledge_base_access ? " · Knowledge base" : ""}
              </p>
              <button
                onClick={() => buy(p.name)}
                disabled={Boolean(busy) || isCurrent}
                className="focus-ring mt-3 inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-accent-contrast hover:bg-accent-hover disabled:opacity-50"
              >
                {busy === p.name ? (
                  <>
                    <Loader2 size={14} className="animate-spin" /> Opening…
                  </>
                ) : isCurrent ? (
                  "Current plan"
                ) : (
                  <>
                    <CreditCard size={14} /> Pay {money(p.monthly_price_cents, p.currency)}
                  </>
                )}
              </button>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
