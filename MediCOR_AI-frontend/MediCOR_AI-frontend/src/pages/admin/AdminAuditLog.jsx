import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ScrollText, Search, Filter, ChevronLeft, ChevronRight } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, EmptyState, ErrorState, Modal, Table, THead, TH, TBody, TR, TD, SkeletonTable, Input, Select,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

// Friendly labels for the action codes written by routers/admin.py and
// routers/admin_plans.py — purely cosmetic, the filter itself still
// matches against the raw action value the backend returns.
const ACTION_LABELS = {
  // DISPLAY ONLY. The audited principal is still a `super_admin`; these
  // are the words shown beside the record, not the record itself.
  "auth.login": "Admin login",
  "auth.login_failed": "Admin login failed",
  "org.update": "Clinic updated",
  "org.suspend": "Clinic suspended",
  "org.reinstate": "Clinic reinstated",
  "org.impersonate": "Impersonated clinic",
  "user.update": "User updated",
  "user.activate": "User activated",
  "user.deactivate": "User deactivated",
  "plan.create": "Plan created",
  "plan.update": "Plan updated",
  "plan.activate": "Plan activated",
  "plan.deactivate": "Plan deactivated",
  "subscription.assign": "Subscription assigned",
  "subscription.change_plan": "Subscription plan changed",
  "subscription.status_change": "Subscription status changed",
};

function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

function useDebounced(value, delay = 350) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

function DetailModal({ entry, onClose }) {
  return (
    <Modal open={!!entry} title="Audit log entry" onClose={onClose}>
      {entry && (
        <div className="space-y-3 text-sm">
          <div>
            <p className="text-xs text-on-surface-variant">Action</p>
            <p className="font-medium text-on-surface">{actionLabel(entry.action)}</p>
            <p className="text-xs text-on-surface-variant">{entry.action}</p>
          </div>
          <div>
            <p className="text-xs text-on-surface-variant">Status</p>
            {entry.status === "failed" ? (
              <Chip tone="error">Failed</Chip>
            ) : (
              <p className="font-medium text-on-surface">Success</p>
            )}
          </div>
          <div>
            <p className="text-xs text-on-surface-variant">Timestamp</p>
            <p className="font-medium text-on-surface">{new Date(entry.occurred_at).toLocaleString()}</p>
          </div>
          <div>
            <p className="text-xs text-on-surface-variant">Admin</p>
            <p className="font-medium text-on-surface">{entry.super_admin_name || "Unknown"}</p>
            {entry.super_admin_email && (
              <p className="text-xs text-on-surface-variant">{entry.super_admin_email}</p>
            )}
          </div>
          {entry.target_org_id && (
            <div>
              <p className="text-xs text-on-surface-variant">Organization / Clinic</p>
              <Link
                to={`/admin/organizations/${entry.target_org_id}`}
                className="focus-ring font-medium text-on-surface hover:underline"
              >
                {entry.target_org_name || entry.target_org_id}
              </Link>
            </div>
          )}
          {entry.target_user_id && (
            <div>
              <p className="text-xs text-on-surface-variant">Target user</p>
              <p className="font-medium text-on-surface">{entry.target_user_email || entry.target_user_id}</p>
            </div>
          )}
          {entry.detail && (
            <div>
              <p className="text-xs text-on-surface-variant">Resource / detail</p>
              <p className="whitespace-pre-wrap break-words font-medium text-on-surface">{entry.detail}</p>
            </div>
          )}
          <div>
            <p className="text-xs text-on-surface-variant">IP address</p>
            <p className="font-medium text-on-surface">{entry.ip_address || "Not available"}</p>
          </div>
          <div>
            <p className="text-xs text-on-surface-variant">User agent</p>
            <p className="break-words font-medium text-on-surface">{entry.user_agent || "Not available"}</p>
          </div>
          <div>
            <p className="text-xs text-on-surface-variant">Entry ID</p>
            <p className="font-mono text-xs text-on-surface-variant">{entry.id}</p>
          </div>
        </div>
      )}
    </Modal>
  );
}

const PAGE_SIZE = 25;

export default function AdminAuditLog() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [actionFilter, setActionFilter] = useState("all");
  const [orgFilter, setOrgFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [page, setPage] = useState(1);

  const [orgs, setOrgs] = useState(null);
  const [actions, setActions] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailEntry, setDetailEntry] = useState(null);

  // Filter option sources — non-fatal if either fails, the table itself
  // will still load and show its own error if needed.
  useEffect(() => {
    adminApi.listOrganizations().then(setOrgs).catch(() => {});
    adminApi.getAdminAuditLogActions().then(setActions).catch(() => {});
  }, []);

  // Any filter change resets to page 1.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, actionFilter, orgFilter, statusFilter, startDate, endDate]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(
        await adminApi.getAdminAuditLog({
          q: debouncedSearch || undefined,
          action: actionFilter !== "all" ? actionFilter : undefined,
          orgId: orgFilter !== "all" ? orgFilter : undefined,
          status: statusFilter !== "all" ? statusFilter : undefined,
          startDate: startDate || undefined,
          endDate: endDate || undefined,
          page,
          pageSize: PAGE_SIZE,
        })
      );
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, actionFilter, orgFilter, statusFilter, startDate, endDate, page]);

  const hasActiveFilters =
    search || actionFilter !== "all" || orgFilter !== "all" || statusFilter !== "all" || startDate || endDate;

  function resetFilters() {
    setSearch("");
    setActionFilter("all");
    setOrgFilter("all");
    setStatusFilter("all");
    setStartDate("");
    setEndDate("");
  }

  const entries = data?.items;
  const totalPages = data?.total_pages || 1;

  const actionOptions = useMemo(() => {
    const list = actions && actions.length ? actions : Object.keys(ACTION_LABELS);
    return [...list].sort();
  }, [actions]);

  return (
    <AdminShell>
      <h1 className="font-display text-2xl font-bold text-on-surface">Audit logs</h1>
      <p className="mt-1 text-sm text-on-surface-variant">
        Every cross-organization action taken from this admin console — logins, clinic and user changes, plan
        and subscription changes, and impersonation.
      </p>

      {/* ---------- Filters ---------- */}
      <div className="mt-5 flex flex-wrap items-end gap-3">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-on-surface-variant">Search</label>
          <div className="relative w-64">
            <Search
              size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
            />
            <Input
              aria-label="Search the platform audit log"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Action, admin, clinic, IP…"
              className="pl-9"
            />
          </div>
        </div>

        <div>
          <label htmlFor="adminauditlog-from" className="mb-1.5 block text-xs font-medium text-on-surface-variant">From</label>
          <Input id="adminauditlog-from"
            type="date"
            value={startDate}
            max={endDate || undefined}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="adminauditlog-to" className="mb-1.5 block text-xs font-medium text-on-surface-variant">To</label>
          <Input id="adminauditlog-to"
            type="date"
            value={endDate}
            min={startDate || undefined}
            max={isoDate(new Date())}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>

        <div>
          <label htmlFor="adminauditlog-clinic" className="mb-1.5 block text-xs font-medium text-on-surface-variant">Clinic</label>
          <Select id="adminauditlog-clinic"
            value={orgFilter}
            onChange={(e) => setOrgFilter(e.target.value)}
          >
            <option value="all">All clinics</option>
            {(orgs || []).map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <label htmlFor="adminauditlog-action" className="mb-1.5 block text-xs font-medium text-on-surface-variant">Action</label>
          <Select id="adminauditlog-action"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
          >
            <option value="all">All actions</option>
            {actionOptions.map((a) => (
              <option key={a} value={a}>
                {actionLabel(a)}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <label htmlFor="adminauditlog-status" className="mb-1.5 block text-xs font-medium text-on-surface-variant">Status</label>
          <Select id="adminauditlog-status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
          </Select>
        </div>

        {hasActiveFilters && (
          <button
            onClick={resetFilters}
            className="focus-ring inline-flex items-center gap-1.5 rounded-lg px-3 py-2.5 text-sm font-medium text-on-surface-variant hover:bg-surface-container"
          >
            <Filter size={14} />
            Reset filters
          </button>
        )}

        {!loading && !error && data && (
          <p className="text-sm text-on-surface-variant">
            {data.total} {data.total === 1 ? "entry" : "entries"}
          </p>
        )}
      </div>

      <Card className="mt-4 overflow-hidden">
        {loading && <SkeletonTable rows={8} cols={8} label="Loading…" />}

        {!loading && error && <ErrorState detail={error.message} onRetry={load} />}

        {!loading && !error && entries?.length === 0 && hasActiveFilters && (
          <EmptyState
            icon={Search}
            title="No entries match your filters"
            detail="Try a different search term, date range, or filter."
            action={
              <button onClick={resetFilters} className="text-accent focus-ring text-sm font-semibold">
                Clear filters
              </button>
            }
          />
        )}

        {!loading && !error && entries?.length === 0 && !hasActiveFilters && (
          <EmptyState icon={ScrollText} title="No admin actions logged yet" />
        )}

        {!loading && !error && entries?.length > 0 && (
          <Table>
              <THead>
                <tr>
                  <TH>Timestamp</TH>
                  <TH>Admin</TH>
                  <TH>Organization / Clinic</TH>
                  <TH>Action</TH>
                  <TH>Resource</TH>
                  <TH>Status</TH>
                  <TH>IP address</TH>
                  <TH />
                </tr>
              </THead>
              <TBody>
                {entries.map((entry) => (
                  <TR key={entry.id} className="transition hover:bg-surface-container/40">
                    <TD className="whitespace-nowrap text-on-surface-variant">
                      {new Date(entry.occurred_at).toLocaleString()}
                    </TD>
                    <TD>
                      <p className="font-medium text-on-surface">{entry.super_admin_name || "Unknown"}</p>
                      {entry.super_admin_email && (
                        <p className="text-xs text-on-surface-variant">{entry.super_admin_email}</p>
                      )}
                    </TD>
                    <TD>
                      {entry.target_org_id ? (
                        <Link
                          to={`/admin/organizations/${entry.target_org_id}`}
                          className="focus-ring font-medium text-on-surface hover:underline"
                        >
                          {entry.target_org_name || entry.target_org_id}
                        </Link>
                      ) : (
                        <span className="text-on-surface-variant">Platform-wide</span>
                      )}
                    </TD>
                    <TD className="font-medium text-on-surface">{actionLabel(entry.action)}</TD>
                    <TD className="text-on-surface-variant">
                      {entry.target_user_email || (entry.detail ? entry.detail.slice(0, 60) : "—")}
                    </TD>
                    <TD>
                      {/* Nearly every entry succeeds; only failures carry
                          colour, so they are findable at a glance. */}
                      {entry.status === "failed" ? (
                        <Chip tone="error">Failed</Chip>
                      ) : (
                        <span className="text-on-surface-variant">Success</span>
                      )}
                    </TD>
                    <TD className="text-on-surface-variant">{entry.ip_address || "—"}</TD>
                    <TD className="text-right">
                      <button
                        onClick={() => setDetailEntry(entry)}
                        className="focus-ring text-sm font-semibold text-on-surface-variant hover:underline"
                      >
                        View details
                      </button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
        )}

        {!loading && !error && entries?.length > 0 && totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-outline-variant px-5 py-3">
            <p className="text-xs text-on-surface-variant">
              Page {data.page} of {totalPages}
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="focus-ring inline-flex items-center gap-1 rounded-lg border border-outline-variant px-2.5 py-1.5 text-xs font-medium text-on-surface disabled:opacity-40"
              >
                <ChevronLeft size={14} />
                Prev
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="focus-ring inline-flex items-center gap-1 rounded-lg border border-outline-variant px-2.5 py-1.5 text-xs font-medium text-on-surface disabled:opacity-40"
              >
                Next
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </Card>

      <DetailModal entry={detailEntry} onClose={() => setDetailEntry(null)} />
    </AdminShell>
  );
}
