/**
 * The shared component layer.
 *
 * Two rules hold this together, and both exist because they were broken:
 *
 * 1. **No hex literals.** The brand colour used to be an inline hex
 *    right here in `Button`, and in ~100 more places across the app.
 *    An inline style beats a CSS
 *    variable, so dark mode could not re-theme any of it and a rebrand
 *    meant editing 100 files. Everything now resolves through the
 *    `--color-*` tokens in index.css.
 *
 * 2. **Forms live here.** There were 66 hand-rolled input class strings
 *    and 11 local `const inputClass` re-declarations, which is why no two
 *    forms had quite the same focus ring or error treatment. `Input`,
 *    `Select`, `Textarea`, `Field` and `FieldError` replace all of them.
 *
 * Accessibility notes that are load-bearing rather than decorative are
 * marked A11Y at the point they apply.
 */

import { cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, ArrowUpDown, Eye, EyeOff, Loader2, X } from "lucide-react";

// The primitives added in the Phase 1 design-system pass live in their own
// modules to keep this file readable, and are re-exported here so a page's
// import list stays one line.
//
// `useToast` is deliberately NOT re-exported: it is a hook, and a module
// that exports both components and non-components loses React Fast
// Refresh (oxlint's react/only-export-components). Import it from
// "../components/toast" directly — one extra line in the few files that
// raise a toast, in exchange for this file staying components-only.
export { ToastProvider } from "./toast";
export { Tabs, TabPanel } from "./tabs";
export { Menu, MenuItem, Tooltip } from "./menu";
export { BarChart, Sparkline, ChartEmpty, ChartSkeleton } from "./chart";

/* ------------------------------------------------------------------ */
/* Surfaces                                                            */
/* ------------------------------------------------------------------ */

const ELEVATION = {
  flat: "",
  card: "shadow-(--shadow-card)",
  raised: "shadow-(--shadow-raised)",
  overlay: "shadow-(--shadow-overlay)",
};

/**
 * Card — the default container.
 *
 * `elevation` exists because there used to be exactly one shadow value in
 * the app: every card sat at the same depth, so nothing could read as
 * foreground and a dialog looked no closer than the list behind it.
 */
export function Card({
  className = "",
  children,
  hoverable = false,
  elevation = "card",
  as: Tag = "div",
  ...props
}) {
  return (
    <Tag
      className={`rounded-xl border border-outline-variant bg-surface-lowest transition-shadow duration-200 ${
        ELEVATION[elevation] ?? ELEVATION.card
      } ${hoverable ? "hover:shadow-(--shadow-raised)" : ""} ${className}`}
      {...props}
    >
      {children}
    </Tag>
  );
}

/** SectionHeader — one heading treatment instead of a bespoke h2 per page. */
export function SectionHeader({ title, detail, action, className = "" }) {
  return (
    <div className={`flex flex-wrap items-start justify-between gap-3 ${className}`}>
      <div className="min-w-0">
        <h2 className="font-display text-lg font-bold text-on-surface">{title}</h2>
        {detail && <p className="mt-0.5 text-sm text-on-surface-variant">{detail}</p>}
      </div>
      {action}
    </div>
  );
}

const STAT_TONE = { warning: "text-warning", error: "text-error" };

/**
 * One reading in a stat strip: label, figure, and what the figure counts.
 *
 * Cells go inside a `grid gap-px bg-outline-variant` wrapper in a Card, so
 * the 1px gap draws the rules between them. That replaced rows of floating
 * stat cards on the Dashboard, Call Quality and Analytics, which had each
 * drawn their own copy.
 *
 * `tone` colours the figure only when it asks for action (missed calls,
 * calls needing review). A normal reading stays ink.
 */
export function StatCell({ icon: Icon, label, value, hint, tone, loading = false, className = "" }) {
  return (
    <div className={`bg-surface-lowest px-5 py-4 sm:px-6 sm:py-5 ${className}`}>
      <p className="flex items-center gap-1.5 text-sm font-medium text-on-surface-variant">
        {Icon && <Icon size={14} aria-hidden="true" />}
        {label}
      </p>
      {loading ? (
        <Skeleton className="mt-2 h-9 w-20" />
      ) : (
        <p className={`mt-1.5 text-3xl font-semibold tracking-tight tabular-nums ${STAT_TONE[tone] ?? "text-on-surface"}`}>
          {value}
        </p>
      )}
      {hint && <p className="mt-0.5 text-xs text-on-surface-variant">{hint}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Chips                                                               */
/* ------------------------------------------------------------------ */

const TONES = {
  success: "bg-success-bg text-success",
  warning: "bg-warning-bg text-warning",
  info: "bg-info-bg text-info",
  neutral: "bg-surface-container text-on-surface-variant",
  error: "bg-error-bg text-error",
  accent: "bg-accent-subtle text-accent",
  // A call happening right now. Deliberately its own tone rather than
  // `error` — an active call is the product working, not a fault.
  live: "bg-live-bg text-live",
};

export function Chip({ tone = "neutral", children, className = "", ...props }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap ${
        TONES[tone] ?? TONES.neutral
      } ${className}`}
      {...props}
    >
      {children}
    </span>
  );
}

/** A pulsing dot for live/active state. Respects prefers-reduced-motion via index.css. */
export function LiveDot({ className = "" }) {
  return (
    <span className={`relative flex h-2 w-2 ${className}`} aria-hidden="true">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-live opacity-70" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-live" />
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Buttons                                                             */
/* ------------------------------------------------------------------ */

const VARIANTS = {
  primary: "bg-accent text-accent-contrast hover:bg-accent-hover",
  secondary: "border border-outline text-on-surface hover:bg-surface-container",
  outline: "border border-outline-variant text-on-surface hover:bg-surface-container",
  ghost: "text-on-surface-variant hover:bg-surface-container",
  danger: "bg-error text-error-contrast hover:bg-error-hover",
};

const SIZES = {
  sm: "px-3 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2.5 text-sm gap-2",
  lg: "px-5 py-3 text-sm gap-2",
  // A square target for a lone icon. ~30 of these were hand-rolled across
  // the app as `rounded-md p-2`, which is 32px — under the 44px touch
  // minimum. 40px plus the 2px focus ring clears it, and having one size
  // means the next one is not another guess.
  icon: "h-10 w-10 p-0",
};

/**
 * Button.
 *
 * `loading` exists because every page hand-rolled the same pattern —
 * `disabled={busy}` plus a ternary swapping the label to "Saving…" — and
 * each one worded and spaced it slightly differently.
 *
 * A11Y: a loading button stays disabled *and* announces itself with
 * `aria-busy`, so the state is not conveyed by a spinner alone.
 */
export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled = false,
  className = "",
  children,
  ...props
}) {
  return (
    <button
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`focus-ring inline-flex items-center justify-center rounded-lg font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${
        VARIANTS[variant] ?? VARIANTS.primary
      } ${SIZES[size] ?? SIZES.md} ${className}`}
      {...props}
    >
      {loading && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}

/** A text button that reads as a link. Was an inline-styled hex in five places. */
export function LinkButton({ className = "", children, ...props }) {
  return (
    <button
      className={`focus-ring rounded text-sm font-semibold text-accent hover:text-accent-hover hover:underline ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Forms                                                               */
/* ------------------------------------------------------------------ */

/**
 * The one control skin.
 *
 * Two things here are accessibility fixes rather than taste:
 *
 * `border-border-control` replaces `border-outline-variant`. That token is
 * #e2e8f0 — 1.23:1 on white — and a control's border is what identifies
 * it as a control, which WCAG 1.4.11 requires to reach 3:1. Every input
 * in the application failed that. Dividers and card edges keep
 * `outline-variant`; they are decorative and the rule does not apply.
 * See the token comment in index.css before merging the two back.
 *
 * `text-base sm:text-sm` is not a size preference. Mobile Safari zooms
 * the viewport when a focused control's text is under 16px, which shifts
 * the whole layout mid-typing and does not shift back. 14px everywhere
 * was doing exactly that on every form in the app.
 */
const CONTROL =
  "focus-ring w-full rounded-lg border border-border-control bg-surface-lowest px-3 py-2.5 text-base sm:text-sm text-on-surface " +
  "placeholder:text-on-surface-variant/70 disabled:cursor-not-allowed disabled:bg-surface-low disabled:opacity-70";

const CONTROL_INVALID = "border-error focus-visible:outline-error";

/**
 * Field — label + control + error, wired together.
 *
 * A11Y: this is the reason the component exists rather than a bare class
 * string. It generates one id and uses it for `htmlFor`, `aria-describedby`
 * and `aria-invalid`, so a validation message is announced to a screen
 * reader instead of only turning the border red. Before this the app had
 * zero `aria-invalid` and zero `aria-describedby` anywhere.
 */
export function Field({ label, error, hint, required = false, children, className = "" }) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  // `invalid` is injected only when this Field carries an error, so a
  // control that decides its own validity (one already showing a server
  // rejection, say) is not silently reset to valid by the clone below.
  const controlProps = error ? { id, describedBy, invalid: true } : { id, describedBy };

  return (
    <div className={className}>
      {label && (
        <label htmlFor={id} className="block text-xs font-semibold text-on-surface-variant">
          {label}
          {required && (
            <span className="text-error" aria-hidden="true">
              {" *"}
            </span>
          )}
        </label>
      )}
      <div className={label ? "mt-1" : ""}>
        {/* Two call shapes, because both read naturally in different
            places. A single control is written plainly —
            `<Field label="Email"><Input …/></Field>` — and gets the id,
            `aria-describedby` and `aria-invalid` grafted on. Anything
            more (a radio group, two controls sharing one label) takes a
            function and wires them itself. */}
        {typeof children === "function"
          ? children(controlProps)
          : isValidElement(children)
          ? cloneElement(children, controlProps)
          : children}
      </div>
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-xs font-medium text-error" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-on-surface-variant">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function Input({ invalid = false, describedBy, className = "", ...props }) {
  return (
    <input
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      className={`${CONTROL} ${invalid ? CONTROL_INVALID : ""} ${className}`}
      {...props}
    />
  );
}

export function Textarea({ invalid = false, describedBy, className = "", ...props }) {
  return (
    <textarea
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      className={`${CONTROL} ${invalid ? CONTROL_INVALID : ""} ${className}`}
      {...props}
    />
  );
}

/**
 * A password box with a show/hide control.
 *
 * Every sign-in surface in the app had a bare `type="password"` and no
 * way to check what was typed, which is the single most common reason a
 * correct password gets reported as wrong. The toggle is a real button
 * with an accessible name, and the type flips rather than any value
 * being echoed anywhere.
 */
export function PasswordInput({ invalid = false, describedBy, className = "", ...props }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <Input
        {...props}
        type={shown ? "text" : "password"}
        invalid={invalid}
        describedBy={describedBy}
        className={`pr-10 ${className}`}
      />
      <button
        type="button"
        onClick={() => setShown((v) => !v)}
        // Not in the tab order: it is a convenience, and stopping between
        // the password box and the submit button on every sign-in is a
        // worse trade than reaching for it with a pointer.
        tabIndex={-1}
        aria-label={shown ? "Hide password" : "Show password"}
        className="focus-ring absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
      >
        {shown ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

export function Select({ invalid = false, describedBy, className = "", children, ...props }) {
  return (
    <select
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      className={`${CONTROL} ${invalid ? CONTROL_INVALID : ""} ${className}`}
      {...props}
    >
      {children}
    </select>
  );
}

/** A standalone banner for form/page-level failures. Was hand-written per page. */
export function InlineAlert({ tone = "error", children, className = "" }) {
  const styles = {
    error: "bg-error-bg text-error",
    warning: "bg-warning-bg text-warning",
    success: "bg-success-bg text-success",
    info: "bg-info-bg text-info",
  };
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-lg px-3 py-2 text-sm ${styles[tone] ?? styles.error} ${className}`}
    >
      {children}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Tables                                                              */
/* ------------------------------------------------------------------ */

/**
 * Table — a real <table>, always inside a horizontal scroll container.
 *
 * The overflow wrapper is the point. The call-log table was a bare
 * `<table className="w-full">` inside a Card, so on a narrow screen it
 * either crushed its columns or pushed the page sideways. Wrapping is
 * easy to forget per page and impossible to forget here.
 */
export function Table({ children, className = "" }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={`w-full text-sm ${className}`}>{children}</table>
    </div>
  );
}

export function THead({ children, className = "" }) {
  return (
    <thead
      // Sentence case, not ALL-CAPS: capitals are slower to read and
      // shout the least important text in the table.
      className={`bg-surface-low text-left text-xs font-semibold text-on-surface-variant ${className}`}
    >
      {children}
    </thead>
  );
}

export function TH({ children, className = "", ...props }) {
  return (
    <th scope="col" className={`px-4 py-3 font-semibold ${className}`} {...props}>
      {children}
    </th>
  );
}

/**
 * A column header that sorts.
 *
 * A11Y: `aria-sort` on the <th> is what a screen reader announces — the
 * caret alone says nothing, and it must read "none" rather than be absent
 * on the columns that are sortable but not currently sorted.
 *
 * Sorting is client-side over rows already loaded. That is a deliberate
 * limit: no list endpoint here takes a sort parameter, and inventing one
 * in the query string would send a parameter the backend ignores, which
 * looks like a broken control.
 */
export function SortableTH({
  children,
  sortKey,
  active = null,
  direction = "asc",
  onSort,
  className = "",
  ...props
}) {
  const isActive = active === sortKey;
  return (
    <th
      scope="col"
      aria-sort={isActive ? (direction === "asc" ? "ascending" : "descending") : "none"}
      className={`px-4 py-3 font-semibold ${className}`}
      {...props}
    >
      <button
        type="button"
        onClick={() => onSort?.(sortKey, isActive && direction === "asc" ? "desc" : "asc")}
        className="focus-ring group inline-flex items-center gap-1 rounded font-semibold hover:text-on-surface"
      >
        {children}
        <ArrowUpDown
          size={12}
          aria-hidden="true"
          className={isActive ? "text-accent" : "text-on-surface-variant/40 group-hover:text-on-surface-variant"}
        />
      </button>
    </th>
  );
}

export function TBody({ children, className = "" }) {
  return <tbody className={`divide-y divide-outline-variant ${className}`}>{children}</tbody>;
}

export function TR({ children, selected = false, onClick, className = "", ...props }) {
  const interactive = Boolean(onClick);
  return (
    <tr
      onClick={onClick}
      // A11Y: a clickable row is only reachable by keyboard if it says it
      // is one. The call log made rows clickable with no tabIndex and no
      // key handler, so the entire list was mouse-only.
      tabIndex={interactive ? 0 : undefined}
      role={interactive ? "button" : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick(e);
              }
            }
          : undefined
      }
      className={`${interactive ? "focus-ring cursor-pointer" : ""} transition-colors ${
        selected ? "bg-surface-container" : interactive ? "hover:bg-surface-low" : ""
      } ${className}`}
      {...props}
    >
      {children}
    </tr>
  );
}

export function TD({ children, className = "", ...props }) {
  return (
    <td className={`px-4 py-3.5 align-middle ${className}`} {...props}>
      {children}
    </td>
  );
}

/* ------------------------------------------------------------------ */
/* States                                                              */
/* ------------------------------------------------------------------ */

export function EmptyState({ icon: Icon, title, detail, action, className = "" }) {
  return (
    <div className={`flex flex-col items-center justify-center px-6 py-14 text-center ${className}`}>
      {Icon && (
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-surface-container">
          <Icon size={20} className="text-on-surface-variant" aria-hidden="true" />
        </div>
      )}
      <p className="text-sm font-semibold text-on-surface">{title}</p>
      {detail && <p className="mt-1 max-w-xs text-sm text-on-surface-variant">{detail}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", detail, onRetry, className = "" }) {
  return (
    <div
      role="alert"
      className={`flex flex-col items-center justify-center px-6 py-14 text-center ${className}`}
    >
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-error-bg">
        <AlertTriangle size={20} className="text-error" aria-hidden="true" />
      </div>
      <p className="text-sm font-semibold text-on-surface">{title}</p>
      {detail && <p className="mt-1 max-w-xs text-sm text-on-surface-variant">{detail}</p>}
      {onRetry && (
        <LinkButton onClick={onRetry} className="mt-4">
          Try again
        </LinkButton>
      )}
    </div>
  );
}

export function Skeleton({ className = "", style }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-surface-container ${className}`}
      style={style}
      aria-hidden="true"
    />
  );
}

/**
 * Three assembled loading shapes.
 *
 * Every page was building these by hand, so no two agreed on row height or
 * count — and six admin pages skipped them entirely, gating their content
 * on `{!loading && …}` and therefore rendering an empty card while
 * fetching. An empty card reads as "no data", which is a different and
 * wrong answer.
 *
 * A11Y: the wrapper carries `role="status"` with an `sr-only` label, so a
 * screen reader is told the region is loading rather than finding it
 * silently empty. The bars themselves stay `aria-hidden`.
 */
function LoadingRegion({ label, children, className = "" }) {
  return (
    <div role="status" aria-live="polite" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

export function SkeletonTable({ rows = 5, cols = 4, label = "Loading table…", className = "" }) {
  return (
    <LoadingRegion label={label} className={`divide-y divide-outline-variant ${className}`}>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 px-4 py-3.5">
          {Array.from({ length: cols }).map((_, c) => (
            // The first column carries the identifying text and reads
            // wider; the rest are uniform. Matching the real proportions
            // is what stops the swap causing a visible jump.
            <Skeleton key={c} className={`h-4 ${c === 0 ? "flex-[2]" : "flex-1"}`} />
          ))}
        </div>
      ))}
    </LoadingRegion>
  );
}

export function SkeletonList({ rows = 4, avatar = true, label = "Loading…", className = "" }) {
  return (
    <LoadingRegion label={label} className={`divide-y divide-outline-variant ${className}`}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3.5">
          {avatar && <Skeleton className="h-10 w-10 shrink-0 rounded-full" />}
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-1/4" />
          </div>
        </div>
      ))}
    </LoadingRegion>
  );
}

export function SkeletonCard({ lines = 3, label = "Loading…", className = "" }) {
  return (
    <LoadingRegion label={label} className={`space-y-3 p-5 ${className}`}>
      <Skeleton className="h-4 w-1/3" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? "w-2/3" : "w-full"}`} />
      ))}
    </LoadingRegion>
  );
}

/* ------------------------------------------------------------------ */
/* Dialogs                                                             */
/* ------------------------------------------------------------------ */

/**
 * Shared dialog behaviour: Escape to close, background scroll lock, focus
 * moved in on open and returned to the trigger on close, and Tab kept
 * inside.
 *
 * None of this existed. `Modal` and `ConfirmDialog` set `aria-modal` and
 * stopped there, so a keyboard user could Tab straight out of an open
 * dialog into the page behind it and operate controls they could not see,
 * and Escape did nothing anywhere in the application.
 */
function useDialog(open, onClose) {
  const ref = useRef(null);
  const restoreTo = useRef(null);

  const close = useCallback(() => onClose?.(), [onClose]);

  useEffect(() => {
    if (!open) return undefined;

    restoreTo.current = document.activeElement;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";

    const node = ref.current;
    const focusable = () =>
      Array.from(
        node?.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );

    // A form opens on its first field, so it is immediately typeable. That
    // used to be "the first control", which in every Modal is the header's
    // close button. Without a field it still falls back to the first
    // control, never skipping ahead to an action button, so a dialog can
    // never open with focus on something like "Cancel appointment".
    const items = focusable();
    (items.find((el) => el.matches("input, select, textarea")) ?? items[0] ?? node)?.focus?.();

    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = overflow;
      restoreTo.current?.focus?.();
    };
  }, [open, close]);

  return ref;
}

export function ConfirmDialog({
  open,
  title,
  detail,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "error",
  busy = false,
  onConfirm,
  onCancel,
  children,
}) {
  const ref = useDialog(open, busy ? undefined : onCancel);
  const titleId = useId();

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel?.()}
    >
      <Card
        ref={ref}
        elevation="overlay"
        className="w-full max-w-sm p-5 outline-none"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <p id={titleId} className="text-sm font-semibold text-on-surface">
          {title}
        </p>
        {detail && <p className="mt-1.5 text-sm text-on-surface-variant">{detail}</p>}
        {children && <div className="mt-3">{children}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "error" ? "danger" : "primary"}
            onClick={onConfirm}
            loading={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </Card>
    </div>
  );
}

/**
 * `sheet` presents the dialog as a bottom sheet below `sm`.
 *
 * A centred box with side gutters wastes the narrowest dimension a phone
 * has, and pushes a long form's actions below the fold. A sheet uses the
 * full width, sits against the bottom edge where the thumb already is, and
 * squares off its lower corners so it reads as attached to the edge rather
 * than floating. Above `sm` it is the ordinary centred dialog.
 *
 * Default true because every dialog in this app is a form or a
 * confirmation. Pass `sheet={false}` for something genuinely small.
 */
export function Modal({ open, title, onClose, children, sheet = true, className = "" }) {
  const ref = useDialog(open, onClose);
  const titleId = useId();

  if (!open) return null;
  return (
    <div
      className={`fixed inset-0 z-50 flex justify-center overflow-y-auto bg-black/50 backdrop-blur-[2px] sm:items-center sm:px-4 sm:py-8 ${
        sheet ? "items-end" : "items-start px-4 py-8"
      }`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}
    >
      <Card
        ref={ref}
        elevation="overlay"
        className={`w-full max-w-lg p-5 outline-none ${
          sheet ? "max-h-[92vh] overflow-y-auto rounded-b-none sm:max-h-none sm:rounded-b-xl" : ""
        } ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="flex items-center justify-between gap-3">
          <p id={titleId} className="font-display text-base font-bold text-on-surface">
            {title}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="focus-ring -mr-2 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </Card>
    </div>
  );
}
