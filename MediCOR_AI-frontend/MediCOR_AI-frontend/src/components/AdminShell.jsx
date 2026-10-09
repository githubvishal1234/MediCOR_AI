import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  LayoutDashboard, Building2, Users, ScrollText, LogOut, ShieldAlert, Layers,
  CreditCard, BarChart3, Phone, LifeBuoy, Menu as MenuIcon, X, Sun, Moon,
} from "lucide-react";
import Logo from "./Logo";
import { useAdminAuth } from "../lib/adminAuth";
import { useDarkMode } from "../lib/theme";

const ICON = 18;

const NAV = [
  { to: "/admin", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/admin/organizations", label: "Organizations", icon: Building2 },
  { to: "/admin/users", label: "Users", icon: Users },
  { to: "/admin/plans", label: "Plans", icon: Layers },
  { to: "/admin/subscriptions", label: "Subscriptions", icon: CreditCard },
  { to: "/admin/usage", label: "Usage", icon: BarChart3 },
  { to: "/admin/phone-numbers", label: "Phone Numbers", icon: Phone },
  { to: "/admin/support", label: "Support", icon: LifeBuoy },
  { to: "/admin/audit-log", label: "Audit Log", icon: ScrollText },
];

function SidebarContent({ onNavigate }) {
  const { admin, signOut } = useAdminAuth();
  const navigate = useNavigate();

  function handleSignOut() {
    signOut();
    navigate("/admin/login", { replace: true });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-5 py-5">
        <Logo variant="mark" />
        <div className="flex flex-col leading-none">
          <span className="font-display text-sm font-bold text-on-surface">Platform admin</span>
          {/* DISPLAY ONLY. The signed-in principal is still a `super_admin`
              — its own table, its own `typ="super_admin"` JWT, and
              `get_current_super_admin` on every /admin route. Nothing here
              is read by anything; this is the word on screen. */}
          <span className="text-xs text-on-surface-variant">Admin</span>
        </div>
      </div>

      {/* This console reads and writes across every clinic on the platform.
          The banner is the standing reminder of that, and it stays. */}
      <div className="mx-4 mb-2 flex items-center gap-2 rounded-lg bg-warning-bg px-3 py-2 text-xs font-semibold text-warning">
        <ShieldAlert size={14} aria-hidden="true" />
        Cross-organization access
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2" aria-label="Platform admin">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              `focus-ring group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-accent-subtle text-accent"
                  : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
              }`
            }
          >
            {({ isActive }) => (
              <>
                {/* A rail, not a solid navy pill. The pill made the selected
                    item the heaviest thing on screen, competing with the
                    page's own content instead of orienting you — the same
                    change AppShell already made, and its reasoning applies
                    identically here. */}
                <span
                  className={`absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-accent transition-opacity ${
                    isActive ? "opacity-100" : "opacity-0"
                  }`}
                  aria-hidden="true"
                />
                <Icon size={ICON} strokeWidth={isActive ? 2.4 : 2} aria-hidden="true" />
                {label}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-outline-variant px-3 py-3">
        <div className="mb-2 px-2">
          <p className="truncate text-sm font-semibold text-on-surface">{admin?.full_name}</p>
          <p className="truncate text-xs text-on-surface-variant">{admin?.email}</p>
        </div>
        <button
          onClick={handleSignOut}
          className="focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-on-surface-variant transition-colors hover:bg-error-bg hover:text-error"
        >
          <LogOut size={ICON} aria-hidden="true" />
          Sign out
        </button>
      </div>
    </div>
  );
}

export default function AdminShell({ children }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [dark, setDark] = useDarkMode();
  const location = useLocation();
  const drawerRef = useRef(null);
  const menuButtonRef = useRef(null);
  const mainRef = useRef(null);
  const firstRender = useRef(true);

  const closeDrawer = useCallback(() => setMobileOpen(false), []);

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [location.pathname]);

  /**
   * A11Y: the drawer is a modal surface.
   *
   * Before this the admin console had NO navigation below `lg` at all —
   * the sidebar was `hidden lg:flex` and the mobile header held a logo and
   * nothing else, so a super admin on a phone or a small tablet could only
   * reach a page by typing its URL. This is the same treatment AppShell
   * already gives its drawer: Escape, scroll lock, focus moved in and
   * returned, and Tab held inside.
   */
  useEffect(() => {
    if (!mobileOpen) return undefined;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";

    const node = drawerRef.current;
    // Captured now, not read in cleanup: by then the ref may point
    // elsewhere, and focus has to return to the button that opened this.
    const returnFocusTo = menuButtonRef.current;
    const focusable = () =>
      Array.from(
        node?.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? []
      );
    focusable()[0]?.focus();

    function onKeyDown(e) {
      if (e.key === "Escape") {
        closeDrawer();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
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
      returnFocusTo?.focus();
    };
  }, [mobileOpen, closeDrawer]);

  return (
    <div className="flex min-h-screen bg-surface-low">
      <a
        href="#admin-main"
        className="focus-ring sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface-lowest focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:shadow-(--shadow-overlay)"
      >
        Skip to content
      </a>

      <aside className="hidden w-64 flex-col border-r border-outline-variant bg-surface-lowest lg:flex">
        <SidebarContent />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px]" onClick={closeDrawer} />
          <aside
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Platform admin navigation"
            className="absolute inset-y-0 left-0 w-72 bg-surface-lowest shadow-(--shadow-overlay)"
          >
            <button
              className="focus-ring absolute right-2 top-2.5 inline-flex h-10 w-10 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
              onClick={closeDrawer}
              aria-label="Close menu"
            >
              <X size={20} aria-hidden="true" />
            </button>
            <SidebarContent onNavigate={closeDrawer} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-outline-variant bg-surface-lowest px-4 py-3 lg:hidden">
          <button
            ref={menuButtonRef}
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            aria-expanded={mobileOpen}
            className="focus-ring -ml-2 inline-flex h-10 w-10 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
          >
            <MenuIcon size={20} aria-hidden="true" />
          </button>
          <Logo variant="mark" />
          <span className="text-sm font-semibold text-on-surface">Platform admin</span>
          <button
            onClick={() => setDark((d) => !d)}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            className="focus-ring ml-auto rounded-full p-2 text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
          >
            {dark ? <Sun size={ICON} /> : <Moon size={ICON} />}
          </button>
        </header>

        {/* The desktop theme toggle rides above the content rather than in
            a bar of its own: this console has no topbar on wide screens
            and adding one to hold a single control would cost more space
            than it earns. */}
        <div className="hidden justify-end px-8 pt-6 lg:flex">
          <button
            onClick={() => setDark((d) => !d)}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            className="focus-ring rounded-full p-2 text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
          >
            {dark ? <Sun size={ICON} /> : <Moon size={ICON} />}
          </button>
        </div>

        <main
          id="admin-main"
          ref={mainRef}
          tabIndex={-1}
          className="flex-1 px-5 py-6 outline-none lg:px-8 lg:pb-8 lg:pt-2"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
