import { useCallback, useEffect, useState } from "react";
import { Phone, Plus, Trash2, PowerOff, Power } from "lucide-react";
import AdminShell from "../../components/AdminShell";
import {
  Button, Card, Chip, ConfirmDialog, EmptyState, ErrorState, Modal, Skeleton, Field, Input, Select, InlineAlert,
} from "../../components/ui";
import * as adminApi from "../../lib/adminApi";


/**
 * Assigns phone numbers to clinics.
 *
 * This is the routing table that lets one voice-agent deployment answer
 * for many clinics: an inbound call's dialled number is looked up here to
 * decide whose patients, doctors and calendar the AI is working with.
 *
 * Platform-level on purpose. A number belongs to exactly one clinic, so a
 * clinic must not be able to claim one itself — pointing a competitor's
 * number at your own practice would redirect their patients.
 */
export default function AdminPhoneNumbers() {
  const [numbers, setNumbers] = useState([]);
  const [orgs, setOrgs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);
  const [form, setForm] = useState({ org_id: "", e164: "", label: "", trunk_id: "" });

  const [releasing, setReleasing] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    Promise.all([adminApi.listPhoneNumbers(), adminApi.listOrganizations()])
      .then(([nums, organizations]) => {
        setNumbers(nums);
        setOrgs(organizations);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  useEffect(load, [load]);

  function openAdd() {
    setForm({ org_id: orgs[0]?.id || "", e164: "", label: "", trunk_id: "" });
    setFormError(null);
    setShowAdd(true);
  }

  async function handleAdd(e) {
    e.preventDefault();
    setFormError(null);
    if (!form.org_id) {
      setFormError("Choose which clinic this number belongs to.");
      return;
    }
    setSaving(true);
    try {
      await adminApi.createPhoneNumber({
        org_id: form.org_id,
        e164: form.e164.trim(),
        label: form.label.trim() || undefined,
        trunk_id: form.trunk_id.trim() || undefined,
        direction: "inbound",
        active: true,
      });
      setShowAdd(false);
      load();
    } catch (err) {
      // 409 names the current owner — worth showing verbatim, since
      // routing a number to the wrong clinic misdirects real patients.
      setFormError(err?.message || "Couldn't assign that number.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(row) {
    await adminApi.updatePhoneNumber(row.id, { active: !row.active });
    load();
  }

  async function confirmRelease() {
    if (!releasing) return;
    await adminApi.releasePhoneNumber(releasing.id);
    setReleasing(null);
    load();
  }

  return (
    <AdminShell>
      <h1 className="font-display text-2xl font-bold text-on-surface">Phone numbers</h1>
      <p className="mt-1 text-sm text-on-surface-variant">
        Which clinic answers when each number is dialled.
      </p>

      <div className="mb-4 mt-5 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-on-surface-variant">
          Calls to an unassigned number fall back to the agent's default clinic
          instead of being routed. Assign every number you serve.
        </p>
        <Button onClick={openAdd} disabled={loading || orgs.length === 0}>
          <Plus size={16} className="mr-1.5" />
          Assign number
        </Button>
      </div>

      <Card className="overflow-hidden">
        {loading ? (
          <div className="space-y-3 p-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : error ? (
          <ErrorState detail="We couldn't load phone numbers." onRetry={load} className="py-20" />
        ) : numbers.length === 0 ? (
          <EmptyState
            icon={Phone}
            title="No numbers assigned yet"
            detail="Until a number is routed to a clinic, every inbound call is handled by the agent's fallback clinic."
          />
        ) : (
          <div className="divide-y divide-outline-variant">
            {numbers.map((row) => (
              <div key={row.id} className="flex items-center gap-4 px-5 py-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-container">
                  <Phone size={16} className="text-on-surface-variant" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-sm font-semibold text-on-surface">
                    {row.e164}
                  </p>
                  <p className="truncate text-xs text-on-surface-variant">
                    {row.org_name}
                    {row.label ? ` · ${row.label}` : ""}
                    {row.trunk_id ? ` · trunk ${row.trunk_id}` : ""}
                  </p>
                </div>
                {/* Routing is the normal state. A paused number is the one
                    to notice: calls to it reach no clinic. */}
                {row.active ? (
                  <span className="shrink-0 text-sm text-on-surface-variant">Routing</span>
                ) : (
                  <Chip tone="warning">Paused</Chip>
                )}
                <button
                  onClick={() => toggleActive(row)}
                  title={row.active ? "Stop routing calls here" : "Resume routing"}
                  aria-label={row.active ? "Pause routing" : "Resume routing"}
                  className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-low"
                >
                  {row.active ? <PowerOff size={16} aria-hidden="true" /> : <Power size={16} aria-hidden="true" />}
                </button>
                <button
                  onClick={() => setReleasing(row)}
                  title="Release this number"
                  aria-label="Release number"
                  className="focus-ring rounded-md p-2 text-error hover:bg-error-bg"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal open={showAdd} title="Assign a phone number" onClose={() => setShowAdd(false)}>
        <form onSubmit={handleAdd} className="space-y-4">
          <Field label="Clinic">
            <Select
              value={form.org_id}
              onChange={(e) => setForm({ ...form, org_id: e.target.value })}
            >
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Phone number"
            required
            hint="Spacing and a leading + are optional — the number is normalised so it matches however your carrier presents it."
          >
            <Input
              required
              inputMode="tel"
              value={form.e164}
              onChange={(e) => setForm({ ...form, e164: e.target.value })}
              placeholder="+91 98765 43210"
            />
          </Field>

          <Field label="Label" hint="Optional">
            <Input
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              placeholder="Main reception"
            />
          </Field>

          <Field label="SIP trunk ID" hint="Optional">
            <Input
              value={form.trunk_id}
              onChange={(e) => setForm({ ...form, trunk_id: e.target.value })}
              placeholder="ST_xxxxxxxx"
            />
          </Field>

          {formError && <InlineAlert>{formError}</InlineAlert>}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setShowAdd(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Assigning…" : "Assign number"}
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={!!releasing}
        title="Release this number?"
        detail={
          releasing
            ? `${releasing.e164} will stop routing to ${releasing.org_name}. Calls to it will fall back to the agent's default clinic until it is reassigned.`
            : ""
        }
        confirmLabel="Release"
        onConfirm={confirmRelease}
        onCancel={() => setReleasing(null)}
      />
    </AdminShell>
  );
}
