/**
 * Privacy Policy.
 *
 * Every factual claim here was checked against the code rather than
 * copied from a template, because a policy that describes something the
 * software does not do is worse than no policy: it is a published
 * commitment that is already being broken.
 *
 * What was verified, and where:
 *
 *   - **Calls are transcribed, never recorded.** `call_logs.recording_url`
 *     exists as a column and has no writer anywhere in the codebase;
 *     `schemas.py` carries a note saying so. Audio passes through Twilio
 *     and Deepgram in the moment and is not stored by this application.
 *   - **Knowledge-base documents never leave the server.**
 *     `embedding_service.py` runs fastembed/ONNX locally.
 *   - **Sub-processors** are Twilio (telephony and messaging), Deepgram
 *     (speech to text), Sarvam AI (speech) and Groq (summarisation and
 *     knowledge-base answers). Those are the only four hosts the codebase
 *     sends anything to.
 *   - **The audit log records field names, never values**
 *     (`audit_service.changed_fields`).
 *   - **No retention schedule is claimed**, because none is implemented.
 *     Nothing in `jobs.py` deletes patient data on a timer, so promising
 *     a deletion period would be a promise nothing keeps.
 *
 * THE REVIEW BANNER: this document is accurate but it is not legal
 * advice, and it has not been through a lawyer. Remove `<ReviewNotice />`
 * once it has, and not before.
 */

import PageShell, { Clause } from "../../components/marketing/PageShell";
import { AlertTriangle } from "lucide-react";

function ReviewNotice() {
  return (
    <div className="mb-10 flex items-start gap-3 rounded-xl border border-warning bg-warning-bg p-4">
      <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warning" />
      <p className="text-sm leading-relaxed text-on-surface">
        <span className="font-semibold">Provisional.</span> This policy describes how the
        software actually handles data today, but it has not yet been reviewed by a
        qualified lawyer or adapted to the jurisdictions we operate in. It will be
        finalised before general availability.
      </p>
    </div>
  );
}

export default function Privacy() {
  return (
    <PageShell
      title="Privacy Policy"
      intro="What MediCOR AI collects, where it goes, and what it never does with it."
    >
      <ReviewNotice />

      <Clause heading="Who holds the data">
        <p>
          MediCOR AI is used by clinics. The clinic decides what patient
          information is entered and why; we process it on the clinic's instructions and
          store it in the database belonging to that clinic's deployment. If you are a
          patient and want to see, correct or delete what is held about you, contact
          the clinic you called — they control the record, and we act on their
          instruction.
        </p>
      </Clause>

      <Clause heading="What is collected">
        <p>
          <strong>From patients:</strong> the phone number you call from, your name and
          date of birth if you give them, and what was said on the call as text.
          Appointments, repeat-prescription requests, anything you submit through a
          pre-visit link, and whether you have agreed to be contacted by message.
        </p>
        <p>
          <strong>From clinic staff:</strong> name, email address, role, and a hashed
          password. Never the password itself.
        </p>
        <p>
          <strong>Automatically:</strong> a log of which member of staff or which agent
          read or changed which record, and when.
        </p>
      </Clause>

      <Clause heading="Calls are transcribed, not recorded">
        <p>
          The audio of a call is not stored. It passes through our telephony and
          speech-to-text providers while the call is happening and is not written to
          disk by this application; what is kept is the transcript, a summary, and the
          outcome. There is no recording to play back, request or leak.
        </p>
      </Clause>

      <Clause heading="Who else sees it">
        <p>
          Four processors, each doing one job, and no others:
        </p>
        <ul className="ml-5 list-disc space-y-1.5">
          <li><strong>Twilio</strong> — carries the phone call and any SMS or WhatsApp message.</li>
          <li><strong>Deepgram</strong> — converts call audio to text while the call runs.</li>
          <li><strong>Sarvam AI</strong> — produces the agent's speech.</li>
          <li>
            <strong>Groq</strong> — summarises a finished call and drafts answers from
            the clinic's own documents.
          </li>
        </ul>
        <p>
          Documents a clinic uploads for the agent to answer from are indexed on the
          clinic's own server and are not sent anywhere. We do not sell data, we do not
          share it for advertising, and we do not use patient conversations to train
          models.
        </p>
      </Clause>

      <Clause heading="How long it is kept">
        <p>
          Records are kept until the clinic deletes them. We do not currently run an
          automatic deletion schedule, so nothing expires on its own — a clinic that
          needs a retention period should ask us, and should tell us if their own rules
          require one.
        </p>
      </Clause>

      <Clause heading="Security">
        <p>
          Access is separated by role: reception staff, administrators, the platform
          team and the voice agent each reach different things, and the agent in
          particular can record a repeat-prescription request but can never approve one.
          Every read and write of an individual record is logged against whoever made
          it. The audit log stores the names of the fields that changed, never their
          contents, so it does not become a second copy of the medical record.
        </p>
        <p>
          No system is perfect. If you believe data has been exposed, tell us and we
          will investigate and inform the affected clinic.
        </p>
      </Clause>

      <Clause heading="Emergencies">
        <p>
          The voice agent is not an emergency service. A caller who describes an
          emergency is told to hang up and call the emergency number, and is offered a
          transfer to a person. Nothing about that interaction is treated differently
          for privacy purposes — it is recorded as a transcript like any other call.
        </p>
      </Clause>

      <Clause heading="Contacting us">
        <p>
          Questions about this policy, or a request about your own data, can be sent
          through the <a href="/contact" className="font-medium text-accent underline">contact page</a>.
          If you are a patient, contact your clinic first — they hold the record.
        </p>
      </Clause>
    </PageShell>
  );
}
