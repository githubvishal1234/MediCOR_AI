/**
 * Terms of Service.
 *
 * Same rule as the privacy policy: nothing here describes behaviour the
 * software does not have. Two clauses in particular are written against
 * what the code actually does rather than what a template would say:
 *
 *   - **Billing.** No payment provider is wired up. `POST /billing/upgrade`
 *     returns 501 by design, no billing run exists, and the only code that
 *     has ever written an `invoices` row is the seed script. So the terms
 *     say charges are arranged directly and are not processed by the
 *     application -- which is exactly what the invoice document itself
 *     states on its face.
 *   - **Availability.** No uptime figure is offered, because nothing
 *     measures one. A number here would be the "SOC 2 Type II" badge in
 *     another form.
 *
 * The clinical clause is the one to leave alone. The agent records a
 * repeat-prescription request and can never approve one; that boundary is
 * enforced by the dependency on each route in `routers/refills.py`, and
 * saying so here is a commitment the code already keeps.
 *
 * THE REVIEW BANNER: remove `<ReviewNotice />` once a lawyer has been
 * through this, and not before.
 */

import PageShell, { Clause } from "../../components/marketing/PageShell";
import { AlertTriangle } from "lucide-react";

function ReviewNotice() {
  return (
    <div className="mb-10 flex items-start gap-3 rounded-xl border border-warning bg-warning-bg p-4">
      <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warning" />
      <p className="text-sm leading-relaxed text-on-surface">
        <span className="font-semibold">Provisional.</span> These terms describe how the
        service works today and are not yet a reviewed contract. They will be finalised
        with legal advice before general availability; until then, anything agreed in
        writing with us takes precedence.
      </p>
    </div>
  );
}

export default function Terms() {
  return (
    <PageShell
      title="Terms of Service"
      intro="The agreement between MediCOR AI and the clinics that use it."
    >
      <ReviewNotice />

      <Clause heading="What the service is">
        <p>
          MediCOR AI answers a clinic's telephone, books and changes
          appointments, answers questions from documents the clinic provides, records
          repeat-prescription requests for staff to decide on, and transfers callers to
          a person when asked or when the clinic's rules say so. It is software for a
          clinic's front desk.
        </p>
      </Clause>

      <Clause heading="It is not a clinical system">
        <p>
          The agent does not diagnose, triage, or give medical advice, and nothing it
          says is a clinical opinion. It records a repeat-prescription request; it can
          never approve one — that decision is always made by a person at the clinic,
          and the software has no route that would let the agent do otherwise.
        </p>
        <p>
          It is not an emergency service. A caller describing an emergency is directed
          to the emergency number and offered a transfer. Clinics are responsible for
          configuring that number correctly for their territory and for having the
          wording reviewed by someone clinically qualified.
        </p>
      </Clause>

      <Clause heading="Your account">
        <p>
          You are responsible for who you invite, what role you give them, and for
          keeping credentials private. Tell us promptly if an account is compromised.
          Note that resetting a password does not end sessions that are already signed
          in; if you are resetting because of a suspected compromise, tell us so we can
          help you close them.
        </p>
      </Clause>

      <Clause heading="Your data stays yours">
        <p>
          Patient records, transcripts, documents and settings belong to the clinic. We
          process them to run the service and on your instructions, as described in the{" "}
          <a href="/privacy" className="font-medium text-accent underline">Privacy Policy</a>.
          We do not sell them and we do not train models on patient conversations. You
          can export call records from the dashboard at any time.
        </p>
      </Clause>

      <Clause heading="Acceptable use">
        <p>
          Do not use the service to contact people who have asked not to be contacted,
          to send marketing, or in any way that breaks the telecommunications or health
          privacy rules that apply to you. The software honours opt-outs — a patient who
          replies STOP stops receiving messages — but the responsibility for lawful
          contact is the clinic's.
        </p>
      </Clause>

      <Clause heading="Plans and charges">
        <p>
          Plans set what a clinic may use: included voice minutes, staff accounts,
          patient records, and whether EHR and knowledge-base features are available.
          The plans currently on offer are listed on the{" "}
          <a href="/#pricing" className="font-medium text-accent underline">pricing page</a>,
          and are read from our live catalogue rather than written into the page.
        </p>
        <p>
          Charges are arranged directly with us and are not processed by the
          application. Invoices you can open in the dashboard reproduce what is on
          record and are not payment receipts; they say so on their face.
        </p>
      </Clause>

      <Clause heading="Availability">
        <p>
          We do not offer an uptime guarantee, because we do not yet measure one and
          will not quote a figure we cannot stand behind. The service depends on third
          parties — telephony, speech and language providers — and an outage at one of
          them can interrupt calls. Where the agent cannot answer, the design is to fail
          towards a person rather than towards silence.
        </p>
      </Clause>

      <Clause heading="Ending it">
        <p>
          You can stop using the service at any time and ask us for your data. We may
          suspend an account that is being used unlawfully or that puts patients at
          risk, and we will tell you why. Suspension stops calls in progress, so it is
          not something we do lightly.
        </p>
      </Clause>

      <Clause heading="Liability">
        <p>
          The service is provided as it is. To the extent the law allows, we are not
          liable for indirect or consequential loss, and our total liability is limited
          to what you have paid us in the preceding twelve months. Nothing here limits
          liability that cannot lawfully be limited.
        </p>
      </Clause>

      <Clause heading="Changes">
        <p>
          If these terms change materially we will tell account administrators before
          the change takes effect. Continuing to use the service after that is
          acceptance. Questions go through the{" "}
          <a href="/contact" className="font-medium text-accent underline">contact page</a>.
        </p>
      </Clause>
    </PageShell>
  );
}
