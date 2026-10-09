import { useEffect, useState } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Phone, Calendar, Stethoscope, Pencil, PhoneCall, ArrowDown, Pill, MapPin, PhoneOff, Languages, Gauge, MessageSquare } from "lucide-react";
import AppShell from "../../components/AppShell";
import { Card, Chip, Button, Skeleton, SkeletonList, EmptyState, ErrorState, Tabs, TabPanel } from "../../components/ui";
import PatientFormModal from "../../components/PatientFormModal";
import AppointmentFormModal from "../../components/AppointmentFormModal";
import * as api from "../../lib/api";
import { formatCallTimeLabel } from "../../lib/date";
import {
  initialsOf, outcomeTone, callStatusTone, callStatusLabel, appointmentStatusTone, appointmentStatusLabel,
} from "../../lib/format";

// The three the voice pipeline actually speaks. Sarvam STT detects the
// caller's language and TTS answers in it; anything outside this set is
// clamped to English before it reaches TTS, so nothing else can appear.
const LANGUAGE_LABEL = {
  "en-IN": "English",
  "hi-IN": "हिन्दी (Hindi)",
  "te-IN": "తెలుగు (Telugu)",
};

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "calls", label: "Call history" },
  { id: "appointments", label: "Appointments" },
];

function CallHistoryTab({ patientId }) {
  const [calls, setCalls] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .listCalls()
      .then((rows) => {
        if (cancelled) return;
        setCalls(rows.filter((c) => c.patient_id === patientId));
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  if (error) return <ErrorState detail="Couldn't load this patient's call history." className="py-12" />;
  if (!calls) return <SkeletonList rows={3} avatar={false} label="Loading call history…" />;
  if (calls.length === 0) {
    return <EmptyState icon={PhoneOff} title="No calls yet" detail="This patient hasn't called or been called by the AI receptionist." className="py-12" />;
  }
  return (
    <div className="space-y-3">
      {calls.map((c) => (
        <div key={c.id} className="rounded-xl border border-outline-variant p-3.5">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-semibold text-on-surface">
              {formatCallTimeLabel(c.started_at) || "—"} {c.duration ? `· ${c.duration}` : ""}
            </p>
            {c.outcome && c.status !== "in_progress" ? (
              <Chip tone={outcomeTone(c.outcome)}>{c.outcome}</Chip>
            ) : (
              <Chip tone={callStatusTone(c.status)}>{callStatusLabel(c.status)}</Chip>
            )}
          </div>
          {c.reason && <p className="mt-1 text-xs text-on-surface-variant">{c.reason}</p>}
        </div>
      ))}
    </div>
  );
}

export default function PatientProfile() {
  const { id } = useParams();
  const navigate = useNavigate();
  // The tab lives in the query string for the same reason it does on the
  // call log: a specific section of a specific patient is a thing people
  // link each other to. `useState` could not do that.
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "overview";
  const setTab = (id) =>
    setParams(id === "overview" ? {} : { tab: id }, { replace: true });
  const [patient, setPatient] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);

  function load() {
    setLoading(true);
    setError(false);
    return api
      .getPatient(id)
      .then((data) => {
        setPatient(data);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    api
      .getPatient(id)
      .then((data) => {
        if (cancelled) return;
        setPatient(data);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (loading) {
    return (
      <AppShell>
        <div className="space-y-6">
          <Skeleton className="h-32 w-full rounded-2xl" />
          <div className="grid gap-6 lg:grid-cols-3">
            <Skeleton className="h-64 w-full rounded-2xl lg:col-span-2" />
            <Skeleton className="h-64 w-full rounded-2xl" />
          </div>
        </div>
      </AppShell>
    );
  }

  if (error || !patient) {
    return (
      <AppShell>
        <EmptyState
          icon={ArrowLeft}
          title={error ? "Couldn't load this patient" : "Patient not found"}
          detail={error ? "Something went wrong loading this profile." : "This patient may have been removed."}
          action={
            error ? (
              <button onClick={load} className="text-accent focus-ring text-sm font-semibold">
                Try again
              </button>
            ) : undefined
          }
        />
      </AppShell>
    );
  }

  // "Upcoming" is anything not already cancelled or done — the sidebar
  // answers "what is still ahead", where the tab holds the full record.
  const upcoming = patient.appointments.filter(
    (a) => a.status !== "cancelled" && a.status !== "done"
  );

  return (
    <AppShell title={patient.name} subtitle={patient.mrn}>
      <button onClick={() => navigate("/app/patients")} className="focus-ring mb-4 flex items-center gap-1.5 text-sm font-semibold text-on-surface-variant hover:text-on-surface">
        <ArrowLeft size={16} aria-hidden="true" /> All patients
      </button>

      <Card className="p-6">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4">
            {/* Neutral, as on the patient list: `bg-navy` has no dark-mode
                value, so the circle disappeared into a dark page. */}
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-surface-container text-lg font-bold text-on-surface-variant">
              {initialsOf(patient.name, patient.initials)}
            </div>
            <div>
              {/* The name is the page title now, so this is a second-level
                  heading rather than a competing h1. */}
              <div className="flex items-center gap-2">
                <h2 className="font-display text-xl font-bold text-on-surface">{patient.name}</h2>
                {/* Only the exception is marked, as on the patient list. */}
                {patient.status === "Archived" && <Chip tone="neutral">{patient.status}</Chip>}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-on-surface-variant">
                {patient.dob && (
                  <span className="flex items-center gap-1.5"><Calendar size={14} /> {patient.dob} {patient.age ? `(${patient.age} y/o)` : ""}</span>
                )}
                {patient.phone && <span className="flex items-center gap-1.5"><Phone size={14} /> {patient.phone}</span>}
                {patient.doctor && <span className="flex items-center gap-1.5"><Stethoscope size={14} /> {patient.doctor}</span>}
                {/* Not staff-entered — this is the language the caller
                    actually spoke, detected on their last call, and it is
                    what the AI receptionist will greet them in next time.
                    Absent until they have called and said something. */}
                {patient.preferred_language && (
                  <span className="flex items-center gap-1.5" title="Detected on this patient's last call — the AI greets them in this language">
                    <Languages size={14} /> {LANGUAGE_LABEL[patient.preferred_language] || patient.preferred_language}
                  </span>
                )}
                {/* Set only because this patient asked for it on a call,
                    never inferred. Shown so staff know why the AI sounds
                    different to them, and so it can be cleared if it was
                    picked up in error. */}
                {patient.speech_pace_preference === "slow" && (
                  <span
                    className="flex items-center gap-1.5"
                    title="This patient asked to be spoken to more slowly; the AI remembers it"
                  >
                    <Gauge size={14} /> Slower speech
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setShowEdit(true)}>
              <Pencil size={14} aria-hidden="true" /> Edit
            </Button>
            {/* Deliberately inert, and it says why. Placing a call from the
                dashboard needs an endpoint and gateway coordination that do
                not exist — a missing feature, not a wiring gap. A disabled
                control that explains itself is the honest state. */}
            <Button
              variant="outline"
              disabled
              title="Placing a live call from the dashboard isn't available yet — it needs an endpoint and voice-gateway coordination that don't exist."
            >
              <PhoneCall size={14} aria-hidden="true" /> Call
            </Button>
          </div>
        </div>

        <Tabs
          items={TABS}
          value={tab}
          onChange={setTab}
          variant="underline"
          label="Patient sections"
          className="mt-6"
        />
      </Card>

      <TabPanel id="overview" value={tab}>
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <div className="flex items-center gap-2">
              <Pill size={16} className="text-accent" aria-hidden="true" />
              <h2 className="font-display text-base font-bold text-on-surface">Active Prescriptions</h2>
            </div>
            {patient.prescriptions.length === 0 ? (
              <EmptyState
                icon={Pill}
                title="No prescriptions on file"
                detail="Prescriptions recorded for this patient will appear here."
                className="py-10"
              />
            ) : (
              <div className="mt-4 space-y-3">
                {patient.prescriptions.map((rx) => (
                  <div key={rx.id} className="flex items-center justify-between rounded-xl border border-outline-variant p-3.5">
                    <div>
                      <p className="text-sm font-semibold text-on-surface">{rx.name}</p>
                      <p className="text-xs text-on-surface-variant">{rx.detail}</p>
                    </div>
                    <div className="text-right">
                      <Chip tone={rx.status === "Refill Soon" ? "warning" : "neutral"}>{rx.status}</Chip>
                      <p className="mt-1 text-xs text-on-surface-variant">{rx.note}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* A green dot reading "Live agent available" used to sit here.
                Nothing checked anything — same class as the "Online now"
                indicator already removed from Support and the "Insurance
                Verified" badge removed from the landing page. */}
            <h2 className="mt-7 font-display text-base font-bold text-on-surface">
              Recent AI interactions
            </h2>
            {patient.interactions.length === 0 ? (
              <EmptyState
                icon={MessageSquare}
                title="No AI interactions yet"
                detail="Notes the AI receptionist records about this patient will appear here."
                className="py-10"
              />
            ) : (
              <div className="mt-4 space-y-3">
                {patient.interactions.map((it) => (
                  <div key={it.id} className="flex items-center gap-3 rounded-xl border border-outline-variant p-3.5">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-on-surface">{it.title}</p>
                      <p className="text-xs text-on-surface-variant">{it.date_label} — {it.detail}</p>
                    </div>
                    {/* A play/pause control used to sit here, gated on
                        `has_audio && audio_url`. `audio_url` has no writer
                        anywhere in the backend and `has_audio` is set only
                        by seed.py, so the control could never appear — it
                        advertised a capability this product does not have.
                        Calls are transcribed, never recorded. The duration
                        stays: it is a real column staff can set, and on its
                        own it claims nothing about a recording. */}
                    {it.duration && (
                      <span className="shrink-0 text-xs tabular-nums text-on-surface-variant">
                        {it.duration}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>

          <div className="space-y-6">
            <Card className="p-6">
              <h2 className="font-display text-base font-bold text-on-surface">Latest Vitals</h2>
              {!patient.vitals_bp && !patient.vitals_hr ? (
                <p className="mt-3 text-sm text-on-surface-variant">
                  No vitals recorded. Add them from Edit.
                </p>
              ) : (
                <>
                  <div className="mt-4 space-y-4">
                    {patient.vitals_bp && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-on-surface-variant">Blood Pressure</span>
                        <span className="flex items-center gap-1 text-sm font-semibold text-on-surface">
                          {patient.vitals_bp}
                          {patient.vitals_bp_trend === "down" && <ArrowDown size={13} className="text-success" aria-label="trending down" />}
                        </span>
                      </div>
                    )}
                    {patient.vitals_hr && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-on-surface-variant">Heart Rate</span>
                        <span className="text-sm font-semibold text-on-surface">{patient.vitals_hr}</span>
                      </div>
                    )}
                    {patient.vitals_weight && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-on-surface-variant">Weight</span>
                        <span className="text-sm font-semibold text-on-surface">{patient.vitals_weight}</span>
                      </div>
                    )}
                  </div>
                  {patient.vitals_recorded && (
                    <p className="mt-4 text-xs text-on-surface-variant">Recorded: {patient.vitals_recorded}</p>
                  )}
                </>
              )}
            </Card>

            {/* A summary, not a second copy. This card used to render the
                same full list as the Appointments tab, so the page showed
                every appointment twice — once in the sidebar and once in a
                tab whose whole purpose was to show them. */}
            <Card className="p-6">
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-display text-base font-bold text-on-surface">Upcoming</h2>
                <button
                  onClick={() => setShowSchedule(true)}
                  className="focus-ring rounded text-sm font-semibold text-accent hover:underline"
                >
                  Schedule
                </button>
              </div>
              {upcoming.length === 0 ? (
                <p className="mt-3 text-sm text-on-surface-variant">Nothing scheduled.</p>
              ) : (
                <>
                  <div className="mt-4 space-y-3">
                    {upcoming.slice(0, 3).map((a) => (
                      <div key={a.id} className="rounded-xl border border-outline-variant p-3.5">
                        <p className="text-sm font-semibold text-on-surface">{a.title}</p>
                        <p className="text-xs text-on-surface-variant">{a.time_label || a.day_label}</p>
                      </div>
                    ))}
                  </div>
                  {patient.appointments.length > upcoming.slice(0, 3).length && (
                    <button
                      onClick={() => setTab("appointments")}
                      className="focus-ring mt-3 rounded text-sm font-semibold text-accent hover:underline"
                    >
                      See all {patient.appointments.length}
                    </button>
                  )}
                </>
              )}
            </Card>
          </div>
        </div>
      </TabPanel>

      <TabPanel id="calls" value={tab}>
        <Card className="mt-6 p-6">
          <CallHistoryTab patientId={patient.id} />
        </Card>
      </TabPanel>

      <TabPanel id="appointments" value={tab}>
        <Card className="mt-6 p-6">
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-display text-base font-bold text-on-surface">All appointments</h2>
            <button
              onClick={() => setShowSchedule(true)}
              className="focus-ring rounded text-sm font-semibold text-accent hover:underline"
            >
              Schedule new
            </button>
          </div>
          {patient.appointments.length === 0 ? (
            <EmptyState icon={Calendar} title="No appointments" detail="This patient has no appointments on record." className="py-12" />
          ) : (
            <div className="mt-4 space-y-3">
              {patient.appointments.map((a) => (
                <div key={a.id} className="flex items-center justify-between rounded-xl border border-outline-variant p-3.5">
                  <div>
                    <p className="text-sm font-semibold text-on-surface">{a.title}</p>
                    <p className="text-xs text-on-surface-variant">{a.time_label || a.day_label}</p>
                    {a.location && (
                      <p className="mt-1 flex items-center gap-1 text-xs text-on-surface-variant">
                        <MapPin size={12} /> {a.location}
                      </p>
                    )}
                  </div>
                  <Chip tone={appointmentStatusTone(a.status)}>{appointmentStatusLabel(a.status)}</Chip>
                </div>
              ))}
            </div>
          )}
        </Card>
      </TabPanel>

      <PatientFormModal
        open={showEdit}
        onClose={() => setShowEdit(false)}
        patient={patient}
        onSaved={(updated) => setPatient((p) => ({ ...p, ...updated }))}
      />
      <AppointmentFormModal
        open={showSchedule}
        onClose={() => setShowSchedule(false)}
        defaultPatient={patient}
        onSaved={(appt) => setPatient((p) => ({ ...p, appointments: [...p.appointments, appt] }))}
      />
    </AppShell>
  );
}