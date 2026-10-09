/**
 * Platform support inbox — QW-8.
 *
 * `GET /support/tickets` was org-scoped and admin-only, so a clinic could
 * see its own tickets and nobody could see anyone else's. Submitting one
 * put it somewhere only a database query could reach. This is where they
 * arrive.
 *
 * One inbox with an org filter rather than a per-org section, because the
 * job is "what came in today", not "click through fifty clinics". The
 * org detail page links here pre-filtered instead of duplicating the list.
 *
 * Triage is the `status` column and nothing more. It already existed,
 * defaulted to "Open", and was never written again by anything;
 * `assigned_to` / `resolved_at` would need a migration and a second
 * person's workflow before they'd earn their keep.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { LifeBuoy, Building2, Loader2, Inbox, Mail } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, EmptyState, ErrorState, Skeleton, Tabs, Select, } from "../../components/ui";
import * as adminApi from "../../lib/adminApi";

const STATUSES = ["Open", "In Progress", "Resolved"];

// This is the platform team's inbox, so the colour marks what is waiting
// on them: an open ticket. Teal (`info`) means the AI at work elsewhere in
// the app, and a resolved ticket needs nothing from anyone.
const STATUS_TONE = {
  Open: "warning",
  "In Progress": "neutral",
  Resolved: "neutral",
};

/** Priority is free text on the model; these are the values the clinic form offers. */
const PRIORITY_TONE = {
  Urgent: "error",
  High: "warning",
  Normal: "neutral",
  Low: "neutral",
};


function whenLabel(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/**
 * The two views of this inbox.
 *
 * Named `SupportTabs` rather than `Tabs` because the shared primitive
 * now owns that name — this file used to define its own component
 * called `Tabs`, which is exactly how four different tab treatments
 * ended up in the product.
 *
 * The filter round-trips through the query string, so this stays a
 * controlled component.
 */
function SupportTabs({ tab, setFilter }) {
  return (
    <Tabs
      variant="underline"
      label="Support views"
      className="mt-5"
      value={tab}
      onChange={(key) => setFilter("tab", key === "tickets" ? "" : key)}
      items={[
        { id: "tickets", label: "Clinic tickets" },
        { id: "enquiries", label: "Contact enquiries" },
      ]}
    />
  );
}

/**
 * The other end of the public contact form.
 *
 * Separate from the ticket list rather than merged into it, because the
 * two are different things: a ticket comes from a clinic that has signed
 * in and carries an org, an enquiry comes from a stranger and cannot.
 * Merging them would need an "org" column that is empty half the time.
 *
 * It exists in the same change as the form for a reason: a public write
 * whose rows nobody reads is a working control over a capability that
 * does not exist -- the `webhooks` defect. If you add another public
 * write, add its reader too.
 */
function EnquiriesPanel() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    adminApi
      .listContactEnquiries()
      .then((data) => {
        setRows(data);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  useEffect(load, [load]);

  async function setStatus(row, status) {
    setBusyId(row.id);
    setActionError(null);
    try {
      const updated = await adminApi.updateContactEnquiry(row.id, { status });
      setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
    } catch (err) {
      setActionError(err?.message || "Couldn't update that enquiry.");
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return <ErrorState detail="We couldn't load contact enquiries." onRetry={load} className="py-24" />;
  }

  const newCount = rows.filter((r) => r.status === "new").length;

  return (
    <>
      <div className="mb-5 mt-5 flex flex-wrap items-center gap-3">
        <p className="text-sm text-on-surface-variant">
          Sent from the public contact page by people who do not have an account.
        </p>
        {!loading && (
          <span className="ml-auto text-sm text-on-surface-variant">
            {rows.length} enquir{rows.length === 1 ? "y" : "ies"}
            {newCount ? ` · ${newCount} new` : ""}
          </span>
        )}
      </div>

      {actionError && (
        <p className="mb-4 rounded-lg bg-error-bg px-3.5 py-2.5 text-sm text-error">{actionError}</p>
      )}

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-28 w-full rounded-2xl" />)}
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            icon={Inbox}
            title="No enquiries yet"
            detail="Messages sent from the public contact page arrive here."
            className="py-20"
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {rows.map((r) => (
            <Card key={r.id} className="p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Mail size={15} className="text-on-surface-variant" aria-hidden="true" />
                    <h2 className="font-display text-base font-bold text-on-surface">{r.name}</h2>
                    {/* Same rule as the tickets: a new enquiry is waiting on
                        someone; a handled one is not. */}
                    <Chip tone={r.status === "new" ? "warning" : "neutral"}>
                      {r.status === "new" ? "New" : "Handled"}
                    </Chip>
                  </div>
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-on-surface-variant">
                    <a href={`mailto:${r.email}`} className="focus-ring rounded-sm underline">
                      {r.email}
                    </a>
                    {r.phone && <span>{r.phone}</span>}
                    {r.clinic_name && (
                      <span className="flex items-center gap-1">
                        <Building2 size={12} /> {r.clinic_name}
                      </span>
                    )}
                    <span>{whenLabel(r.created_at)}</span>
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  {busyId === r.id && <Loader2 size={14} className="animate-spin text-on-surface-variant" />}
                  <button
                    onClick={() => setStatus(r, r.status === "new" ? "handled" : "new")}
                    disabled={busyId === r.id}
                    className="focus-ring rounded-lg border border-outline-variant px-3 py-2 text-sm font-medium text-on-surface hover:bg-surface-container disabled:opacity-60"
                  >
                    {r.status === "new" ? "Mark handled" : "Reopen"}
                  </button>
                </div>
              </div>

              {/* Written by a stranger: rendered as text, never parsed. */}
              <p className="mt-3 whitespace-pre-wrap border-t border-outline-variant pt-3 text-sm leading-relaxed text-on-surface-variant">
                {r.message}
              </p>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

export default function AdminSupport() {
  const [params, setParams] = useSearchParams();
  const orgFilter = params.get("org") || "";
  const statusFilter = params.get("status") || "";
  // In the query string, following the convention the rest of the app
  // uses for tab state somebody might link to (/app/calls?tab=quality).
  const tab = params.get("tab") === "enquiries" ? "enquiries" : "tickets";

  const [tickets, setTickets] = useState([]);
  const [orgs, setOrgs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    Promise.all([
      adminApi.listSupportTickets({ orgId: orgFilter, status: statusFilter }),
      adminApi.listOrganizations(),
    ])
      .then(([rows, organizations]) => {
        setTickets(rows);
        setOrgs(organizations);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, [orgFilter, statusFilter]);

  useEffect(load, [load]);

  function setFilter(key, value) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  async function changeStatus(ticket, status) {
    setBusyId(ticket.id);
    setActionError(null);
    try {
      const updated = await adminApi.updateSupportTicket(ticket.id, { status });
      setTickets((prev) =>
        // A status filter is active, so a ticket that no longer matches it
        // leaves the list rather than sitting there contradicting it.
        statusFilter && updated.status !== statusFilter
          ? prev.filter((t) => t.id !== updated.id)
          : prev.map((t) => (t.id === updated.id ? updated : t)),
      );
    } catch (err) {
      setActionError(err?.message || "Couldn't update that ticket.");
    } finally {
      setBusyId(null);
    }
  }

  const openCount = useMemo(
    () => tickets.filter((t) => t.status === "Open").length,
    [tickets],
  );

  const header = (
    <>
      <h1 className="font-display text-2xl font-bold text-on-surface">Support</h1>
      <p className="mt-1 text-sm text-on-surface-variant">
        Tickets from clinics, and enquiries from the public contact page.
      </p>
    </>
  );

  if (error) {
    return (
      <AdminShell>
        {header}
        <ErrorState detail="We couldn't load support tickets." onRetry={load} className="py-24" />
      </AdminShell>
    );
  }

  if (tab === "enquiries") {
    return (
      <AdminShell>
        {header}
        <SupportTabs tab={tab} setFilter={setFilter} />
        <EnquiriesPanel />
      </AdminShell>
    );
  }

  return (
    <AdminShell>
      {header}
      <SupportTabs tab={tab} setFilter={setFilter} />

      <div className="mb-5 mt-5 flex flex-wrap items-center gap-3">
        <Select
          value={orgFilter}
          onChange={(e) => setFilter("org", e.target.value)}
          aria-label="Filter by clinic"
          className="w-full sm:w-56"
        >
          <option value="">All clinics</option>
          {orgs.map((o) => (
            <option key={o.id} value={o.id}>{o.name}</option>
          ))}
        </Select>

        <Select
          value={statusFilter}
          onChange={(e) => setFilter("status", e.target.value)}
          aria-label="Filter by status"
          className="w-full sm:w-44"
        >
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>

        {!loading && (
          <span className="ml-auto text-sm text-on-surface-variant">
            {tickets.length} ticket{tickets.length === 1 ? "" : "s"}
            {openCount ? ` · ${openCount} open` : ""}
          </span>
        )}
      </div>

      {actionError && (
        <p className="mb-4 rounded-lg bg-error-bg px-3.5 py-2.5 text-sm text-error">{actionError}</p>
      )}

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-28 w-full rounded-2xl" />)}
        </div>
      ) : tickets.length === 0 ? (
        <Card>
          <EmptyState
            icon={Inbox}
            title={orgFilter || statusFilter ? "No tickets match these filters" : "No tickets yet"}
            detail={
              orgFilter || statusFilter
                ? "Try widening the filters above."
                : "When a clinic submits a support request from its dashboard, it appears here."
            }
            className="py-20"
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {tickets.map((t) => (
            <Card key={t.id} className="p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <LifeBuoy size={15} className="text-on-surface-variant" />
                    <h2 className="font-display text-base font-bold text-on-surface">{t.subject}</h2>
                    <Chip tone={PRIORITY_TONE[t.priority] || "neutral"}>{t.priority}</Chip>
                    <Chip tone={STATUS_TONE[t.status] || "neutral"}>{t.status}</Chip>
                  </div>
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-on-surface-variant">
                    <span className="flex items-center gap-1">
                      <Building2 size={12} /> {t.org_name || t.org_id}
                    </span>
                    {t.submitted_by && (
                      <span>
                        {t.submitted_by}
                        {t.submitted_by_email ? ` · ${t.submitted_by_email}` : ""}
                      </span>
                    )}
                    <span>{whenLabel(t.created_at)}</span>
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  {busyId === t.id && <Loader2 size={14} className="animate-spin text-on-surface-variant" />}
                  <Select
                    value={t.status}
                    onChange={(e) => changeStatus(t, e.target.value)}
                    disabled={busyId === t.id}
                    aria-label={`Status for ${t.subject}`}
                    className="w-auto px-2.5 py-1.5 text-xs font-medium"
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </Select>
                </div>
              </div>

              <p className="mt-3 whitespace-pre-wrap border-t border-outline-variant pt-3 text-sm leading-relaxed text-on-surface-variant">
                {t.message}
              </p>
            </Card>
          ))}
        </div>
      )}
    </AdminShell>
  );
}
