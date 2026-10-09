import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import Logo from "../components/Logo";

/** Catch-all route — previously an unknown URL rendered nothing at all. */
export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-low px-4">
      <div className="w-full max-w-sm text-center">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Compass size={26} className="mx-auto mb-3 text-on-surface-variant" />
        <h1 className="font-display text-xl font-bold text-on-surface">Page not found</h1>
        <p className="mt-2 text-sm text-on-surface-variant">
          That link doesn't lead anywhere in MediCOR AI.
        </p>
        <div className="mt-6 flex justify-center gap-2">
          <Link
            to="/app"
            className="focus-ring rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover"
          >
            Go to dashboard
          </Link>
          <Link
            to="/"
            className="focus-ring rounded-lg border border-outline-variant px-4 py-2 text-sm font-medium text-on-surface hover:bg-surface-low"
          >
            Home
          </Link>
        </div>
      </div>
    </div>
  );
}
