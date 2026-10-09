/**
 * Team management — HV-2.
 *
 * Until this page existed a clinic had exactly one login, because the only
 * user-creating path in the product (`POST /auth/register`) creates a new
 * *organization*. Everything here is admin-only server-side, so a
 * medical_staff user who reaches the URL gets a clear message rather than
 * a wall of failed requests.
 */

import { useCallback, useEffect, useState } from "react";
import {
  UserPlus, Mail, Copy, Check, RefreshCw, ShieldCheck, Stethoscope,
  Loader2, AlertTriangle, Clock, UserX, UserCheck,
} from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, Skeleton, ErrorState, Modal, ConfirmDialog, Field, Input, Select,
} from "../../components/ui";
import { useAuth } from "../../lib/auth";
import * as api from "../../lib/api";
import { clinicRoleLabel } from "../../lib/format";

function initialsOf(name) {
  return (name || "?")
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0])
    .join("")
    .toUpperCase();
}

/**
 * The invite link is returned exactly once by the API. If SMTP isn't
 * configured it is the ONLY way the invited person can complete signup,
 * so it gets a dedicated dialog with copy-to-clipboard rather than a
 * toast that can be dismissed by accident.
 */
function InviteResultModal({ result, onClose }) {
  const [copied, setCopied] = useState(false);
  if (!result) return null;

  const link = result.invite_url;
  async function copy() {
    try {
      await navigator.clipboard.writeText(link || result.invite_token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can be blocked (insecure context, permissions). The
      // value is selectable on screen, so this is not worth an error.
    }
  }

  return (
    <Modal open title={`Invitation for ${result.user.full_name}`} onClose={onClose}>
      {result.email_sent ? (
        <p className="flex items-start gap-2 text-sm text-on-surface-variant">
          <Mail size={15} className="mt-0.5 shrink-0 text-accent" />
          <span>
            An invitation email is on its way to{" "}
            <span className="font-medium text-on-surface">{result.user.email}</span>. You can also
            share the link below directly.
          </span>
        </p>
      ) : (
        <p className="flex items-start gap-2 rounded-lg bg-warning-bg p-3 text-sm text-warning">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            Email isn&apos;t configured on this deployment, so nothing was sent.
            <strong className="font-semibold"> Send this link to {result.user.full_name} yourself</strong> —
            it won&apos;t be shown again.
          </span>
        </p>
      )}

      <div className="mt-4">
        <label
          htmlFor="team-invite-credential"
          className="mb-1 block text-xs font-semibold text-on-surface-variant"
        >
          {link ? "Invitation link" : "Invitation token"}
        </label>
        <div className="flex gap-2">
          <div className="min-w-0 flex-1">
            <Input
              id="team-invite-credential"
              readOnly
              value={link || result.invite_token}
              onFocus={(e) => e.target.select()}
              className="bg-surface-low font-mono text-xs"
            />
          </div>
          <Button variant="outline" onClick={copy} type="button">
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        {!link && (
          <p className="mt-2 text-xs text-on-surface-variant">
            No frontend URL is configured on the backend, so only the raw token could be
            generated. Set <code className="font-mono">CORS_ORIGINS</code> to produce full links.
          </p>
        )}
      </div>

      <div className="mt-5 flex justify-end">
        <Button onClick={onClose}>Done</Button>
      </div>
    </Modal>
  );
}

export default function Team() {
  const { user: me } = useAuth();

  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [forbidden, setForbidden] = useState(false);

  const [showInvite, setShowInvite] = useState(false);
  const [draft, setDraft] = useState({ email: "", full_name: "", role: "medical_staff" });
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [inviteResult, setInviteResult] = useState(null);

  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState("");
  const [confirmDeactivate, setConfirmDeactivate] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    api
      .listTeam()
      .then((data) => {
        setMembers(data);
        setError(false);
        setForbidden(false);
      })
      .catch((err) => {
        if (err?.status === 403) setForbidden(true);
        else setError(true);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function submitInvite(e) {
    e.preventDefault();
    if (inviting) return;
    setInviting(true);
    setInviteError("");
    try {
      const result = await api.inviteTeamMember(draft);
      setInviteResult(result);
      setShowInvite(false);
      setDraft({ email: "", full_name: "", role: "medical_staff" });
      load();
    } catch (err) {
      setInviteError(err?.message || "Couldn't send that invitation.");
    } finally {
      setInviting(false);
    }
  }

  /** Wraps a mutation so every failure surfaces the server's own message. */
  async function runAction(id, fn) {
    setBusyId(id);
    setActionError("");
    try {
      await fn();
      load();
    } catch (err) {
      setActionError(err?.message || "That change couldn't be applied.");
    } finally {
      setBusyId(null);
    }
  }

  const subtitle = "Invite colleagues, set what they can access, and deactivate people who leave.";

  if (forbidden) {
    return (
      <AppShell title="Team" subtitle={subtitle}>
        <Card className="p-10 text-center">
          <ShieldCheck size={28} className="mx-auto text-on-surface-variant" />
          <h2 className="mt-3 font-display text-base font-bold text-on-surface">
            Admin access required
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-on-surface-variant">
            Only clinic admins can manage team members. Ask an admin at your clinic if you
            need access changed.
          </p>
        </Card>
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell title="Team" subtitle={subtitle}>
        <ErrorState detail="We couldn't load your team." onRetry={load} className="py-24" />
      </AppShell>
    );
  }

  return (
    <AppShell title="Team" subtitle={subtitle}>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Button onClick={() => { setInviteError(""); setShowInvite(true); }}>
          <UserPlus size={14} /> Invite member
        </Button>
        {!loading && (
          <span className="text-sm text-on-surface-variant">
            {members.filter((m) => m.is_active).length} active
            {members.some((m) => m.invite_pending) &&
              ` · ${members.filter((m) => m.invite_pending).length} pending`}
          </span>
        )}
      </div>

      {actionError && (
        <div className="mb-4 flex items-start gap-2 rounded-xl bg-error-bg p-3 text-sm text-error" role="alert">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{actionError}</span>
        </div>
      )}

      <Card className="divide-y divide-outline-variant">
        {loading ? (
          <div className="space-y-3 p-6">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}
          </div>
        ) : (
          members.map((m) => {
            const isMe = me?.id === m.id;
            const busy = busyId === m.id;
            return (
              <div key={m.id} className="flex flex-wrap items-center gap-4 p-4 sm:px-6">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-container text-sm font-bold text-on-surface-variant">
                  {initialsOf(m.full_name)}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-semibold text-on-surface">{m.full_name}</p>
                    {isMe && <Chip tone="info">You</Chip>}
                    {m.invite_pending && <Chip tone="warning"><Clock size={12} /> Invite pending</Chip>}
                    {!m.is_active && !m.invite_pending && <Chip tone="neutral">Deactivated</Chip>}
                  </div>
                  <p className="truncate text-xs text-on-surface-variant">{m.email}</p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {/* Role is a live control, but never for yourself — the
                      server refuses self-demotion and this mirrors that. */}
                  <Select
                    value={m.role}
                    disabled={isMe || busy || !m.is_active}
                    onChange={(e) =>
                      runAction(m.id, () => api.updateTeamMember(m.id, { role: e.target.value }))
                    }
                    aria-label={`Role for ${m.full_name}`}
                    className="w-auto bg-surface-low px-2.5 py-1.5 text-xs font-medium"
                  >
                    <option value="admin">{clinicRoleLabel("admin")}</option>
                    <option value="medical_staff">{clinicRoleLabel("medical_staff")}</option>
                  </Select>

                  {m.invite_pending && (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        runAction(m.id, async () => setInviteResult(await api.resendInvite(m.id)))
                      }
                    >
                      {busy ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                      Resend
                    </Button>
                  )}

                  {m.is_active && !isMe && (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => setConfirmDeactivate(m)}
                    >
                      <UserX size={14} /> Deactivate
                    </Button>
                  )}

                  {!m.is_active && !m.invite_pending && (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        runAction(m.id, () => api.updateTeamMember(m.id, { is_active: true }))
                      }
                    >
                      <UserCheck size={14} /> Reactivate
                    </Button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </Card>

      <Modal open={showInvite} title="Invite a team member" onClose={() => setShowInvite(false)}>
        <form onSubmit={submitInvite} className="space-y-4">
          <Field label="Full name" required>
            <Input
              required
              value={draft.full_name}
              onChange={(e) => setDraft({ ...draft, full_name: e.target.value })}
              placeholder="Dr. Priya Sharma"
              autoComplete="name"
            />
          </Field>
          <Field label="Email" required hint="The invitation is sent here.">
            <Input
              required
              type="email"
              inputMode="email"
              autoComplete="email"
              value={draft.email}
              onChange={(e) => setDraft({ ...draft, email: e.target.value })}
              placeholder="priya@yourclinic.com"
            />
          </Field>
          <div role="group" aria-label="Role">
            <p className="mb-1 block text-xs font-semibold text-on-surface-variant">Role</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {[
                { value: "medical_staff", label: "Medical Staff", detail: "Day-to-day clinical work", icon: Stethoscope },
                { value: "admin", label: "User", detail: "Full access, including billing", icon: ShieldCheck },
              ].map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  // The choice was conveyed by border colour alone.
                  aria-pressed={draft.role === opt.value}
                  onClick={() => setDraft({ ...draft, role: opt.value })}
                  className={`focus-ring rounded-xl border p-3 text-left transition-colors ${
                    draft.role === opt.value
                      ? "border-accent bg-accent-subtle"
                      : "border-outline-variant hover:bg-surface-low"
                  }`}
                >
                  <opt.icon size={16} className="text-on-surface-variant" aria-hidden="true" />
                  <p className="mt-1.5 text-sm font-semibold text-on-surface">{opt.label}</p>
                  <p className="text-xs text-on-surface-variant">{opt.detail}</p>
                </button>
              ))}
            </div>
          </div>

          {inviteError && <p className="text-sm text-error" role="alert">{inviteError}</p>}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={() => setShowInvite(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={inviting}>
              {inviting ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
              {inviting ? "Sending..." : "Send invitation"}
            </Button>
          </div>
        </form>
      </Modal>

      <InviteResultModal result={inviteResult} onClose={() => setInviteResult(null)} />

      <ConfirmDialog
        open={Boolean(confirmDeactivate)}
        title={`Deactivate ${confirmDeactivate?.full_name || ""}?`}
        detail="They'll be signed out and won't be able to log in. Their record and history are kept, and you can reactivate them later."
        confirmLabel="Deactivate"
        onCancel={() => setConfirmDeactivate(null)}
        onConfirm={() => {
          const target = confirmDeactivate;
          setConfirmDeactivate(null);
          runAction(target.id, () => api.deactivateTeamMember(target.id));
        }}
      />
    </AppShell>
  );
}
