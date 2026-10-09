/**
 * Tabs.
 *
 * Replaces four hand-rolled implementations that had drifted into four
 * different appearances: bordered segmented pills (CallLogs), an underline
 * (PatientProfile), and two more in ClinicProfile and AdminSupport. None
 * of them implemented the keyboard contract, so a tab set could only be
 * operated by pointer.
 *
 * Deliberately CONTROLLED — `value` + `onChange`, with no internal state
 * fallback. Three of the four call sites keep their tab in the query
 * string (`/app/calls?tab=quality`, `/app/clinic?tab=doctors`), which is
 * what makes a flagged call or a specific settings section linkable, and
 * what the dashboard's setup checklist deep-links to. A component holding
 * its own state would quietly break those links.
 */

import { useRef } from "react";

const VARIANTS = {
  // Peer views of the same data — Transcripts vs Quality.
  segmented: {
    list: "inline-flex w-fit max-w-full overflow-x-auto rounded-lg border border-outline-variant p-0.5",
    tab: "rounded-md px-4 py-1.5 whitespace-nowrap",
    active: "bg-surface-container text-on-surface",
    idle: "text-on-surface-variant hover:text-on-surface",
  },
  // Sections of one record — a patient's Overview / Calls / Appointments.
  underline: {
    list: "flex gap-6 overflow-x-auto border-b border-outline-variant",
    tab: "border-b-2 px-1 pb-3 whitespace-nowrap",
    active: "border-accent text-on-surface",
    idle: "border-transparent text-on-surface-variant hover:text-on-surface",
  },
};

/**
 * @param {{id: string, label: React.ReactNode}[]} items
 */
export function Tabs({ items, value, onChange, variant = "segmented", label = "Sections", className = "" }) {
  const style = VARIANTS[variant] ?? VARIANTS.segmented;
  const refs = useRef({});

  // A11Y: arrow keys move between tabs and activate as they go, which is
  // the expected behaviour for a tablist whose panels are already loaded.
  // Home/End jump to the ends. Without this the whole set is pointer-only.
  function onKeyDown(e) {
    const idx = items.findIndex((i) => i.id === value);
    if (idx === -1) return;
    let next = null;
    if (e.key === "ArrowRight") next = (idx + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    if (next === null) return;
    e.preventDefault();
    const id = items[next].id;
    onChange(id);
    refs.current[id]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={`${style.list} text-sm font-semibold ${className}`}
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            ref={(el) => { refs.current[item.id] = el; }}
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={selected}
            aria-controls={`panel-${item.id}`}
            // Roving tabindex: one stop for the whole set, then arrows
            // within it. Eleven tabs should not mean eleven Tab presses.
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.id)}
            className={`focus-ring transition-colors ${style.tab} ${selected ? style.active : style.idle}`}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

/** The panel a tab controls. Pairs with the ids Tabs generates. */
export function TabPanel({ id, value, children, className = "" }) {
  if (id !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`panel-${id}`}
      aria-labelledby={`tab-${id}`}
      tabIndex={0}
      className={`focus-ring ${className}`}
    >
      {children}
    </div>
  );
}
