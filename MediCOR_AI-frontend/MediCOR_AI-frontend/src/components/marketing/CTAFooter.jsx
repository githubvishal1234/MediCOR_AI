import { Link, useNavigate } from "react-router-dom";
import Logo from "../Logo";

export function CTA() {
  const navigate = useNavigate();
  return (
    <section className="bg-navy py-20">
      <div className="mx-auto max-w-3xl px-5 text-center sm:px-8">
        <h2 className="font-display text-3xl font-bold text-white sm:text-[40px]">
          Ready to Transform Your Practice's Front Desk?
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-lg text-white/60">
          Point your clinic number at an autonomous medical receptionist, and connect your
          EHR when you are ready.
        </p>
        <button
          onClick={() => navigate("/app")}
          className="bg-accent focus-ring mt-8 rounded-lg px-7 py-3.5 text-sm font-semibold text-accent-contrast shadow-sm hover:bg-accent-hover"
        >
          Get started
        </button>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="bg-surface-low py-12">
      <div className="mx-auto flex max-w-(--container-max) flex-col items-center gap-6 px-5 text-center sm:px-8 lg:flex-row lg:justify-between lg:text-left">
        <div>
          <Logo />
          <p className="mt-2 text-sm text-on-surface-variant">
            © {new Date().getFullYear()} MediCOR AI. All rights reserved.
          </p>
        </div>
        {/* Privacy, Terms and Contact are real routes now (see
            src/pages/marketing/). They were href="#privacy", "#terms"
            and "#contact" -- anchors to sections that never existed on
            this page, so all three scrolled nowhere. Use <Link> for the
            routes and a plain anchor for the in-page section, because
            react-router will not scroll to an id it does not own. */}
        <nav className="flex flex-wrap items-center justify-center gap-6 text-sm text-on-surface-variant">
          <Link to="/privacy" className="focus-ring rounded-sm hover:text-on-surface">Privacy Policy</Link>
          <Link to="/terms" className="focus-ring rounded-sm hover:text-on-surface">Terms of Service</Link>
          {/* "/#security", not "#security": this footer also renders on
              /privacy and /terms, where a bare fragment points at a
              section that is not on the page. With the path included the
              browser goes home and scrolls, and on the landing page
              itself the path already matches so it is a fragment jump
              with no reload. */}
          <a href="/#security" className="focus-ring rounded-sm hover:text-on-surface">Security</a>
          <Link to="/contact" className="focus-ring rounded-sm hover:text-on-surface">Contact Sales</Link>
        </nav>
      </div>
    </footer>
  );
}
