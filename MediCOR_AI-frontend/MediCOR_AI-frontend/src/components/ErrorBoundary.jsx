import { Component } from "react";
import { AlertTriangle } from "lucide-react";

/**
 * Catches render-time crashes so one broken component doesn't leave the
 * user staring at a blank white page with no way forward.
 *
 * Deliberately a class component: React has no hook equivalent of
 * componentDidCatch.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surfaced in the browser console for whoever is debugging; there is
    // no error-reporting backend wired up yet.
    console.error("Unhandled UI error:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-low px-4">
        <div className="w-full max-w-md rounded-xl border border-outline-variant bg-surface-lowest p-8 text-center">
          <AlertTriangle size={28} className="mx-auto mb-4 text-warning" />
          <h1 className="font-display text-lg font-bold text-on-surface">
            Something went wrong on this page
          </h1>
          <p className="mt-2 text-sm text-on-surface-variant">
            The rest of the app is still running. Reloading usually clears it.
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <button
              onClick={() => window.location.reload()}
              className="focus-ring rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover"
            >
              Reload
            </button>
            <a
              href="/app"
              className="focus-ring rounded-lg border border-outline px-4 py-2 text-sm font-medium text-on-surface hover:bg-surface-low"
            >
              Back to dashboard
            </a>
          </div>
        </div>
      </div>
    );
  }
}
