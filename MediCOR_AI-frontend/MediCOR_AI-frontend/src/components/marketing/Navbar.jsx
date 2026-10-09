import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Menu, Moon, Sun, X } from "lucide-react";
import Logo from "../Logo";
import { useDarkMode } from "../../lib/theme";

const LINKS = [
  { href: "#features", label: "Features" },
  { href: "#how-it-works", label: "How It Works" },
  { href: "#integration", label: "Integration" },
  { href: "#security", label: "Security" },
  { href: "#pricing", label: "Pricing" },
];

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [dark, setDark] = useDarkMode();
  const navigate = useNavigate();

  return (
    <header className="sticky top-0 z-40 border-b border-outline-variant/70 bg-surface-lowest/90 backdrop-blur">
      <div className="mx-auto flex max-w-(--container-max) items-center justify-between px-5 py-4 sm:px-8">
        <Link to="/" className="focus-ring rounded-md">
          <Logo />
        </Link>

        <nav className="hidden items-center gap-8 lg:flex">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="focus-ring rounded-sm text-sm font-medium text-on-surface-variant transition-colors hover:text-on-surface"
            >
              {l.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-3 lg:flex">
          <button
            onClick={() => setDark((d) => !d)}
            className="focus-ring rounded-lg p-2 text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
          >
            {dark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button
            onClick={() => navigate("/app")}
            className="focus-ring rounded-lg border border-outline-variant px-4 py-2 text-sm font-semibold text-on-surface hover:bg-surface-container"
          >
            Sign In
          </button>
          <button
            onClick={() => navigate("/app")}
            className="bg-accent focus-ring rounded-lg px-4 py-2 text-sm font-semibold text-accent-contrast shadow-sm hover:bg-accent-hover"
          >
            Book a Live Demo
          </button>
        </div>

        <div className="flex items-center gap-1 lg:hidden">
          <button
            className="focus-ring rounded-md p-2 text-on-surface-variant hover:text-on-surface"
            onClick={() => setDark((d) => !d)}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
          >
            {dark ? <Sun size={20} /> : <Moon size={20} />}
          </button>
          <button
            className="focus-ring rounded-md p-2 text-on-surface"
            onClick={() => setOpen((o) => !o)}
            aria-label="Toggle menu"
          >
            {open ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>

      {open && (
        <div className="border-t border-outline-variant bg-surface-lowest px-5 py-4 lg:hidden">
          <nav className="flex flex-col gap-1">
            {LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="focus-ring rounded-md px-2 py-2.5 text-sm font-medium text-on-surface-variant hover:bg-surface-container"
              >
                {l.label}
              </a>
            ))}
          </nav>
          <div className="mt-3 flex flex-col gap-2">
            <button
              onClick={() => navigate("/app")}
              className="focus-ring rounded-lg border border-outline-variant px-4 py-2.5 text-sm font-semibold hover:bg-surface-container"
            >
              Sign In
            </button>
            <button
              onClick={() => navigate("/app")}
              className="bg-accent focus-ring rounded-lg px-4 py-2.5 text-sm font-semibold text-accent-contrast"
            >
              Book a Live Demo
            </button>
          </div>
        </div>
      )}
    </header>
  );
}
