/**
 * Transient feedback.
 *
 * The one genuine hole in the component layer: every confirmation in this
 * application was an inline banner, which only works if the user happens
 * to be looking at the place it appears. Realtime events had it worse —
 * a call starting or an appointment being booked updated a panel silently,
 * so anyone on another page learned nothing.
 *
 * The API shape (provider + hook, a queue, per-toast timeouts, an optional
 * action) follows the pattern the well-tested libraries converged on. None
 * of their code is used: every one installs through shadcn and pulls in
 * Radix, CVA, tailwind-merge and clsx, and this project deliberately runs
 * on four runtime dependencies. Built against the existing tokens it is
 * this file, and it themes for dark mode for free.
 *
 * WHEN TO USE WHICH — this is not a style choice:
 *
 *   Toast          something happened ELSEWHERE, or is done and needs no
 *                  action. A realtime call.started. A save that worked.
 *   InlineAlert    something the user must act on, shown where they act.
 *                  A rejected form. A failed export.
 *
 * Never both for one event.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

const ToastContext = createContext(null);

const DEFAULT_DURATION = 4000;
// Three is the point at which a stack stops being readable. Older ones are
// dropped rather than queued: a backlog of nine that plays out over half a
// minute is noise, and by the time the ninth appears it is describing
// something the user has scrolled past.
const MAX_VISIBLE = 3;

const TONES = {
  success: { icon: CheckCircle2, className: "text-success" },
  error: { icon: AlertTriangle, className: "text-error" },
  warning: { icon: AlertTriangle, className: "text-warning" },
  info: { icon: Info, className: "text-info" },
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  // Timers are kept out of state: they are not rendered, and putting them
  // in state would restart every countdown on each unrelated re-render.
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const schedule = useCallback(
    (id, duration) => {
      if (!duration) return; // 0 / null means "stays until dismissed"
      timers.current.set(
        id,
        setTimeout(() => {
          timers.current.delete(id);
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, duration)
      );
    },
    []
  );

  const push = useCallback(
    (tone, title, { description, action, duration } = {}) => {
      const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      // An error that auto-dismisses is an error the user can miss, and
      // then it may as well not have been raised. Errors persist until
      // acknowledged; everything else clears itself.
      const ms = duration === undefined ? (tone === "error" ? 0 : DEFAULT_DURATION) : duration;
      setToasts((prev) => [...prev, { id, tone, title, description, action }].slice(-MAX_VISIBLE));
      schedule(id, ms);
      return id;
    },
    [schedule]
  );

  const api = useMemo(
    () => ({
      success: (title, opts) => push("success", title, opts),
      error: (title, opts) => push("error", title, opts),
      warning: (title, opts) => push("warning", title, opts),
      info: (title, opts) => push("info", title, opts),
      dismiss,
    }),
    [push, dismiss]
  );

  // Clear every outstanding timer on unmount, or a fired callback would
  // call setState on a gone component.
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach(clearTimeout);
      map.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/**
 * A11Y: `aria-live="polite"` and `role="status"`, never `alert` — and the
 * viewport must NOT take focus. A toast that grabs focus mid-typing is
 * worse than one that is missed, and WCAG treats an unexpected focus
 * change as a failure in its own right. Anything a user has to act on
 * belongs in an InlineAlert next to the control, not here.
 */
function ToastViewport({ toasts, onDismiss }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed inset-x-0 top-0 z-[60] flex flex-col items-center gap-2 px-4 py-4 sm:inset-x-auto sm:bottom-0 sm:right-0 sm:top-auto sm:items-end sm:p-6"
    >
      {toasts.map((t) => {
        const tone = TONES[t.tone] ?? TONES.info;
        const Icon = tone.icon;
        return (
          <div
            key={t.id}
            // pointer-events re-enabled per toast: the viewport spans the
            // screen edge and must not swallow clicks meant for the page.
            className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border border-outline-variant bg-surface-lowest p-3.5 shadow-(--shadow-overlay)"
          >
            <Icon size={17} className={`mt-0.5 shrink-0 ${tone.className}`} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-on-surface">{t.title}</p>
              {t.description && (
                <p className="mt-0.5 text-sm text-on-surface-variant">{t.description}</p>
              )}
              {t.action && (
                <button
                  onClick={() => {
                    t.action.onClick?.();
                    onDismiss(t.id);
                  }}
                  className="focus-ring mt-2 rounded text-sm font-semibold text-accent hover:underline"
                >
                  {t.action.label}
                </button>
              )}
            </div>
            <button
              onClick={() => onDismiss(t.id)}
              aria-label={`Dismiss: ${t.title}`}
              className="focus-ring -mr-1 -mt-1 shrink-0 rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
            >
              <X size={15} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Returns the toast API, or a no-op stand-in when no provider is mounted.
 *
 * Deliberately does not throw, unlike `useRealtime`. A toast is feedback
 * about something that already succeeded; a missing provider must not turn
 * a working save into a crashed page. The SSR smoke render and any surface
 * outside AppShell both hit this path.
 */
const NOOP = {
  success: () => {},
  error: () => {},
  warning: () => {},
  info: () => {},
  dismiss: () => {},
};

export function useToast() {
  return useContext(ToastContext) ?? NOOP;
}
