/**
 * What the agent does, limited to what it does.
 *
 * "Multilingual by Default -- fluent in English, Spanish, and more" used
 * to sit here. This stack is English only by explicit decision
 * (DEEPGRAM_STT_LANGUAGE=en, an -en voice, and a prompt whose language
 * claim is derived from the TTS provider so the two cannot disagree);
 * a caller speaking anything else is transferred to a person. Spanish
 * was never on the list at all -- the legacy stack speaks English, Hindi
 * and Telugu, and this one speaks the first of those. See CLAUDE.md,
 * "This stack is English only, by decision, for now".
 *
 * The clinical-context line also claimed patient history is pulled from
 * "your EHR" mid-call. It is read from this application's own records,
 * which is true whether or not a clinic has connected anything.
 */
import { PhoneCall, CalendarClock, FileText, Mic, Bell, Stethoscope } from "lucide-react";

const FEATURES = [
  {
    icon: PhoneCall,
    title: "24/7 Call Handling",
    body: "Every call is answered instantly, day or night, with a natural voice tuned for medical terminology.",
  },
  {
    icon: CalendarClock,
    title: "Native Scheduling",
    body: "Books, reschedules, and confirms appointments directly in your calendar — no double entry, no missed slots.",
  },
  {
    icon: FileText,
    title: "Automatic Transcripts",
    body: "Every call is transcribed and summarized with action items, so your staff can review in seconds.",
  },
  {
    icon: Mic,
    title: "Indian English Voice",
    body: "A voice tuned for Indian English rather than an American one, so callers are understood and understand back.",
  },
  {
    icon: Bell,
    title: "Smart Escalation",
    body: "Emergencies and complex billing issues are routed straight to your human staff, instantly and reliably.",
  },
  {
    icon: Stethoscope,
    title: "Clinical Context Aware",
    body: "Recognises returning callers and has their history, appointments and prescriptions to hand during the call.",
  },
];

export default function Features() {
  return (
    <section id="features" className="mx-auto max-w-(--container-max) px-5 py-24 sm:px-8">
      <div className="mx-auto max-w-2xl text-center">
        <p className="text-accent text-sm font-semibold uppercase tracking-wider">Features</p>
        <h2 className="mt-3 font-display text-3xl font-bold text-on-surface sm:text-[40px]">
          Everything your front desk needs, none of the wait times
        </h2>
        <p className="mt-4 text-lg text-on-surface-variant">
          Built specifically for clinical workflows — not a generic call center bot.
        </p>
      </div>

      <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <div
            key={f.title}
            className="rounded-2xl border border-outline-variant bg-surface-lowest p-7 shadow-(--shadow-card) transition-shadow hover:shadow-(--shadow-raised)"
          >
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-info-bg">
              <f.icon size={20} className="text-accent" aria-hidden="true" />
            </div>
            <h3 className="mt-5 font-display text-lg font-bold text-on-surface">{f.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">{f.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
