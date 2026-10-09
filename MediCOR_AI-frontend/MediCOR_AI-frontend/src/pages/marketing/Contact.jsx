/**
 * Contact — a real form, not an anchor.
 *
 * "Contact Sales" in the footer used to be href="#contact", a section
 * that did not exist on the page, so it scrolled nowhere. This posts to
 * `POST /public/contact`, which records the enquiry in `contact_enquiries`
 * and puts it in the super-admin inbox beside the support tickets.
 *
 * Two things it deliberately does not do:
 *
 *   - **It never claims a response time.** No "we reply within one
 *     business day". That is a promise this application cannot keep, and
 *     it is the same rule the missed-call outreach message follows when
 *     it declines to say anyone will ring back.
 *   - **It shows a phone number only if one is configured.** SUPPORT_PHONE
 *     and SUPPORT_EMAIL are blank by default and nothing here invents a
 *     placeholder, because somebody dials a placeholder.
 */

import { useEffect, useState } from "react";
import { Mail, Phone, Send, Clock, CheckCircle2 } from "lucide-react";
import PageShell from "../../components/marketing/PageShell";
import * as api from "../../lib/api";

const FIELD =
  "focus-ring w-full rounded-lg border border-outline-variant bg-surface-lowest px-3 py-2.5 text-sm text-on-surface";

export default function Contact() {
  const [form, setForm] = useState({
    name: "",
    email: "",
    phone: "",
    clinic_name: "",
    message: "",
  });
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getContactInfo()
      .then((data) => {
        if (!cancelled) setInfo(data);
      })
      // A missing number is the normal case, not an error worth showing.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    setSending(true);
    setError("");
    try {
      await api.submitContactEnquiry({
        name: form.name,
        email: form.email,
        phone: form.phone || null,
        clinic_name: form.clinic_name || null,
        message: form.message,
      });
      setSent(true);
    } catch (err) {
      // A write handler needs a catch, not just a finally: without one the
      // rate limiter's 429 would clear the spinner and show nothing, and
      // the visitor would retype the whole message.
      setError(
        err?.status === 429
          ? "That's a few messages in a short time. Please try again a little later."
          : err?.message || "Something went wrong sending that. Please try again."
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <PageShell
      title="Talk to us"
      intro="Tell us about your practice and what you need the agent to do. A person reads every message."
    >
      {(info?.phone || info?.email) && (
        <div className="mb-10 grid gap-4 sm:grid-cols-2">
          {info.phone && (
            <a
              href={`tel:${info.phone.replace(/[^\d+]/g, "")}`}
              className="focus-ring flex items-start gap-3 rounded-xl border border-outline-variant bg-surface-lowest p-5 hover:bg-surface-container"
            >
              <Phone size={18} className="mt-0.5 shrink-0 text-accent" />
              <span>
                <span className="block text-sm font-semibold text-on-surface">{info.phone}</span>
                {info.hours && (
                  <span className="mt-1 flex items-center gap-1.5 text-xs text-on-surface-variant">
                    <Clock size={12} /> {info.hours}
                  </span>
                )}
              </span>
            </a>
          )}
          {info.email && (
            <a
              href={`mailto:${info.email}`}
              className="focus-ring flex items-start gap-3 rounded-xl border border-outline-variant bg-surface-lowest p-5 hover:bg-surface-container"
            >
              <Mail size={18} className="mt-0.5 shrink-0 text-accent" />
              <span className="text-sm font-semibold text-on-surface">{info.email}</span>
            </a>
          )}
        </div>
      )}

      {sent ? (
        <div className="flex items-start gap-3 rounded-xl bg-success-bg p-5">
          <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-success" />
          <div>
            <p className="text-sm font-semibold text-success">Message received.</p>
            <p className="mt-1 text-sm text-on-surface-variant">
              We have your message and will get back to you at {form.email}.
            </p>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="contact-name" className="mb-1 block text-xs font-semibold text-on-surface-variant">
                Your name
              </label>
              <input id="contact-name" required maxLength={160} value={form.name}
                onChange={set("name")} className={FIELD} />
            </div>
            <div>
              <label htmlFor="contact-email" className="mb-1 block text-xs font-semibold text-on-surface-variant">
                Email
              </label>
              <input id="contact-email" type="email" required maxLength={255} value={form.email}
                onChange={set("email")} className={FIELD} />
            </div>
            <div>
              <label htmlFor="contact-clinic" className="mb-1 block text-xs font-semibold text-on-surface-variant">
                Clinic <span className="font-normal">(optional)</span>
              </label>
              <input id="contact-clinic" maxLength={200} value={form.clinic_name}
                onChange={set("clinic_name")} className={FIELD} />
            </div>
            <div>
              <label htmlFor="contact-phone" className="mb-1 block text-xs font-semibold text-on-surface-variant">
                Phone <span className="font-normal">(optional)</span>
              </label>
              <input id="contact-phone" maxLength={40} value={form.phone}
                onChange={set("phone")} className={FIELD} />
            </div>
          </div>

          <div>
            <label htmlFor="contact-message" className="mb-1 block text-xs font-semibold text-on-surface-variant">
              How can we help?
            </label>
            <textarea id="contact-message" required rows={6} maxLength={4000} value={form.message}
              onChange={set("message")} className={FIELD}
              placeholder="How many calls you take, what you would want the agent to handle, and anything you need it not to." />
          </div>

          {error && (
            <p className="rounded-lg bg-error-bg px-4 py-3 text-sm font-medium text-error">{error}</p>
          )}

          <button
            type="submit"
            disabled={sending}
            className="bg-accent focus-ring inline-flex items-center gap-2 rounded-lg px-6 py-3 text-sm font-semibold text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
          >
            <Send size={14} /> {sending ? "Sending..." : "Send message"}
          </button>
        </form>
      )}
    </PageShell>
  );
}
