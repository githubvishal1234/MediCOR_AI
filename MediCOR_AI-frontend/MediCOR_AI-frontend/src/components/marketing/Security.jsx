/**
 * Security, stated as what is configured rather than what is certified.
 *
 * Three claims here were assertions nobody had checked, which is the
 * exact rule CLAUDE.md sets for the in-app Security page: AES-256 and
 * TLS 1.3 are schema defaults in `ComplianceOut`, not measurements -- so
 * naming the cipher and the protocol version on a public page states two
 * specific facts about a deployment this code cannot inspect. "Audited
 * regularly" described an audit that has not happened. And the audit log
 * is an ordinary table: it is complete and attributed, which is worth
 * saying, but it is not tamper-evident -- there is no hash chain and no
 * write-once storage, and claiming otherwise would misrepresent what a
 * clinic could rely on it for in a dispute.
 */
import { ShieldCheck, Lock, FileCheck2, UserCog } from "lucide-react";

const POINTS = [
  { icon: ShieldCheck, title: "Data Protection", body: "Patient data stays in your own database. The knowledge base is embedded on your server, so clinic documents never leave it." },
  { icon: Lock, title: "Encryption", body: "Transport security in front of the API, and encryption at rest configured on the database that holds transcripts and records." },
  { icon: FileCheck2, title: "Audit Logs", body: "Every read and write is recorded against the person or agent that made it, and the log is yours to review at any time." },
  { icon: UserCog, title: "Role-Based Access", body: "Granular, role-based permissions for admins, medical staff, and AI agents." },
];

export default function Security() {
  return (
    <section id="security" className="bg-navy py-24">
      <div className="mx-auto max-w-(--container-max) px-5 sm:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-semibold uppercase tracking-wider text-cyan-light">Security</p>
          <h2 className="mt-3 font-display text-3xl font-bold text-white sm:text-[40px]">
            Built for high-trust healthcare environments
          </h2>
        </div>

        <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {POINTS.map((p) => (
            <div key={p.title} className="rounded-2xl border border-white/10 bg-white/5 p-6">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/10">
                <p.icon size={18} className="text-cyan-light" />
              </div>
              <h3 className="mt-4 font-display font-bold text-white">{p.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-white/60">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
