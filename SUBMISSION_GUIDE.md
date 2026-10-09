# MediCOR AI — Submission Guide

**Status (2026-09-25): working, verified end to end, ready to demo.**

MediCOR AI is an AI receptionist for clinics. It answers the phone, identifies the
caller, books, moves and cancels appointments against real availability, answers
questions from the clinic's own documents, and gives staff a dashboard with every call's
transcript, summary and outcome.

Step-by-step setup detail lives in `MEDICOR_AI_RUN_COMMANDS.md`; architecture and every
design decision in `CLAUDE.md`. This page is the short version for a reviewer.

---

## 1. Verified before submission

| Check | Result |
|---|---|
| Backend test suite (in-memory SQLite, never touches a real DB) | **978 passed**, 0 failed |
| Live endpoint smoke test against the running API + database | **43 of 44** — the one miss is the script's hard-coded demo super-admin email, which this database does not use; super-admin sign-in itself was verified by hand |
| Frontend production build | builds cleanly (one advisory: a single 650 KB JS bundle) |
| Frontend lint (oxlint) + server-side render of all 61 pages | clean / 61 of 61 |
| Real workflows in the browser, as a signed-in clinic | create patient → book appointment (real open slots) → reschedule → cancel; call list, transcript, AI summary; admin console |
| Security review (static, whole repo) | done — fixes applied, remaining items listed in §6 |

## 2. What the application does (all working)

- **Dashboard** — today's calls, bookings, AI resolution rate, staff time saved, live-call
  band, recent calls, today's schedule, 24-hour call volume. Updates live over WebSocket.
- **Call logs** — every call with outcome, transcript (caller vs AI receptionist, named),
  AI summary, direction, linked patient/appointment, CSV export, missed-call callback queue.
  **Quality** tab: LLM-judged call scores and a review queue for hallucinations/policy flags.
- **Appointments** — day/week/month calendar (agenda list on phones), booking against real
  doctor availability, reschedule, cancel (with confirmation), no-show, patient
  confirmation status, AI-booking review queue, EHR sync status.
- **Patients** — searchable, sortable roster, archive/restore, patient profile
  (prescriptions, vitals, call history, appointments), repeat-prescription request queue.
- **Analytics** — calls per day/hour/weekday, reasons, outcomes, sentiment, bookings by doctor.
- **Clinic settings** — clinic profile, services & fees, doctors & working hours, knowledge
  base (document upload + local semantic search, FAQs, "Ask the AI" test console), agent
  greeting/voice and escalation rules, team & roles, EHR/API keys/webhooks, security &
  audit trail, billing, support tickets.
- **Platform admin console** (`/admin`) — all clinics, users, plans, subscriptions, usage,
  phone-number routing, support inbox, cross-clinic audit log, "view as clinic".
- **Voice receptionist** (`voice-agent/`) — Twilio + Deepgram STT + LLM + Sarvam
  Indian-English TTS, multi-clinic routing by dialled number, barge-in, emergency handling,
  transfer to staff. Separate process on port 8080.

## 3. Redesign completed

The whole clinic app and admin console use one design system ("Signal & Ink"):
tokens in `src/index.css`, every shared component in `src/components/ui.jsx`.

- Colour carries meaning only: **teal = the AI at work**, amber = needs attention,
  red = failure, live-red = call in progress. Normal states (Active, Completed, Upcoming,
  Success) are plain text, so the exceptions stand out.
- Stat strips instead of card grids; hairline cards; sentence-case headings (no all-caps);
  40 px touch targets; one primary action per screen.
- Full dark mode (every colour redefined, contrast measured), keyboard-operable dialogs
  (focus moves to the first field, Escape closes, focus returns), screen-reader data
  tables behind every chart, phone layouts for the calendar, call detail and lists.
- Landing and login pages were deliberately left unchanged (owner's decision).

## 4. Bugs fixed during this pass (highlights)

- Any failed "who am I" request (a 500 or a network blip, e.g. one slow call to the remote
  database) signed the user out of every open tab. Now only a real 401 ends a session;
  transient failures are retried once (clinic and admin consoles).
- Call logs froze page scrolling on desktop after any call was clicked.
- Opening a missed call on a phone did nothing; filtered-out calls opened the wrong call.
- Pre-visit answers from one patient could appear under another (request race).
- Appointment calendar columns misaligned when a title was long.
- The appointment dialog's **"Cancel"** button cancelled the booking instantly (now
  "Cancel appointment" with a confirmation that defaults to "Keep it").
- Reschedule now opens on the appointment's own day; failed document indexing showed a
  green "Indexed"; chart bars were invisible keyboard tab stops; analytics range race;
  dark-mode avatars disappeared; several false or overclaiming labels removed
  ("End-to-End Encryption", named EHR vendors, "re-crawls" wording flagged).
- Security: webhook URLs can no longer target private/internal addresses (SSRF), SMS
  replies are XML-escaped, CSV export is protected against formula injection
  (`tests/test_security_hardening.py`).

## 5. How to run

Prerequisites: Python 3.12, Node 20+, ODBC Driver 18 for SQL Server, access to the SQL
Server holding database **MediVoice2** (currently `192.168.29.53,50491`, set in the backend `.env`).

```powershell
cd D:\claude_pro_inspired\MediCOR_AI
powershell -ExecutionPolicy Bypass -File .\scripts\start_medicor_dev.ps1   # backend :8000 + frontend :5173
# stop:  powershell -ExecutionPolicy Bypass -File .\scripts\stop_medicor_dev.ps1
```

- Clinic app: <http://localhost:5173/login> · Admin console: <http://localhost:5173/admin/login>
- API health: <http://localhost:8000/health> · API docs (development only): <http://localhost:8000/docs>
- From a fresh copy: copy each `.env.example` to `.env`, fill in values, then
  `alembic upgrade head` (see `MEDICOR_AI_RUN_COMMANDS.md` §5–§7).

**Making the submission zip:** run `.\scripts\make_submission_zip.ps1`. It excludes every
`.env`, the `_backups` folders, venv/node_modules and uploads, and refuses to build if a
secret file slips through. **Do not zip the folder by hand** — the live `.env` files hold
the database password and API keys.

## 6. External configuration still required / known limitations

| Item | State | What is needed |
|---|---|---|
| Live phone calls | Code complete, not running | `TWILIO_AUTH_TOKEN`, a Twilio number, a public HTTPS URL (ngrok), Deepgram/Sarvam/Groq keys in `voice-agent\.env`, then start the gateway (`MEDICOR_AI_RUN_COMMANDS.md` §10). Existing call history is real data from earlier calls. |
| Email / SMS / WhatsApp | Code complete, off | SMTP or Twilio messaging settings in the backend `.env`. Nothing is sent until configured. |
| Online payment | Code complete, off | Razorpay keys. Plans are assigned per clinic account (owner's choice); no public catalogue. |
| Credential rotation | **Outstanding** | Live keys were once copied into backup files; rotate `SECRET_KEY` and provider keys (`SECURITY-ROTATION.md`). |
| Seed passwords | Demo only | HealthLink seed staff use `password123`; change before exposing the app beyond this machine. |
| Voice gateway hardening | Open (gateway not running) | Authenticate the media WebSocket and bind to 127.0.0.1 behind the tunnel before taking real calls. |
| Website sources (Knowledge Base) | Not functional | URLs are saved but never fetched; documents and FAQs work. Left as is by owner's decision. |
| Voice picker | Partial | Applies only to the managed Deepgram voice engine; the default engine uses one fixed Indian-English voice (stated on the page). |
| Hindi / Telugu | Legacy stack only | The current voice stack is English-only by decision. |
| Admin tables on phones | Scroll sideways | Internal desktop console; wide tables scroll inside their own box. |

## 7. Suggested demo (≈10 minutes)

Sign in to the clinic app as a **HealthLink Clinic** user (the clinic with data).

1. **Dashboard** — the live-status strip and today's schedule; toggle dark mode (moon icon).
2. **Call logs** — open a "Booked" call: named transcript turns (AI in teal), AI summary,
   outcome and direction; switch to the **Quality** tab to show AI self-evaluation.
3. **Patients** → **Add patient** → open the patient's profile.
4. **Appointments** → **New appointment**: pick the patient and a doctor; only genuinely open
   slots appear. Book → open it → **Reschedule** → **Cancel appointment** (note the
   confirmation defaulting to "Keep it").
5. **Knowledge base** → "Ask the AI" a question the clinic's documents answer.
6. **Agent settings** — the six-step "how a call is handled" explainer, greeting, escalation rules.
7. **Admin console** (`/admin`) — platform overview, organizations, usage by clinic.
