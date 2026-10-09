/**
 * Dropdown menu and tooltip.
 *
 * `Menu` is extracted from the click-outside/Escape logic already working
 * inside AppShell's NotificationBell rather than invented from scratch —
 * that code was correct, it was just trapped in one component. Two things
 * need it: the user menu in the app shell (the avatar is currently a
 * static div, and Sign Out is buried at the bottom of the sidebar, which
 * on mobile means opening the drawer to leave), and per-row overflow
 * actions in tables.
 */

import { cloneElement, useCallback, useEffect, useId, useRef, useState } from "react";

const ALIGN = {
  right: "right-0",
  left: "left-0",
};

export function Menu({ trigger, children, align = "right", label = "Menu", className = "" }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const listRef = useRef(null);
  const triggerRef = useRef(null);
  const id = useId();

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    // Focus goes back to the trigger, or the user is dropped at the top of
    // the document with no idea where they were.
    if (restoreFocus) triggerRef.current?.focus?.();
  }, []);

  useEffect(() => {
    if (!open) return undefined;

    function onPointerDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }

    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
      const items = Array.from(
        listRef.current?.querySelectorAll('[role="menuitem"]:not([disabled])') ?? []
      );
      if (!items.length) return;
      e.preventDefault();
      const at = items.indexOf(document.activeElement);
      let next = 0;
      if (e.key === "ArrowDown") next = at < 0 ? 0 : (at + 1) % items.length;
      else if (e.key === "ArrowUp") next = at < 0 ? items.length - 1 : (at - 1 + items.length) % items.length;
      else if (e.key === "End") next = items.length - 1;
      items[next].focus();
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open, close]);

  // Opening with the keyboard should land on the first item; opening with
  // a pointer should not steal the cursor's place. Focusing the list's
  // first item only once, on open, covers both acceptably.
  useEffect(() => {
    if (!open) return;
    const first = listRef.current?.querySelector('[role="menuitem"]:not([disabled])');
    first?.focus?.();
  }, [open]);

  return (
    <div className={`relative ${className}`} ref={wrapRef}>
      {cloneElement(trigger, {
        ref: triggerRef,
        onClick: (e) => {
          trigger.props.onClick?.(e);
          setOpen((o) => !o);
        },
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": open ? id : undefined,
      })}
      {open && (
        <div
          ref={listRef}
          id={id}
          role="menu"
          aria-label={label}
          className={`absolute z-30 mt-2 min-w-52 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-outline-variant bg-surface-lowest py-1 shadow-(--shadow-overlay) ${ALIGN[align] ?? ALIGN.right}`}
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ icon: Icon, children, danger = false, className = "", ...props }) {
  return (
    <button
      role="menuitem"
      tabIndex={-1}
      className={`focus-ring flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-sm font-medium transition-colors ${
        danger
          ? "text-on-surface-variant hover:bg-error-bg hover:text-error"
          : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
      } ${className}`}
      {...props}
    >
      {Icon && <Icon size={16} aria-hidden="true" />}
      {children}
    </button>
  );
}

/**
 * Tooltip.
 *
 * Opens on hover AND on focus. Hover-only would make the content
 * unreachable by keyboard, and this is used on chart values and truncated
 * table cells where that content is the point.
 *
 * A tooltip is never the ONLY source of a fact. It supplements a visible
 * label; chart values also appear in each chart's visually-hidden table.
 */
export function Tooltip({ content, children, className = "" }) {
  const [open, setOpen] = useState(false);
  const id = useId();

  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <span
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {cloneElement(children, { "aria-describedby": open ? id : undefined })}
      {open && (
        <span
          role="tooltip"
          id={id}
          className="pointer-events-none absolute bottom-full left-1/2 z-40 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-lg bg-on-surface px-2 py-1 text-xs font-medium text-surface-lowest shadow-(--shadow-overlay)"
        >
          {content}
        </span>
      )}
    </span>
  );
}
