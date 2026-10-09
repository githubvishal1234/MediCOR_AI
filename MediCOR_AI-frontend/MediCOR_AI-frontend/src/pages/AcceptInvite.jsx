/**
 * Accept a staff invitation — HV-2.
 *
 * Public route (outside AppShell and ProtectedRoute): the person landing
 * here has no account yet, and the token in the URL *is* their credential.
 *
 * The backend deliberately returns one identical error for an unknown,
 * expired, or already-used token, so this page cannot tell them apart
 * either — it shows that message as-is rather than guessing a cause.
 */

import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, UserCheck } from "lucide-react";
import Logo from "../components/Logo";
import { Card, Button, Field, InlineAlert, PasswordInput } from "../components/ui";
import * as api from "../lib/api";

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (submitting) return;
    if (password !== confirm) {
      setError("Those passwords don't match.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      // On success this stores the returned token, so the new user lands
      // in the dashboard already signed in rather than at the login form.
      await api.acceptInvite(token, password);
      navigate("/app", { replace: true });
    } catch (err) {
      setError(err?.message || "We couldn't complete your invitation.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-low px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>

        <Card className="p-6 sm:p-8">
          {!token ? (
            <div className="text-center">
              <AlertTriangle size={26} className="mx-auto text-warning" />
              <h1 className="mt-3 font-display text-lg font-bold text-on-surface">
                This link is incomplete
              </h1>
              <p className="mt-1 text-sm text-on-surface-variant">
                It's missing its invitation token. Use the full link from your invitation
                email, or ask your clinic admin to send a new one.
              </p>
              <Link to="/login" className="focus-ring mt-5 inline-block rounded text-sm font-semibold text-accent underline">
                Go to sign in
              </Link>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <UserCheck size={18} className="text-accent" aria-hidden="true" />
                <h1 className="font-display text-lg font-bold text-on-surface">
                  Set your password
                </h1>
              </div>
              <p className="mt-1 text-sm text-on-surface-variant">
                Choose a password to finish joining your clinic's MediCOR AI dashboard.
              </p>

              <form onSubmit={submit} className="mt-6 space-y-4">
                <Field label="Password" hint="At least 8 characters, with a number or symbol." required>
                  <PasswordInput
                    required
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </Field>

                <Field
                  label="Confirm password"
                  required
                  // Surfaced on the field itself rather than only in the
                  // banner below, so it is obvious *which* box to fix.
                  error={confirm && password !== confirm ? "These don't match." : undefined}
                >
                  <PasswordInput
                    required
                    autoComplete="new-password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                  />
                </Field>

                {error && <InlineAlert>{error}</InlineAlert>}

                <Button
                  type="submit"
                  loading={submitting}
                  size="lg"
                  className="w-full justify-center"
                >
                  {submitting ? "Setting up your account…" : "Join the clinic"}
                </Button>
              </form>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
