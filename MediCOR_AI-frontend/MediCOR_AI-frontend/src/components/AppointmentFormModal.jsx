import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Loader2 } from "lucide-react";
import { Button, Field, InlineAlert, Input, Modal, Select } from "./ui";
import * as api from "../lib/api";
import { ApiError } from "../lib/api";

/** A read-only value shown where a control would otherwise sit. */
function StaticValue({ children }) {
  return (
    <p className="rounded-lg border border-outline-variant bg-surface-low px-3 py-2.5 text-sm text-on-surface">
      {children}
    </p>
  );
}

/** Today in the browser's own timezone, as YYYY-MM-DD, for the date input's min. */
function todayISO() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

/**
 * Book or reschedule an appointment against real, validated availability.
 *
 * This used to post free-text date/time straight to POST /appointments,
 * which performs no doctor validation, no working-hours check and no
 * conflict check — so staff could double-book a doctor while the voice
 * agent could not, and the calendar had two disagreeing sources of truth.
 *
 * It now uses the same endpoints the agent does: /appointments/availability
 * to list genuinely open slots, then /appointments/book (or
 * /appointments/{id}/reschedule) to commit one. Both re-validate
 * server-side, so a slot taken while this modal was open is rejected
 * rather than silently double-booked.
 *
 * Pass `appointment` to reschedule an existing booking instead of
 * creating a new one.
 */
export default function AppointmentFormModal({
  open,
  onClose,
  defaultPatient,
  appointment = null,
  onSaved,
}) {
  const isReschedule = Boolean(appointment);

  const [patients, setPatients] = useState([]);
  const [doctors, setDoctors] = useState([]);
  const [patientId, setPatientId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");

  const [slots, setSlots] = useState([]);
  const [selectedSlot, setSelectedSlot] = useState("");
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [slotError, setSlotError] = useState(null);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSlotError(null);
    setSubmitting(false);
    setSlots([]);
    setSelectedSlot("");
    setPatientId(defaultPatient?.id || appointment?.patient_id || "");
    setDoctorId(appointment?.doctor_id || "");
    // A reschedule starts on the appointment's own day (usually the move is
    // small), so its open slots show at once. `start_at` is clinic
    // wall-clock naive, so its first 10 chars are the clinic's date. A past
    // day is left blank rather than set below the input's `min`.
    const currentDay = appointment?.start_at?.slice(0, 10);
    setDate(currentDay && currentDay >= todayISO() ? currentDay : "");
    setReason(appointment?.reason || "");

    if (!defaultPatient && !isReschedule) {
      api.listPatients().then(setPatients).catch(() => setPatients([]));
    }
    api.listDoctors().then(setDoctors).catch(() => setDoctors([]));
  }, [open, defaultPatient, appointment, isReschedule]);

  // Reload slots whenever the doctor or date changes. Availability is
  // per doctor-day, so both are required before there is anything to ask for.
  const loadSlots = useCallback(() => {
    if (!doctorId || !date) {
      setSlots([]);
      setSelectedSlot("");
      return;
    }
    let cancelled = false;
    setLoadingSlots(true);
    setSlotError(null);
    api
      .getAvailability(doctorId, date)
      .then((data) => {
        if (cancelled) return;
        setSlots(data.slots || []);
        setSelectedSlot("");
        setLoadingSlots(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setSlots([]);
        setSlotError(
          err instanceof ApiError ? err.message : "Couldn't load available times."
        );
        setLoadingSlots(false);
      });
    return () => {
      cancelled = true;
    };
  }, [doctorId, date]);

  useEffect(() => loadSlots(), [loadSlots]);

  if (!open) return null;

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (!isReschedule) {
      const chosenPatient = defaultPatient || patients.find((p) => p.id === patientId);
      if (!chosenPatient) {
        setError("Please select a patient.");
        return;
      }
    }
    if (!doctorId) {
      setError("Please choose a doctor.");
      return;
    }
    if (!selectedSlot) {
      setError("Please pick an available time.");
      return;
    }

    setSubmitting(true);
    try {
      let saved;
      if (isReschedule) {
        saved = await api.rescheduleAppointment(appointment.id, {
          start_at: selectedSlot,
          doctor_id: doctorId,
        });
      } else {
        saved = await api.bookAppointment({
          patient_id: defaultPatient?.id || patientId,
          doctor_id: doctorId,
          start_at: selectedSlot,
          reason: reason.trim() || undefined,
        });
      }
      onSaved?.(saved);
      onClose();
    } catch (err) {
      // A 409 means someone took the slot while this modal was open —
      // refresh so the user sees what is actually still free.
      if (err instanceof ApiError && err.status === 409) {
        setError("That time was just taken. Here are the times still available.");
        loadSlots();
      } else {
        setError(
          err instanceof ApiError
            ? err.message
            : "Couldn't save the appointment. Please try again."
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  const patientLabel =
    defaultPatient?.name || appointment?.patient_name || "this patient";

  return (
    <Modal
      open={open}
      title={isReschedule ? "Reschedule appointment" : "New appointment"}
      onClose={submitting ? () => {} : onClose}
      className="max-w-md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {isReschedule ? (
          <Field label="Patient">
            <StaticValue>
              {patientLabel}
              {appointment?.day_label && (
                <span className="block text-xs text-on-surface-variant">
                  Currently {appointment.day_label} at {appointment.time_label}
                </span>
              )}
            </StaticValue>
          </Field>
        ) : defaultPatient ? (
          <Field label="Patient">
            <StaticValue>{defaultPatient.name}</StaticValue>
          </Field>
        ) : (
          <Field label="Patient" required>
            <Select required value={patientId} onChange={(e) => setPatientId(e.target.value)}>
              <option value="" disabled>Select a patient…</option>
              {patients.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.mrn ? ` (${p.mrn})` : ""}
                </option>
              ))}
            </Select>
          </Field>
        )}

        <Field
          label="Doctor"
          required
          hint={
            doctors.length === 0
              ? "No doctors yet — add one under Clinic Profile → Doctors before booking."
              : undefined
          }
        >
          <Select required value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
            <option value="" disabled>Select a doctor…</option>
            {doctors.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}{d.specialty ? ` — ${d.specialty}` : ""}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Date" required>
          <Input type="date" required min={todayISO()} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>

        {/* A group of buttons, not a form control, so it carries
            role="group" + aria-label rather than a <label> pointing at
            nothing. */}
        <div role="group" aria-label="Available times">
          <p className="block text-xs font-semibold text-on-surface-variant">Available times</p>
          <div className="mt-1">
            {!doctorId || !date ? (
              <p className="rounded-lg border border-dashed border-outline-variant px-3 py-4 text-center text-sm text-on-surface-variant">
                Choose a doctor and a date to see open times.
              </p>
            ) : loadingSlots ? (
              <p className="flex items-center justify-center gap-2 rounded-lg border border-outline-variant px-3 py-4 text-sm text-on-surface-variant">
                <Loader2 size={15} className="animate-spin" /> Checking availability…
              </p>
            ) : slotError ? (
              <InlineAlert>{slotError}</InlineAlert>
            ) : slots.length === 0 ? (
              <p className="rounded-lg border border-dashed border-outline-variant px-3 py-4 text-center text-sm text-on-surface-variant">
                <CalendarClock size={16} className="mx-auto mb-1.5 opacity-60" />
                No open times that day. Try another date.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {slots.map((slot) => {
                  const active = selectedSlot === slot.start_at;
                  return (
                    <button
                      key={slot.start_at}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setSelectedSlot(slot.start_at)}
                      // px-3 py-1.5 gave a 32px target; these are the
                      // controls the whole modal exists to press, and on a
                      // phone they sit in a dense wrapped grid.
                      className={`focus-ring min-h-10 rounded-full border px-4 py-2 text-sm transition-colors ${
                        active
                          ? "border-accent bg-accent text-accent-contrast"
                          : "border-border-control bg-surface-lowest text-on-surface hover:bg-surface-low"
                      }`}
                    >
                      {slot.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {!isReschedule && (
          <Field label="Reason" hint="Optional">
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Follow-up, annual physical"
            />
          </Field>
        )}

        {error && <InlineAlert>{error}</InlineAlert>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting} disabled={submitting || !selectedSlot}>
            {submitting
              ? isReschedule ? "Moving…" : "Booking…"
              : isReschedule ? "Reschedule" : "Book"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
