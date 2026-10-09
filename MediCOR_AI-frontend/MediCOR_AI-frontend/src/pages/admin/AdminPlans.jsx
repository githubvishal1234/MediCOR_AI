import { useEffect, useState } from "react";
import { Layers, Plus, Pencil, Power, PowerOff } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Card, Chip, Button, Modal, ConfirmDialog, EmptyState, ErrorState, Table, THead, TH, TBody, TR, TD, SkeletonTable, Field, Input, Textarea,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";

const EMPTY_FORM = {
  name: "",
  description: "",
  monthly_price_cents: "",
  voice_minutes_limit: "",
  user_limit: "",
  patient_limit: "",
  ehr_access: false,
  knowledge_base_access: false,
  features: "",
  is_active: true,
};

function formatMoney(cents) {
  return `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formToPayload(form) {
  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    monthly_price_cents: Math.round(Number(form.monthly_price_cents || 0) * 100),
    voice_minutes_limit: Number(form.voice_minutes_limit || 0),
    user_limit: form.user_limit === "" ? null : Number(form.user_limit),
    patient_limit: form.patient_limit === "" ? null : Number(form.patient_limit),
    ehr_access: form.ehr_access,
    knowledge_base_access: form.knowledge_base_access,
    features: form.features
      .split(",")
      .map((f) => f.trim())
      .filter(Boolean),
    is_active: form.is_active,
  };
}

function planToForm(plan) {
  return {
    name: plan.name,
    description: plan.description || "",
    monthly_price_cents: (plan.monthly_price_cents / 100).toString(),
    voice_minutes_limit: String(plan.voice_minutes_limit),
    user_limit: plan.user_limit === null || plan.user_limit === undefined ? "" : String(plan.user_limit),
    patient_limit: plan.patient_limit === null || plan.patient_limit === undefined ? "" : String(plan.patient_limit),
    ehr_access: plan.ehr_access,
    knowledge_base_access: plan.knowledge_base_access,
    features: (plan.features || []).join(", "),
    is_active: plan.is_active,
  };
}

function PlanForm({ form, setForm, onSubmit, busy, error, submitLabel }) {
  return (
    <form onSubmit={onSubmit} className="space-y-3.5">
      {error && <p className="rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{error}</p>}

      <Field label="Plan name" required>
            <Input
          required
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          placeholder="e.g. Professional Plan"
            />
          </Field>

      <Field label="Description">
            <Textarea
          rows={2}
          value={form.description}
          onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          placeholder="Short summary shown to admins"
            />
          </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Monthly price ($)" required>
            <Input
            required
            type="number"
            min="0"
            step="0.01"
            value={form.monthly_price_cents}
            onChange={(e) => setForm((f) => ({ ...f, monthly_price_cents: e.target.value }))}
            />
          </Field>
        <Field label="Voice minutes / mo" required>
            <Input
            required
            type="number"
            min="0"
            value={form.voice_minutes_limit}
            onChange={(e) => setForm((f) => ({ ...f, voice_minutes_limit: e.target.value }))}
            />
          </Field>
        <Field label="User limit">
            <Input
            type="number"
            min="0"
            value={form.user_limit}
            onChange={(e) => setForm((f) => ({ ...f, user_limit: e.target.value }))}
            placeholder="Blank = unlimited"
            />
          </Field>
        <Field label="Patient limit">
            <Input
            type="number"
            min="0"
            value={form.patient_limit}
            onChange={(e) => setForm((f) => ({ ...f, patient_limit: e.target.value }))}
            placeholder="Blank = unlimited"
            />
          </Field>
      </div>

      <Field label="Feature access (comma-separated)">
            <Input
          value={form.features}
          onChange={(e) => setForm((f) => ({ ...f, features: e.target.value }))}
          placeholder="priority_support, api_access, custom_branding"
            />
          </Field>

      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <input
            type="checkbox"
            checked={form.ehr_access}
            onChange={(e) => setForm((f) => ({ ...f, ehr_access: e.target.checked }))}
            className="focus-ring h-4 w-4 shrink-0 accent-accent"
          />
          EHR access
        </label>
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <input
            type="checkbox"
            checked={form.knowledge_base_access}
            onChange={(e) => setForm((f) => ({ ...f, knowledge_base_access: e.target.checked }))}
            className="focus-ring h-4 w-4 shrink-0 accent-accent"
          />
          Knowledge Base access
        </label>
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
            className="focus-ring h-4 w-4 shrink-0 accent-accent"
          />
          Active
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}

export default function AdminPlans() {
  const [plans, setPlans] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(EMPTY_FORM);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState(null);

  const [editPlan, setEditPlan] = useState(null); // plan object being edited
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState(null);

  const [detailPlan, setDetailPlan] = useState(null); // plan object for view-details modal

  const [toggleConfirm, setToggleConfirm] = useState(null); // plan being activated/deactivated
  const [toggleBusy, setToggleBusy] = useState(false);
  const [toggleError, setToggleError] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setPlans(await adminApi.listPlans());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    setCreateBusy(true);
    setCreateError(null);
    try {
      await adminApi.createPlan(formToPayload(createForm));
      setCreateOpen(false);
      setCreateForm(EMPTY_FORM);
      await load();
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setCreateBusy(false);
    }
  }

  function openEdit(plan) {
    setEditPlan(plan);
    setEditForm(planToForm(plan));
    setEditError(null);
  }

  async function handleEdit(e) {
    e.preventDefault();
    setEditBusy(true);
    setEditError(null);
    try {
      await adminApi.updatePlan(editPlan.id, formToPayload(editForm));
      setEditPlan(null);
      await load();
    } catch (err) {
      setEditError(err.message);
    } finally {
      setEditBusy(false);
    }
  }

  async function confirmToggleActive() {
    if (!toggleConfirm) return;
    setToggleBusy(true);
    setToggleError(null);
    try {
      if (toggleConfirm.is_active) {
        await adminApi.deactivatePlan(toggleConfirm.id);
      } else {
        await adminApi.activatePlan(toggleConfirm.id);
      }
      setToggleConfirm(null);
      await load();
    } catch (err) {
      setToggleError(err.message);
    } finally {
      setToggleBusy(false);
    }
  }

  return (
    <AdminShell>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">Plans</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            The platform's subscription plan catalog — limits and feature access clinics can be assigned.
          </p>
        </div>
        <Button
          onClick={() => {
            setCreateForm(EMPTY_FORM);
            setCreateError(null);
            setCreateOpen(true);
          }}
        >
          <Plus size={16} />
          Create plan
        </Button>
      </div>

      {toggleError && (
        <p className="mt-4 rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{toggleError}</p>
      )}

      <Card className="mt-5 overflow-hidden">
        {loading && <SkeletonTable rows={3} cols={8} label="Loading…" />}

        {!loading && error && <ErrorState detail={error.message} onRetry={load} />}

        {!loading && !error && plans?.length === 0 && (
          <EmptyState
            icon={Layers}
            title="No plans yet"
            detail="Create your first plan to start assigning it to clinics."
            action={
              <Button onClick={() => setCreateOpen(true)}>
                <Plus size={16} />
                Create plan
              </Button>
            }
          />
        )}

        {!loading && !error && plans?.length > 0 && (
          <Table>
              <THead>
                <tr>
                  <TH>Plan</TH>
                  <TH>Price</TH>
                  <TH>Voice minutes</TH>
                  <TH>Users</TH>
                  <TH>Patients</TH>
                  <TH>Clinics</TH>
                  <TH>Status</TH>
                  <TH />
                </tr>
              </THead>
              <TBody>
                {plans.map((plan) => (
                  <TR key={plan.id} className="transition hover:bg-surface-container/40">
                    <TD>
                      <button
                        onClick={() => setDetailPlan(plan)}
                        className="focus-ring font-semibold text-on-surface hover:underline"
                      >
                        {plan.name}
                      </button>
                    </TD>
                    <TD className="text-on-surface-variant">
                      {formatMoney(plan.monthly_price_cents)}/mo
                    </TD>
                    <TD className="text-on-surface-variant">{plan.voice_minutes_limit}</TD>
                    <TD className="text-on-surface-variant">
                      {plan.user_limit ?? "Unlimited"}
                    </TD>
                    <TD className="text-on-surface-variant">
                      {plan.patient_limit ?? "Unlimited"}
                    </TD>
                    <TD className="text-on-surface-variant">{plan.subscribed_org_count}</TD>
                    <TD>
                      {plan.is_active ? (
                        <span className="text-on-surface-variant">Active</span>
                      ) : (
                        <Chip tone="neutral">Inactive</Chip>
                      )}
                    </TD>
                    <TD>
                      <div className="flex items-center justify-end gap-3">
                        <button
                          onClick={() => openEdit(plan)}
                          className="focus-ring text-sm font-semibold text-on-surface-variant hover:underline"
                        >
                          <Pencil size={14} className="inline -mt-0.5 mr-1" />
                          Edit
                        </button>
                        <button
                          onClick={() => {
                            setToggleError(null);
                            setToggleConfirm(plan);
                          }}
                          className="focus-ring text-sm font-semibold text-on-surface-variant hover:underline"
                        >
                          {plan.is_active ? (
                            <>
                              <PowerOff size={14} className="inline -mt-0.5 mr-1" />
                              Deactivate
                            </>
                          ) : (
                            <>
                              <Power size={14} className="inline -mt-0.5 mr-1" />
                              Activate
                            </>
                          )}
                        </button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
        )}
      </Card>

      {/* ---------- Create plan ---------- */}
      <Modal open={createOpen} title="Create plan" onClose={() => !createBusy && setCreateOpen(false)}>
        <PlanForm
          form={createForm}
          setForm={setCreateForm}
          onSubmit={handleCreate}
          busy={createBusy}
          error={createError}
          submitLabel="Create plan"
        />
      </Modal>

      {/* ---------- Edit plan ---------- */}
      <Modal open={!!editPlan} title={`Edit ${editPlan?.name || "plan"}`} onClose={() => !editBusy && setEditPlan(null)}>
        <PlanForm
          form={editForm}
          setForm={setEditForm}
          onSubmit={handleEdit}
          busy={editBusy}
          error={editError}
          submitLabel="Save changes"
        />
      </Modal>

      {/* ---------- Plan details ---------- */}
      <Modal open={!!detailPlan} title={detailPlan?.name} onClose={() => setDetailPlan(null)}>
        {detailPlan && (
          <div className="space-y-3 text-sm">
            {detailPlan.description && (
              <p className="text-on-surface-variant">{detailPlan.description}</p>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-on-surface-variant">Monthly price</p>
                <p className="font-semibold text-on-surface">{formatMoney(detailPlan.monthly_price_cents)}</p>
              </div>
              <div>
                <p className="text-xs text-on-surface-variant">Voice minutes / mo</p>
                <p className="font-semibold text-on-surface">{detailPlan.voice_minutes_limit}</p>
              </div>
              <div>
                <p className="text-xs text-on-surface-variant">User limit</p>
                <p className="font-semibold text-on-surface">{detailPlan.user_limit ?? "Unlimited"}</p>
              </div>
              <div>
                <p className="text-xs text-on-surface-variant">Patient limit</p>
                <p className="font-semibold text-on-surface">{detailPlan.patient_limit ?? "Unlimited"}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {detailPlan.ehr_access && <Chip tone="info">EHR access</Chip>}
              {detailPlan.knowledge_base_access && <Chip tone="info">Knowledge Base access</Chip>}
              {detailPlan.features.map((f) => (
                <Chip key={f} tone="neutral">
                  {f}
                </Chip>
              ))}
            </div>
            <p className="text-xs text-on-surface-variant">
              {detailPlan.subscribed_org_count} clinic{detailPlan.subscribed_org_count === 1 ? "" : "s"} currently
              subscribed
            </p>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!toggleConfirm}
        title={
          toggleConfirm?.is_active
            ? `Deactivate ${toggleConfirm?.name}?`
            : `Activate ${toggleConfirm?.name}?`
        }
        detail={
          toggleConfirm?.is_active
            ? "Clinics already on this plan keep it. It just can't be newly assigned to any clinic while inactive."
            : "This plan becomes available to assign to clinics again."
        }
        confirmLabel={toggleConfirm?.is_active ? "Deactivate plan" : "Activate plan"}
        tone={toggleConfirm?.is_active ? "error" : "success"}
        busy={toggleBusy}
        onCancel={() => setToggleConfirm(null)}
        onConfirm={confirmToggleActive}
      />
    </AdminShell>
  );
}
