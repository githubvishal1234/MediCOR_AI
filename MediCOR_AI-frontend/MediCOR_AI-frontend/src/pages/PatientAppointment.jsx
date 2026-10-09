/**
 * The patient's own page for one appointment (PF-06).
 *
 * Public route, no account, no login. The token in the URL is the only
 * credential, and it is scoped to a single appointment.
 *
 * Two things shape how this is written:
 *
 *  * **It is read on a phone, by someone who is not a user of this
 *    product.** No sidebar, no navigation, no jargon, one decision per
 *    screen. Deliberately outside `AppShell`, which assumes a signed-in
 *    member of staff.
 *
 *  * **It must never over-claim.** The backend returns one identical
 *    message for every failure, so this page shows that message rather
 *    than guessing whether a link was mistyped, expired or already used.
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  CalendarCheck,
  CalendarClock,
  ClipboardList,
  CheckCircle2,
  Clock,
  MapPin,
  Stethoscope,
  TriangleAlert,
  X,
} from "lucide-react";
import Logo from "../components/Logo";
import {
  Button,
  Card,
  ConfirmDialog,
  Field,
  InlineAlert,
  Modal,
  Skeleton,
  Textarea,
} from "../components/ui";
import * as papi from "../lib/patientApi";

/** The next 14 days, so a patient never has to operate a date picker. */
function nextDays(count = 14) {
  const out = [];
  const today = new Date();
  for (let i = 1; i <= count; i += 1) {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    out.push(d);
  }
  return out;
}

const dayKey = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const dayLabel = (d) =>
  d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

function Row({ icon: Icon, children }) {
  if (!children) return null;
  return (
    <div className="flex items-start gap-2.5 text-sm text-on-surface">
      <Icon size={16} className="mt-0.5 shrink-0 text-on-surface-variant" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export default function PatientAppointment() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";

  const [appt, setAppt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null); // confirm | cancel | reschedule
  const [confirmingCancel, setConfirmingCancel] = useState(false);

  const [preVisitOpen, setPreVisitOpen] = useState(false);
  const [preVisit, setPreVisit] = useState({
    visit_reason: "", medications: "", allergies: "", notes: "",
  });
  const [preVisitSaved, setPreVisitSaved] = useState(false);

  const [movingOpen, setMovingOpen] = useState(false);
  const [chosenDay, setChosenDay] = useState(null);
  const [slots, setSlots] = useState(null);
  const [slotsError, setSlotsError] = useState(null);

  const load = useCallback(() => {
    if (!token) {
      setLoading(false);
      setLoadError("This link is incomplete. Please use the full link from your message.");
      return;
    }
    setLoading(true);
    setLoadError(null);
    papi
      .getAppointment(token)
      .then((data) => setAppt(data))
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(load, [load]);

  async function act(kind, fn) {
    setBusy(kind);
    setActionError(null);
    try {
      setAppt(await fn());
      setMovingOpen(false);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(null);
    }
  }

  async function pickDay(d) {
    setChosenDay(d);
    setSlots(null);
    setSlotsError(null);
    try {
      setSlots(await papi.getAvailability(token, dayKey(d)));
    } catch (err) {
      setSlotsError(err.message);
    }
  }

  const cancelled = appt?.status === "cancelled";
  const confirmed = appt?.confirmation_status === "confirmed";

  return (
    <div className="flex min-h-screen flex-col bg-surface-low px-4 py-8">
      <div className="mx-auto w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>

        {loading ? (
          <Card className="p-6">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="mt-3 h-4 w-full" />
            <Skeleton className="mt-2 h-4 w-1/2" />
          </Card>
        ) : loadError ? (
          <Card className="p-6 text-center">
            <TriangleAlert size={26} className="mx-auto text-warning" aria-hidden="true" />
            <h1 className="mt-3 font-display text-lg font-bold text-on-surface">
              We couldn&apos;t open this appointment
            </h1>
            <p className="mt-1.5 text-sm text-on-surface-variant">{loadError}</p>
          </Card>
        ) : (
          <Card className="overflow-hidden">
            <div className="border-b border-outline-variant bg-surface-lowest px-6 py-5">
              <p className="text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
                {appt.clinic_name}
              </p>
              <h1 className="mt-1 font-display text-xl font-bold text-on-surface">
                {appt.patient_first_name
                  ? `${appt.patient_first_name}, here's your appointment`
                  : "Your appointment"}
              </h1>
            </div>

            <div className="space-y-3 px-6 py-5">
              <Row icon={CalendarClock}>
                <span className="font-semibold">
                  {appt.day_label} {appt.time_label && `· ${appt.time_label}`}
                </span>
              </Row>
              <Row icon={Stethoscope}>{appt.doctor_name}</Row>
              <Row icon={MapPin}>{appt.location}</Row>
              <Row icon={Clock}>{appt.title}</Row>
            </div>

            {cancelled ? (
              <div className="border-t border-outline-variant px-6 py-5">
                <InlineAlert tone="warning">
                  This appointment has been cancelled. Please call the clinic if you would like
                  to book another.
                </InlineAlert>
              </div>
            ) : (
              <div className="border-t border-outline-variant px-6 py-5">
                {confirmed && (
                  <p className="mb-4 flex items-center gap-2 text-sm font-semibold text-success">
                    <CheckCircle2 size={16} aria-hidden="true" />
                    Thanks — we&apos;ll see you then.
                  </p>
                )}

                {actionError && <InlineAlert className="mb-4">{actionError}</InlineAlert>}

                {!movingOpen ? (
                  <div className="flex flex-col gap-2">
                    {!confirmed && (
                      <Button
                        size="lg"
                        loading={busy === "confirm"}
                        onClick={() => act("confirm", () => papi.confirmAppointment(token))}
                      >
                        <CalendarCheck size={16} aria-hidden="true" /> Yes, I&apos;ll be there
                      </Button>
                    )}
                    {appt.can_reschedule && (
                      <Button
                        variant="outline"
                        size="lg"
                        onClick={() => {
                          setMovingOpen(true);
                          setActionError(null);
                        }}
                      >
                        <CalendarClock size={16} aria-hidden="true" /> Change the time
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="lg"
                      onClick={() => {
                        setActionError(null);
                        papi
                          .getPreVisit(token)
                          .then((d) =>
                            setPreVisit({
                              visit_reason: d.visit_reason || "",
                              medications: d.medications || "",
                              allergies: d.allergies || "",
                              notes: d.notes || "",
                            })
                          )
                          .catch(() => {})
                          .finally(() => setPreVisitOpen(true));
                      }}
                    >
                      <ClipboardList size={16} aria-hidden="true" />{" "}
                      {appt.pre_visit_submitted || preVisitSaved
                        ? "Update what you told us"
                        : "Tell us about your visit"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="lg"
                      onClick={() => setConfirmingCancel(true)}
                    >
                      <X size={16} aria-hidden="true" /> Cancel this appointment
                    </Button>
                  </div>
                ) : (
                  <div>
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-on-surface">Pick a new day</p>
                      <button
                        onClick={() => setMovingOpen(false)}
                        className="focus-ring rounded text-sm font-semibold text-on-surface-variant hover:underline"
                      >
                        Back
                      </button>
                    </div>

                    <div className="flex gap-2 overflow-x-auto pb-2">
                      {nextDays().map((d) => {
                        const active = chosenDay && dayKey(chosenDay) === dayKey(d);
                        return (
                          <button
                            key={dayKey(d)}
                            onClick={() => pickDay(d)}
                            aria-pressed={active}
                            className={`focus-ring shrink-0 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors ${
                              active
                                ? "border-accent bg-accent text-accent-contrast"
                                : "border-outline-variant text-on-surface hover:bg-surface-container"
                            }`}
                          >
                            {dayLabel(d)}
                          </button>
                        );
                      })}
                    </div>

                    {chosenDay && (
                      <div className="mt-4">
                        {slotsError ? (
                          <InlineAlert>{slotsError}</InlineAlert>
                        ) : slots === null ? (
                          <Skeleton className="h-10 w-full" />
                        ) : slots.length === 0 ? (
                          <p className="text-sm text-on-surface-variant">
                            Nothing free that day. Try another, or call the clinic.
                          </p>
                        ) : (
                          <div className="grid grid-cols-3 gap-2">
                            {slots.map((s) => (
                              <button
                                key={s.start_at}
                                disabled={busy === "reschedule"}
                                onClick={() =>
                                  act("reschedule", () =>
                                    papi.rescheduleAppointment(token, s.start_at)
                                  )
                                }
                                className="focus-ring rounded-lg border border-outline-variant px-2 py-2 text-sm font-semibold text-on-surface transition-colors hover:border-accent hover:bg-accent-subtle disabled:opacity-50"
                              >
                                {s.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </Card>
        )}

        <p className="mt-5 text-center text-xs text-on-surface-variant">
          Need something else? Please call the clinic directly.
        </p>
      </div>

      {/* PF-13. Everything reception would otherwise collect at the desk
          while other patients wait. Nothing typed here changes the
          patient's record — it is read by staff before the visit and
          applied by a person, if at all. */}
      <Modal
        open={preVisitOpen}
        title="Tell us about your visit"
        onClose={() => setPreVisitOpen(false)}
      >
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy("previsit");
            setActionError(null);
            try {
              await papi.submitPreVisit(token, preVisit);
              setPreVisitSaved(true);
              setPreVisitOpen(false);
              setAppt((a) => ({ ...a, pre_visit_submitted: true }));
            } catch (err) {
              setActionError(err.message);
            } finally {
              setBusy(null);
            }
          }}
        >
          <p className="mb-4 text-sm text-on-surface-variant">
            This helps the clinic prepare, so you spend less time at the desk. Everything
            is optional.
          </p>

          {[
            ["visit_reason", "What are you coming about?", "e.g. a cough for two weeks"],
            ["medications", "Anything you're taking", "medicines, doses if you know them"],
            ["allergies", "Anything you react to", "medicines, foods"],
            ["notes", "Anything else we should know", ""],
          ].map(([key, label, hint]) => (
            <div key={key} className="mb-3">
              <Field label={label} hint={hint || undefined}>
                <Textarea
                  rows={2}
                  maxLength={2000}
                  value={preVisit[key]}
                  onChange={(e) =>
                    setPreVisit((v) => ({ ...v, [key]: e.target.value }))
                  }
                />
              </Field>
            </div>
          ))}

          <p className="mt-4 text-xs text-on-surface-variant">
            A member of staff will read this before you arrive. If your phone number
            has changed, please tell them at reception — we can&apos;t change your
            records from this page.
          </p>

          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setPreVisitOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={busy === "previsit"}>
              Send to the clinic
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={confirmingCancel}
        title="Cancel this appointment?"
        detail="The time will be offered to someone else. You'll need to call the clinic to book again."
        confirmLabel="Yes, cancel it"
        cancelLabel="Keep it"
        busy={busy === "cancel"}
        onConfirm={() => {
          setConfirmingCancel(false);
          act("cancel", () => papi.cancelAppointment(token));
        }}
        onCancel={() => setConfirmingCancel(false)}
      />
    </div>
  );
}
