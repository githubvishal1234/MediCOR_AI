import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { LogIn, ShieldOff, ShieldCheck, Mail, Phone, MapPin, Globe, UserCog, LifeBuoy } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Button, Chip, ConfirmDialog, ErrorState, Skeleton, StatCell, InlineAlert, Table, THead, TH, TBody, TR, TD, Input, Select,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";
import * as api from "../../lib/api";
import { clinicRoleLabel } from "../../lib/format";

// Active is the normal state; only past-due asks for action.
const SUB_STATUS_TONE = { active: "neutral", canceled: "neutral", past_due: "warning" };

function ContactRow({ icon: Icon, label, value }) {
  if (!value) return null;
  return (
    <div className="flex items-start gap-2.5">
      <Icon size={15} className="mt-0.5 shrink-0 text-on-surface-variant" />
      <div>
        <p className="text-xs text-on-surface-variant">{label}</p>
        <p className="text-sm font-medium text-on-surface">{value}</p>
      </div>
    </div>
  );
}

export default function AdminOrgDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [org, setOrg] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);

  const [planDraft, setPlanDraft] = useState("");
  const [limitDraft, setLimitDraft] = useState("");
  const [reasonDraft, setReasonDraft] = useState("");

  // Subscription (Phase 5) — separate load, since an org may not have a
  // subscription row yet (e.g. created before Plans existed).
  const [subscription, setSubscription] = useState(null);
  const [subLoading, setSubLoading] = useState(true);
  const [plans, setPlans] = useState([]);
  const [subPlanDraft, setSubPlanDraft] = useState("");
  const [subBusy, setSubBusy] = useState(false);
  const [subError, setSubError] = useState(null);

  // null | { type: "suspend" | "reinstate" }
  const [orgConfirm, setOrgConfirm] = useState(null);
  // null | user object being (de)activated
  const [userConfirm, setUserConfirm] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.getOrganization(id);
      setOrg(data);
      setPlanDraft(data.plan);
      setLimitDraft(String(data.voice_minutes_limit));
      setReasonDraft(data.suspended_reason || "");
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  async function loadSubscription() {
    setSubLoading(true);
    try {
      // getSubscription resolves to `null` (not a 404) when the org
      // simply doesn't have a subscription row yet — see admin_plans.py.
      const [plansData, subData] = await Promise.all([adminApi.listPlans(), adminApi.getSubscription(id)]);
      setPlans(plansData);
      setSubscription(subData);
      setSubPlanDraft(subData?.plan_id || plansData.find((p) => p.is_active)?.id || "");
    } catch (err) {
      // Non-fatal: the rest of the org detail page still works without
      // subscription data, so just leave the panel showing its error state.
      setSubError(err.message);
    } finally {
      setSubLoading(false);
    }
  }

  useEffect(() => {
    load();
    loadSubscription();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function handleSavePlan(e) {
    e.preventDefault();
    setBusy(true);
    setActionError(null);
    try {
      const updated = await adminApi.updateOrganization(id, {
        plan: planDraft,
        voice_minutes_limit: Number(limitDraft),
      });
      setOrg(updated);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmToggleSuspend() {
    setBusy(true);
    setActionError(null);
    try {
      const updated = await adminApi.updateOrganization(id, {
        suspended: !org.suspended,
        suspended_reason: !org.suspended ? reasonDraft || "Suspended by platform admin" : null,
      });
      setOrg(updated);
      setOrgConfirm(null);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmToggleUserActive() {
    if (!userConfirm) return;
    setBusy(true);
    setActionError(null);
    try {
      await adminApi.updateOrgUser(id, userConfirm.id, { is_active: !userConfirm.is_active });
      setUserConfirm(null);
      await load();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleImpersonate() {
    setBusy(true);
    setActionError(null);
    try {
      const result = await adminApi.impersonateOrganization(id);
      // Mints a REGULAR org-scoped token — set it as the normal clinic
      // session token, then hand off to the ordinary /app experience.
      api.setToken(result.access_token);
      window.location.href = "/app";
    } catch (err) {
      setActionError(err.message);
      setBusy(false);
    }
  }

  async function handleAssignSubscription(e) {
    e.preventDefault();
    setSubBusy(true);
    setSubError(null);
    try {
      const updated = await adminApi.assignSubscription({ org_id: id, plan_id: subPlanDraft });
      setSubscription(updated);
      await load(); // org.plan / voice_minutes_limit are mirrored from the plan — refresh to show it
    } catch (err) {
      setSubError(err.message);
    } finally {
      setSubBusy(false);
    }
  }

  if (loading) {
    return (
      <AdminShell>
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-4 h-40 rounded-2xl" />
      </AdminShell>
    );
  }

  if (error || !org) {
    return (
      <AdminShell>
        <Card>
          <ErrorState detail={error?.message} onRetry={load} />
        </Card>
      </AdminShell>
    );
  }

  const hasContactInfo = org.contact_email || org.contact_phone || org.contact_address || org.contact_website;
  // Plans available to pick in the "Assign / Change plan" dropdown: active
  // plans, plus the org's current plan even if it's since been deactivated
  // (so an existing assignment never disappears from view). When this is
  // empty the Plans catalog simply has nothing in it yet.
  const assignablePlans = plans.filter((p) => p.is_active || p.id === subscription?.plan_id);

  return (
    <AdminShell>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <button
            onClick={() => navigate("/admin/organizations")}
            className="focus-ring inline-flex min-h-10 items-center rounded text-sm text-on-surface-variant hover:underline"
          >
            ← All organizations
          </button>
          <div className="mt-1 flex items-center gap-3">
            <h1 className="font-display text-2xl font-bold text-on-surface">{org.name}</h1>
            {/* Only the exception is marked; active is every org's normal state. */}
            {org.suspended && <Chip tone="error">Suspended</Chip>}
          </div>
          <p className="mt-1 text-xs text-on-surface-variant">
            Created {new Date(org.created_at).toLocaleDateString()}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* QW-8: the inbox itself lives at /admin/support, filtered.
              Duplicating the ticket list here would mean two places to
              keep the triage controls working. */}
          <Button
            variant="outline"
            onClick={() => navigate(`/admin/support?org=${encodeURIComponent(org.id)}`)}
          >
            <LifeBuoy size={16} />
            Support tickets
          </Button>
          <Button variant="outline" onClick={handleImpersonate} disabled={busy || org.suspended}>
            <LogIn size={16} />
            View as this organization
          </Button>
        </div>
      </div>

      {/* InlineAlert rather than a hand-styled <p>: it carries role="alert",
          so a failed suspend or impersonate is announced. */}
      {actionError && <InlineAlert className="mt-4">{actionError}</InlineAlert>}

      <Card as="section" aria-label="Organization at a glance" className="mt-6 overflow-hidden">
        <div className="grid grid-cols-3 gap-px bg-outline-variant">
          <StatCell label="Patients" value={org.patient_count} />
          <StatCell label="Calls" value={org.call_count} />
          <StatCell label="Appointments" value={org.appointment_count} />
        </div>
      </Card>

      {/* ---------- Contact info + Admin/owner ---------- */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="font-display text-base font-bold text-on-surface">Contact information</h2>
          {!hasContactInfo && (
            <p className="mt-2 text-sm text-on-surface-variant">
              This clinic hasn't filled in its Clinic Info settings yet.
            </p>
          )}
          {hasContactInfo && (
            <div className="mt-4 space-y-3">
              <ContactRow icon={Mail} label="Email" value={org.contact_email} />
              <ContactRow icon={Phone} label="Phone" value={org.contact_phone} />
              <ContactRow icon={MapPin} label="Address" value={org.contact_address} />
              <ContactRow icon={Globe} label="Website" value={org.contact_website} />
            </div>
          )}
        </Card>

        <Card className="p-5">
          <h2 className="font-display text-base font-bold text-on-surface">Admin / owner</h2>
          {!org.owner_name ? (
            <p className="mt-2 text-sm text-on-surface-variant">
              No active admin user found for this organization.
            </p>
          ) : (
            <div className="mt-4 flex items-start gap-2.5">
              <UserCog size={15} className="mt-0.5 shrink-0 text-on-surface-variant" />
              <div>
                <p className="text-sm font-medium text-on-surface">{org.owner_name}</p>
                <p className="text-sm text-on-surface-variant">{org.owner_email}</p>
              </div>
            </div>
          )}
        </Card>
      </div>

      <Card className="mt-6 p-5">
        <h2 className="font-display text-base font-bold text-on-surface">Plan &amp; usage</h2>
        <form onSubmit={handleSavePlan} className="mt-4 flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="adminorgdetail-plan" className="mb-1.5 block text-sm font-medium text-on-surface">Plan</label>
            <Input id="adminorgdetail-plan"
              value={planDraft}
              onChange={(e) => setPlanDraft(e.target.value)}
              className="w-48"
            />
          </div>
          <div>
            <label htmlFor="adminorgdetail-voice-minutes-limit" className="mb-1.5 block text-sm font-medium text-on-surface">Voice minutes limit</label>
            <Input id="adminorgdetail-voice-minutes-limit"
              type="number"
              min="0"
              value={limitDraft}
              onChange={(e) => setLimitDraft(e.target.value)}
              className="focus-ring w-40 rounded-lg border border-outline-variant bg-surface-lowest px-3 py-2 text-sm text-on-surface"
            />
          </div>
          <p className="text-sm text-on-surface-variant">
            Used: {org.voice_minutes_used} minutes
          </p>
          <Button type="submit" variant="outline" disabled={busy}>
            Save
          </Button>
        </form>
      </Card>

      <Card className="mt-6 p-5">
        <h2 className="font-display text-base font-bold text-on-surface">Subscription</h2>
        <p className="mt-1 text-sm text-on-surface-variant">
          The formal plan record from the Plans catalog (Phase 5). Assigning a plan here also updates the
          "Plan &amp; usage" fields above.
        </p>

        {subLoading && <Skeleton className="mt-4 h-8 w-48" />}

        {!subLoading && (
          <>
            {subscription && (
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <p className="text-sm text-on-surface">
                  Currently on <span className="font-semibold">{subscription.plan_name}</span>
                </p>
                <Chip tone={SUB_STATUS_TONE[subscription.status] || "neutral"}>
                  {subscription.status.replace("_", " ")}
                </Chip>
              </div>
            )}
            {!subscription && (
              <p className="mt-4 text-sm text-on-surface-variant">
                This clinic doesn't have a subscription record yet — assign one below.
              </p>
            )}

            {subError && (
              <p className="mt-3 rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{subError}</p>
            )}

            {assignablePlans.length === 0 ? (
              <p className="mt-4 text-sm text-on-surface-variant">
                No plans in the catalog yet —{" "}
                <Link to="/admin/plans" className="font-medium text-primary underline underline-offset-2">
                  create one in the Plans catalog
                </Link>{" "}
                first.
              </p>
            ) : (
              <form onSubmit={handleAssignSubscription} className="mt-4 flex flex-wrap items-end gap-4">
                <div>
                  <label htmlFor="adminorgdetail-subscription-change-plan-assign-plan" className="mb-1.5 block text-sm font-medium text-on-surface">
                    {subscription ? "Change plan" : "Assign plan"}
                  </label>
                  <Select id="adminorgdetail-subscription-change-plan-assign-plan"
                    value={subPlanDraft}
                    onChange={(e) => setSubPlanDraft(e.target.value)}
                    className="w-56"
                  >
                    {assignablePlans.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {!p.is_active ? " (inactive)" : ""}
                      </option>
                    ))}
                  </Select>
                </div>
                <Button type="submit" variant="outline" disabled={subBusy || !subPlanDraft}>
                  {subBusy ? "Saving…" : subscription ? "Change plan" : "Assign plan"}
                </Button>
              </form>
            )}
          </>
        )}
      </Card>

      <Card className="mt-6 p-5">
        <h2 className="font-display text-base font-bold text-on-surface">Suspension</h2>
        <p className="mt-1 text-sm text-on-surface-variant">
          Suspending an organization immediately blocks all of its dashboard logins and voice-agent API
          calls for that org. It does not affect any other organization.
        </p>
        {!org.suspended && (
          <Input
            aria-label="Reason for suspension (recorded in the audit log)"
            value={reasonDraft}
            onChange={(e) => setReasonDraft(e.target.value)}
            placeholder="Reason (shown in audit log)"
            className="focus-ring mt-3 w-full max-w-md rounded-lg border border-outline-variant bg-surface-lowest px-3 py-2 text-sm text-on-surface"
          />
        )}
        {org.suspended && org.suspended_reason && (
          <p className="mt-3 text-sm text-on-surface-variant">Reason: {org.suspended_reason}</p>
        )}
        <Button
          variant={org.suspended ? "outline" : "secondary"}
          className="mt-4"
          onClick={() => setOrgConfirm({ type: org.suspended ? "reinstate" : "suspend" })}
          disabled={busy}
        >
          {org.suspended ? <ShieldCheck size={16} /> : <ShieldOff size={16} />}
          {org.suspended ? "Reinstate organization" : "Suspend organization"}
        </Button>
      </Card>

      <Card className="mt-6 overflow-hidden">
        <div className="border-b border-outline-variant px-5 py-4">
          <h2 className="font-display text-base font-bold text-on-surface">Users</h2>
        </div>
        <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Email</TH>
                <TH>Role</TH>
                <TH>Status</TH>
                <TH />
              </tr>
            </THead>
            <TBody>
              {org.users.map((user) => (
                <TR key={user.id}>
                  <TD className="font-medium text-on-surface">{user.full_name}</TD>
                  <TD className="text-on-surface-variant">{user.email}</TD>
                  <TD className="text-on-surface-variant">{clinicRoleLabel(user.role)}</TD>
                  <TD>
                    {user.is_active ? (
                      <span className="text-on-surface-variant">Active</span>
                    ) : (
                      <Chip tone="neutral">Deactivated</Chip>
                    )}
                  </TD>
                  <TD className="text-right">
                    <button
                      onClick={() => setUserConfirm(user)}
                      disabled={busy}
                      className="focus-ring inline-flex min-h-10 items-center rounded px-2 text-sm font-semibold text-on-surface-variant hover:underline"
                    >
                      {user.is_active ? "Deactivate" : "Reactivate"}
                    </button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
      </Card>

      <ConfirmDialog
        open={!!orgConfirm}
        title={
          orgConfirm?.type === "suspend"
            ? `Suspend ${org.name}?`
            : `Reinstate ${org.name}?`
        }
        detail={
          orgConfirm?.type === "suspend"
            ? "This immediately blocks every user in this organization from logging in and stops the voice agent from taking calls for this org."
            : "This restores dashboard logins and voice-agent access for this organization."
        }
        confirmLabel={orgConfirm?.type === "suspend" ? "Suspend organization" : "Reinstate organization"}
        tone={orgConfirm?.type === "suspend" ? "error" : "success"}
        busy={busy}
        onCancel={() => setOrgConfirm(null)}
        onConfirm={confirmToggleSuspend}
      />

      <ConfirmDialog
        open={!!userConfirm}
        title={
          userConfirm?.is_active
            ? `Deactivate ${userConfirm?.full_name}?`
            : `Reactivate ${userConfirm?.full_name}?`
        }
        detail={
          userConfirm?.is_active
            ? "This user will immediately lose access to the clinic dashboard."
            : "This user will regain access to the clinic dashboard."
        }
        confirmLabel={userConfirm?.is_active ? "Deactivate user" : "Reactivate user"}
        tone={userConfirm?.is_active ? "error" : "success"}
        busy={busy}
        onCancel={() => setUserConfirm(null)}
        onConfirm={confirmToggleUserActive}
      />
    </AdminShell>
  );
}