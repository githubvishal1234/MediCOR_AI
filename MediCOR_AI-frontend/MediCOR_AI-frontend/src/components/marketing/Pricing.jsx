/**
 * Public pricing, read from the plan catalogue rather than asserted.
 *
 * This section used to hold its own price list, and every figure in it
 * had drifted from the catalogue a super admin maintains and the platform
 * bills against: $199 and $499 against a real $99 / $299 / $799, "500
 * minutes" against 1,000, a plan named "Professional" that does not
 * exist, and "1 EHR integration" listed on the one plan whose
 * `ehr_access` is False. A visitor was being quoted a price nobody had
 * set. `GET /public/plans` is now the single source, and this file only
 * formats it.
 *
 * Three rules follow from that, and they are the whole design:
 *
 *   - **Nothing is invented when the catalogue cannot be read.** No
 *     placeholder prices, no "from $X" - the section says plainly that
 *     pricing could not be loaded and points at a person. A wrong price
 *     on a public page is worse than no price.
 *   - **Every feature line is derived from a real column.** Minutes,
 *     seats, patient records, EHR and knowledge-base access all come off
 *     the plan. Nothing here can say something the platform will not do.
 *   - **No "Best Value" badge and no "Most popular".** Both are claims,
 *     and the catalogue carries no column that would support either.
 */

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { useNavigate } from "react-router-dom";
import * as api from "../../lib/api";

function formatPrice(cents, currency) {
  // Was a hardcoded "$" while the platform bills in rupees, so every
  // visitor was quoted the right number in the wrong money. The
  // catalogue now serves its currency and Intl does the formatting --
  // it throws on an unknown ISO code rather than guessing a symbol.
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "INR",
      maximumFractionDigits: 2,
      minimumFractionDigits: Number.isInteger((cents || 0) / 100) ? 0 : 2,
    }).format((cents || 0) / 100);
  } catch {
    return `${currency || ""} ${((cents || 0) / 100).toFixed(2)}`.trim();
  }
}

/**
 * The plan's own columns, phrased for a reader. Returned in the order a
 * clinic would ask about them: how much talking, how many people, how
 * many patients, then what it connects to.
 */
function featuresOf(plan) {
  const lines = [
    `${(plan.voice_minutes_limit || 0).toLocaleString()} voice minutes each month`,
    plan.user_limit == null
      ? "Unlimited staff accounts"
      : `Up to ${plan.user_limit.toLocaleString()} staff accounts`,
    plan.patient_limit == null
      ? "Unlimited patient records"
      : `Up to ${plan.patient_limit.toLocaleString()} patient records`,
  ];
  if (plan.knowledge_base_access) lines.push("Knowledge base the agent answers from");
  if (plan.ehr_access) lines.push("EHR integration");
  return lines;
}

export default function Pricing() {
  const navigate = useNavigate();
  const [plans, setPlans] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getPublicPlans()
      .then((data) => {
        if (!cancelled) setPlans(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loading = plans === null && !failed;

  return (
    <section id="pricing" className="mx-auto max-w-(--container-max) px-5 py-24 sm:px-8">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="font-display text-3xl font-bold text-on-surface sm:text-[40px]">
          Transparent Pricing for Clinics of All Sizes
        </h2>
        <p className="mt-4 text-lg text-on-surface-variant">
          Choose the plan that fits your practice's volume and integration needs.
        </p>
      </div>

      {loading && (
        <div className="mt-14 grid gap-6 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-96 animate-pulse rounded-2xl border border-outline-variant bg-surface-container"
            />
          ))}
        </div>
      )}

      {/* Deliberately not a price. A pricing page that guesses is worse
          than one that admits it cannot reach the catalogue. */}
      {(failed || (plans && plans.length === 0)) && (
        <div className="mt-14 rounded-2xl border border-outline-variant bg-surface-lowest p-10 text-center">
          <p className="font-display text-lg font-bold text-on-surface">
            {failed ? "Pricing couldn't be loaded just now" : "Plans are being updated"}
          </p>
          <p className="mx-auto mt-2 max-w-md text-sm text-on-surface-variant">
            Get in touch and we'll talk you through the options and quote your
            practice directly.
          </p>
          <button
            onClick={() => navigate("/app")}
            className="bg-accent focus-ring mt-6 rounded-lg px-6 py-3 text-sm font-semibold text-accent-contrast hover:bg-accent-hover"
          >
            Talk to us
          </button>
        </div>
      )}

      {plans && plans.length > 0 && (
        <div className="mt-14 grid gap-6 lg:grid-cols-3">
          {plans.map((plan) => (
            <div
              key={plan.name}
              className="relative rounded-2xl border border-outline-variant bg-surface-lowest p-8 shadow-(--shadow-card)"
            >
              <h3 className="font-display text-lg font-bold text-on-surface">{plan.name}</h3>
              {plan.description && (
                <p className="mt-1 text-sm text-on-surface-variant">{plan.description}</p>
              )}
              <p className="mt-6 font-display text-4xl font-extrabold text-on-surface">
                {formatPrice(plan.monthly_price_cents, plan.currency)}
                <span className="text-base font-medium text-on-surface-variant">/mo</span>
              </p>

              <ul className="mt-6 space-y-3">
                {featuresOf(plan).map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-sm text-on-surface">
                    <Check size={16} className="text-accent mt-0.5 shrink-0" />
                    {f}
                  </li>
                ))}
              </ul>

              <button
                onClick={() => navigate("/app")}
                className="focus-ring mt-8 w-full rounded-lg border border-on-surface py-3 text-sm font-semibold text-on-surface transition hover:bg-surface-container"
              >
                Get started
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
