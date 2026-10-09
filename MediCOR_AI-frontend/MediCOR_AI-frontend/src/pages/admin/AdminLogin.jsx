import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import Logo from "../../components/Logo";
import { Card, Button, Chip, Field, Input, InlineAlert, PasswordInput } from "../../components/ui";
import { useAdminAuth } from "../../lib/adminAuth";
import { ApiError } from "../../lib/adminApi";

export default function AdminLogin() {
  const { signIn } = useAdminAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from?.pathname || "/admin";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sign in. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-low px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card className="p-6" elevation="raised">
          {/* Says which console this is before anyone types. A clinic
              account cannot sign in here — the two token families are
              separate — so the wrong login is a dead end worth avoiding. */}
          <Chip tone="warning">
            <ShieldAlert size={13} aria-hidden="true" />
            Restricted
          </Chip>
          <h1 className="mt-3 font-display text-xl font-bold text-on-surface">Platform admin</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Sign in with your admin account.
          </p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <Field label="Email" required>
              <Input
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@platform.com"
              />
            </Field>

            <Field label="Password" required>
              <PasswordInput
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </Field>

            {error && <InlineAlert>{error}</InlineAlert>}

            {/* The ordinary primary button. The navy override is not
                redefined for dark mode, so there it was a near-black
                button on a near-black card with no visible edge. */}
            <Button
              type="submit"
              loading={submitting}
              size="lg"
              className="w-full"
            >
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </Card>

        <p className="mt-5 text-center text-sm text-on-surface-variant">
          Looking for your clinic dashboard?{" "}
          <Link to="/login" className="focus-ring rounded font-semibold text-accent hover:underline">
            Sign in there
          </Link>
        </p>
      </div>
    </div>
  );
}
