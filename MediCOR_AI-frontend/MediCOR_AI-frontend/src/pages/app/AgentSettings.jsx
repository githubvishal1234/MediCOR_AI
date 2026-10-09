import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Mic2, GitBranch, BookOpen, RefreshCw, Plus, Trash2, Info, ArrowRight } from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Button, Chip, Skeleton, ErrorState, Field, Input, Select, Textarea, InlineAlert,
} from "../../components/ui";
// `useToast` is a hook, so it comes from its own module rather than being
// re-exported through ui.jsx — that file stays components-only so it keeps
// React Fast Refresh. See the note on the re-export block in ui.jsx.
import { useToast } from "../../components/toast";
import VoiceFlow from "../../components/VoiceFlow";
import * as api from "../../lib/api";

const VOICES = [
  { name: "Dr. Sarah (Calm, Professional)", note: "Recommended for general reception" },
  { name: "Mark (Clear, Authoritative)", note: "Recommended for billing inquiries" },
];

/**
 * How each condition reads on screen.
 *
 * Keyed by the vocabulary the server sends, never used to *decide* which
 * options exist -- that list is fetched. A condition this build has no
 * label for still renders, as its raw name, which is the right failure:
 * a newer backend offering a condition an older screen has not been
 * taught about should be selectable, not invisible.
 */
const CONDITION_LABELS = {
  model: "AI judgement",
  keyword: "Caller says a keyword",
  unknown_caller: "Caller is not a patient",
  outside_hours: "Outside working hours",
};

/**
 * What each one actually does -- written to be honest about the
 * trade-off rather than to sell the feature.
 */
const CONDITION_HELP = {
  model:
    "The AI reads this rule and decides. It understands paraphrases and other languages, but two similar calls can be judged differently.",
  keyword:
    "Escalates the moment a caller says one of your terms. Exact and repeatable, but it only catches the words you list — add them in every language your patients use.",
  unknown_caller:
    "Escalates when the caller's number does not match any patient on file.",
  outside_hours:
    "Escalates when no doctor is scheduled to be working. Needs at least one doctor with working hours set.",
};


export default function AgentSettings() {
  const navigate = useNavigate();
  const toast = useToast();
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState({ voice_profile: "", greeting_script: "" });
  const [rules, setRules] = useState([]);
  const [docCount, setDocCount] = useState(0);
  const [ehrIntegration, setEhrIntegration] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [ruleDraft, setRuleDraft] = useState({ title: "", detail: "", condition_type: "model", keywords: "", destination: "" });
  const [savingRule, setSavingRule] = useState(false);
  // The vocabulary comes from the server. Hardcoding it here is how the
  // webhook settings screen ended up offering three events that nothing
  // in the backend ever emitted (F14).
  const [conditions, setConditions] = useState(["model"]);
  // A write handler needs a catch, not just a finally: without one the
  // 422 this endpoint returns for a keyword rule with no keywords clears
  // the spinner and shows nothing at all.
  const [ruleError, setRuleError] = useState("");

  const loadAll = useCallback(() => {
    setLoading(true);
    setError(false);
    return Promise.all([
      api.getAgentSettings(),
      api.listRoutingRules(),
      api.listKBDocuments(),
      api.listEHRIntegrations(),
      // Never fatal: an older backend without this route must still let
      // the page load and rules be edited as prose.
      api.listRoutingRuleConditions().catch(() => ["model"]),
    ])
      .then(([settingsData, rulesData, docsData, ehrData, conditionData]) => {
        setConditions(conditionData?.length ? conditionData : ["model"]);
        setSettings(settingsData);
        setDraft({ voice_profile: settingsData.voice_profile, greeting_script: settingsData.greeting_script });
        setRules(rulesData);
        setDocCount(docsData.length);
        setEhrIntegration(ehrData[0] || null);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadAll().then(() => {
      if (cancelled) return;
    });
    return () => {
      cancelled = true;
    };
  }, [loadAll]);

  function discard() {
    if (settings) setDraft({ voice_profile: settings.voice_profile, greeting_script: settings.greeting_script });
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await api.updateAgentSettings(draft);
      setSettings(updated);
      setDraft({ voice_profile: updated.voice_profile, greeting_script: updated.greeting_script });
      toast.success("Greeting saved", {
        description: "Your AI receptionist will use it on the next call.",
      });
    } catch (err) {
      // This `catch` is the whole point. Without one, a rejected save
      // cleared the spinner and showed nothing at all — so a failed
      // write was indistinguishable from a successful one, and the
      // operator walked away believing the greeting had changed. This
      // is the string the agent reads to every caller, so "looks like
      // it saved" is the worst possible failure mode here.
      setSaveError(err?.message || "Couldn't save those settings. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleRule(rule) {
    setRules((prev) => prev.map((r) => (r.id === rule.id ? { ...r, enabled: !r.enabled } : r)));
    await api.updateRoutingRule(rule.id, { enabled: !rule.enabled });
  }

  async function removeRule(id) {
    setRules((prev) => prev.filter((r) => r.id !== id));
    await api.deleteRoutingRule(id);
  }

  async function submitRule(e) {
    e.preventDefault();
    if (!ruleDraft.title.trim()) return;
    setSavingRule(true);
    setRuleError("");
    try {
      // Only send what this condition actually uses, so a clinic that
      // typed keywords and then switched to a prose rule does not store
      // a list nothing reads.
      const payload = {
        title: ruleDraft.title,
        detail: ruleDraft.detail,
        condition_type: ruleDraft.condition_type,
        keywords: ruleDraft.condition_type === "keyword" ? ruleDraft.keywords : null,
        destination: ruleDraft.destination?.trim() || null,
      };
      const rule = await api.createRoutingRule(payload);
      setRules((prev) => [...prev, rule]);
      setRuleDraft({ title: "", detail: "", condition_type: "model", keywords: "", destination: "" });
      setShowRuleForm(false);
    } catch (err) {
      setRuleError(err?.message || "Could not add the rule.");
    } finally {
      setSavingRule(false);
    }
  }

  const dirty =
    settings && (draft.voice_profile !== settings.voice_profile || draft.greeting_script !== settings.greeting_script);

  if (error) {
    return (
      <AppShell title="AI Agent Settings" subtitle="Configure MediCOR AI behaviors, knowledge, and integrations.">
        <ErrorState detail="We couldn't load your agent settings." onRetry={loadAll} className="py-24" />
      </AppShell>
    );
  }

  return (
    <AppShell
      title="AI Receptionist"
      subtitle="How your AI answers the phone, and when it hands a caller to a person."
      actions={
        <>
          <Button variant="outline" onClick={discard} disabled={!dirty || saving}>
            Discard
          </Button>
          <Button onClick={save} disabled={!dirty} loading={saving}>
            {saving ? "Saving…" : "Save changes"}
          </Button>
        </>
      }
    >
      {/* Read-only. Explains the product to whoever has been asked to
          configure it, before the controls that configure it. */}
      <VoiceFlow />

      <div className="space-y-6">
        <Card className="p-6">
          <div className="flex items-center gap-2">
            <Mic2 size={16} className="text-accent" aria-hidden="true" />
            <h2 className="font-display text-base font-bold text-on-surface">Greeting &amp; voice</h2>
          </div>

          {loading ? (
            <div className="mt-4 space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : (
            <>
              <p className="mt-4 text-xs font-semibold text-on-surface-variant">Voice profile</p>
              {/* A single choice, so each card says whether it is the one
                  chosen: the selection used to be conveyed by the border
                  colour alone. */}
              <div className="mt-2 grid gap-3 sm:grid-cols-2" role="group" aria-label="Voice profile">
                {VOICES.map((v) => (
                  <button
                    key={v.name}
                    type="button"
                    aria-pressed={draft.voice_profile === v.name}
                    onClick={() => setDraft((d) => ({ ...d, voice_profile: v.name }))}
                    className={`focus-ring rounded-xl border p-4 text-left transition-colors ${draft.voice_profile === v.name ? "border-accent bg-accent-subtle" : "border-outline-variant hover:bg-surface-low"}`}
                  >
                    <p className="text-sm font-semibold text-on-surface">{v.name}</p>
                    <p className="mt-0.5 text-xs text-on-surface-variant">{v.note}</p>
                  </button>
                ))}
              </div>
              {/* True of the backend today: only the managed (Deepgram agent)
                  engine maps voice_profile to a voice; the default pipeline
                  engine speaks with a fixed Sarvam speaker
                  (SARVAM_TTS_SPEAKER). No vendor named on screen. Remove
                  this line if the pipeline starts honouring the choice. */}
              <p className="mt-2 text-xs text-on-surface-variant">
                This choice applies only when your deployment uses the managed voice engine. The
                standard engine always speaks in one Indian-English voice.
              </p>

              <Field
                label="Opening greeting"
                className="mt-5"
                hint="The first thing every caller hears. Say the clinic name."
              >
                <Textarea
                  rows={3}
                  value={draft.greeting_script}
                  onChange={(e) => setDraft((d) => ({ ...d, greeting_script: e.target.value }))}
                />
              </Field>
              {saveError && <InlineAlert className="mt-3">{saveError}</InlineAlert>}
            </>
          )}
        </Card>

        <Card className="p-6">
          <div className="flex items-center gap-2">
            <GitBranch size={16} className="text-accent" aria-hidden="true" />
            <h2 className="font-display text-base font-bold text-on-surface">Routing rules</h2>
          </div>
          <p className="mt-1 text-sm text-on-surface-variant">Define when MediCOR AI should escalate calls to human staff.</p>
          <div className="mt-4 space-y-3">
            {loading ? (
              [1, 2].map((i) => <Skeleton key={i} className="h-14 w-full rounded-xl" />)
            ) : rules.length === 0 ? (
              <p className="text-sm text-on-surface-variant">No routing rules configured yet.</p>
            ) : (
              rules.map((r) => (
                <div key={r.id} className="flex items-center justify-between rounded-xl border border-outline-variant p-3.5">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-on-surface">{r.title}</p>
                      {/* The shared Chip instead of a hand-built pill, and
                          the colours swapped to match their meaning: teal is
                          the AI at work, so it marks "AI judgement"; the
                          exact server-side rules are plain. It was the
                          other way round. */}
                      <Chip
                        tone={(r.condition_type || "model") === "model" ? "accent" : "neutral"}
                        title={CONDITION_HELP[r.condition_type || "model"]}
                      >
                        {CONDITION_LABELS[r.condition_type || "model"] || r.condition_type}
                      </Chip>
                    </div>
                    {r.detail && <p className="text-xs text-on-surface-variant">{r.detail}</p>}
                    {(r.condition_type || "model") === "keyword" && r.keywords && (
                      <p className="text-xs text-on-surface-variant">Keywords: {r.keywords}</p>
                    )}
                    {r.destination && (
                      <p className="text-xs text-on-surface-variant">Transfers to {r.destination}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    {/* Was a bare 16px checkbox with no accessible name:
                        a screen reader announced "checkbox" with no idea
                        which rule it switched off, and it was well under
                        the 44px touch minimum. The label supplies both. */}
                    <label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-1 text-xs font-medium text-on-surface-variant hover:bg-surface-low">
                      <input
                        type="checkbox"
                        checked={r.enabled}
                        onChange={() => toggleRule(r)}
                        className="focus-ring h-4 w-4 shrink-0 accent-accent"
                      />
                      {r.enabled ? "On" : "Off"}
                      <span className="sr-only">{` — ${r.title}`}</span>
                    </label>
                    <button
                      onClick={() => removeRule(r.id)}
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                      aria-label={`Remove ${r.title}`}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>

          {showRuleForm ? (
            <form onSubmit={submitRule} className="mt-3 space-y-2 rounded-xl border border-outline-variant p-3.5">
              <Field label="Rule title" required>
                <Input
                  required
                  value={ruleDraft.title}
                  onChange={(e) => setRuleDraft((d) => ({ ...d, title: e.target.value }))}
                  placeholder="e.g. Medical emergencies"
                />
              </Field>
              <Field label="Detail">
                <Input
                  value={ruleDraft.detail}
                  onChange={(e) => setRuleDraft((d) => ({ ...d, detail: e.target.value }))}
                  placeholder="e.g. transfer anyone describing severe pain"
                />
              </Field>
              {/* The condition vocabulary is fetched, never hardcoded —
                  hardcoding it is how the webhook screen came to offer
                  three events the backend never emitted. */}
              <Field
                label="When should this fire?"
                hint={CONDITION_HELP[ruleDraft.condition_type] || undefined}
              >
                <Select
                  value={ruleDraft.condition_type}
                  onChange={(e) => setRuleDraft((d) => ({ ...d, condition_type: e.target.value }))}
                >
                  {conditions.map((c) => (
                    <option key={c} value={c}>
                      {CONDITION_LABELS[c] || c}
                    </option>
                  ))}
                </Select>
              </Field>
              {ruleDraft.condition_type === "keyword" && (
                <Field
                  label="Keywords"
                  required
                  hint="Only the words you list are matched — add them in every language your patients use."
                >
                  <Input
                    required
                    value={ruleDraft.keywords}
                    onChange={(e) => setRuleDraft((d) => ({ ...d, keywords: e.target.value }))}
                    placeholder="chest pain, bleeding, सीने में दर्द"
                  />
                </Field>
              )}
              <Field label="Transfer to" hint="Optional — defaults to the main number.">
                <Input
                  value={ruleDraft.destination}
                  onChange={(e) => setRuleDraft((d) => ({ ...d, destination: e.target.value }))}
                  inputMode="tel"
                />
              </Field>
              {ruleError && <InlineAlert>{ruleError}</InlineAlert>}
              <div className="flex gap-2">
                <Button type="submit" size="sm" loading={savingRule}>
                  {savingRule ? "Adding…" : "Add rule"}
                </Button>
                <button
                  type="button"
                  onClick={() => setShowRuleForm(false)}
                  className="focus-ring rounded-lg border border-outline-variant px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container"
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <button
              onClick={() => setShowRuleForm(true)}
              className="text-accent focus-ring mt-3 flex items-center gap-2 text-sm font-semibold"
            >
              <Plus size={15} /> Add Custom Rule
            </button>
          )}
        </Card>

        <Card className="p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen size={16} className="text-accent" aria-hidden="true" />
              <h2 className="font-display text-base font-bold text-on-surface">Knowledge Base</h2>
            </div>
            <button
              onClick={() => navigate("/app/knowledge-base")}
              className="text-accent focus-ring flex items-center gap-1 text-sm font-semibold"
            >
              Manage <ArrowRight size={14} />
            </button>
          </div>
          {loading ? (
            <Skeleton className="mt-4 h-10 w-48" />
          ) : (
            <p className="mt-3 text-sm text-on-surface-variant">
              {docCount} document{docCount === 1 ? "" : "s"} indexed. Upload files, add website sources, and manage FAQs from the Knowledge Base page.
            </p>
          )}
        </Card>

        <Card className="p-6">
          <div className="flex items-center gap-2">
            <RefreshCw size={16} className="text-accent" aria-hidden="true" />
            <h2 className="font-display text-base font-bold text-on-surface">EHR Integration</h2>
          </div>
          {loading ? (
            <Skeleton className="mt-4 h-16 w-full rounded-xl" />
          ) : ehrIntegration ? (
            <div className="mt-4 flex items-center justify-between rounded-xl border border-outline-variant p-3.5">
              <div>
                <p className="text-sm font-semibold text-on-surface">{ehrIntegration.name}</p>
                <Chip tone={ehrIntegration.connected ? "success" : "neutral"} className="mt-1">{ehrIntegration.status}</Chip>
              </div>
              <Button variant="outline" className="px-3 py-1.5 text-xs" onClick={() => navigate("/app/ehr")}>
                Manage Integration
              </Button>
            </div>
          ) : (
            <p className="mt-4 text-sm text-on-surface-variant">No EHR system connected yet.</p>
          )}
          <div className="mt-3 flex items-start gap-2 text-xs text-on-surface-variant">
            <Info size={14} className="mt-0.5 shrink-0" />
            Changes to EHR integration settings may require up to 5 minutes to propagate across active MediCOR AI instances.
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
