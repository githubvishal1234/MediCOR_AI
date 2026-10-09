/**
 * Four things this product actually does, stated without a badge.
 *
 * "SOC 2 Type II" used to sit here. No such audit has been carried out
 * and no report exists, so it was a certification claim the company
 * cannot make -- the same defect as the "Insurance Verified" badge that
 * was removed from the hero, and the same rule CLAUDE.md states about the
 * Security page: don't present compliance the application cannot check.
 *
 * The three that remain are each backed by something in the codebase:
 * role separation is enforced by the dependency on every route
 * (tests/test_qw7_rbac.py), every action is written through
 * `audit_service.record`, and the EHR layer connects to whatever FHIR or
 * REST gateway a clinic configures.
 */

import { ShieldCheck, ScrollText, UserCog, RefreshCw } from "lucide-react";

const ITEMS = [
  { icon: ShieldCheck, label: "Built for Healthcare" },
  { icon: UserCog, label: "Role-Based Access" },
  { icon: ScrollText, label: "Full Audit Trail" },
  { icon: RefreshCw, label: "Works With Your EHR" },
];

export default function TrustStrip() {
  return (
    <section className="border-y border-outline-variant bg-surface-low">
      <div className="mx-auto flex max-w-(--container-max) flex-wrap items-center justify-center gap-x-12 gap-y-4 px-5 py-8 sm:px-8">
        {ITEMS.map((item) => (
          <div key={item.label} className="flex items-center gap-2 text-sm font-medium text-on-surface-variant">
            <item.icon size={17} />
            {item.label}
          </div>
        ))}
      </div>
    </section>
  );
}
