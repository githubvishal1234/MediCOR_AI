import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Circle, ChevronRight, X } from "lucide-react";
import { Card } from "./ui";
import * as api from "../lib/api";

// Where each outstanding step is actually fixed. `phone_number` has no
// destination on purpose — only a super admin can assign a number, so
// sending the clinic to a page they can't act on would be a dead end.
//
// These all pointed at /app/knowledge-base, which was where QW-1 was
// *proposed* to live. It shipped as its own /app/clinic page instead, so
// every link here sent a new clinic to a page that cannot fix the step
// it was told to fix.
const STEP_LINKS = {
  timezone: "/app/clinic",
  clinic_hours: "/app/clinic",
  contact: "/app/clinic",
  services: "/app/clinic?tab=services",
  doctors: "/app/clinic?tab=doctors",
  phone_number: null,
};

const DISMISS_KEY = "medvoice-onboarding-dismissed";

/**
 * What a newly-created clinic still has to set up.
 *
 * Registration deliberately creates an empty clinic — no placeholder
 * doctors, services or fees, because the voice agent reads those fields
 * out to patients. The cost of that choice is that a new clinic's agent
 * knows nothing until someone fills it in, so the gap is shown here
 * rather than left silent.
 *
 * Hides itself once every step is done, so an established clinic never
 * sees it.
 */
export default function OnboardingChecklist() {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return window.localStorage.getItem(DISMISS_KEY) === "true";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let cancelled = false;
    api
      .getOnboardingStatus()
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        // A checklist is not worth an error state on the dashboard.
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function dismiss() {
    setDismissed(true);
    try {
      window.localStorage.setItem(DISMISS_KEY, "true");
    } catch {
      // Private mode / blocked storage — hiding for this session is fine.
    }
  }

  if (!status || status.complete || dismissed) return null;

  const remaining = status.steps.filter((s) => !s.done).length;
  const done = status.steps.length - remaining;

  return (
    <Card className="mb-6 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-display text-base font-bold text-on-surface">
            Finish setting up your AI receptionist
          </p>
          <p className="mt-1 text-sm text-on-surface-variant">
            {done} of {status.steps.length} done. Until these are filled in, your
            receptionist has to tell callers it doesn't know.
          </p>
        </div>
        <button
          onClick={dismiss}
          aria-label="Hide setup checklist"
          className="focus-ring rounded-lg p-1 text-on-surface-variant hover:bg-surface-container"
        >
          <X size={16} />
        </button>
      </div>

      <ul className="mt-4 divide-y divide-outline-variant border-t border-outline-variant">
        {status.steps.map((step) => {
          const link = STEP_LINKS[step.key];
          const actionable = !step.done && link;
          const Row = actionable ? "button" : "div";
          return (
            <li key={step.key}>
              <Row
                {...(actionable ? { onClick: () => navigate(link), type: "button" } : {})}
                className={`flex w-full items-center gap-3 py-3 text-left ${
                  actionable ? "focus-ring rounded-md hover:bg-surface-low" : ""
                }`}
              >
                {step.done ? (
                  <CheckCircle2 size={17} className="shrink-0 text-success" />
                ) : (
                  <Circle size={17} className="shrink-0 text-on-surface-variant" />
                )}
                <div className="min-w-0 flex-1">
                  <p
                    className={`text-sm font-semibold ${
                      step.done ? "text-on-surface-variant line-through" : "text-on-surface"
                    }`}
                  >
                    {step.label}
                  </p>
                  {!step.done && (
                    <p className="text-xs text-on-surface-variant">{step.detail}</p>
                  )}
                </div>
                {actionable && (
                  <ChevronRight size={16} className="shrink-0 text-on-surface-variant" />
                )}
              </Row>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
