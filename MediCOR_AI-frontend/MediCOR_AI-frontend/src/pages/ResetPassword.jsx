/**
 * Password reset (GAP-U1) — both halves on one public route.
 *
 * Without a token in the URL it asks for an email address. With one, it
 * asks for a new password. Two screens, one route, because a patient of
 * this product is a member of clinic staff who has been locked out and is
 * already having a bad morning.
 *
 * The important behaviour is the same as the backend's: **the request
 * step says the same thing whether or not the address is registered.**
 * Showing "no such account" here would leak exactly what the API is
 * careful not to.
 */

import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, MailCheck } from "lucide-react";
import Logo from "../components/Logo";
import { Button, Card, Field, InlineAlert, Input, PasswordInput } from "../components/ui";
import * as api from "../lib/api";
import { ApiError, setToken } from "../lib/api";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [requested, setRequested] = useState(false);

  async function askForLink(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.requestPasswordReset(email.trim());
      // Shown for any address at all — see the module comment.
      setRequested(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Couldn't send that. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function setNewPassword(e) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Those passwords don't match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { access_token: accessToken } = await api.resetPassword(token, password);
      // The API signs them straight in, so a reset ends at the dashboard
      // rather than at a login form they have just proved they can pass.
      setToken(accessToken);
      navigate("/app", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reset your password. Please request a new link."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-low px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>

        <Card className="p-6">
          {requested ? (
            <div className="text-center">
              <MailCheck size={26} className="mx-auto text-success" aria-hidden="true" />
              <h1 className="mt-3 font-display text-lg font-bold text-on-surface">
                Check your email
              </h1>
              <p className="mt-1.5 text-sm text-on-surface-variant">
                If that address has an account, we&apos;ve sent a link to reset the
                password. It expires shortly.
              </p>
            </div>
          ) : token ? (
            <>
              <h1 className="font-display text-xl font-bold text-on-surface">
                Set a new password
              </h1>
              <p className="mt-1 text-sm text-on-surface-variant">
                Choose a password you haven&apos;t used here before.
              </p>

              <form onSubmit={setNewPassword} className="mt-6 space-y-4">
                <Field
                  label="New password"
                  hint="At least 8 characters, with a number or symbol."
                  required
                >
                  <PasswordInput
                    autoComplete="new-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </Field>

                <Field
                  label="Confirm password"
                  required
                  error={confirm && password !== confirm ? "These don't match." : undefined}
                >
                  <PasswordInput
                    autoComplete="new-password"
                    required
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                  />
                </Field>

                {error && <InlineAlert>{error}</InlineAlert>}

                <Button type="submit" loading={submitting} size="lg" className="w-full">
                  {submitting ? "Saving…" : "Set password and sign in"}
                </Button>
              </form>
            </>
          ) : (
            <>
              <h1 className="font-display text-xl font-bold text-on-surface">
                Reset your password
              </h1>
              <p className="mt-1 text-sm text-on-surface-variant">
                We&apos;ll email you a link to set a new one.
              </p>

              <form onSubmit={askForLink} className="mt-6 space-y-4">
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

                {error && <InlineAlert>{error}</InlineAlert>}

                <Button type="submit" loading={submitting} size="lg" className="w-full">
                  {submitting ? "Sending…" : "Send reset link"}
                </Button>
              </form>
            </>
          )}
        </Card>

        <p className="mt-5 text-center text-sm">
          <Link
            to="/login"
            className="focus-ring inline-flex items-center gap-1.5 rounded font-semibold text-on-surface-variant hover:text-on-surface"
          >
            <ArrowLeft size={14} aria-hidden="true" /> Back to sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
