import { useNavigate } from "react-router-dom";
import { ShieldCheck, Phone, CheckCircle2, CalendarCheck, User, MessageSquareText } from "lucide-react";

function Waveform() {
  return (
    <div className="flex h-4 items-end gap-0.5">
      {[6, 10, 14, 9, 12, 7].map((h, i) => (
        <span
          key={i}
          className="waveform-bar w-0.5 rounded-full bg-accent"
          style={{ height: h }}
        />
      ))}
    </div>
  );
}

export default function Hero() {
  const navigate = useNavigate();
  return (
    <section className="relative overflow-hidden">
      <div className="pointer-events-none absolute -top-40 right-[-10%] h-[520px] w-[520px] rounded-full bg-accent/20 blur-3xl" />
      <div className="mx-auto grid max-w-(--container-max) gap-12 px-5 py-16 sm:px-8 lg:grid-cols-2 lg:items-center lg:py-24">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-outline-variant bg-surface-lowest px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
            <ShieldCheck size={14} className="text-accent" />
            Healthcare Ready &amp; 24/7 Active
          </span>

          <h1 className="mt-6 font-display text-[40px] font-extrabold leading-[1.1] tracking-tight text-on-surface sm:text-[56px]">
            Never Miss a Patient Call.{" "}
            <span className="text-accent">Autonomous AI Receptionist</span>{" "}
            for Modern Clinics.
          </h1>

          <p className="mt-6 max-w-xl text-lg leading-relaxed text-on-surface-variant">
            Streamline front-desk operations with a high-fidelity AI agent that handles patient
            intake, books appointments natively into your EHR, and keeps patient data protected with
            enterprise-grade encryption every step of the way.
          </p>

          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            {/* Was "Test Live Voice Call" with no handler — and no demo
                number exists in this application for it to have dialled.
                It now does the thing the page is actually for. */}
            <button
              onClick={() => navigate("/login")}
              className="focus-ring rounded-lg bg-accent px-6 py-3.5 text-sm font-semibold text-accent-contrast shadow-sm transition-colors hover:bg-accent-hover"
            >
              Set up your clinic
            </button>
            <button
              onClick={() => navigate("/app")}
              className="focus-ring rounded-lg border border-outline-variant bg-surface-lowest px-6 py-3.5 text-sm font-semibold text-on-surface hover:bg-surface-container"
            >
              Explore Interactive Demo
            </button>
          </div>
        </div>

        <div className="relative">
          {/* Floating status cards — glimpses of what the AI does, not the full app */}
          <div
            className="float-card absolute -right-4 top-16 z-10 hidden items-center gap-2 rounded-xl border border-outline-variant bg-surface-lowest px-3.5 py-2.5 shadow-(--shadow-raised) sm:flex"
            style={{ animationDelay: "0s" }}
          >
            <MessageSquareText size={16} className="text-info" />
            <span className="text-xs font-semibold text-on-surface">SMS Confirmation Sent</span>
          </div>
          <div
            className="float-card absolute -bottom-5 left-8 z-10 hidden items-center gap-2 rounded-xl border border-outline-variant bg-surface-lowest px-3.5 py-2.5 shadow-(--shadow-raised) sm:flex"
            style={{ animationDelay: "3s" }}
          >
            <CalendarCheck size={16} className="text-accent" />
            <span className="text-xs font-semibold text-on-surface">Calendar Updated</span>
          </div>

          <div className="rounded-2xl border border-outline-variant bg-surface-lowest p-5 shadow-(--shadow-raised) sm:p-6">
            <div className="flex items-center justify-between border-b border-outline-variant pb-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-container">
                  <User size={18} className="text-on-surface-variant" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-on-surface">Incoming Call</p>
                  <p className="text-xs text-on-surface-variant">Unknown Number (Local)</p>
                </div>
              </div>
              <Waveform />
            </div>

            <div className="mt-4 space-y-3">
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant/70">AI Agent</p>
                <div className="rounded-xl rounded-tl-sm bg-surface-low px-4 py-2.5 text-sm text-on-surface">
                  Hello, you've reached CityCare Medical. How can I help you today?
                </div>
              </div>
              <div className="flex justify-end">
                <div className="max-w-[85%]">
                  <p className="mb-1 text-right text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant/70">Patient</p>
                  <div className="bg-accent rounded-xl rounded-tr-sm px-4 py-2.5 text-sm text-accent-contrast">
                    Hi, I'm an existing patient, John Doe. I need to book a follow-up for next Tuesday afternoon.
                  </div>
                </div>
              </div>
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant/70">AI Agent</p>
                <div className="rounded-xl rounded-tl-sm bg-surface-low px-4 py-2.5 text-sm text-on-surface">
                  Thank you, Mr. Doe. I see your file. Dr. Smith has availability at 2:00 PM or 3:30 PM next Tuesday. Does either work for you?
                </div>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap gap-2 border-t border-outline-variant pt-4">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-info-bg px-2.5 py-1 text-xs font-semibold text-info">
                <CheckCircle2 size={13} /> Patient Identified
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container px-2.5 py-1 text-xs font-semibold text-on-surface-variant">
                <CalendarCheck size={13} /> Calendar Checked
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-success-bg px-2.5 py-1 text-xs font-semibold text-success">
                <Phone size={13} /> Slot Confirmed
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
