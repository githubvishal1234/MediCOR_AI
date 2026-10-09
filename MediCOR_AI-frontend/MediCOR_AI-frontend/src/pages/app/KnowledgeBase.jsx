import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search, UploadCloud, FileText, Globe, HelpCircle, Trash2,
  CheckCircle2, Loader2, Plus, ExternalLink, X,
  MessageSquare, AlertTriangle, PhoneForwarded,
} from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, Input, Textarea, Field, SkeletonList, EmptyState, ErrorState,
  InlineAlert,
} from "../../components/ui";
import * as api from "../../lib/api";

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatUpdated(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function KnowledgeBase() {
  const [query, setQuery] = useState("");
  const [docs, setDocs] = useState([]);
  const [sources, setSources] = useState([]);
  const [faqs, setFaqs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [addingSource, setAddingSource] = useState(false);
  const [showFaqForm, setShowFaqForm] = useState(false);
  const [faqDraft, setFaqDraft] = useState({ question: "", answer: "" });
  const [savingFaq, setSavingFaq] = useState(false);
  // Every write on this page had a `finally` but no `catch`, so a
  // rejected request just stopped: the spinner cleared and nothing
  // appeared. That already swallowed the upload allowlist's 415 and the
  // size cap's 413, and HV-4 adds a third — a 403 when the plan excludes
  // the knowledge base. One banner for all of them.
  const [writeError, setWriteError] = useState(null);

  // QW-6 — "Ask the AI" test console. Runs a question through the same
  // GET /knowledge-base/ask the voice agent calls mid-call, so a clinic can
  // check what a patient would be told before a patient is told it.
  const [askQuestion, setAskQuestion] = useState("");
  const [askResult, setAskResult] = useState(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState("");
  const fileInputRef = useRef(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.all([api.listKBDocuments(), api.listKBSources(), api.listFAQs()])
      .then(([docsData, sourcesData, faqsData]) => {
        if (cancelled) return;
        setDocs(docsData);
        setSources(sourcesData);
        setFaqs(faqsData);
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

  async function handleUpload(e) {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setWriteError(null);
    setUploading(true);
    try {
      for (const file of files) {
        const doc = await api.uploadKBDocument(file);
        setDocs((prev) => [doc, ...prev]);
      }
    } catch (err) {
      setWriteError(err?.message || "Couldn't upload that document.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function removeDoc(id) {
    setDocs((prev) => prev.filter((d) => d.id !== id));
    await api.deleteKBDocument(id);
  }

  async function submitSource(e) {
    e.preventDefault();
    if (!sourceUrl.trim()) return;
    setWriteError(null);
    setAddingSource(true);
    try {
      const source = await api.addKBSource(sourceUrl.trim());
      setSources((prev) => [source, ...prev]);
      setSourceUrl("");
    } catch (err) {
      setWriteError(err?.message || "Couldn't add that source.");
    } finally {
      setAddingSource(false);
    }
  }

  async function removeSource(id) {
    setSources((prev) => prev.filter((s) => s.id !== id));
    await api.deleteKBSource(id);
  }

  async function submitFaq(e) {
    e.preventDefault();
    if (!faqDraft.question.trim() || !faqDraft.answer.trim()) return;
    setWriteError(null);
    setSavingFaq(true);
    try {
      const faq = await api.addFAQ(faqDraft);
      setFaqs((prev) => [faq, ...prev]);
      setFaqDraft({ question: "", answer: "" });
      setShowFaqForm(false);
    } catch (err) {
      setWriteError(err?.message || "Couldn't save that FAQ.");
    } finally {
      setSavingFaq(false);
    }
  }

  async function removeFaq(id) {
    setFaqs((prev) => prev.filter((f) => f.id !== id));
    await api.deleteFAQ(id);
  }

  async function submitAsk(e) {
    e.preventDefault();
    const q = askQuestion.trim();
    if (!q || asking) return;
    setAsking(true);
    setAskError("");
    try {
      setAskResult(await api.askKnowledgeBase(q));
    } catch (err) {
      setAskResult(null);
      setAskError(err?.message || "Couldn't reach the knowledge base.");
    } finally {
      setAsking(false);
    }
  }

  const filteredDocs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return docs;
    return docs.filter((d) => d.name.toLowerCase().includes(q));
  }, [docs, query]);

  const filteredFaqs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return faqs;
    return faqs.filter((f) => f.question.toLowerCase().includes(q) || f.answer.toLowerCase().includes(q));
  }, [faqs, query]);

  if (error) {
    return (
      <AppShell
        title="Knowledge Base"
        subtitle="What MediCOR AI knows about your clinic — upload documents, add sources, and manage FAQs."
      >
        <ErrorState detail="We couldn't load your knowledge base." onRetry={load} className="py-24" />
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Knowledge Base"
      subtitle="What MediCOR AI knows about your clinic — upload documents, add sources, and manage FAQs."
    >
      {/* The banner that surfaces upload rejections. It is the reason
          this page has a `catch` at all: the KB upload allowlist 415 and
          the size-cap 413 were both invisible for as long as they had
          existed, because a `try/finally` cleared the spinner and showed
          nothing. Dismissable because it is not tied to one field. */}
      {writeError && (
        <InlineAlert className="mb-4 flex items-start justify-between gap-3">
          <span className="flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
            {writeError}
          </span>
          <button
            onClick={() => setWriteError(null)}
            className="focus-ring shrink-0 font-semibold hover:underline"
          >
            Dismiss
          </button>
        </InlineAlert>
      )}

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="relative max-w-md flex-1">
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant" />
          <Input
            aria-label="Search the knowledge base"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            type="search"
            placeholder="Search the knowledge base…"
            className="pl-9"
          />
        </div>
        {/* AI-3: reflects real indexing state. This used to be a
            hardcoded "AI Index Up To Date" that was true by assertion —
            documents were flipped to Indexed without ever being opened. */}
        {(() => {
          const failed = docs.filter((d) => d.status === "Failed").length;
          const indexing = docs.filter((d) => d.status === "Indexing").length;
          if (failed) {
            return (
              <Chip tone="error" className="ml-auto">
                <AlertTriangle size={12} /> {failed} document{failed > 1 ? "s" : ""} failed to index
              </Chip>
            );
          }
          // Indexing is the AI at work, so it takes the accent (`info`)
          // rather than a warning -- nothing is wrong while it runs. And an
          // empty index is not a success: it no longer gets a green tick.
          if (indexing) {
            return (
              <Chip tone="info" className="ml-auto">
                <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Indexing {indexing} document{indexing > 1 ? "s" : ""}
              </Chip>
            );
          }
          return docs.length ? (
            <Chip tone="success" className="ml-auto">
              <CheckCircle2 size={12} aria-hidden="true" /> AI index up to date
            </Chip>
          ) : (
            <Chip className="ml-auto">No documents indexed yet</Chip>
          );
        })()}
      </div>

      {/* QW-6 — Ask the AI test console. */}
      <Card className="mb-6 p-6">
        <div className="flex items-center gap-2">
          <MessageSquare size={16} className="text-accent" />
          <h2 className="font-display text-base font-bold text-on-surface">Ask the AI</h2>
        </div>
        <p className="mt-1 text-sm text-on-surface-variant">
          Ask a question the way a patient would. This runs through the same knowledge
          base the AI receptionist uses on a live call, so you can check an answer
          before a patient hears it.
        </p>

        <form onSubmit={submitAsk} className="mt-4 flex flex-wrap gap-2">
          <div className="min-w-0 flex-1">
            <Input
              value={askQuestion}
              onChange={(e) => setAskQuestion(e.target.value)}
              placeholder="e.g. What time do you open on Saturdays?"
              aria-label="Question to ask the AI"
            />
          </div>
          <Button type="submit" disabled={!askQuestion.trim() || asking}>
            {asking ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            {asking ? "Asking..." : "Ask"}
          </Button>
        </form>

        {askError && (
          <p className="mt-3 text-sm text-error" role="alert">{askError}</p>
        )}

        {askResult && !askError && (
          <div className="mt-4 rounded-xl border border-outline-variant bg-surface-low p-4">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-on-surface">The AI would say</span>
              {askResult.source === "document" ? (
                <Chip tone="success"><CheckCircle2 size={12} /> From a document</Chip>
              ) : askResult.source === "faq" ? (
                <Chip tone="success"><CheckCircle2 size={12} /> Matched an FAQ</Chip>
              ) : (
                <Chip tone="warning"><PhoneForwarded size={12} /> No match — offers a transfer</Chip>
              )}
              {askResult.confidence > 0 && (
                <Chip tone="neutral">{Math.round(askResult.confidence * 100)}% match</Chip>
              )}
            </div>

            <p className="whitespace-pre-line text-sm text-on-surface">{askResult.answer}</p>

            {askResult.matched_question && (
              <p className="mt-3 border-t border-outline-variant pt-3 text-xs text-on-surface-variant">
                Matched FAQ: <span className="font-medium text-on-surface">{askResult.matched_question}</span>
              </p>
            )}

            {askResult.source !== "none" && (
              <p className="mt-3 flex items-start gap-1.5 text-xs text-warning">
                <AlertTriangle size={13} className="mt-px shrink-0" />
                <span>
                  This is what a caller would hear, generated from your
                  {askResult.source === "document" ? " uploaded documents" : " FAQs"}.
                  If it&apos;s wrong or incomplete, fix the source — the AI answers only
                  from what you&apos;ve given it, and says it doesn&apos;t know otherwise.
                </span>
              </p>
            )}

            {askResult.source !== "faq" && (
              <p className="mt-3 text-xs text-on-surface-variant">
                Nothing in your knowledge base answered this, so the AI would offer to
                transfer the caller rather than guess. Upload a document or add an FAQ below, or fill in your{" "}
                <a href="/app/clinic" className="font-medium text-accent underline">clinic profile</a>{" "}
                if this is about timings or contact details.
              </p>
            )}
          </div>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-6">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-accent" />
            <h2 className="font-display text-base font-bold text-on-surface">Documents</h2>
          </div>
          <label className="mt-4 flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-outline-variant bg-surface-low px-6 py-8 text-center transition-colors hover:border-accent/50 hover:bg-accent/5">
            {uploading ? (
              <Loader2 size={22} className="animate-spin text-on-surface-variant" />
            ) : (
              <UploadCloud size={22} className="text-on-surface-variant" />
            )}
            <p className="text-sm font-semibold text-on-surface">{uploading ? "Uploading..." : "Upload Clinic Documents"}</p>
            <p className="text-xs text-on-surface-variant">Drag and drop PDFs, or click to browse</p>
            <input
              aria-label="Choose clinic documents to upload"
              ref={fileInputRef}
              type="file"
              className="hidden"
              multiple
              accept=".pdf,.doc,.docx"
              disabled={uploading}
              onChange={handleUpload}
            />
          </label>

          <div className="mt-4 space-y-2">
            {loading ? (
              <SkeletonList rows={3} avatar={false} label="Loading documents…" />
            ) : filteredDocs.length === 0 ? (
              <EmptyState
                icon={FileText}
                title={query ? "No matching documents" : "No documents uploaded yet"}
                detail={
                  query
                    ? "Nothing here matches that search."
                    : "Upload your handbook, policies or price list and the AI will answer from them."
                }
                className="py-8"
              />
            ) : (
              filteredDocs.map((d) => (
                <div key={d.id} className="flex items-center justify-between rounded-lg border border-outline-variant px-3.5 py-2.5">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <FileText size={15} className="shrink-0 text-on-surface-variant" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-on-surface">{d.name}</p>
                      <p className="text-xs text-on-surface-variant">{formatBytes(d.size_bytes)} · {formatUpdated(d.updated_at)}</p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {/* Indexed is the normal state, so plain text; only
                        work in progress and failure are marked. This used
                        to print a green "Indexed" for anything not
                        indexing -- including a document that FAILED to
                        index (rag_service.index_document sets "Failed"),
                        which the AI therefore cannot answer from. */}
                    {d.status === "Indexing" ? (
                      <Chip tone="info"><Loader2 size={12} className="animate-spin" aria-hidden="true" /> Indexing</Chip>
                    ) : d.status === "Failed" ? (
                      <Chip tone="error">Failed to index</Chip>
                    ) : (
                      <span className="text-xs text-on-surface-variant">{d.status}</span>
                    )}
                    <button
                      onClick={() => removeDoc(d.id)}
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                      aria-label={`Remove ${d.name}`}
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
          <div className="flex items-center gap-2">
            <Globe size={16} className="text-accent" />
            <h2 className="font-display text-base font-bold text-on-surface">Website sources</h2>
          </div>
          <p className="mt-1 text-sm text-on-surface-variant">
            MediCOR AI periodically re-crawls these pages to keep answers current.
          </p>
          <form onSubmit={submitSource} className="mt-4 flex gap-2">
            <Input
              type="url"
              inputMode="url"
              aria-label="Web page URL to import"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://your-clinic.com/page"
            />
            {/* Icon-only, so it needs a name: a screen reader announced
                this as just "button". */}
            <Button type="submit" size="icon" className="shrink-0" loading={addingSource} aria-label="Add website source">
              {!addingSource && <Plus size={16} aria-hidden="true" />}
            </Button>
          </form>
          <div className="mt-4 space-y-2">
            {loading ? (
              <SkeletonList rows={2} avatar={false} label="Loading sources…" />
            ) : sources.length === 0 ? (
              <EmptyState
                icon={Globe}
                title="No website sources yet"
                detail="Point the AI at a page on your own site and it will read from it."
                className="py-8"
              />
            ) : (
              sources.map((s) => (
                <div key={s.id} className="flex items-center justify-between rounded-lg border border-outline-variant px-3.5 py-2.5">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <ExternalLink size={14} className="shrink-0 text-on-surface-variant" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-on-surface">{s.url}</p>
                      <p className="text-xs text-on-surface-variant">Updated {formatUpdated(s.updated_at)}</p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Chip tone={s.status === "Indexing" ? "warning" : "success"}>{s.status}</Chip>
                    <button
                      onClick={() => removeSource(s.id)}
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                      aria-label={`Remove ${s.url}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="mt-7 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <HelpCircle size={16} className="text-accent" />
              <h2 className="font-display text-base font-bold text-on-surface">FAQs</h2>
            </div>
            <button
              onClick={() => setShowFaqForm((v) => !v)}
              className="focus-ring flex items-center gap-1.5 text-sm font-semibold text-accent"
            >
              {showFaqForm ? <X size={14} /> : <Plus size={14} />} {showFaqForm ? "Cancel" : "Add FAQ"}
            </button>
          </div>

          {showFaqForm && (
            <form onSubmit={submitFaq} className="mt-3 space-y-2 rounded-lg border border-outline-variant p-3.5">
              <Field label="Question" required>
                <Input
                  required
                  value={faqDraft.question}
                  onChange={(e) => setFaqDraft((d) => ({ ...d, question: e.target.value }))}
                  placeholder="What are your opening hours?"
                />
              </Field>
              <Field label="Answer" required hint="This is read aloud to callers, so write it the way you would say it.">
                <Textarea
                  required
                  rows={3}
                  value={faqDraft.answer}
                  onChange={(e) => setFaqDraft((d) => ({ ...d, answer: e.target.value }))}
                  placeholder="We open at 9am on Saturdays and close at 1pm."
                />
              </Field>
              <Button type="submit" size="sm" loading={savingFaq}>
                {savingFaq ? "Saving…" : "Save FAQ"}
              </Button>
            </form>
          )}

          <div className="mt-3 space-y-2">
            {loading ? (
              <SkeletonList rows={3} avatar={false} label="Loading FAQs…" />
            ) : filteredFaqs.length === 0 ? (
              <EmptyState
                icon={HelpCircle}
                title="No FAQs yet"
                detail="Add the questions callers ask most, with the answer in your own words."
                className="py-8"
              />
            ) : (
              filteredFaqs.map((f) => (
                <details key={f.id} className="group rounded-lg border border-outline-variant px-3.5 py-2.5">
                  <summary className="focus-ring flex cursor-pointer list-none items-center justify-between text-sm font-medium text-on-surface marker:content-none">
                    {f.question}
                    <button
                      onClick={(e) => {
                        e.preventDefault();
                        removeFaq(f.id);
                      }}
                      className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                      aria-label={`Remove ${f.question}`}
                    >
                      <Trash2 size={14} />
                    </button>
                  </summary>
                  <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">{f.answer}</p>
                </details>
              ))
            )}
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
