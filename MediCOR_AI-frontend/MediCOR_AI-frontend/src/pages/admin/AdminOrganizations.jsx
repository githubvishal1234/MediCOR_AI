import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Building2, Search } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, EmptyState, ErrorState, Table, THead, TH, TBody, TR, TD, SkeletonTable, Input, Select,
} from "../../components/ui";

import * as adminApi from "../../lib/adminApi";

const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "suspended", label: "Suspended" },
];

export default function AdminOrganizations() {
  const [orgs, setOrgs] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setOrgs(await adminApi.listOrganizations());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    if (!orgs) return null;
    const q = search.trim().toLowerCase();
    return orgs.filter((org) => {
      if (q && !org.name.toLowerCase().includes(q)) return false;
      if (statusFilter === "active" && org.suspended) return false;
      if (statusFilter === "suspended" && !org.suspended) return false;
      return true;
    });
  }, [orgs, search, statusFilter]);

  return (
    <AdminShell>
      <h1 className="font-display text-2xl font-bold text-on-surface">Organizations</h1>
      <p className="mt-1 text-sm text-on-surface-variant">Every clinic registered on the platform.</p>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-xs">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
          />
          <Input
            aria-label="Search clinics by name"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search clinics by name…"
            className="pl-9"
          />
        </div>
        <Select
          aria-label="Filter clinics by status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          {STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </Select>
        {!loading && !error && orgs && (
          <p className="text-sm text-on-surface-variant">
            {filtered.length} of {orgs.length} clinics
          </p>
        )}
      </div>

      <Card className="mt-4 overflow-hidden">
        {loading && <SkeletonTable rows={5} cols={6} label="Loading…" />}

        {!loading && error && <ErrorState detail={error.message} onRetry={load} />}

        {!loading && !error && orgs?.length === 0 && (
          <EmptyState icon={Building2} title="No organizations yet" />
        )}

        {!loading && !error && orgs?.length > 0 && filtered.length === 0 && (
          <EmptyState
            icon={Search}
            title="No clinics match your search"
            detail="Try a different name or status filter."
          />
        )}

        {!loading && !error && filtered?.length > 0 && (
          <Table>
              <THead>
                <tr>
                  <TH>Organization</TH>
                  <TH>Plan</TH>
                  <TH>Users</TH>
                  <TH>Patients</TH>
                  <TH>Voice minutes</TH>
                  <TH>Status</TH>
                </tr>
              </THead>
              <TBody>
                {filtered.map((org) => (
                  <TR key={org.id} className="transition hover:bg-surface-container/40">
                    <TD>
                      <Link
                        to={`/admin/organizations/${org.id}`}
                        className="focus-ring font-semibold text-on-surface hover:underline"
                      >
                        {org.name}
                      </Link>
                    </TD>
                    <TD className="text-on-surface-variant">{org.plan}</TD>
                    <TD className="text-on-surface-variant">{org.user_count}</TD>
                    <TD className="text-on-surface-variant">{org.patient_count}</TD>
                    <TD className="text-on-surface-variant">
                      {org.voice_minutes_used} / {org.voice_minutes_limit}
                    </TD>
                    <TD>
                      {/* Plain "Active" so a suspended clinic stands out;
                          a green chip on every row hid the one that mattered. */}
                      {org.suspended ? (
                        <Chip tone="error">Suspended</Chip>
                      ) : (
                        <span className="text-on-surface-variant">Active</span>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
        )}
      </Card>
    </AdminShell>
  );
}
