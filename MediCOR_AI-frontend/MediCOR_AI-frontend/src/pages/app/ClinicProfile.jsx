import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Building2, Stethoscope, ClipboardList, Plus, Trash2, Pencil,
  Check, X, Globe2, AlertTriangle,
} from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Button, Chip, Skeleton, ErrorState, EmptyState, Field, Input, Select,
  Textarea, Tabs, InlineAlert, Modal,
} from "../../components/ui";
import * as api from "../../lib/api";
import { ApiError } from "../../lib/api";


const DAYS = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
];

function hourLabel(h) {
  if (h === 0) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

const TABS = [
  { key: "profile", label: "Clinic Profile", icon: Building2 },
  { key: "services", label: "Services", icon: ClipboardList },
  { key: "doctors", label: "Doctors", icon: Stethoscope },
];

/**
 * Everything a clinic needs to configure about itself.
 *
 * The voice agent already reads all of this to answer callers — clinic
 * timings, address, services and fees, and the doctor roster
 * that drives every availability check. Until now none of it could be
 * filled in from the dashboard, so a clinic that signed up had empty
 * clinic info forever and no doctors at all, which meant it could not
 * book anything.
 */
export default function ClinicProfile() {
  // The tab lives in the query string so the dashboard's setup checklist
  // can send a clinic straight to the section that fixes its outstanding
  // step, rather than to the page's default tab.
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab = TABS.some((t) => t.key === requested) ? requested : "profile";
  const setTab = (key) =>
    setParams(key === "profile" ? {} : { tab: key }, { replace: true });

  return (
    <AppShell
      title="Clinic Profile"
      subtitle="The facts your AI receptionist tells callers, and the doctors it can book with."
    >
      {/* The tab id stays in the query string: the dashboard setup
          checklist deep-links to ?tab=doctors, so this must remain
          controlled rather than holding its own state. */}
      <Tabs
        variant="underline"
        label="Clinic sections"
        className="mb-6"
        value={tab}
        onChange={setTab}
        items={TABS.map(({ key, label, icon: Icon }) => ({
          id: key,
          label: (
            <span className="flex items-center gap-2">
              <Icon size={15} aria-hidden="true" />
              {label}
            </span>
          ),
        }))}
      />

      {tab === "profile" && <ProfileSection />}
      {tab === "services" && <ServicesSection />}
      {tab === "doctors" && <DoctorsSection />}
    </AppShell>
  );
}

/* ------------------------------------------------------------------ */
/* Clinic profile                                                      */
/* ------------------------------------------------------------------ */

const PROFILE_FIELDS = [
  { key: "timings_text", label: "Opening hours", placeholder: "Mon–Fri 9:00 AM – 5:00 PM, Sat 9:00 AM – 1:00 PM", textarea: true },
  { key: "address", label: "Address", placeholder: "12 Residency Road, Bengaluru 560025" },
  { key: "phone", label: "Phone", placeholder: "+91 80 1234 5678" },
  { key: "email", label: "Email", placeholder: "reception@yourclinic.com" },
  { key: "website", label: "Website", placeholder: "https://yourclinic.com" },
  { key: "general_info", label: "General information", placeholder: "Parking, accessibility, what to expect on a first visit…", textarea: true },
  // PF-14. Deliberately blank until a clinician writes it: aftercare
  // wording is a clinical statement, and nothing is sent until this is
  // filled in. The hint says so, so an empty field reads as a decision
  // rather than an oversight.
  {
    key: "follow_up_message",
    label: "Post-visit message",
    placeholder: "Written by your clinic — nothing is sent until you fill this in.",
    hint:
      "Sent to patients a few days after their appointment. Use {name} for their " +
      "first name. This is clinical wording, so it should be written or approved " +
      "by a clinician — we deliberately don't provide a default.",
    textarea: true,
  },
  {
    key: "follow_up_delay_days",
    label: "Days to wait before sending it",
    placeholder: "3",
    type: "number",
  },
];

function ProfileSection() {
  const [form, setForm] = useState(null);
  const [timezones, setTimezones] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    Promise.all([api.getClinicInfo(), api.listTimezones().catch(() => [])])
      .then(([info, zones]) => {
        setForm(info);
        setTimezones(zones);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  useEffect(() => load(), [load]);

  function setField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
    setSaved(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const payload = { timezone: form.timezone };
      PROFILE_FIELDS.forEach(({ key }) => {
        payload[key] = form[key] || null;
      });
      const updated = await api.updateClinicInfo(payload);
      setForm(updated);
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Couldn't save the clinic profile.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (error) return <ErrorState detail="We couldn't load your clinic profile." onRetry={load} className="py-20" />;

  return (
    <form onSubmit={handleSubmit} className="grid gap-6 lg:grid-cols-3">
      <Card className="p-6 lg:col-span-1">
        <div className="mb-1 flex items-center gap-2">
          <Globe2 size={16} className="text-accent" />
          <h2 className="font-display text-base font-bold text-on-surface">Timezone</h2>
        </div>
        <p className="mb-4 text-sm text-on-surface-variant">
          Every appointment time, working hour and slot the AI offers is read in this
          zone. Get it wrong and callers are offered the wrong times.
        </p>
        <Field label="Clinic timezone">
          <Select
            value={form.timezone || "UTC"}
            onChange={(e) => setField("timezone", e.target.value)}
          >
            {(timezones.length ? timezones : [form.timezone || "UTC"]).map((z) => (
              <option key={z} value={z}>{z}</option>
            ))}
          </Select>
        </Field>
        {form.timezone_label && (
          <p className="mt-2 text-xs text-on-surface-variant">
            Currently {form.timezone_label}
          </p>
        )}
        {timezones.length === 0 && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-warning">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            Timezone list unavailable — the server may be missing its timezone database.
          </p>
        )}
      </Card>

      <Card className="p-6 lg:col-span-2">
        <div className="mb-1 flex items-center gap-2">
          <Building2 size={16} className="text-accent" />
          <h2 className="font-display text-base font-bold text-on-surface">What the AI tells callers</h2>
        </div>
        <p className="mb-4 text-sm text-on-surface-variant">
          Leave a field blank and the AI will say it doesn't have that detail rather than guess.
        </p>

        <div className="space-y-4">
          {PROFILE_FIELDS.map(({ key, label, placeholder, textarea, hint, type }) => (
            <Field key={key} label={label} hint={hint}>
              {textarea ? (
                <Textarea
                  rows={3}
                  value={form[key] || ""}
                  onChange={(e) => setField(key, e.target.value)}
                  placeholder={placeholder}
                />
              ) : (
                <Input
                  type={type || "text"}
                  min={type === "number" ? 1 : undefined}
                  max={type === "number" ? 90 : undefined}
                  inputMode={type === "number" ? "numeric" : undefined}
                  value={form[key] ?? ""}
                  onChange={(e) =>
                    setField(
                      key,
                      type === "number"
                        ? (e.target.value === "" ? "" : Number(e.target.value))
                        : e.target.value
                    )
                  }
                  placeholder={placeholder}
                />
              )}
            </Field>
          ))}
        </div>

        {saveError && <InlineAlert className="mt-4">{saveError}</InlineAlert>}

        <div className="mt-5 flex items-center gap-3">
          <Button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save profile"}
          </Button>
          {saved && (
            <span className="flex items-center gap-1.5 text-sm text-accent">
              <Check size={15} /> Saved
            </span>
          )}
        </div>
      </Card>
    </form>
  );
}

/* ------------------------------------------------------------------ */
/* Services                                                            */
/* ------------------------------------------------------------------ */

function ServicesSection() {
  const [services, setServices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [draft, setDraft] = useState({ name: "", description: "", fee_label: "" });
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState({});
  const [actionError, setActionError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    api
      .listServices()
      .then((data) => { setServices(data); setLoading(false); })
      .catch(() => { setError(true); setLoading(false); });
  }, []);

  useEffect(() => load(), [load]);

  async function addService(e) {
    e.preventDefault();
    if (!draft.name.trim()) return;
    setAdding(true);
    setActionError(null);
    try {
      const created = await api.createService({
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        fee_label: draft.fee_label.trim() || null,
        active: true,
      });
      setServices((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      setDraft({ name: "", description: "", fee_label: "" });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't add that service.");
    } finally {
      setAdding(false);
    }
  }

  async function saveEdit(id) {
    setActionError(null);
    try {
      const updated = await api.updateService(id, {
        name: editDraft.name?.trim(),
        description: editDraft.description?.trim() || null,
        fee_label: editDraft.fee_label?.trim() || null,
      });
      setServices((prev) => prev.map((s) => (s.id === id ? updated : s)));
      setEditingId(null);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't save that service.");
    }
  }

  async function removeService(id) {
    setActionError(null);
    const previous = services;
    setServices((prev) => prev.filter((s) => s.id !== id));
    try {
      await api.deleteService(id);
    } catch {
      setServices(previous);
      setActionError("Couldn't remove that service.");
    }
  }

  if (loading) return <Skeleton className="h-72 w-full" />;
  if (error) return <ErrorState detail="We couldn't load your services." onRetry={load} className="py-20" />;

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="p-6 lg:col-span-2">
        <h2 className="mb-1 font-display text-base font-bold text-on-surface">Services &amp; fees</h2>
        <p className="mb-4 text-sm text-on-surface-variant">
          What the AI lists when a caller asks what you offer, or what something costs.
        </p>

        {actionError && <p className="mb-3 rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{actionError}</p>}

        {services.length === 0 ? (
          <EmptyState icon={ClipboardList} title="No services yet" detail="Add your first service to the right." />
        ) : (
          <ul className="divide-y divide-outline-variant">
            {services.map((s) => (
              <li key={s.id} className="py-3">
                {editingId === s.id ? (
                  <div className="space-y-2">
                    <Input
                      value={editDraft.name || ""}
                      onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))}
                      aria-label="Service name"
                    />
                    <Input
                      value={editDraft.description || ""}
                      onChange={(e) => setEditDraft((d) => ({ ...d, description: e.target.value }))}
                      placeholder="Description"
                      aria-label="Service description"
                    />
                    <div className="flex gap-2">
                      <Input
                        value={editDraft.fee_label || ""}
                        onChange={(e) => setEditDraft((d) => ({ ...d, fee_label: e.target.value }))}
                        placeholder="Fee, e.g. ₹800"
                        aria-label="Service fee"
                      />
                      <Button type="button" onClick={() => saveEdit(s.id)}>Save</Button>
                      <Button type="button" variant="outline" onClick={() => setEditingId(null)}>Cancel</Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-on-surface">{s.name}</p>
                      {s.description && (
                        <p className="text-xs text-on-surface-variant">{s.description}</p>
                      )}
                    </div>
                    {s.fee_label && <Chip>{s.fee_label}</Chip>}
                    <button
                      type="button"
                      onClick={() => { setEditingId(s.id); setEditDraft(s); }}
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-low"
                      aria-label={`Edit ${s.name}`}
                    >
                      <Pencil size={15} />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeService(s.id)}
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-low hover:text-error"
                      aria-label={`Remove ${s.name}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-6">
        <h2 className="mb-4 font-display text-base font-bold text-on-surface">Add a service</h2>
        <form onSubmit={addService} className="space-y-3">
          <Field label="Name" required>
            <Input
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder="General consultation"
              required
            />
          </Field>
          <Field label="Description" hint="Optional">
            <Input
              value={draft.description}
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
            />
          </Field>
          <Field label="Fee" hint="Optional — shown to callers who ask.">
            <Input
              value={draft.fee_label}
              onChange={(e) => setDraft((d) => ({ ...d, fee_label: e.target.value }))}
              placeholder="₹800"
            />
          </Field>
          <Button type="submit" disabled={adding} className="w-full">
            {adding ? "Adding…" : <><Plus size={15} /> Add service</>}
          </Button>
        </form>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Doctors                                                             */
/* ------------------------------------------------------------------ */

const BLANK_DOCTOR = {
  name: "",
  specialty: "",
  slot_minutes: 30,
  work_start_hour: 9,
  work_end_hour: 17,
  work_days: [0, 1, 2, 3, 4],
  consultation_fee_label: "",
  active: true,
};

function DoctorsSection() {
  const [doctors, setDoctors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState(null); // null | BLANK_DOCTOR | existing doctor
  const [actionError, setActionError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    api
      .listAllDoctors()
      .then((data) => { setDoctors(data); setLoading(false); })
      .catch(() => { setError(true); setLoading(false); });
  }, []);

  useEffect(() => load(), [load]);

  async function toggleActive(doctor) {
    setActionError(null);
    try {
      if (doctor.active) {
        await api.deactivateDoctor(doctor.id);
        setDoctors((prev) => prev.map((d) => (d.id === doctor.id ? { ...d, active: false } : d)));
      } else {
        const updated = await api.updateDoctor(doctor.id, { active: true });
        setDoctors((prev) => prev.map((d) => (d.id === doctor.id ? updated : d)));
      }
    } catch (err) {
      setActionError(
        err instanceof ApiError && err.status === 403
          ? "Only an admin can change the doctor roster."
          : "Couldn't update that doctor."
      );
    }
  }

  if (loading) return <Skeleton className="h-72 w-full" />;
  if (error) return <ErrorState detail="We couldn't load your doctors." onRetry={load} className="py-20" />;

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-on-surface-variant">
          Doctors drive every availability check and booking — the AI can only offer
          times that fit a doctor's working hours here.
        </p>
        <Button type="button" onClick={() => setEditing({ ...BLANK_DOCTOR })}>
          <Plus size={15} /> Add doctor
        </Button>
      </div>

      {actionError && <p className="mb-3 rounded-lg bg-error-bg px-3 py-2 text-sm text-error">{actionError}</p>}

      <Card className="overflow-hidden">
        {doctors.length === 0 ? (
          <EmptyState
            icon={Stethoscope}
            title="No doctors yet"
            detail="Add a doctor so the AI and your staff can start booking appointments."
          />
        ) : (
          <ul className="divide-y divide-outline-variant">
            {doctors.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-on-surface">
                    {d.name}
                    {!d.active && <span className="ml-2 text-xs font-normal text-on-surface-variant">(inactive)</span>}
                  </p>
                  <p className="truncate text-xs text-on-surface-variant">
                    {d.specialty || "General"} · {hourLabel(d.work_start_hour)}–{hourLabel(d.work_end_hour)} ·{" "}
                    {d.work_days.map((n) => DAYS[n]?.label).filter(Boolean).join(", ") || "No working days"} ·{" "}
                    {d.slot_minutes} min slots
                  </p>
                </div>
                {d.consultation_fee_label && <Chip>{d.consultation_fee_label}</Chip>}
                {/* Active is the normal state and needs no badge; a green
                    "Active" on every doctor said nothing. Only the
                    exception is marked. */}
                {!d.active && <Chip>Inactive</Chip>}
                <button
                  type="button"
                  onClick={() => setEditing(d)}
                  className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-low"
                  aria-label={`Edit ${d.name}`}
                >
                  <Pencil size={15} />
                </button>
                <button
                  type="button"
                  onClick={() => toggleActive(d)}
                  className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-low"
                  aria-label={d.active ? `Deactivate ${d.name}` : `Reactivate ${d.name}`}
                >
                  {d.active ? <X size={15} /> : <Check size={15} />}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {editing && (
        <DoctorFormModal
          doctor={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setDoctors((prev) => {
              const exists = prev.some((d) => d.id === saved.id);
              const next = exists ? prev.map((d) => (d.id === saved.id ? saved : d)) : [...prev, saved];
              return next.sort((a, b) => a.name.localeCompare(b.name));
            });
            setEditing(null);
          }}
        />
      )}
    </>
  );
}

function DoctorFormModal({ doctor, onClose, onSaved }) {
  const isCreate = !doctor.id;
  const [form, setForm] = useState({ ...BLANK_DOCTOR, ...doctor });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  function toggleDay(value) {
    setForm((f) => ({
      ...f,
      work_days: f.work_days.includes(value)
        ? f.work_days.filter((d) => d !== value)
        : [...f.work_days, value].sort((a, b) => a - b),
    }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.name.trim()) {
      setError("A name is required.");
      return;
    }
    if (form.work_days.length === 0) {
      setError("Select at least one working day.");
      return;
    }
    if (Number(form.work_end_hour) <= Number(form.work_start_hour)) {
      setError("The end of the working day must be after the start.");
      return;
    }

    setSubmitting(true);
    setError(null);
    const payload = {
      name: form.name.trim(),
      specialty: form.specialty?.trim() || null,
      slot_minutes: Number(form.slot_minutes),
      work_start_hour: Number(form.work_start_hour),
      work_end_hour: Number(form.work_end_hour),
      work_days: form.work_days,
      consultation_fee_label: form.consultation_fee_label?.trim() || null,
      active: form.active,
    };
    try {
      const saved = isCreate
        ? await api.createDoctor(payload)
        : await api.updateDoctor(doctor.id, payload);
      onSaved(saved);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.status === 403
            ? "Only an admin can add or change doctors."
            : err.message
          : "Couldn't save that doctor."
      );
    } finally {
      setSubmitting(false);
    }
  }

  // The shared Modal, not a hand-built overlay. This was the one dialog in
  // the app with no role="dialog", no Escape and no focus trap, so Tab
  // walked out of it into the page behind. While saving, closing is
  // disabled exactly as the backdrop click was before.
  return (
    <Modal
      open
      title={isCreate ? "Add doctor" : "Edit doctor"}
      onClose={submitting ? undefined : onClose}
      className="max-w-md"
    >
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Name" required>
            <Input
              required
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Dr. Robert Chen"
              autoComplete="name"
            />
          </Field>

          <Field label="Specialty" hint="Optional">
            <Input
              value={form.specialty || ""}
              onChange={(e) => setForm((f) => ({ ...f, specialty: e.target.value }))}
              placeholder="Cardiology / Primary Care"
            />
          </Field>

          {/* A toggle group, not a form control: role="group" with a
              label, rather than a <label> pointing at nothing. */}
          <div role="group" aria-label="Working days">
            <p className="block text-xs font-semibold text-on-surface-variant">Working days</p>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {DAYS.map(({ value, label }) => {
                const on = form.work_days.includes(value);
                return (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleDay(value)}
                    className={`focus-ring min-h-10 rounded-full border px-4 py-2 text-sm transition-colors ${
                      // Selected = accent rule + tint, the same treatment as
                      // the voice and role pickers, rather than a solid teal
                      // fill competing with the "Add doctor" button.
                      on
                        ? "border-accent bg-accent-subtle font-semibold text-on-surface"
                        : "border-border-control bg-surface-lowest text-on-surface hover:bg-surface-low"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Field label="Day starts">
              <Select
                value={form.work_start_hour}
                onChange={(e) => setForm((f) => ({ ...f, work_start_hour: Number(e.target.value) }))}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>{hourLabel(h)}</option>
                ))}
              </Select>
            </Field>
            <Field label="Day ends">
              <Select
                value={form.work_end_hour}
                onChange={(e) => setForm((f) => ({ ...f, work_end_hour: Number(e.target.value) }))}
              >
                {Array.from({ length: 24 }, (_, h) => h + 1).map((h) => (
                  <option key={h} value={h}>{h === 24 ? "Midnight" : hourLabel(h)}</option>
                ))}
              </Select>
            </Field>
            <Field label="Slot length">
              <Select
                value={form.slot_minutes}
                onChange={(e) => setForm((f) => ({ ...f, slot_minutes: Number(e.target.value) }))}
              >
                {[10, 15, 20, 30, 45, 60].map((m) => (
                  <option key={m} value={m}>{m} min</option>
                ))}
              </Select>
            </Field>
          </div>

            <Field label="Consultation fee" hint="Optional — the AI reads this to callers who ask.">
              <Input
                value={form.consultation_fee_label || ""}
                onChange={(e) => setForm((f) => ({ ...f, consultation_fee_label: e.target.value }))}
                placeholder="₹800 · free for follow-ups"
              />
            </Field>

          {error && <InlineAlert>{error}</InlineAlert>}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Saving…" : isCreate ? "Add doctor" : "Save"}
            </Button>
          </div>
        </form>
    </Modal>
  );
}
