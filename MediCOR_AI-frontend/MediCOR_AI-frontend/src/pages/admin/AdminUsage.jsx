import { useEffect, useState } from "react";
import {
  BarChart3,
  PhoneCall,
  Clock,
  CalendarCheck,
  HeartPulse,
  Users,
  BookOpen,
  Database,
  KeyRound,
  Filter,
} from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, EmptyState, ErrorState, StatCell, Table, THead, TH, TBody, TR, TD, SkeletonTable, Input, Select,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function defaultRange() {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 29);
  return { startDate: isoDate(start), endDate: isoDate(end) };
}

function UsageBar({ used, limit }) {
  if (!limit) {
    return <span className="text-xs text-on-surface-variant">No limit set</span>;
  }
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const tone =
    pct >= 100 ? "var(--color-error)" : pct >= 80 ? "var(--color-warning)" : "var(--color-accent)";
  return (
    <div className="w-32">
      <div className="flex items-center justify-between text-xs text-on-surface-variant">
        <span>{used.toLocaleString()}</span>
        <span>{limit.toLocaleString()}</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-container">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: tone }} />
      </div>
    </div>
  );
}

export default function AdminUsage() {
  const [range, setRange] = useState(defaultRange());
  const [orgId, setOrgId] = useState("");

  const [orgs, setOrgs] = useState(null);
  const [usage, setUsage] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  async function loadOrgs() {
    try {
      setOrgs(await adminApi.listOrganizations());
    } catch {
      // Non-fatal — the clinic filter just won't populate; the usage
      // table itself will still load and show its own error if needed.
    }
  }

  async function loadUsage() {
    setLoading(true);
    setError(null);
    try {
      setUsage(
        await adminApi.getUsage({
          startDate: range.startDate,
          endDate: range.endDate,
          orgId: orgId || undefined,
        })
      );
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadOrgs();
  }, []);

  useEffect(() => {
    loadUsage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range.startDate, range.endDate, orgId]);

  const summary = usage?.summary;
  // What the strip shows: nothing (skeletons) while a new range loads.
  const s = loading ? null : summary;
  const clinics = usage?.clinics;

  return (
    <AdminShell>
      <h1 className="font-display text-2xl font-bold text-on-surface">Usage management</h1>
      <p className="mt-1 text-sm text-on-surface-variant">
        Real usage by clinic — calls, voice minutes, appointments, patients, AI activity, knowledge base, and
        EHR/API, against each clinic's plan limits.
      </p>

      {/* ---------- Filters ---------- */}
      <div className="mt-5 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="adminusage-from" className="mb-1.5 block text-xs font-medium text-on-surface-variant">From</label>
          <Input id="adminusage-from"
            type="date"
            value={range.startDate}
            max={range.endDate}
            onChange={(e) => setRange((r) => ({ ...r, startDate: e.target.value }))}
          />
        </div>
        <div>
          <label htmlFor="adminusage-to" className="mb-1.5 block text-xs font-medium text-on-surface-variant">To</label>
          <Input id="adminusage-to"
            type="date"
            value={range.endDate}
            min={range.startDate}
            max={isoDate(new Date())}
            onChange={(e) => setRange((r) => ({ ...r, endDate: e.target.value }))}
          />
        </div>
        <div>
          <label htmlFor="adminusage-clinic" className="mb-1.5 block text-xs font-medium text-on-surface-variant">Clinic</label>
          <Select id="adminusage-clinic"
            value={orgId}
            onChange={(e) => setOrgId(e.target.value)}
          >
            <option value="">All clinics</option>
            {(orgs || []).map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </div>
        <button
          onClick={() => {
            setRange(defaultRange());
            setOrgId("");
          }}
          className="focus-ring inline-flex items-center gap-1.5 rounded-lg px-3 py-2.5 text-sm font-medium text-on-surface-variant hover:bg-surface-container"
        >
          <Filter size={14} />
          Reset filters
        </button>
      </div>

      {/* ---------- Summary ----------
          One strip rather than eight floating cards, as on the Overview. */}
      {!loading && error ? (
        <Card className="mt-6">
          <ErrorState detail={error.message} onRetry={loadUsage} />
        </Card>
      ) : (
        <Card as="section" aria-label="Usage summary" className="mt-6 overflow-hidden">
          <div className="grid grid-cols-2 gap-px bg-outline-variant lg:grid-cols-4">
            <StatCell icon={BarChart3} label="Clinics in view" loading={!s} value={s?.clinic_count} />
            <StatCell icon={PhoneCall} label="Total calls" loading={!s} value={s?.total_calls} hint="Selected range" />
            <StatCell icon={Clock} label="Voice minutes" loading={!s}
              value={s?.total_voice_minutes_used_period?.toLocaleString()} hint="Selected range" />
            <StatCell icon={CalendarCheck} label="Appointments" loading={!s} value={s?.total_appointments}
              hint={s && `${s.total_ai_appointments} AI-booked`} />
            <StatCell icon={HeartPulse} label="Patients" loading={!s} value={s?.total_patients} hint="Current roster" />
            <StatCell icon={Users} label="Staff users" loading={!s} value={s?.total_users} />
            <StatCell icon={BookOpen} label="Knowledge base" loading={!s}
              value={s && s.total_kb_documents + s.total_kb_sources + s.total_faqs}
              hint={s && `${s.total_kb_documents} docs, ${s.total_kb_sources} sources, ${s.total_faqs} FAQs`} />
            <StatCell icon={Database} label="EHR connected" loading={!s}
              value={s?.total_ehr_integrations_connected}
              hint={s && `${s.total_api_keys_active} active API keys`} />
          </div>
        </Card>
      )}

      {/* ---------- Per-clinic usage ---------- */}
      <h2 className="mt-8 font-display text-lg font-bold text-on-surface">Usage by clinic</h2>
      <Card className="mt-3 overflow-hidden">
        {loading && <SkeletonTable rows={5} cols={9} label="Loading…" />}

        {!loading && error && <ErrorState detail={error.message} onRetry={loadUsage} />}

        {!loading && !error && clinics?.length === 0 && (
          <EmptyState
            icon={BarChart3}
            title="No usage data"
            detail="No clinics match the current filters, or none have activity in this range."
          />
        )}

        {!loading && !error && clinics?.length > 0 && (
          <Table>
              <THead>
                <tr>
                  <TH>Clinic</TH>
                  <TH>Plan</TH>
                  <TH>Calls</TH>
                  <TH>Voice min.</TH>
                  <TH>Appointments</TH>
                  <TH>Patients</TH>
                  <TH>Knowledge base</TH>
                  <TH>EHR / API</TH>
                  <TH>Plan usage (all-time)</TH>
                </tr>
              </THead>
              <TBody>
                {clinics.map((c) => (
                  <TR key={c.org_id} className="transition hover:bg-surface-container/40">
                    <TD className="font-semibold text-on-surface">{c.org_name}</TD>
                    <TD>
                      <Chip tone={c.has_subscription ? "info" : "neutral"}>{c.plan_name}</Chip>
                    </TD>
                    <TD className="text-on-surface-variant">{c.total_calls}</TD>
                    <TD className="text-on-surface-variant">{c.voice_minutes_used_period}</TD>
                    <TD className="text-on-surface-variant">
                      {c.appointments_total}
                      <span className="text-xs text-on-surface-variant"> ({c.appointments_ai_booked} AI)</span>
                    </TD>
                    <TD className="text-on-surface-variant">
                      {c.patient_count}
                      {c.plan_patient_limit != null && (
                        <span className="text-xs"> / {c.plan_patient_limit}</span>
                      )}
                    </TD>
                    <TD className="text-on-surface-variant">
                      <span className="text-xs">
                        {c.kb_document_count} docs · {c.kb_source_count} sources · {c.faq_count} FAQs
                      </span>
                    </TD>
                    <TD className="text-on-surface-variant">
                      <div className="flex flex-col gap-1 text-xs">
                        <span className="inline-flex items-center gap-1">
                          <Database size={12} />
                          {c.ehr_integrations_connected}/{c.ehr_integrations_total} connected
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <KeyRound size={12} />
                          {c.api_keys_active} active keys
                        </span>
                        <span className="text-on-surface-variant/70">
                          EHR syncs: {c.ehr_sync_count == null ? "Not available" : c.ehr_sync_count}
                        </span>
                      </div>
                    </TD>
                    <TD>
                      <UsageBar used={c.plan_voice_minutes_used_alltime} limit={c.plan_voice_minutes_limit} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
        )}
      </Card>
      <p className="mt-3 text-xs text-on-surface-variant">
        Calls, voice minutes, and appointments reflect activity within the selected date range. Patients, users,
        knowledge base, EHR, and API figures reflect current totals. "Plan usage" is each clinic's all-time voice
        minute counter against its plan limit, the same figure shown on its Billing page.
      </p>
    </AdminShell>
  );
}
