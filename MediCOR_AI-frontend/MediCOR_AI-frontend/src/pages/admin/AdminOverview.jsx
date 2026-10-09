import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Building2,
  Users,
  UserCheck,
  HeartPulse,
  PhoneCall,
  Clock,
  ShieldOff,
  CalendarCheck,
  CreditCard,
  Activity,
  ScrollText,
} from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, EmptyState, ErrorState, Skeleton, StatCell, Table, THead, TH, TBody, TR, TD,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";

// The chip's word says what happened; colour is kept for the one event
// that needs attention. A completed call is routine, and teal (`info`)
// means the AI at work, which a new org is not.
const ACTIVITY_STYLE = {
  org_created: { tone: "neutral", label: "New org" },
  org_suspended: { tone: "error", label: "Suspended" },
  super_admin_action: { tone: "neutral", label: "Admin action" },
  call_completed: { tone: "neutral", label: "Call" },
};

function ActivityRow({ item }) {
  const style = ACTIVITY_STYLE[item.type] || { tone: "neutral", label: item.type };
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Chip tone={style.tone}>{style.label}</Chip>
          <p className="truncate text-sm font-semibold text-on-surface">{item.title}</p>
        </div>
        {item.detail && <p className="mt-1 text-sm text-on-surface-variant">{item.detail}</p>}
        {item.org_id && (
          <Link
            to={`/admin/organizations/${item.org_id}`}
            className="focus-ring text-xs text-on-surface-variant hover:underline"
          >
            {item.org_name || item.org_id}
          </Link>
        )}
      </div>
      <p className="whitespace-nowrap text-xs text-on-surface-variant">
        {new Date(item.occurred_at).toLocaleString()}
      </p>
    </div>
  );
}

export default function AdminOverview() {
  const [stats, setStats] = useState(null);
  const [statsError, setStatsError] = useState(null);
  const [statsLoading, setStatsLoading] = useState(true);

  const [activity, setActivity] = useState(null);
  const [activityError, setActivityError] = useState(null);
  const [activityLoading, setActivityLoading] = useState(true);

  async function loadStats() {
    setStatsLoading(true);
    setStatsError(null);
    try {
      setStats(await adminApi.getPlatformStats());
    } catch (err) {
      setStatsError(err);
    } finally {
      setStatsLoading(false);
    }
  }

  async function loadActivity() {
    setActivityLoading(true);
    setActivityError(null);
    try {
      setActivity(await adminApi.getRecentActivity(15));
    } catch (err) {
      setActivityError(err);
    } finally {
      setActivityLoading(false);
    }
  }

  useEffect(() => {
    loadStats();
    loadActivity();
  }, []);

  return (
    <AdminShell>
      <h1 className="font-display text-2xl font-bold text-on-surface">Platform overview</h1>
      <p className="mt-1 text-sm text-on-surface-variant">
        Aggregate usage across every organization on MediCOR AI.
      </p>

      {/* ---------- Stat strip ----------
          One strip, as on the clinic Dashboard, rather than eight floating
          cards each with its own icon tile. */}
      {!statsLoading && statsError ? (
        <Card className="mt-6">
          <ErrorState detail={statsError.message} onRetry={loadStats} />
        </Card>
      ) : (
        <Card as="section" aria-label="Platform at a glance" className="mt-6 overflow-hidden">
          <div className="grid grid-cols-2 gap-px bg-outline-variant lg:grid-cols-4">
            <StatCell icon={Building2} label="Total clinics" loading={!stats} value={stats?.org_count} />
            <StatCell icon={UserCheck} label="Active clinics" loading={!stats} value={stats?.active_org_count} />
            <StatCell icon={ShieldOff} label="Suspended clinics" loading={!stats} value={stats?.suspended_org_count} />
            <StatCell icon={Users} label="Total users" loading={!stats} value={stats?.user_count}
              hint={stats && `${stats.active_user_count} active`} />
            <StatCell icon={HeartPulse} label="Total patients" loading={!stats} value={stats?.patient_count} />
            <StatCell icon={PhoneCall} label="Total calls" loading={!stats} value={stats?.total_calls}
              hint={stats && `${stats.calls_today} today`} />
            <StatCell icon={Clock} label="Voice minutes used" loading={!stats}
              value={stats?.total_voice_minutes_used?.toLocaleString()}
              hint={stats && `of ${stats.total_voice_minutes_limit?.toLocaleString()} limit`} />
            <StatCell icon={CalendarCheck} label="Appointments" loading={!stats} value={stats?.total_appointments}
              hint={stats && `${stats.appointments_today} today`} />
          </div>
        </Card>
      )}

      {/* ---------- Subscription / plan overview ---------- */}
      <h2 className="mt-8 flex items-center gap-2 font-display text-lg font-bold text-on-surface">
        <CreditCard size={18} className="text-on-surface-variant" />
        Subscription &amp; plan overview
      </h2>
      <Card className="mt-3 overflow-hidden">
        {statsLoading && (
          <div className="space-y-0 divide-y divide-outline-variant">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="px-5 py-4">
                <Skeleton className="h-5 w-2/3" />
              </div>
            ))}
          </div>
        )}

        {!statsLoading && statsError && <ErrorState detail={statsError.message} onRetry={loadStats} />}

        {!statsLoading && !statsError && stats?.plan_breakdown?.length === 0 && (
          <EmptyState icon={CreditCard} title="No organizations yet" />
        )}

        {!statsLoading && !statsError && stats?.plan_breakdown?.length > 0 && (
          <Table>
              <THead>
                <tr className="border-b border-outline-variant text-xs font-medium text-on-surface-variant">
                  <TH>Plan</TH>
                  <TH>Organizations</TH>
                  <TH>Voice minutes used</TH>
                  <TH>Voice minutes limit</TH>
                </tr>
              </THead>
              <TBody>
                {stats.plan_breakdown.map((row) => (
                  <TR key={row.plan}>
                    <TD className="font-semibold text-on-surface">{row.plan}</TD>
                    <TD className="text-on-surface-variant">{row.org_count}</TD>
                    <TD className="text-on-surface-variant">
                      {row.voice_minutes_used.toLocaleString()}
                    </TD>
                    <TD className="text-on-surface-variant">
                      {row.voice_minutes_limit.toLocaleString()}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
        )}
      </Card>

      {/* ---------- Recent platform activity ---------- */}
      <h2 className="mt-8 flex items-center gap-2 font-display text-lg font-bold text-on-surface">
        <Activity size={18} className="text-on-surface-variant" />
        Recent platform activity
      </h2>
      <Card className="mt-3 overflow-hidden">
        {activityLoading && (
          <div className="space-y-0 divide-y divide-outline-variant">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="px-5 py-4">
                <Skeleton className="h-5 w-2/3" />
              </div>
            ))}
          </div>
        )}

        {!activityLoading && activityError && (
          <ErrorState detail={activityError.message} onRetry={loadActivity} />
        )}

        {!activityLoading && !activityError && activity?.length === 0 && (
          <EmptyState icon={ScrollText} title="No platform activity yet" />
        )}

        {!activityLoading && !activityError && activity?.length > 0 && (
          <div className="divide-y divide-outline-variant">
            {activity.map((item, i) => (
              <ActivityRow key={`${item.type}-${item.occurred_at}-${i}`} item={item} />
            ))}
          </div>
        )}
      </Card>
    </AdminShell>
  );
}
