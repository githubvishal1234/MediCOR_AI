/**
 * Security & compliance.
 *
 * HV-1 changed what this page is. The audit table it renders was written
 * by exactly two things — `seed.py` and `ehr_service.py` — so it showed
 * five planted demo rows and nothing recorded who had read a patient
 * record. Every PHI touch now writes a real entry, which makes three
 * things here worth doing properly:
 *
 *   * The search and filter buttons were decorative. They now filter
 *     server-side, because a log you cannot query is a recent-events
 *     widget, and 25 rows of "patient.viewed" is what a busy morning
 *     looks like.
 *   * "View All Logs" went nowhere. The endpoint is paged now.
 *   * The compliance banner asserted things the system did not know:
 *     `hipaa_verified` defaulted to true and was never set, and the
 *     encryption values are still hardcoded fallbacks rather than
 *     anything the app verifies. What is knowable is now derived; what
 *     is not is labelled rather than presented as a green tick.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ShieldCheck, ShieldOff, Lock, Users, Headset, ShieldAlert, ScrollText,
  Search, Download, X, Loader2, Eye, UserCog, Bot, Building2,
} from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, Skeleton, EmptyState, ErrorState, Input, Select,
  Table, THead, TH, TBody, TR, TD,
} from "../../components/ui";
import * as api from "../../lib/api";
import { useAuth } from "../../lib/auth";

const ROLE_ICON = { Administrators: Users, "Medical Staff": ShieldAlert, "AI Agents": Headset };
// Nearly every row is a Success, so green there was colour on every line
// and a Blocked row did not stand out. Only the exception carries colour.
const STATUS_TONE = { Success: "neutral", Blocked: "error", Logged: "neutral" };

/**
 * Human labels for the closed action vocabulary in
 * `app/audit_service.py`. An unknown code falls back to the raw string
 * rather than being hidden — a log entry nobody can name is still an
 * entry somebody needs to see.
 */
const ACTION_LABEL = {
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in",
  "auth.invite_accepted": "Invitation accepted",
  "patient.viewed": "Patient record opened",
  "patient.created": "Patient created",
  "patient.updated": "Patient updated",
  "patient.archived": "Patient archived",
  "patient.restored": "Patient restored",
  "patient.record_changed": "Clinical record changed",
  "patient.history_viewed": "Clinical history opened",
  "call.transcript_viewed": "Call transcript opened",
  "appointment.created": "Appointment created",
  "appointment.updated": "Appointment updated",
  "appointment.cancelled": "Appointment cancelled",
  "appointment.rescheduled": "Appointment rescheduled",
  "agent_settings.updated": "AI settings changed",
  "knowledge_base.changed": "Knowledge base changed",
  "clinic_info.updated": "Clinic profile updated",
  "api_key.created": "API key created",
  "api_key.revoked": "API key revoked",
  "ehr.integration_changed": "EHR integration changed",
  "ehr.sync": "EHR sync",
  "team.member_invited": "Team member invited",
  "team.member_changed": "Team member changed",
  "audit_log.exported": "Audit log exported",
  "platform.impersonation_started": "MediCOR support signed in",
};

/**
 * Who did it. `platform` is the one that earns an icon of its own: it
 * means we were inside this clinic's data, and before HV-1 it was
 * indistinguishable from the clinic's own admin.
 */
const ACTOR_ICON = {
  user: UserCog,
  service: Bot,
  platform: Building2,
  anonymous: ShieldAlert,
};

const PAGE_SIZE = 25;

function formatTimestamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Security() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [compliance, setCompliance] = useState(null);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Audit log state is separate from the page's: refiltering should
  // reload the table, not blank out the compliance cards above it.
  const [log, setLog] = useState(null);
  const [logLoading, setLogLoading] = useState(true);
  const [logError, setLogError] = useState(false);
  const [actions, setActions] = useState([]);
  const [filters, setFilters] = useState({ q: "", action: "", status: "" });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.all([api.getCompliance(), api.getRoles(), api.getAuditLogActions()])
      .then(([complianceData, rolesData, actionsData]) => {
        if (cancelled) return;
        setCompliance(complianceData);
        setRoles(rolesData);
        setActions(actionsData);
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

  const loadLog = useCallback(() => {
    let cancelled = false;
    setLogLoading(true);
    setLogError(false);
    api
      .getAuditLog({ ...filters, page, pageSize: PAGE_SIZE })
      .then((data) => {
        if (cancelled) return;
        setLog(data);
        setLogLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setLogError(true);
        setLogLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters, page]);

  useEffect(() => {
    return load();
  }, [load]);

  useEffect(() => {
    return loadLog();
  }, [loadLog]);

  function applyFilter(patch) {
    // Any filter change resets to page 1 — staying on page 4 of a
    // narrower result set silently shows an empty table.
    setPage(1);
    setFilters((prev) => ({ ...prev, ...patch }));
  }

  function submitSearch(e) {
    e.preventDefault();
    applyFilter({ q: search.trim() });
  }

  function clearFilters() {
    setSearch("");
    setPage(1);
    setFilters({ q: "", action: "", status: "" });
  }

  async function exportCsv() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await api.downloadAuditLogCsv(filters);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err?.message || "Couldn't export the audit log.");
    } finally {
      setExporting(false);
    }
  }

  const filtersActive = Boolean(filters.q || filters.action || filters.status);
  const rows = useMemo(() => log?.items || [], [log]);

  if (error) {
    return (
      <AppShell
        title="Security & Compliance"
        subtitle="Manage your organization's security posture, monitor audit logs, and maintain data protection."
      >
        <ErrorState detail="We couldn't load your security settings." onRetry={load} className="py-24" />
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Security & Compliance"
      subtitle="Manage your organization's security posture, monitor audit logs, and maintain data protection."
    >
      <Card className="mb-6 p-6">
        <div className="flex items-start gap-3">
          <div
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${
              compliance?.hipaa_verified ? "bg-success-bg" : "bg-surface-container"
            }`}
          >
            {compliance?.hipaa_verified ? (
              <ShieldCheck size={20} className="text-success" />
            ) : (
              <ShieldOff size={20} className="text-on-surface-variant" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="font-display text-base font-bold text-on-surface">Security &amp; privacy</h2>
              {loading ? (
                <Skeleton className="h-5 w-24 rounded-full" />
              ) : (
                <Chip tone={compliance?.hipaa_verified ? "success" : "warning"}>
                  {compliance?.hipaa_verified ? "Audit on file" : "No audit on file"}
                </Chip>
              )}
            </div>
            {/* This used to read "Your instance is fully verified and
                compliant with healthcare privacy regulations" for every
                clinic, unconditionally. The system cannot know that. What
                it can say is what it is doing. */}
            <p className="mt-1 text-sm text-on-surface-variant">
              Every action that touches a patient record is recorded below, including
              anything your AI receptionist does on a call.
            </p>
            {loading ? (
              <div className="mt-3 flex gap-8">
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-3 w-32" />
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap gap-x-8 gap-y-1 text-xs text-on-surface-variant">
                <span>Last security audit: <b className="text-on-surface">{compliance?.last_security_audit}</b></span>
                <span>Data retention: <b className="text-on-surface">{compliance?.data_retention_years} years</b></span>
                <span>BAA: <b className="text-on-surface">{compliance?.baa_status}</b></span>
                <span>
                  Audit events recorded:{" "}
                  <b className="text-on-surface">{compliance?.audit_log_entries?.toLocaleString() ?? 0}</b>
                </span>
              </div>
            )}
          </div>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-6">
          <div className="flex items-center gap-2">
            <Lock size={16} className="text-accent" aria-hidden="true" />
            {/* Not "End-to-End": that means only the two ends can read the
                data, and here the server and the voice providers process
                it. The body below already says these are intended values. */}
            <h2 className="font-display text-base font-bold text-on-surface">Encryption</h2>
          </div>
          {/* The two values below are schema defaults, not measurements
              — nothing in the app verifies that the database has TDE
              enabled or which TLS version terminated the request. The
              green ticks that used to sit beside them are gone for that
              reason; the intended configuration is worth stating, but
              not as a verified fact. */}
          <p className="mt-1 text-sm text-on-surface-variant">
            The configuration this deployment is intended to run under.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-outline-variant p-3.5">
              <p className="text-xs text-on-surface-variant">At rest</p>
              <p className="text-sm font-semibold text-on-surface">{compliance?.encryption_at_rest || "AES-256"}</p>
            </div>
            <div className="rounded-xl border border-outline-variant p-3.5">
              <p className="text-xs text-on-surface-variant">In transit</p>
              <p className="text-sm font-semibold text-on-surface">{compliance?.encryption_in_transit || "TLS 1.3"}</p>
            </div>
          </div>
          <p className="mt-3 text-xs text-on-surface-variant">
            Not verified from inside the application — confirm against your database and
            proxy configuration before quoting these.
          </p>
        </Card>

        <Card className="p-6">
          {/* "Create Custom Role" used to sit here and do nothing. The
              three roles are a fixed enum (`models.UserRole`) that RBAC
              is built on, so there is no custom role to create. */}
          <h2 className="font-display text-base font-bold text-on-surface">Access control</h2>
          <div className="mt-4 space-y-3">
            {loading ? (
              [1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full rounded-xl" />)
            ) : (
              roles.map((r) => {
                const Icon = ROLE_ICON[r.name] || Users;
                return (
                  <div key={r.name} className="flex items-start gap-3 rounded-xl border border-outline-variant p-3.5">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-container">
                      <Icon size={16} className="text-on-surface-variant" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-on-surface">
                        {r.name} · <span className="text-on-surface-variant">{r.count} User{r.count === 1 ? "" : "s"}</span>
                      </p>
                      <p className="text-xs text-on-surface-variant">{r.detail}</p>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </Card>
      </div>

      <Card className="mt-6">
        <div className="flex flex-col gap-3 border-b border-outline-variant px-6 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="font-display text-base font-bold text-on-surface">Audit trail</h2>
              <p className="text-xs text-on-surface-variant">
                Every sign-in, record opened, and change made — by staff, by your AI
                receptionist, and by MediCOR support.
              </p>
            </div>
            {isAdmin && (
              <Button
                variant="outline"
                className="px-3 py-2 text-xs"
                onClick={exportCsv}
                disabled={exporting || logLoading}
              >
                {exporting ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                Export CSV
              </Button>
            )}
          </div>

          {/* These two buttons were decorative. They filter server-side
              now, which they have to: a busy clinic writes an entry per
              record opened, so an unfilterable table is unreadable by
              the second morning. */}
          <div className="flex flex-wrap items-center gap-2">
            <form onSubmit={submitSearch} className="relative min-w-[14rem] flex-1">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
              />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                type="search"
                placeholder="Search who, action, detail, IP…"
                aria-label="Search the audit trail"
                className="pl-9"
              />
            </form>

            <Select
              value={filters.action}
              onChange={(e) => applyFilter({ action: e.target.value })}
              aria-label="Filter by action"
              className="w-full sm:w-52"
            >
              <option value="">All actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>{ACTION_LABEL[a] || a}</option>
              ))}
            </Select>

            <Select
              value={filters.status}
              onChange={(e) => applyFilter({ status: e.target.value })}
              aria-label="Filter by status"
              className="w-full sm:w-40"
            >
              <option value="">Any status</option>
              <option value="Success">Success</option>
              <option value="Logged">Logged</option>
              <option value="Blocked">Blocked</option>
            </Select>

            {filtersActive && (
              <button
                onClick={clearFilters}
                className="focus-ring flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container"
              >
                <X size={13} /> Clear
              </button>
            )}

            {!logLoading && log && (
              <span className="ml-auto text-xs text-on-surface-variant">
                {log.total.toLocaleString()} event{log.total === 1 ? "" : "s"}
              </span>
            )}
          </div>

          {exportError && (
            <p className="rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{exportError}</p>
          )}
        </div>

        {logLoading ? (
          <div className="space-y-2 p-4">
            {[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-9 w-full" />)}
          </div>
        ) : logError ? (
          <ErrorState detail="We couldn't load the audit trail." onRetry={loadLog} className="py-16" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title={filtersActive ? "No events match these filters" : "No audit events yet"}
            detail={
              filtersActive
                ? "Try widening the search or clearing the filters above."
                : "Signing in, opening a patient record, or changing a setting will appear here."
            }
            className="py-16"
          />
        ) : (
          <>
            <Table>
              <THead>
                <tr>
                  <TH>When</TH>
                  <TH>Action</TH>
                  <TH>Who</TH>
                  <TH>Detail</TH>
                  <TH>Status</TH>
                </tr>
              </THead>
              <TBody>
                {rows.map((row) => {
                  const ActorIcon = ACTOR_ICON[row.actor_type] || Eye;
                  return (
                    <TR key={row.id}>
                      <TD className="whitespace-nowrap text-on-surface-variant">
                        {formatTimestamp(row.occurred_at)}
                      </TD>
                      <TD className="font-medium text-on-surface">
                        {ACTION_LABEL[row.action] || row.action}
                      </TD>
                      <TD className="text-on-surface-variant">
                        <span className="flex items-start gap-1.5">
                          <ActorIcon size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
                          <span>{row.who || "System"}</span>
                        </span>
                      </TD>
                      <TD className="text-on-surface-variant">
                        {row.detail || "\u2014"}
                        {row.ip_address && (
                          <span className="block text-xs opacity-70">{row.ip_address}</span>
                        )}
                      </TD>
                      <TD>
                        <Chip tone={STATUS_TONE[row.status] || "neutral"}>{row.status}</Chip>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>

            {/* "View All Logs" used to sit here and go nowhere. */}
            {log.total_pages > 1 && (
              <div className="flex items-center justify-between gap-3 border-t border-outline-variant px-6 py-3 text-sm">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={log.page <= 1}
                  className="focus-ring rounded-lg px-3 py-1.5 font-semibold text-on-surface-variant hover:bg-surface-container disabled:opacity-40"
                >
                  Previous
                </button>
                <span className="text-xs text-on-surface-variant">
                  Page {log.page} of {log.total_pages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(log.total_pages, p + 1))}
                  disabled={log.page >= log.total_pages}
                  className="focus-ring rounded-lg px-3 py-1.5 font-semibold text-on-surface-variant hover:bg-surface-container disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </Card>
    </AppShell>
  );
}