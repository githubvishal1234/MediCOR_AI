import { useEffect, useMemo, useState } from "react";
import { CreditCard, Search, RefreshCw } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, Button, Modal, ConfirmDialog, EmptyState, ErrorState, Table, THead, TH, TBody, TR, TD, SkeletonTable, Field, Select, InlineAlert, Input,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";

const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "canceled", label: "Canceled" },
  { value: "past_due", label: "Past due" },
];

// Active is the normal state; only past-due asks for action.
const STATUS_TONE = {
  active: "neutral",
  canceled: "neutral",
  past_due: "warning",
};

export default function AdminSubscriptions() {
  const [subs, setSubs] = useState(null);
  const [orgs, setOrgs] = useState(null);
  const [plans, setPlans] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [assignOpen, setAssignOpen] = useState(false);
  const [assignOrgId, setAssignOrgId] = useState("");
  const [assignPlanId, setAssignPlanId] = useState("");
  const [assignBusy, setAssignBusy] = useState(false);
  const [assignError, setAssignError] = useState(null);

  // Editing an existing subscription's plan/status
  const [editSub, setEditSub] = useState(null);
  const [editPlanId, setEditPlanId] = useState("");
  const [editStatus, setEditStatus] = useState("active");
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState(null);

  const [cancelConfirm, setCancelConfirm] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [subsData, orgsData, plansData] = await Promise.all([
        adminApi.listSubscriptions(),
        adminApi.listOrganizations(),
        adminApi.listPlans(),
      ]);
      setSubs(subsData);
      setOrgs(orgsData);
      setPlans(plansData);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const activePlans = useMemo(() => (plans || []).filter((p) => p.is_active), [plans]);

  // Clinics that don't yet have a subscription row — these are who
  // "Assign plan" should default to, since existing ones use "Change plan".
  const unassignedOrgs = useMemo(() => {
    if (!orgs || !subs) return [];
    const assignedIds = new Set(subs.map((s) => s.org_id));
    return orgs.filter((o) => !assignedIds.has(o.id));
  }, [orgs, subs]);

  const filtered = useMemo(() => {
    if (!subs) return null;
    const q = search.trim().toLowerCase();
    return subs.filter((s) => {
      if (q && !s.org_name.toLowerCase().includes(q) && !s.plan_name.toLowerCase().includes(q)) return false;
      if (statusFilter !== "all" && s.status !== statusFilter) return false;
      return true;
    });
  }, [subs, search, statusFilter]);

  function openAssign() {
    setAssignOrgId(unassignedOrgs[0]?.id || "");
    setAssignPlanId(activePlans[0]?.id || "");
    setAssignError(null);
    setAssignOpen(true);
  }

  async function handleAssign(e) {
    e.preventDefault();
    setAssignBusy(true);
    setAssignError(null);
    try {
      await adminApi.assignSubscription({ org_id: assignOrgId, plan_id: assignPlanId });
      setAssignOpen(false);
      await load();
    } catch (err) {
      setAssignError(err.message);
    } finally {
      setAssignBusy(false);
    }
  }

  function openEdit(sub) {
    setEditSub(sub);
    setEditPlanId(sub.plan_id);
    setEditStatus(sub.status);
    setEditError(null);
  }

  async function handleEditSave(e) {
    e.preventDefault();
    setEditBusy(true);
    setEditError(null);
    try {
      if (editPlanId !== editSub.plan_id) {
        await adminApi.assignSubscription({ org_id: editSub.org_id, plan_id: editPlanId, status: editStatus });
      } else if (editStatus !== editSub.status) {
        await adminApi.updateSubscriptionStatus(editSub.org_id, { status: editStatus });
      }
      setEditSub(null);
      await load();
    } catch (err) {
      setEditError(err.message);
    } finally {
      setEditBusy(false);
    }
  }

  async function confirmCancel() {
    if (!cancelConfirm) return;
    setCancelBusy(true);
    setCancelError(null);
    try {
      await adminApi.updateSubscriptionStatus(cancelConfirm.org_id, { status: "canceled" });
      setCancelConfirm(null);
      await load();
    } catch (err) {
      setCancelError(err.message);
    } finally {
      setCancelBusy(false);
    }
  }

  return (
    <AdminShell>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">Subscriptions</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Which plan each clinic is on, and its current subscription status.
          </p>
        </div>
        <Button onClick={openAssign} disabled={loading || !plans?.length}>
          <RefreshCw size={16} />
          Assign plan to clinic
        </Button>
      </div>

      {cancelError && (
        <p className="mt-4 rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{cancelError}</p>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-xs">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
          />
          <Input
            aria-label="Search subscriptions by clinic or plan"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by clinic or plan…"
            className="pl-9"
          />
        </div>
        <Select
          aria-label="Filter subscriptions by status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          {STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </Select>
        {!loading && !error && subs && (
          <p className="text-sm text-on-surface-variant">
            {filtered.length} of {subs.length} clinics
          </p>
        )}
      </div>

      <Card className="mt-4 overflow-hidden">
        {loading && <SkeletonTable rows={4} cols={6} label="Loading…" />}

        {!loading && error && <ErrorState detail={error.message} onRetry={load} />}

        {!loading && !error && subs?.length === 0 && (
          <EmptyState
            icon={CreditCard}
            title="No subscriptions yet"
            detail="Assign a plan to a clinic to get started."
            action={
              <Button onClick={openAssign} disabled={!plans?.length}>
                Assign plan to clinic
              </Button>
            }
          />
        )}

        {!loading && !error && subs?.length > 0 && filtered.length === 0 && (
          <EmptyState icon={Search} title="No subscriptions match your filters" />
        )}

        {!loading && !error && filtered?.length > 0 && (
          <Table>
              <THead>
                <tr>
                  <TH>Clinic</TH>
                  <TH>Plan</TH>
                  <TH>Voice minutes</TH>
                  <TH>Status</TH>
                  <TH>Updated</TH>
                  <TH />
                </tr>
              </THead>
              <TBody>
                {filtered.map((sub) => (
                  <TR key={sub.id} className="transition hover:bg-surface-container/40">
                    <TD className="font-semibold text-on-surface">{sub.org_name}</TD>
                    <TD className="text-on-surface-variant">{sub.plan_name}</TD>
                    <TD className="text-on-surface-variant">
                      {sub.voice_minutes_used} / {sub.voice_minutes_limit}
                    </TD>
                    <TD>
                      <Chip tone={STATUS_TONE[sub.status] || "neutral"}>
                        {sub.status.replace("_", " ")}
                      </Chip>
                    </TD>
                    <TD className="text-on-surface-variant">
                      {new Date(sub.updated_at).toLocaleDateString()}
                    </TD>
                    <TD>
                      <div className="flex items-center justify-end gap-3">
                        <button
                          onClick={() => openEdit(sub)}
                          className="focus-ring text-sm font-semibold text-on-surface-variant hover:underline"
                        >
                          Change plan
                        </button>
                        {sub.status !== "canceled" && (
                          <button
                            onClick={() => {
                              setCancelError(null);
                              setCancelConfirm(sub);
                            }}
                            className="focus-ring text-sm font-semibold text-on-surface-variant hover:underline"
                          >
                            Cancel
                          </button>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
        )}
      </Card>

      {/* ---------- Assign plan to a clinic without one yet ---------- */}
      <Modal open={assignOpen} title="Assign plan to clinic" onClose={() => !assignBusy && setAssignOpen(false)}>
        <form onSubmit={handleAssign} className="space-y-3.5">
          {assignError && <InlineAlert>{assignError}</InlineAlert>}
          <Field label="Clinic" required>
            <Select
              required
              value={assignOrgId}
              onChange={(e) => setAssignOrgId(e.target.value)}
            >
              <option value="" disabled>
                Select a clinic
              </option>
              {(unassignedOrgs.length ? unassignedOrgs : orgs || []).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </Select>
          </Field>
          {unassignedOrgs.length === 0 && orgs?.length > 0 && (
            <p className="-mt-2 text-xs text-on-surface-variant">
              Every clinic already has a subscription — use &quot;Change plan&quot; from the
              list to reassign one.
            </p>
          )}
          <Field label="Plan" required>
            <Select
              required
              value={assignPlanId}
              onChange={(e) => setAssignPlanId(e.target.value)}
            >
              <option value="" disabled>
                Select a plan
              </option>
              {activePlans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>
          <div className="flex justify-end gap-2 pt-1">
            <Button type="submit" disabled={assignBusy || !assignOrgId || !assignPlanId}>
              {assignBusy ? "Assigning…" : "Assign plan"}
            </Button>
          </div>
        </form>
      </Modal>

      {/* ---------- Change plan / status for an existing subscription ---------- */}
      <Modal open={!!editSub} title={`${editSub?.org_name} — subscription`} onClose={() => !editBusy && setEditSub(null)}>
        <form onSubmit={handleEditSave} className="space-y-3.5">
          {editError && <InlineAlert>{editError}</InlineAlert>}
          <Field label="Plan">
            <Select
              value={editPlanId}
              onChange={(e) => setEditPlanId(e.target.value)}
            >
              {editSub && !activePlans.some((p) => p.id === editSub.plan_id) && (
                <option value={editSub.plan_id}>{editSub.plan_name} (inactive)</option>
              )}
              {activePlans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            
            </Select>
          </Field>
          <Field label="Status">
            <Select
              value={editStatus}
              onChange={(e) => setEditStatus(e.target.value)}
            >
              <option value="active">Active</option>
              <option value="past_due">Past due</option>
              <option value="canceled">Canceled</option>
            
            </Select>
          </Field>
          <div className="flex justify-end gap-2 pt-1">
            <Button type="submit" disabled={editBusy}>
              {editBusy ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={!!cancelConfirm}
        title={`Cancel ${cancelConfirm?.org_name}'s subscription?`}
        detail="The clinic keeps its current plan limits until you assign a new plan, but its subscription status changes to canceled."
        confirmLabel="Cancel subscription"
        tone="error"
        busy={cancelBusy}
        onCancel={() => setCancelConfirm(null)}
        onConfirm={confirmCancel}
      />
    </AdminShell>
  );
}
