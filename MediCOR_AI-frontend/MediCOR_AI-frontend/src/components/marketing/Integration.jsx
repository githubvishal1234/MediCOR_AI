/**
 * What the EHR layer actually is.
 *
 * This section used to name four vendors -- Epic Systems, Oracle Cerner,
 * athenahealth and Veradigm -- as supported integrations, one of them
 * with a cadence ("Scheduled sync every 15 min") that no scheduled job in
 * this application performs. `app/ehr_service.py` says the opposite in
 * its own docstring: *"No real EHR vendor is hardcoded or invented."* An
 * org's integration is whatever FHIR or REST gateway a clinic points it
 * at from the EHR Integration page, and if nobody has configured one the
 * clinic runs `local_only` with this database as the system of record.
 *
 * Naming those four was three separate problems: it advertised
 * partnerships that do not exist, it used other companies' trademarks to
 * imply them, and it promised a clinic on one of those systems a
 * connector that would not be there on the day they signed. The four
 * cards now describe the mechanism, which is real.
 */

import { PlugZap, Webhook, Code2, DatabaseZap, CheckCircle2 } from "lucide-react";

const CAPABILITIES = [
  {
    icon: PlugZap,
    name: "FHIR / REST gateway",
    detail: "Point it at your EHR's endpoint and key from the dashboard",
  },
  {
    icon: DatabaseZap,
    name: "Works without one",
    detail: "No EHR connected? This database is the system of record",
  },
  {
    icon: Webhook,
    name: "Signed webhooks",
    detail: "Clinic events posted to your own infrastructure, HMAC-signed",
  },
  {
    icon: Code2,
    name: "REST API",
    detail: "The same API the dashboard and the voice agent use",
  },
];

export default function Integration() {
  return (
    <section id="integration" className="mx-auto max-w-(--container-max) px-5 py-24 sm:px-8">
      <div className="grid gap-14 lg:grid-cols-2 lg:items-center">
        <div>
          <p className="text-accent text-sm font-semibold uppercase tracking-wider">Integration</p>
          <h2 className="mt-3 font-display text-3xl font-bold text-on-surface sm:text-[40px]">
            Connects to the systems you already run
          </h2>
          <p className="mt-4 text-lg leading-relaxed text-on-surface-variant">
            MediCOR AI keeps its own patient, appointment and call records, and
            syncs them out to your EHR when you connect one — so the agent is useful on
            day one and better once it is wired in.
          </p>
          <ul className="mt-8 space-y-3">
            {[
              "Patient demographics pushed to your EHR as they change",
              "Appointment availability and booking held in one place",
              "Signed webhooks for custom infrastructure",
              "REST API for advanced workflows",
            ].map((t) => (
              <li key={t} className="flex items-start gap-3 text-sm text-on-surface">
                <CheckCircle2 size={18} className="text-accent mt-0.5 shrink-0" />
                {t}
              </li>
            ))}
          </ul>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {CAPABILITIES.map((e) => (
            <div key={e.name} className="rounded-2xl border border-outline-variant bg-surface-lowest p-6 shadow-(--shadow-card)">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-container">
                <e.icon size={18} className="text-on-surface" />
              </div>
              <p className="mt-4 font-display font-bold text-on-surface">{e.name}</p>
              <p className="mt-1 text-sm text-on-surface-variant">{e.detail}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
