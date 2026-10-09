/**
 * Sign in / create a clinic.
 *
 * This is the first screen anyone sees, and it was the thinnest in the
 * application: a centred card on a flat grey field, with its own local
 * `inputClass` string rather than the shared form primitives.
 *
 * It is now a two-column layout — the form on the left, and on wide
 * screens a panel saying what the product actually does. Everything in
 * that panel is a capability this codebase implements; there are no
 * customer counts, logos or testimonials, because there is nothing here
 * that knows any.
 */

import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { PhoneCall, CalendarCheck, Languages, FileText, ArrowLeft } from "lucide-react";
import Logo from "../components/Logo";
import { Button, Field, Input, InlineAlert, PasswordInput, Select } from "../components/ui";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";

/**
 * A short list of common clinic timezones, plus whatever the browser
 * reports. The full IANA list is ~600 entries — far too many for a signup
 * form — and the Clinic Profile page offers the complete picker later.
 *
 * Asked for here because scheduling is wrong until it is right: working
 * hours, offered slots and "today" on the dashboard are all read in this
 * zone, and a clinic left on the UTC default has every slot shifted by
 * its own offset.
 */
const COMMON_TIMEZONES = [
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Europe/London",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Australia/Sydney",
  "UTC",
];

/**
 * Four things the receptionist does, each backed by something in this
 * repository: the voice gateway, the appointments router and its agent
 * tools, the voice the gateway actually speaks with, and the retrieval
 * service that answers from a clinic's own uploaded documents.
 *
 * The third entry used to read "Speaks English, Hindi and Telugu". That
 * is the *legacy* stack's capability. The current gateway is English
 * only by explicit decision — see CLAUDE.md, "This stack is English
 * only, by decision, for now" — and `marketing/Features.jsx` was already
 * corrected to say so. This is the same correction, on the page that
 * pass missed.
 */
const CAPABILITIES = [
  {
    icon: PhoneCall,
    title: "Answers every call",
    detail: "Out of hours, during a rush, and when the front desk is already on another line.",
  },
  {
    icon: CalendarCheck,
    title: "Books, moves and cancels appointments",
    detail: "Against your real availability, with staff review before anything is confirmed.",
  },
  {
    icon: Languages,
    title: "Speaks Indian English",
    detail: "A voice tuned for Indian English rather than an American one, so callers are understood and understand back.",
  },
  {
    icon: FileText,
    title: "Answers from your own documents",
    detail: "Opening hours, fees and policies, taken from what you upload — never invented.",
  },
];

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export default function Login() {
  const { signIn, signUp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from?.pathname || "/app";

  const [mode, setMode] = useState("signin"); // signin | signup
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [fullName, setFullName] = useState("");
  const [timezone, setTimezone] = useState(browserTimezone);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const isSignup = mode === "signup";

  // The browser's own zone is usually right and may not be in the short
  // list above, so make sure it's always selectable.
  const timezoneOptions = COMMON_TIMEZONES.includes(timezone)
    ? COMMON_TIMEZONES
    : [timezone, ...COMMON_TIMEZONES];

  function switchMode(next) {
    setMode(next);
    setError(null);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (isSignup) {
        await signUp({ orgName, fullName, email, password, timezone });
      } else {
        await signIn(email, password);
      }
      navigate(from, { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : isSignup
          ? "Couldn't create your clinic. Please try again."
          : "Unable to sign in. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-surface-lowest lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      {/* ---- form ---------------------------------------------------- */}
      <div className="flex min-h-screen flex-col px-4 py-8 sm:px-8 lg:min-h-0 lg:px-12">
        <div className="flex items-center justify-between gap-4">
          <Link to="/" className="focus-ring rounded-lg" aria-label="MediCOR AI home">
            <Logo />
          </Link>
          <Link
            to="/"
            className="focus-ring hidden items-center gap-1.5 rounded-lg px-2 py-1 text-sm font-medium text-on-surface-variant transition-colors hover:text-on-surface sm:flex"
          >
            <ArrowLeft size={15} aria-hidden="true" /> Back to site
          </Link>
        </div>

        <div className="flex flex-1 items-center justify-center py-10">
          <div className="w-full max-w-sm">
            <h1 className="font-display text-2xl font-bold tracking-tight text-on-surface">
              {isSignup ? "Create your clinic" : "Welcome back"}
            </h1>
            <p className="mt-1.5 text-sm text-on-surface-variant">
              {isSignup
                ? "Set up your AI receptionist in a couple of minutes."
                : "Sign in to your MediCOR AI dashboard."}
            </p>

            <form onSubmit={handleSubmit} className="mt-7 space-y-4">
              {isSignup && (
                <>
                  <Field label="Clinic name" required>
                    <Input
                      autoComplete="organization"
                      required
                      value={orgName}
                      onChange={(e) => setOrgName(e.target.value)}
                      placeholder="HealthLink Clinic"
                    />
                  </Field>

                  <Field label="Your name" required>
                    <Input
                      autoComplete="name"
                      required
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      placeholder="Dr. Priya Sharma"
                    />
                  </Field>

                  <Field
                    label="Timezone"
                    hint="Appointment times and working hours are read in this zone. You can change it later in Clinic Profile."
                  >
                    <Select value={timezone} onChange={(e) => setTimezone(e.target.value)}>
                      {timezoneOptions.map((tz) => (
                        <option key={tz} value={tz}>
                          {tz}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </>
              )}

              <Field label="Email" required>
                <Input
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@clinic.com"
                />
              </Field>

              <Field
                label="Password"
                required
                hint={isSignup ? "At least 8 characters, with a number or symbol." : undefined}
              >
                <PasswordInput
                  autoComplete={isSignup ? "new-password" : "current-password"}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                />
              </Field>

              {!isSignup && (
                <p className="-mt-1 text-right">
                  <Link
                    to="/reset-password"
                    className="focus-ring rounded text-xs font-medium text-on-surface-variant hover:text-on-surface"
                  >
                    Forgotten your password?
                  </Link>
                </p>
              )}

              {error && <InlineAlert>{error}</InlineAlert>}

              <Button type="submit" loading={submitting} size="lg" className="w-full">
                {submitting
                  ? isSignup
                    ? "Creating your clinic…"
                    : "Signing in…"
                  : isSignup
                  ? "Create clinic"
                  : "Sign in"}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-on-surface-variant">
              {isSignup ? "Already have an account?" : "New clinic?"}{" "}
              <button
                type="button"
                onClick={() => switchMode(isSignup ? "signin" : "signup")}
                className="focus-ring rounded font-semibold text-accent hover:underline"
              >
                {isSignup ? "Sign in" : "Create one"}
              </button>
            </p>
          </div>
        </div>
      </div>

      {/* ---- capability panel ---------------------------------------- */}
      {/* Hidden below `lg` rather than stacked: on a phone it would push
          the form itself below the fold, and signing in is the job. */}
      <aside className="relative hidden overflow-hidden bg-navy p-12 lg:flex lg:flex-col lg:justify-center">
        {/* Decorative field wash. Sits behind the content and is not
            announced. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-24 -top-24 h-96 w-96 rounded-full bg-logo/20 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-32 -left-20 h-96 w-96 rounded-full bg-accent/20 blur-3xl"
        />

        <div className="relative max-w-md">
          <h2 className="font-display text-3xl font-bold leading-tight tracking-tight text-white">
            An AI receptionist that never puts a patient on hold.
          </h2>
          <p className="mt-4 text-base leading-relaxed text-white/70">
            MediCOR AI answers your clinic&apos;s phone, handles the booking, and hands
            your team a transcript and summary of every call.
          </p>

          <ul className="mt-10 space-y-6">
            {CAPABILITIES.map(({ icon: Icon, title, detail }) => (
              <li key={title} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10">
                  <Icon size={18} className="text-cyan-light" aria-hidden="true" />
                </span>
                <div>
                  <p className="font-semibold text-white">{title}</p>
                  <p className="mt-0.5 text-sm leading-relaxed text-white/60">{detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
