/**
 * The four steps, without the invented statistic.
 *
 * Step 01 claimed "most clinics are live in under 10 minutes" -- a figure
 * about a customer base that does not exist yet. Step 02 named four EHR
 * vendors as connectable; see the note in Integration.jsx for why that
 * was wrong, and app/ehr_service.py for the backend saying so itself.
 */

const STEPS = [
  {
    n: "01",
    title: "Connect your line",
    body: "Point your existing clinic phone number at MediCOR AI. No new hardware and no change to the number your patients already dial.",
  },
  {
    n: "02",
    title: "Add your clinic",
    body: "Hours, doctors, services and the documents the agent should answer from. Connect your EHR gateway too, if you have one.",
  },
  {
    n: "03",
    title: "The agent takes calls",
    body: "Patients are greeted, identified, and helped — booking, rescheduling, or answering FAQs in real time.",
  },
  {
    n: "04",
    title: "Your staff reviews and confirms",
    body: "Summaries, transcripts, and flagged bookings land in your dashboard for a quick human check when needed.",
  },
];

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="bg-surface-low py-24">
      <div className="mx-auto max-w-(--container-max) px-5 sm:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-accent text-sm font-semibold uppercase tracking-wider">How It Works</p>
          <h2 className="mt-3 font-display text-3xl font-bold text-on-surface sm:text-[40px]">
            Live in a day, not a quarter
          </h2>
        </div>

        <div className="mt-14 grid gap-8 lg:grid-cols-4 lg:gap-6">
          {STEPS.map((s, i) => (
            <div key={s.n} className="relative">
              <div
                className="font-display text-4xl font-extrabold text-accent/30"
              >
                {s.n}
              </div>
              <h3 className="mt-3 font-display text-lg font-bold text-on-surface">{s.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">{s.body}</p>
              {i < STEPS.length - 1 && (
                <div className="mt-6 hidden h-px w-full bg-outline-variant lg:block" />
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
