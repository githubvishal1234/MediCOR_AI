import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Minus } from "lucide-react";

const FAQS = [
  {
    q: "Is MediCOR AI secure for healthcare use?",
    a: "Patient data stays in your own database, access is controlled by role for every member of staff, and every read and write is recorded in an audit log you can review. Clinic documents are indexed on your own server, so they are never sent to a third party.",
  },
  {
    q: "How does it integrate with my EHR?",
    a: "You point it at your EHR's FHIR or REST gateway from the dashboard and it syncs patient records out as they change. We do not ship vendor-specific connectors — if your system has an API, it connects, and if you would rather not connect one, the agent works from its own records instead.",
  },
  {
    q: "Can patients tell they're talking to an AI?",
    a: "The agent says what it is when asked, and the voice speaks Indian English rather than an American accent. Anyone who asks for a person is put through to one.",
  },
  {
    q: "What happens if the AI can't answer a question?",
    a: "The system is trained to identify complex or urgent inquiries and can automatically route those calls to your human staff or triage them based on your protocols.",
  },
  {
    q: "How long does setup take?",
    a: "Point your clinic number at the service, then fill in your hours, doctors and services. Nothing needs to be installed, and you can add an EHR connection and your own documents whenever you are ready.",
  },
];

export default function FAQ() {
  const [openIndex, setOpenIndex] = useState(0);

  return (
    <section id="faq" className="mx-auto max-w-3xl px-5 py-24 sm:px-8">
      <div className="text-center">
        <h2 className="font-display text-3xl font-bold text-on-surface sm:text-[40px]">
          Frequently Asked Questions
        </h2>
        <p className="mt-4 text-lg text-on-surface-variant">
          Everything you need to know about our AI receptionist and its security & privacy practices.
        </p>
      </div>

      <div className="mt-12 space-y-3">
        {FAQS.map((item, i) => {
          const isOpen = openIndex === i;
          return (
            <div key={item.q} className="overflow-hidden rounded-xl border border-outline-variant bg-surface-lowest">
              <button
                onClick={() => setOpenIndex(isOpen ? -1 : i)}
                className="focus-ring flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
                aria-expanded={isOpen}
              >
                <span className="font-display text-base font-semibold text-on-surface">{item.q}</span>
                {isOpen ? <Minus size={18} className="shrink-0 text-on-surface-variant" /> : <Plus size={18} className="shrink-0 text-on-surface-variant" />}
              </button>
              {isOpen && (
                <div className="px-5 pb-4 text-sm leading-relaxed text-on-surface-variant">
                  {item.a}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Restored, and now pointing at a page that exists. This was
          href="#contact" -- a section that was never on this page. */}
      <div className="mt-10 text-center">
        <Link
          to="/contact"
          className="focus-ring inline-block rounded-lg border border-outline-variant px-5 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container"
        >
          Still have questions? Talk to us
        </Link>
      </div>
    </section>
  );
}
