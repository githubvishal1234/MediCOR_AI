import { useEffect, useState } from "react";
import { Button, Field, InlineAlert, Input, Modal, Select } from "./ui";
import * as api from "../lib/api";
import { ApiError } from "../lib/api";

/**
 * Create or edit a patient.
 *
 * Omit `patient` to create one: until now `api.createPatient` was exported
 * and called by nothing, so the only way a patient could enter the system
 * was the AI registering them over the phone. Front-desk staff could not
 * pre-register a walk-in or a referral.
 *
 * The MRN is generated server-side when left blank.
 *
 * This used to hand-roll its own dialog — a fixed backdrop plus a Card —
 * which meant it had none of what `Modal` provides: no focus trap, no
 * Escape, no scroll lock, no focus returned to whatever opened it. A
 * keyboard user could Tab straight out of the open form into the patient
 * list behind it. It also carried its own `inputClass`, one of five copies
 * that had each drifted; every control here now resolves the shared one,
 * which is what carries the 3:1 border.
 */
export default function PatientFormModal({ open, onClose, patient = null, onSaved }) {
  const isCreate = !patient?.id;

  const [name, setName] = useState("");
  const [mrn, setMrn] = useState("");
  const [dob, setDob] = useState("");
  const [age, setAge] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [doctor, setDoctor] = useState("");
  const [status, setStatus] = useState("Active");
  // "" means nobody has asked, which is a real third state and not the
  // same as a refusal — see the backend note on Patient.contact_consent.
  const [consent, setConsent] = useState("");
  const [channel, setChannel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSubmitting(false);
    setName(patient?.name || "");
    setMrn(patient?.mrn || "");
    setDob(patient?.dob || "");
    setAge(patient?.age != null ? String(patient.age) : "");
    setPhone(patient?.phone || "");
    setEmail(patient?.email || "");
    setDoctor(patient?.doctor || "");
    setStatus(patient?.status || "Active");
    setConsent(patient?.contact_consent || "");
    setChannel(patient?.message_channel || "");
  }, [open, patient]);

  if (!open) return null;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setError(null);
    setSubmitting(true);

    const fields = {
      name: name.trim(),
      mrn: mrn.trim() || undefined,
      dob: dob.trim() || undefined,
      age: age !== "" ? Number(age) : undefined,
      phone: phone.trim() || undefined,
      email: email.trim() || undefined,
      doctor: doctor.trim() || undefined,
      status,
      contact_consent: consent || undefined,
      message_channel: channel || undefined,
    };

    try {
      const saved = isCreate
        ? await api.createPatient(fields)
        : await api.updatePatient(patient.id, fields);
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : isCreate
          ? "Couldn't add the patient. Please try again."
          : "Couldn't save changes. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title={isCreate ? "Add patient" : "Edit patient"}
      // Escape and backdrop-dismiss are suppressed mid-submit rather than
      // left live: the request is already in flight, and closing would
      // leave the caller unsure whether it landed.
      onClose={submitting ? () => {} : onClose}
      className="max-w-md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="MRN" hint={isCreate ? "Generated if left blank" : undefined}>
            <Input value={mrn} onChange={(e) => setMrn(e.target.value)} />
          </Field>
          <Field label="Status">
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="Active">Active</option>
              <option value="Inactive">Inactive</option>
            </Select>
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Date of birth">
            <Input
              placeholder="MM/DD/YYYY"
              value={dob}
              onChange={(e) => setDob(e.target.value)}
              inputMode="numeric"
            />
          </Field>
          <Field label="Age">
            <Input
              type="number"
              min="0"
              max="150"
              value={age}
              onChange={(e) => setAge(e.target.value)}
              inputMode="numeric"
            />
          </Field>
        </div>

        <Field label="Phone">
          {/* inputMode/autoComplete so a phone keypad appears on mobile and
              the browser can fill it — this is the field a receptionist
              types most often. */}
          <Input
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            inputMode="tel"
            autoComplete="tel"
          />
        </Field>

        <Field label="Email">
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            inputMode="email"
            autoComplete="email"
          />
        </Field>

        <Field label="Doctor">
          <Input value={doctor} onChange={(e) => setDoctor(e.target.value)} />
        </Field>

        {/* Whether this patient may be messaged at all. Appointment
            confirmations and reminders are checked against this before
            anything is sent, so leaving it unanswered is meaningful
            rather than merely incomplete. */}
        <fieldset className="space-y-3 rounded-lg border border-outline-variant p-3">
          <legend className="px-1 text-sm font-semibold text-on-surface">
            Contact consent
          </legend>
          <Field
            label="May we message this patient?"
            hint={'Confirmations and reminders are only sent to patients who have agreed. "Not asked" is not a refusal, but nothing is sent until someone asks.'}
          >
            <Select value={consent} onChange={(e) => setConsent(e.target.value)}>
              <option value="">Not asked</option>
              <option value="granted">Yes — they agreed</option>
              <option value="declined">No — they declined</option>
            </Select>
          </Field>
          <Field
            label="How should we message them?"
            hint="SMS reaches patients who don't use WhatsApp — often the ones most likely to miss an appointment."
          >
            <Select value={channel} onChange={(e) => setChannel(e.target.value)}>
              <option value="">Clinic default</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="sms">SMS</option>
            </Select>
          </Field>
        </fieldset>

        {error && <InlineAlert>{error}</InlineAlert>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            {submitting ? (isCreate ? "Adding…" : "Saving…") : isCreate ? "Add patient" : "Save changes"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
