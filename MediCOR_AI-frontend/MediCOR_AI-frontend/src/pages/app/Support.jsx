import { useCallback, useEffect, useRef, useState } from "react";
import {
  MessageCircle, BookOpen, Mail, Send, Inbox, Phone, Clock,
} from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, Skeleton, Field, Select, Textarea, InlineAlert,
} from "../../components/ui";
import * as api from "../../lib/api";

export default function Support() {
  const [subject, setSubject] = useState("Technical Issue");
  const [priority, setPriority] = useState("Normal");
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [sent, setSent] = useState(false);
  const [docsLinks, setDocsLinks] = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [docsError, setDocsError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSupportDocs()
      .then((data) => {
        if (cancelled) return;
        setDocsLinks(data);
        setDocsLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setDocsError(true);
        setDocsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // QW-8 — until now `GET /support/tickets` had no client wrapper, so a
  // submitted ticket was unviewable by anyone: staff filed into a black
  // hole. The endpoint is admin-only, so a 403 here is an expected outcome
  // for medical_staff, not an error worth surfacing.
  const [tickets, setTickets] = useState([]);
  const [ticketsLoading, setTicketsLoading] = useState(true);
  const [ticketsForbidden, setTicketsForbidden] = useState(false);

  const loadTickets = useCallback(() => {
    setTicketsLoading(true);
    api
      .listSupportTickets()
      .then((data) => {
        setTickets(data);
        setTicketsForbidden(false);
      })
      .catch((err) => {
        if (err?.status === 403) setTicketsForbidden(true);
        setTickets([]);
      })
      .finally(() => setTicketsLoading(false));
  }, []);

  useEffect(() => { loadTickets(); }, [loadTickets]);

  async function submit(e) {
    e.preventDefault();
    setSending(true);
    setSendError(null);
    try {
      await api.createSupportTicket({ subject, priority, message });
      setSent(true);
      // Show the ticket that was just filed, rather than leaving the
      // list stale until the next page load.
      loadTickets();
    } catch (err) {
      // Without a `catch` this was `try/finally`, so a rejected POST
      // just cleared the spinner: a ticket that never filed looked
      // exactly like one that did. Somebody reporting a problem with
      // the product would then be waiting on a reply to a message
      // nobody ever received.
      setSendError(err?.message || "Couldn't send that message. Please try again.");
    } finally {
      setSending(false);
    }
  }

  // The three cards at the top of this page used to carry buttons with
  // no handler -- "Start Chat", "Browse Docs" and "Call Us" -- beside
  // statistics that were invented: "typical response under 3 minutes",
  // "Avg. response: 12 min", and a green dot reading "Online now" that
  // nothing checked. There is no chat, no published documentation and no
  // support telephone number anywhere in this application, so all three
  // were controls over capabilities that do not exist -- the same defect
  // as call_logs.recording_url.
  //
  // What this page really offers is a ticket, a list of the tickets you
  // have filed, and an honest note that the guides are still being
  // written. The cards now point at those three things, and each button
  // does what it says.
  // Whether this deployment has a support line at all. Blank is the
  // normal state, not an error, so a failed fetch is silent and the card
  // falls back to the message form.
  const [contact, setContact] = useState(null);
  useEffect(() => {
    let cancelled = false;
    api
      .getContactInfo()
      .then((data) => {
        if (!cancelled) setContact(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const formRef = useRef(null);
  const messageRef = useRef(null);
  const docsRef = useRef(null);
  const ticketsRef = useRef(null);

  function jumpTo(ref, focus) {
    const el = ref.current;
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    // Scrolling alone leaves a keyboard user where they were, so move
    // focus too when there is somewhere sensible to put it.
    if (focus && focus.current) focus.current.focus({ preventScroll: true });
  }

  return (
    <AppShell
      title="Support"
      subtitle="Get help from our team, or find answers in the documentation."
    >
      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="flex flex-col p-6">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-container">
            <MessageCircle size={20} className="text-on-surface" aria-hidden="true" />
          </div>
          <h2 className="mt-4 font-display text-base font-bold text-on-surface">Message us</h2>
          <p className="mt-1 flex-1 text-sm text-on-surface-variant">
            Describe what you need and we will reply by email. Urgent, patient-facing
            problems can be marked as such.
          </p>
          <Button className="mt-4" onClick={() => jumpTo(formRef, messageRef)}>
            Write a message
          </Button>
        </Card>

        <Card className="flex flex-col p-6">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-container">
            <Inbox size={20} className="text-on-surface" />
          </div>
          <h2 className="mt-4 font-display text-base font-bold text-on-surface">Your tickets</h2>
          <p className="mt-1 flex-1 text-sm text-on-surface-variant">
            Everything this clinic has sent us, with its current status.
          </p>
          {/* The count is the real one, and the card says nothing when
              the list has not loaded or is not visible to this role. */}
          {!ticketsLoading && !ticketsForbidden && (
            <p className="mt-4 text-xs font-medium text-on-surface-variant">
              {tickets.length === 0
                ? "Nothing filed yet"
                : `${tickets.length} ticket${tickets.length === 1 ? "" : "s"} filed`}
            </p>
          )}
          <Button
            variant="outline"
            className="mt-4"
            disabled={ticketsForbidden}
            title={ticketsForbidden ? "Only administrators can view filed tickets." : undefined}
            onClick={() => jumpTo(ticketsRef)}
          >
            View tickets
          </Button>
        </Card>

        {/* "Call us" restored, and now a control that works.
            It was a <Button> with no handler beside an invented
            "Avg. response: 12 min". The number is configuration
            (SUPPORT_PHONE / SUPPORT_EMAIL / SUPPORT_HOURS), blank by
            default, so this renders one of three real things and never a
            placeholder: a tel: link, a mailto: link, or -- when this
            deployment has been given neither -- a button to the message
            form, saying plainly that no line is set up. */}
        <Card className="flex flex-col p-6">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-container">
            <Phone size={20} className="text-on-surface" />
          </div>
          <h2 className="mt-4 font-display text-base font-bold text-on-surface">Call us</h2>
          <p className="mt-1 flex-1 text-sm text-on-surface-variant">
            {contact?.phone
              ? "Speak to someone about an urgent, patient-facing problem."
              : contact?.email
                ? "No support line is set up yet. Email reaches us directly."
                : "No support line is set up for this deployment yet — send a message and we will reply by email."}
          </p>
          {contact?.phone && contact?.hours && (
            <p className="mt-4 flex items-center gap-1.5 text-xs font-medium text-on-surface-variant">
              <Clock size={13} /> {contact.hours}
            </p>
          )}
          {contact?.phone ? (
            <a
              href={"tel:" + contact.phone.replace(/[^\d+]/g, "")}
              className="focus-ring mt-4 inline-flex items-center justify-center gap-2 rounded-lg border border-outline-variant px-4 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container"
            >
              <Phone size={14} /> {contact.phone}
            </a>
          ) : contact?.email ? (
            <a
              href={"mailto:" + contact.email}
              className="focus-ring mt-4 inline-flex items-center justify-center gap-2 rounded-lg border border-outline-variant px-4 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container"
            >
              <Mail size={14} /> {contact.email}
            </a>
          ) : (
            <Button variant="outline" className="mt-4" onClick={() => jumpTo(formRef, messageRef)}>
              Send a message instead
            </Button>
          )}
        </Card>

        <Card className="flex flex-col p-6">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-container">
            <BookOpen size={20} className="text-on-surface" />
          </div>
          <h2 className="mt-4 font-display text-base font-bold text-on-surface">Documentation</h2>
          <p className="mt-1 flex-1 text-sm text-on-surface-variant">
            Guides for configuring your AI receptionist. These are still being written —
            ask us for anything you need in the meantime.
          </p>
          <Button variant="outline" className="mt-4" onClick={() => jumpTo(docsRef)}>
            See what is coming
          </Button>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        {/* `ref` reaches the underlying <div> because Card spreads its
            remaining props onto it, and React 19 passes ref to a function
            component as an ordinary prop. */}
        <Card ref={formRef} className="p-6 lg:col-span-2">
          <div className="flex items-center gap-2">
            <Mail size={16} className="text-accent" />
            <h2 className="font-display text-base font-bold text-on-surface">Contact us</h2>
          </div>
          <p className="mt-1 text-sm text-on-surface-variant">
            Send a message and we'll get back to you by email.
          </p>

          {sent ? (
            <div className="mt-5 flex items-center gap-3 rounded-xl bg-success-bg p-4">
              <Send size={16} className="shrink-0 text-success" />
              <p className="text-sm font-medium text-success">
                Message sent — our team will reply within one business day.
              </p>
            </div>
          ) : (
            <form className="mt-5 space-y-4" onSubmit={submit}>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Subject">
                  <Select value={subject} onChange={(e) => setSubject(e.target.value)}>
                    <option>Technical Issue</option>
                    <option>Billing Question</option>
                    <option>EHR Integration</option>
                    <option>Feature Request</option>
                  </Select>
                </Field>
                <Field label="Priority">
                  <Select value={priority} onChange={(e) => setPriority(e.target.value)}>
                    <option>Normal</option>
                    <option>Urgent — Patient Impact</option>
                    <option>Low</option>
                  </Select>
                </Field>
              </div>
              <Field label="Message" required>
                {(props) => (
                  <Textarea
                    {...props}
                    ref={messageRef}
                    required
                    rows={4}
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    placeholder="Describe the issue or question…"
                  />
                )}
              </Field>
              {sendError && <InlineAlert>{sendError}</InlineAlert>}
              <Button type="submit" loading={sending}>
                {!sending && <Send size={14} aria-hidden="true" />}
                {sending ? "Sending…" : "Send message"}
              </Button>
            </form>
          )}
        </Card>

        {/* QW-8 — `/support/docs` returns titles and descriptions with no
            URLs, because the guides are not published anywhere yet. These
            rendered as <button> elements with a chevron and a hover
            state, so they read as links and did nothing when clicked.
            Rows now describe what is coming without pretending to be
            navigable; add hrefs here the day the docs exist. */}
        <Card ref={docsRef} className="p-6">
          <h2 className="font-display text-base font-bold text-on-surface">Documentation</h2>
          <p className="mt-1 text-xs text-on-surface-variant">
            These guides are being written. Ask us for anything you need in the meantime.
          </p>
          <div className="mt-4 space-y-1">
            {docsLoading ? (
              [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-12 w-full rounded-lg" />)
            ) : docsError ? (
              <p className="py-4 text-center text-sm text-on-surface-variant">Couldn't load documentation links.</p>
            ) : (
              docsLinks.map((d) => (
                <div key={d.title} className="flex items-center gap-3 rounded-lg px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-on-surface">{d.title}</p>
                    <p className="truncate text-xs text-on-surface-variant">{d.detail}</p>
                  </div>
                  <Chip tone="neutral">Soon</Chip>
                </div>
              ))
            )}
          </div>
        </Card>

        {/* QW-8 — the tickets this clinic has filed. */}
        {!ticketsForbidden && (
          <Card ref={ticketsRef} className="p-6 lg:col-span-3">
            <div className="flex items-center gap-2">
              <Inbox size={16} className="text-accent" />
              <h2 className="font-display text-base font-bold text-on-surface">Your tickets</h2>
              {!ticketsLoading && tickets.length > 0 && (
                <Chip tone="neutral" className="ml-auto">{tickets.length}</Chip>
              )}
            </div>

            <div className="mt-4 space-y-2">
              {ticketsLoading ? (
                [1, 2].map((i) => <Skeleton key={i} className="h-16 w-full rounded-lg" />)
              ) : tickets.length === 0 ? (
                <p className="py-6 text-center text-sm text-on-surface-variant">
                  No tickets yet. Anything you send above will appear here.
                </p>
              ) : (
                tickets.map((t) => (
                  <div
                    key={t.id}
                    className="rounded-lg border border-outline-variant bg-surface-lowest p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-on-surface">{t.subject}</p>
                      <Chip tone={t.status === "Open" ? "neutral" : "success"}>{t.status}</Chip>
                      {/* The form's urgent option is "Urgent — Patient Impact",
                          so match on the prefix rather than the whole string. */}
                      {t.priority && t.priority !== "Normal" && (
                        <Chip tone={t.priority.startsWith("Urgent") ? "error" : "warning"}>
                          {t.priority}
                        </Chip>
                      )}
                      <span className="ml-auto text-xs text-on-surface-variant">
                        {t.created_at ? new Date(t.created_at).toLocaleString() : ""}
                      </span>
                    </div>
                    <p className="mt-2 whitespace-pre-line text-sm text-on-surface-variant">
                      {t.message}
                    </p>
                  </div>
                ))
              )}
            </div>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
