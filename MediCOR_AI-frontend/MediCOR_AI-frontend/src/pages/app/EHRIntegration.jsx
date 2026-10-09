import { useCallback, useEffect, useState } from "react";
import { Database, CheckCircle2, Info, Key, Plus, Copy, Check, Trash2, Webhook, X, Lock, TriangleAlert } from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, SkeletonCard, EmptyState, ErrorState, InlineAlert, Input, Field,
  Modal, ConfirmDialog, SectionHeader,
} from "../../components/ui";
import { Link } from "react-router-dom";
import * as api from "../../lib/api";

export default function EHRIntegration() {
  const [integrations, setIntegrations] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [webhook, setWebhook] = useState({ endpoint_url: "", events: [], available_events: [] });
  const [webhookError, setWebhookError] = useState("");
  const [webhookSaved, setWebhookSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [newKeyPlaintext, setNewKeyPlaintext] = useState(null);
  const [webhookUrl, setWebhookUrl] = useState("");
  // HV-4: `/ehr/status` distinguishes "your plan doesn't include EHR"
  // from "nobody has connected one yet". Without that the Configure
  // button below just 403s with nothing shown.
  const [planBlock, setPlanBlock] = useState(null);
  const [connectError, setConnectError] = useState(null);
  const [showNewKey, setShowNewKey] = useState(false);
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [creatingKey, setCreatingKey] = useState(false);
  const [keyError, setKeyError] = useState(null);
  // Revoking is immediate and irreversible — anything still authenticating
  // with that key starts failing — so it asks first.
  const [revoking, setRevoking] = useState(null);
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [copied, setCopied] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.all([
      api.listEHRIntegrations(),
      api.listAPIKeys(),
      api.getWebhook(),
      api.getEHRStatus(),
    ])
      .then(([ints, keys, hook, status]) => {
        if (cancelled) return;
        setIntegrations(ints);
        setApiKeys(keys);
        setWebhook(hook);
        setWebhookUrl(hook.endpoint_url || "");
        setPlanBlock(status?.plan_restricted ? status.detail : null);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return load();
  }, [load]);

  async function toggleConnect(integration) {
    setConnectError(null);
    try {
      const updated = await api.updateEHRIntegration(integration.id, { connected: !integration.connected });
      setIntegrations((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
    } catch (err) {
      // Previously unhandled: a rejected PATCH left the card unchanged
      // with no explanation, which is exactly what a plan-gated clinic
      // now hits.
      setConnectError(err?.message || "Couldn't change that integration.");
    }
  }

  // Was a `window.prompt`, which several browsers now suppress entirely
  // inside cross-origin frames and which no styling reaches. It also had
  // no error path: a rejected create threw into the void.
  async function createKey(e) {
    e.preventDefault();
    const label = newKeyLabel.trim();
    if (!label) return;
    setCreatingKey(true);
    setKeyError(null);
    try {
      const created = await api.createAPIKey({ label, environment: "production" });
      setNewKeyPlaintext(created.plaintext_key);
      setApiKeys((prev) => [created, ...prev]);
      setShowNewKey(false);
      setNewKeyLabel("");
    } catch (err) {
      setKeyError(err?.message || "Couldn't create that key.");
    } finally {
      setCreatingKey(false);
    }
  }

  async function confirmRevoke() {
    if (!revoking) return;
    setRevokeBusy(true);
    setKeyError(null);
    const previous = apiKeys;
    setApiKeys((prev) => prev.filter((k) => k.id !== revoking.id));
    try {
      await api.revokeAPIKey(revoking.id);
      setRevoking(null);
    } catch (err) {
      // The optimistic removal used to be permanent on failure: the key
      // still worked, but the page said it was gone.
      setApiKeys(previous);
      setKeyError(err?.message || "Couldn't revoke that key.");
      setRevoking(null);
    } finally {
      setRevokeBusy(false);
    }
  }

  function copy(text, id) {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(id);
        setTimeout(() => setCopied((c) => (c === id ? null : c)), 2000);
      },
      () => setKeyError("Couldn't copy to the clipboard.")
    );
  }

  async function saveWebhook() {
    // A `catch` is not optional here: without one a rejected save showed
    // nothing at all, so an invalid URL or an unknown event looked
    // identical to success. Same pattern as KnowledgeBase.jsx.
    setWebhookError("");
    setWebhookSaved(false);
    try {
      const updated = await api.updateWebhook({
        endpoint_url: webhookUrl,
        events: webhook.events,
      });
      setWebhook(updated);
      setWebhookSaved(true);
    } catch (err) {
      setWebhookError(err?.message || "Couldn't save that webhook.");
    }
  }

  function toggleEvent(ev) {
    setWebhook((prev) => ({
      ...prev,
      events: prev.events.includes(ev) ? prev.events.filter((e) => e !== ev) : [...prev.events, ev],
    }));
  }

  if (error) {
    return (
      <AppShell title="EHR Integration Hub" subtitle="Manage seamless bidirectional data flow between MediCOR AI and your existing Electronic Health Record systems.">
        <ErrorState detail="We couldn't load your EHR integrations." onRetry={load} className="py-24" />
      </AppShell>
    );
  }

  if (loading) {
    return (
      <AppShell title="EHR Integration Hub" subtitle="Manage seamless bidirectional data flow between MediCOR AI and your existing Electronic Health Record systems.">
        <div className="grid gap-4 sm:grid-cols-2">
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}><SkeletonCard lines={3} label="Loading integrations…" /></Card>
          ))}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      title="EHR Integration Hub"
      subtitle="Manage seamless bidirectional data flow between MediCOR AI and your existing Electronic Health Record systems."
    >
      {planBlock && (
        <Card className="mb-4 border-warning/40 bg-warning-bg/40 p-4">
          <div className="flex items-start gap-2.5">
            <Lock size={16} className="mt-0.5 shrink-0 text-warning" />
            <div className="text-sm">
              <p className="font-semibold text-on-surface">EHR sync isn&apos;t included in your plan</p>
              <p className="mt-0.5 text-on-surface-variant">{planBlock}</p>
              <p className="mt-1.5 text-on-surface-variant">
                Patient records, history and appointments all keep working — they stay in
                MediCOR AI rather than syncing outward.{" "}
                <Link to="/app/billing" className="font-semibold text-success hover:underline">
                  See your plan
                </Link>
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-on-surface-variant">
        <CheckCircle2 size={16} className="text-success" aria-hidden="true" />
        Native Integrations · {integrations.length} Certified Partners
      </div>

      {connectError && <InlineAlert className="mb-4">{connectError}</InlineAlert>}

      <div className="grid gap-4 sm:grid-cols-2">
        {integrations.map((e) => (
          <Card key={e.id} hoverable className="p-5">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-container">
                  <Database size={18} className="text-on-surface" />
                </div>
                <div>
                  <p className="font-display font-bold text-on-surface">{e.name}</p>
                  <Chip tone={e.connected ? "success" : "neutral"} className="mt-1">{e.status}</Chip>
                </div>
              </div>
              <Button
                variant={e.connected ? "outline" : "primary"}
                className="px-3 py-1.5 text-xs"
                onClick={() => toggleConnect(e)}
                disabled={Boolean(planBlock)}
                title={planBlock || undefined}
              >
                {e.connected ? "Manage" : "Configure"}
              </Button>
            </div>
            <p className="mt-3 text-sm leading-relaxed text-on-surface-variant">{e.detail}</p>
            {e.note && (
              <div className="mt-3 flex items-start gap-2 rounded-lg bg-surface-low p-3 text-xs text-on-surface-variant">
                <Info size={14} className="mt-0.5 shrink-0" />
                {e.note}
              </div>
            )}
            {(e.meta1_label || e.meta2_label) && (
              <div className="mt-4 flex gap-6 border-t border-outline-variant pt-3 text-xs">
                {e.meta1_label && (
                  <div>
                    <p className="text-on-surface-variant">{e.meta1_label}</p>
                    <p className="font-semibold text-on-surface">{e.meta1_value}</p>
                  </div>
                )}
                {e.meta2_label && (
                  <div>
                    <p className="text-on-surface-variant">{e.meta2_label}</p>
                    <p className="font-semibold text-on-surface">{e.meta2_value}</p>
                  </div>
                )}
              </div>
            )}
          </Card>
        ))}
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <Card className="p-6">
          <SectionHeader
            title={
              <span className="flex items-center gap-2">
                <Key size={16} className="text-accent" aria-hidden="true" />
                API credentials
              </span>
            }
            action={
            <Button
              size="sm"
              onClick={() => {
                setKeyError(null);
                setNewKeyLabel("");
                setShowNewKey(true);
              }}
            >
              <Plus size={13} aria-hidden="true" /> Generate key
            </Button>
            }
            detail="Use these credentials to authenticate automated requests to the MediCOR AI REST API."
          />

          {newKeyPlaintext && (
            <div
              className="mt-4 flex items-start justify-between gap-2 rounded-xl border border-warning/40 bg-warning-bg/50 p-3.5 text-xs"
              role="status"
            >
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 font-semibold text-on-surface">
                  <TriangleAlert size={13} className="shrink-0 text-warning" aria-hidden="true" />
                  Copy this key now — it won&apos;t be shown again.
                </p>
                <p className="mt-1 break-all font-mono text-on-surface-variant">{newKeyPlaintext}</p>
              </div>
              <div className="flex shrink-0 gap-1">
                <button
                  className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                  onClick={() => copy(newKeyPlaintext, "plaintext")}
                  aria-label="Copy API key"
                >
                  {copied === "plaintext" ? <Check size={15} className="text-success" /> : <Copy size={15} />}
                </button>
                <button
                  className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                  onClick={() => setNewKeyPlaintext(null)}
                  aria-label="Dismiss"
                >
                  <X size={15} />
                </button>
              </div>
            </div>
          )}

          {keyError && <InlineAlert className="mt-4">{keyError}</InlineAlert>}

          <div className="mt-4 space-y-3">
            {apiKeys.length === 0 ? (
              <EmptyState
                icon={Key}
                title="No API keys yet"
                detail="Generate one to let another system read and write this clinic's records."
                className="py-8"
              />
            ) : (
              apiKeys.map((k) => (
                <div key={k.id} className="flex items-center justify-between rounded-xl border border-outline-variant p-3.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-on-surface">{k.label}</p>
                      {/* This names the key's environment. It read "Active"
                          (green) vs "Test Data" (amber), implying test keys
                          were inactive and flagging the harmless one. */}
                      <Chip>{k.environment === "production" ? "Live data" : "Test data"}</Chip>
                    </div>
                    <p className="mt-1 truncate font-mono text-xs text-on-surface-variant">{k.key_prefix}</p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-error-bg hover:text-error"
                      onClick={() => setRevoking(k)}
                      aria-label={`Revoke ${k.label}`}
                      title="Revoke"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card className="p-6">
          <SectionHeader
            title={
              <span className="flex items-center gap-2">
                <Webhook size={16} className="text-accent" aria-hidden="true" />
                Webhooks
              </span>
            }
            detail="We POST a signed JSON body to your endpoint each time one of the events you select happens."
          />
          <div className="mt-4">
            <Field label="Endpoint URL" hint="We POST a JSON body here for each event you select.">
              <Input
                type="url"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
                placeholder="https://your-app.com/webhooks/medvoice"
                invalid={Boolean(webhookError)}
              />
            </Field>
          </div>
          <p className="mt-4 text-xs font-semibold text-on-surface-variant" id="webhook-events-label">
            Events to send
          </p>
          <div className="mt-2 space-y-2" role="group" aria-labelledby="webhook-events-label">
            {(webhook.available_events || []).map((ev) => (
              // The label is the touch target, not the 16px box inside
              // it: `min-h-11` gives the row a 44px hit area, which is what
              // a finger actually needs. `accent-accent` is the only styling
              // a native checkbox honours — a border colour with no width
              // utility sets nothing, which is what used to be here.
              <label
                key={ev}
                className="flex min-h-11 cursor-pointer items-center gap-2.5 rounded-lg px-1 text-sm text-on-surface hover:bg-surface-low"
              >
                <input
                  type="checkbox"
                  checked={webhook.events.includes(ev)}
                  onChange={() => toggleEvent(ev)}
                  className="focus-ring h-4 w-4 shrink-0 accent-accent"
                />
                <span className="font-mono text-xs">{ev}</span>
              </label>
            ))}
          </div>
          {/* Something true about whether this actually works. Before F14
              nothing was ever delivered, so there was nothing to show. */}
          {webhook.last_delivery_at && (
            <p className="mt-4 text-xs text-on-surface-variant">
              Last delivery:{" "}
              <span className="font-mono">{webhook.last_delivery_status}</span>
              {" · "}
              {new Date(webhook.last_delivery_at).toLocaleString()}
            </p>
          )}

          {webhook.secret && (
            <div className="mt-4">
              <label className="block text-xs font-semibold text-on-surface-variant">
                Signing secret
              </label>
              <div className="mt-1 flex items-start gap-2 rounded-lg bg-surface-low px-3 py-2">
                <p className="min-w-0 flex-1 break-all font-mono text-xs text-on-surface">
                  {webhook.secret}
                </p>
                <button
                  onClick={() => copy(webhook.secret, "secret")}
                  className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                  aria-label="Copy signing secret"
                >
                  {copied === "secret" ? <Check size={14} className="text-success" /> : <Copy size={14} />}
                </button>
              </div>
              <p className="mt-1 text-xs text-on-surface-variant">
                Every delivery carries an <span className="font-mono">X-MedVoice-Signature</span>{" "}
                header — an HMAC-SHA256 of the request body under this secret.
                Verify it before trusting a payload.
              </p>
            </div>
          )}

          {webhookError && <InlineAlert className="mt-4">{webhookError}</InlineAlert>}
          {webhookSaved && !webhookError && (
            <InlineAlert tone="success" className="mt-4">
              Webhook saved.
            </InlineAlert>
          )}

          <Button className="mt-5 w-full" onClick={saveWebhook}>Update Webhook</Button>
        </Card>
      </div>

      <Modal
        open={showNewKey}
        title="Generate an API key"
        onClose={() => setShowNewKey(false)}
        className="max-w-sm"
      >
        <form onSubmit={createKey}>
          <Field
            label="Label"
            hint="Names the key in this list — it is not part of the credential."
            required
          >
            <Input
              value={newKeyLabel}
              onChange={(e) => setNewKeyLabel(e.target.value)}
              placeholder="Production application"
              maxLength={120}
              required
            />
          </Field>
          {keyError && <InlineAlert className="mt-3">{keyError}</InlineAlert>}
          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setShowNewKey(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={creatingKey} disabled={!newKeyLabel.trim()}>
              Generate key
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={Boolean(revoking)}
        title={`Revoke "${revoking?.label ?? ""}"?`}
        detail="Anything still authenticating with this key will start failing immediately. This cannot be undone."
        confirmLabel="Revoke key"
        busy={revokeBusy}
        onConfirm={confirmRevoke}
        onCancel={() => setRevoking(null)}
      />
    </AppShell>
  );
}
