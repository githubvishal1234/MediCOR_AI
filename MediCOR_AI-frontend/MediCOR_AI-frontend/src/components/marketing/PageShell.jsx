/**
 * Navbar + Footer around a standalone public page.
 *
 * The landing page is one long scroll of sections; Privacy, Terms and
 * Contact are ordinary pages with their own routes, and this is what
 * stops the chrome being written three times.
 *
 * `Navbar`'s links are in-page anchors (#features, #pricing, ...), which
 * resolve to nothing on a route that is not the landing page. They are
 * rewritten to "/#features" here so they carry the reader home to the
 * right section instead of doing nothing -- the same defect this page
 * set exists to fix, and easy to reintroduce by reusing the component
 * without thinking about it.
 */

import { Link } from "react-router-dom";
import { Footer } from "./CTAFooter";
import Logo from "../Logo";

const LINKS = [
  { href: "/#features", label: "Features" },
  { href: "/#how-it-works", label: "How It Works" },
  { href: "/#pricing", label: "Pricing" },
  { href: "/#faq", label: "FAQ" },
];

export default function PageShell({ title, intro, children }) {
  return (
    <div className="bg-surface">
      <header className="border-b border-outline-variant bg-surface-lowest">
        <div className="mx-auto flex max-w-(--container-max) items-center justify-between gap-6 px-5 py-4 sm:px-8">
          <Link to="/" className="focus-ring rounded-md">
            <Logo />
          </Link>
          <nav className="hidden items-center gap-7 text-sm font-medium text-on-surface-variant md:flex">
            {LINKS.map((l) => (
              <a key={l.href} href={l.href} className="focus-ring rounded-sm hover:text-on-surface">
                {l.label}
              </a>
            ))}
          </nav>
          <Link
            to="/app"
            className="bg-accent focus-ring rounded-lg px-4 py-2 text-sm font-semibold text-accent-contrast hover:bg-accent-hover"
          >
            Sign in
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-5 py-16 sm:px-8">
        <h1 className="font-display text-[34px] font-extrabold leading-tight tracking-tight text-on-surface sm:text-[42px]">
          {title}
        </h1>
        {intro && <p className="mt-4 text-lg leading-relaxed text-on-surface-variant">{intro}</p>}
        <div className="mt-10">{children}</div>
      </main>

      <Footer />
    </div>
  );
}

/** A titled block of policy text. Kept here so both documents match. */
export function Clause({ heading, children }) {
  return (
    <section className="mt-8 first:mt-0">
      <h2 className="font-display text-lg font-bold text-on-surface">{heading}</h2>
      <div className="mt-2 space-y-3 text-sm leading-relaxed text-on-surface-variant">
        {children}
      </div>
    </section>
  );
}
