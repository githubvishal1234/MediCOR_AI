import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, PhoneCall, CalendarDays, Users, PlugZap,
  Bot, ShieldCheck, CreditCard, Search, Bell, HelpCircle, Building2,
  LogOut, Menu as MenuIcon, X, BookOpen, Sun, Moon, UsersRound, BarChart3,
  CheckCircle2, AlertTriangle, Info, ChevronDown,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import Logo from "./Logo";
import { useDarkMode } from "../lib/theme";
import { RealtimeProvider, useRealtime } from "../lib/realtime";
import { useAuth } from "../lib/auth";
import { Input, Menu, MenuItem, ToastProvider } from "./ui";
import { useToast } from "./toast";
import { clinicRoleLabel } from "../lib/format";

// One icon size for the whole shell. It used to be 18 in the sidebar and
// 19 or 22 in the header, which reads as misalignment rather than
// hierarchy when the two sit on the same row.
const ICON = 18;

// QW-7 — `adminOnly` hides a destination a medical_staff user would only
// reach a 403 on. This is presentation, not enforcement: every one of
// these is guarded server-side (see `require_admin` in the routers), and
// the pages themselves render an "admin access required" state if opened
// directly. Hiding them just stops the sidebar advertising dead ends.
//
// Grouped rather than a flat list of twelve. The order follows the working
// day: what is happening now, then the records behind it, then the
// settings a clinic touches once a quarter. A flat list meant scanning all
// twelve to find any one of them.
const NAV_GROUPS = [
  {
    label: null, // the landing destination needs no heading above it
    items: [
      { to: "/app", label: "Dashboard", icon: LayoutDashboard, end: true },
      { to: "/app/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/app/calls", label: "Call Logs", icon: PhoneCall },
      { to: "/app/appointments", label: "Appointments", icon: CalendarDays },
      { to: "/app/patients", label: "Patients", icon: Users },
    ],
  },
  {
    label: "Clinic",
    items: [
      { to: "/app/clinic", label: "Clinic Profile", icon: Building2 },
      { to: "/app/knowledge-base", label: "Knowledge Base", icon: BookOpen },
      { to: "/app/agent", label: "Agent Settings", icon: Bot },
    ],
  },
  {
    label: "Administration",
    items: [
      { to: "/app/team", label: "Team", icon: UsersRound, adminOnly: true },
      { to: "/app/ehr", label: "EHR Sync", icon: PlugZap, adminOnly: true },
      { to: "/app/security", label: "Security", icon: ShieldCheck },
      { to: "/app/billing", label: "Billing", icon: CreditCard, adminOnly: true },
    ],
  },
];

const NOTIF_TONE = {
  success: { icon: CheckCircle2, className: "text-success" },
  warning: { icon: AlertTriangle, className: "text-warning" },
  info: { icon: Info, className: "text-info" },
};

function timeAgo(iso) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

/**
 * Live connection dot — green when the dashboard WebSocket is open.
 *
 * A11Y: `role="status"` so losing live updates is announced, rather than
 * existing only as a colour change a sighted user might notice.
 */
function ConnectionDot() {
  const { connected } = useRealtime();
  return (
    <span
      role="status"
      className="flex items-center gap-1.5 text-xs font-medium text-on-surface-variant"
      title={connected ? "Live updates connected" : "Reconnecting…"}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-success" : "bg-on-surface-variant/40"}`}
        aria-hidden="true"
      />
      {connected ? "Live" : "Offline"}
    </span>
  );
}

/**
 * Turns staff notifications into transient toasts.
 *
 * Subscribes to `notification` and NOT to the domain events beside it
 * (`appointment.booked`, `call.started`, …), which is the whole design and
 * worth stating because the obvious alternative is wrong in two ways.
 *
 * The backend broadcasts a domain event on every change but only calls
 * `notify()` when `ctx.actor == "service"` — that is, only when the AI
 * agent did it, never when a staff member did (see routers/appointments.py
 * and routers/calls.py). Subscribing to `notification` therefore gets us
 * exactly "something happened while you were looking elsewhere", and a
 * receptionist who books an appointment herself is not told about it by a
 * popup. Subscribing to the domain events instead would toast people for
 * their own actions AND fire twice for anything the agent did.
 *
 * The bell keeps the same event as persistent history; the toast is the
 * transient half. One notify still means one of each, never two toasts.
 */
function RealtimeToasts() {
  const { subscribe } = useRealtime();
  const toast = useToast();
  const navigate = useNavigate();

  useEffect(() => {
    return subscribe("notification", (data) => {
      // The backend already wrote a properly worded title and message and
      // picked the level. Re-deriving either here would be a second
      // vocabulary to keep in sync.
      const tone = { success: "success", warning: "warning", info: "info" }[data.level] || "info";
      // Deep-link where there is somewhere to go. `?call=` is an existing
      // contract on the call log; appointments has no per-row route.
      const callId = data.data?.call_id;
      const apptId = data.data?.appointment_id;
      const action = callId
        ? { label: "View call", onClick: () => navigate(`/app/calls?call=${callId}`) }
        : apptId
        ? { label: "View appointments", onClick: () => navigate("/app/appointments") }
        : undefined;
      toast[tone](data.title, { description: data.message, action });
    });
  }, [subscribe, toast, navigate]);

  return null;
}

/**
 * The account menu.
 *
 * The avatar was a static `div` and Sign Out lived only at the bottom of
 * the sidebar — which on a phone meant opening the navigation drawer to
 * leave. The identity block is now the trigger for a real menu.
 */
function UserMenu() {
  const navigate = useNavigate();
  const { signOut, user } = useAuth();

  const orgName = user?.org_name || "Your Clinic";
  const userLabel = user?.full_name || user?.email || "Account";
  // The trigger's second line shows the role rather than the person's
  // name. It used to show `full_name`, which on the seeded account is
  // itself the string "Admin" — so the line read as a role while actually
  // being a name. The name is still reachable, one row down in the menu.
  const role = clinicRoleLabel(user?.role);
  const initials = orgName.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <Menu
      align="right"
      label="Account"
      trigger={
        <button
          type="button"
          className="focus-ring flex items-center gap-2.5 rounded-lg py-1 pl-2 pr-1.5 transition-colors hover:bg-surface-container"
        >
          <span className="hidden text-right leading-tight sm:block">
            <span className="block max-w-[14rem] truncate text-sm font-semibold text-on-surface">
              {orgName}
            </span>
            <span className="block max-w-[14rem] truncate text-xs text-on-surface-variant">
              {role || userLabel}
            </span>
          </span>
          {/* Neutral, like every other avatar. `bg-navy` is not redefined
              for dark mode, so there the circle matched the header and the
              initials floated -- the defect noted in the final audit. */}
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-container text-sm font-semibold text-on-surface-variant">
            {initials}
          </span>
          <ChevronDown size={14} className="hidden shrink-0 text-on-surface-variant sm:block" aria-hidden="true" />
        </button>
      }
    >
      {/* Repeats the identity inside the menu because the trigger's copy
          is hidden below `sm`, where this is the only place it appears. */}
      <div className="border-b border-outline-variant px-3.5 pb-2 pt-1">
        <p className="truncate text-sm font-semibold text-on-surface">{orgName}</p>
        <p className="truncate text-xs text-on-surface-variant">{userLabel}</p>
        {/* `userLabel` above is the person's real name — the one thing the
            trigger no longer shows, so it has to survive here.

            The old duplicate-suppression on this tag is gone: it existed
            because the raw role ("admin") could equal the seeded user's
            name, and the *displayed* label no longer can. */}
        {role && (
          <p className="mt-1 text-xs font-bold uppercase tracking-wider text-on-surface-variant/70">
            {role}
          </p>
        )}
      </div>
      <MenuItem icon={Building2} onClick={() => navigate("/app/clinic")}>
        Clinic profile
      </MenuItem>
      <MenuItem icon={HelpCircle} onClick={() => navigate("/app/support")}>
        Support
      </MenuItem>
      {/* A11Y + convention: the destructive item is separated from the
          navigation ones rather than sitting flush against them. */}
      <div className="my-1 border-t border-outline-variant" />
      <MenuItem
        icon={LogOut}
        danger
        onClick={() => {
          signOut();
          navigate("/");
        }}
      >
        Sign out
      </MenuItem>
    </Menu>
  );
}

function NotificationBell() {
  const { notifications, unreadCount, markAllRead, dismissNotification } = useRealtime();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    // Escape closes it too. Nothing in the app handled Escape before, so a
    // keyboard user had to tab through every notification to get out.
    function onKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  function toggle() {
    setOpen((o) => {
      if (!o) markAllRead();
      return !o;
    });
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={toggle}
        className="focus-ring relative inline-flex h-10 w-10 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
        aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"}
        aria-expanded={open}
        aria-haspopup="true"
      >
        <Bell size={ICON} />
        {unreadCount > 0 && (
          <span className="absolute right-1 top-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-live px-1 text-[10px] font-bold text-white">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-outline-variant bg-surface-lowest shadow-(--shadow-overlay)">
          <div className="border-b border-outline-variant px-4 py-3">
            <p className="text-sm font-bold text-on-surface">Notifications</p>
          </div>
          {notifications.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-on-surface-variant">
              No notifications yet
            </p>
          ) : (
            <div className="max-h-96 divide-y divide-outline-variant overflow-y-auto">
              {notifications.map((n) => {
                const tone = NOTIF_TONE[n.level] || NOTIF_TONE.info;
                const Icon = tone.icon;
                return (
                  <button
                    key={n.id}
                    onClick={() => dismissNotification(n.id)}
                    className="focus-ring flex w-full items-start gap-2.5 px-4 py-3 text-left hover:bg-surface-container"
                  >
                    <Icon size={16} className={`mt-0.5 shrink-0 ${tone.className}`} aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-on-surface">{n.title}</p>
                      <p className="truncate text-xs text-on-surface-variant">{n.message}</p>
                      <p className="mt-0.5 text-xs text-on-surface-variant/70">
                        {timeAgo(n.receivedAt)}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SidebarContent({ onNavigate }) {
  const navigate = useNavigate();
  const { signOut, user } = useAuth();

  // QW-7: hide admin-only destinations from medical_staff. While `user`
  // is still loading, show the unrestricted set rather than flashing a
  // shorter sidebar that then grows.
  const isAdmin = !user || user.role === "admin";
  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((i) => !i.adminOnly || isAdmin),
  })).filter((g) => g.items.length > 0);

  function handleSignOut() {
    signOut();
    navigate("/");
  }

  const linkClass = ({ isActive }) =>
    `focus-ring group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
      isActive
        ? "bg-accent-subtle text-accent"
        : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
    }`;

  return (
    <div className="flex h-full flex-col">
      <div className="px-5 pt-6 pb-5">
        <button
          onClick={() => navigate("/")}
          aria-label="MediCOR AI — home"
          className="focus-ring rounded-md"
        >
          <Logo />
        </button>
        {/* No tagline here. A generic "Clinical Receptionist" label used to
            sit under the logo; the clinic's name is already in the account
            menu at the top right, so repeating it here would be noise. */}
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-2" aria-label="Main">
        {groups.map((group, gi) => (
          <div key={group.label ?? `group-${gi}`} className={gi === 0 ? "" : "mt-5"}>
            {group.label && (
              <p className="px-3 pb-1.5 text-xs font-semibold text-on-surface-variant/80">
                {group.label}
              </p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} onClick={onNavigate} className={linkClass}>
                  {({ isActive }) => (
                    <>
                      {/* The active rail. A solid navy pill used to carry
                          this, which made the selected nav item the
                          heaviest element on screen — competing with the
                          page's own content instead of orienting you. */}
                      <span
                        className={`absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-accent transition-opacity ${
                          isActive ? "opacity-100" : "opacity-0"
                        }`}
                        aria-hidden="true"
                      />
                      <item.icon size={ICON} strokeWidth={isActive ? 2.4 : 2} aria-hidden="true" />
                      {item.label}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="space-y-0.5 border-t border-outline-variant px-3 py-3">
        <button
          onClick={() => {
            onNavigate?.();
            navigate("/app/support");
          }}
          className="focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
        >
          <HelpCircle size={ICON} aria-hidden="true" />
          Support
        </button>
        <button
          onClick={handleSignOut}
          className="focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-on-surface-variant transition-colors hover:bg-error-bg hover:text-error"
        >
          <LogOut size={ICON} aria-hidden="true" />
          Sign Out
        </button>
      </div>
    </div>
  );
}

/**
 * Provider order is load-bearing.
 *
 * RealtimeProvider must stay outermost: `useRealtime()` is called from
 * page bodies rendered as children below, which is why Dashboard and
 * CallLogs split into a `…Body` component in the first place.
 * ToastProvider sits inside it so `RealtimeToasts` can reach both.
 */
export default function AppShell({ title, subtitle, actions, children }) {
  return (
    <RealtimeProvider>
      <ToastProvider>
        <RealtimeToasts />
        <AppShellInner title={title} subtitle={subtitle} actions={actions}>
          {children}
        </AppShellInner>
      </ToastProvider>
    </RealtimeProvider>
  );
}

function AppShellInner({ title, subtitle, actions, children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [dark, setDark] = useDarkMode();
  const [searchQuery, setSearchQuery] = useState("");
  const drawerRef = useRef(null);
  const menuButtonRef = useRef(null);
  const mainRef = useRef(null);
  const firstRender = useRef(true);

  const closeDrawer = useCallback(() => setMobileOpen(false), []);

  /**
   * A11Y: move focus to the main region on navigation.
   *
   * In a single-page app the document never reloads, so a screen-reader
   * user who follows a nav link is left with focus on the link and no
   * announcement that anything changed — the new page simply is not read.
   *
   * Skipped on first render on purpose: pulling focus out of the address
   * bar when someone has just typed a URL is the opposite of helpful, and
   * only a route *change* is the event worth announcing.
   */
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [location.pathname]);

  // A11Y: the mobile drawer is a modal surface and was treated as
  // decoration — no Escape, no scroll lock, no focus containment. On a
  // phone that meant Tab walked invisibly through the page behind the
  // overlay, and the page scrolled underneath the open menu.
  useEffect(() => {
    if (!mobileOpen) return undefined;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";

    const node = drawerRef.current;
    // Captured now rather than read in cleanup: by the time the cleanup
    // runs the ref may point elsewhere, and this has to return focus to
    // the exact button that opened the drawer.
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

  // Only patients are actually searchable server-side (GET /patients?search=),
  // so this wires to that real endpoint rather than promising a unified
  // search across calls/appointments that the backend doesn't support.
  // The placeholder says "patients" for the same reason.
  function submitSearch(e) {
    e.preventDefault();
    const q = searchQuery.trim();
    navigate(q ? `/app/patients?search=${encodeURIComponent(q)}` : "/app/patients");
  }

  return (
    <div className="min-h-screen bg-surface">
      {/* A11Y: lets a keyboard user jump the twelve nav links. */}
      <a
        href="#main-content"
        className="focus-ring sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface-lowest focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:shadow-(--shadow-overlay)"
      >
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-outline-variant bg-surface-lowest lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px]" onClick={closeDrawer} />
          <aside
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 w-72 bg-surface-lowest shadow-(--shadow-overlay)"
          >
            <button
              className="focus-ring absolute right-3 top-4 inline-flex h-10 w-10 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container"
              onClick={closeDrawer}
              aria-label="Close menu"
            >
              <X size={20} />
            </button>
            <SidebarContent onNavigate={closeDrawer} />
          </aside>
        </div>
      )}

      <div className="lg:pl-64">
        {/* Topbar */}
        <header className="sticky top-0 z-20 border-b border-outline-variant bg-surface-lowest/85 backdrop-blur-md">
          <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
            <button
              ref={menuButtonRef}
              className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container lg:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open menu"
              aria-expanded={mobileOpen}
            >
              <MenuIcon size={20} />
            </button>

            <form onSubmit={submitSearch} role="search" className="relative hidden max-w-sm flex-1 sm:block">
              <Search
                size={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
                aria-hidden="true"
              />
              {/* Was a hand-rolled input carrying `border-outline-variant`
                  — the divider token, 1.23:1, which is not a legible
                  boundary for a control. The shared primitive resolves
                  the 3:1 `border-control` token instead. */}
              <Input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search patients…"
                aria-label="Search patients by name or MRN"
                className="bg-surface-low py-2 pl-9 pr-4 transition-colors hover:bg-surface-container focus-visible:bg-surface-lowest"
              />
            </form>

            <div className="ml-auto flex items-center gap-1 sm:gap-2">
              <div className="mr-1 hidden sm:block">
                <ConnectionDot />
              </div>
              <button
                onClick={() => setDark((d) => !d)}
                className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
                aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
              >
                {dark ? <Sun size={ICON} /> : <Moon size={ICON} />}
              </button>
              <NotificationBell />
              <button
                onClick={() => navigate("/app/support")}
                className="focus-ring hidden h-10 w-10 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface sm:inline-flex"
                aria-label="Help and support"
              >
                <HelpCircle size={ICON} />
              </button>
              <div className="ml-1 border-l border-outline-variant pl-2">
                <UserMenu />
              </div>
            </div>
          </div>
        </header>

        {/* tabIndex -1 so the route-change effect above can move focus
            here. It is not in the tab order. */}
        <main
          id="main-content"
          ref={mainRef}
          tabIndex={-1}
          className="px-4 py-6 outline-none sm:px-6 lg:px-8"
        >
          {(title || subtitle || actions) && (
            <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {title && (
                  <h1 className="font-display text-2xl font-bold tracking-tight text-on-surface sm:text-[28px]">
                    {title}
                  </h1>
                )}
                {subtitle && <p className="mt-1 text-sm text-on-surface-variant">{subtitle}</p>}
              </div>
              {/* Page-level actions belong beside the title. Optional —
                  pages that don't pass any are unchanged. */}
              {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}
