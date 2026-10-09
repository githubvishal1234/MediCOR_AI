# MediCOR AI — Final Audit Report

Audit date: **2026-09-21**. Everything under "verified" was actually run on this machine
(Windows 11, SQL Server 2022 Express, Python 3.12.10, Node 24.15). Anything not run is labelled.
Commands: `MEDICOR_AI_RUN_COMMANDS.md`. Deployment: `FINAL_DEPLOYMENT_GUIDE.md`.
Deep architecture notes: `CLAUDE.md`.

---

## 1. Readiness at a glance

| Area | Status | Evidence |
|---|---|---|
| Local development (backend + frontend + database) | **A. Verified working** | start/stop scripts run end to end; `/health` ok; logged-in dashboard checked in a browser at desktop, tablet and phone widths |
| Backend tests | **A. 966 passed, 0 failed** | `pytest tests` (in-memory SQLite) |
| Frontend | **A. Verified** | lint 0 errors; smoke 61/61 pages; production build ok; `npm audit` 0 vulnerabilities; no browser console errors on the dashboard |
| Database schema | **A. Verified** | `alembic current` = `0036_payments (head)`; `alembic check` = no drift |
| Voice gateway (Twilio + Deepgram) | **B. Implemented, not live-tested** | code, tests and gateway self-check present; no real call was placed |
| Real phone calls | **C. Needs your credentials** | gateway reports: `TWILIO_AUTH_TOKEN` not set, public URL is local, no transfer number |
| Production deployment | **D. Not done / blocked** | not deployed; blockers in section 8 |

**Overall:** ready for local development and demos. **Not ready for production** until the
section 8 blockers are cleared, above all the demo passwords and credential rotation.

---

## 2. Architecture discovered

| Layer | Technology (verified in the repository) |
|---|---|
| Frontend | React 19, Vite 8, React Router 7, Tailwind CSS v4, lucide-react; plain JavaScript; no test runner (a custom SSR smoke test instead) |
| Backend | FastAPI 0.115, SQLAlchemy 2.0, Pydantic 2, Alembic 1.13 (36 migrations), pyodbc |
| Database | Microsoft SQL Server 2022 Express, database `Medivoice2`, Windows authentication, ODBC Driver 18 |
| Auth | JWT (HS256) with three token families (staff, super admin, voice agent), bcrypt passwords, service API keys, rate-limited login |
| Real-time | in-process WebSocket fan-out `/ws/dashboard` |
| Background work | in-process scheduler (9 jobs) with a database-claim table. **No Redis, no Celery** |
| AI | Groq (OpenAI-compatible) for call summaries, quality evaluation and knowledge-base answers; local fastembed/ONNX embeddings (bge-small-en-v1.5) |
| Voice (current) | `voice-agent/twilio_deepgram`: Twilio Media Streams → Deepgram STT → Groq LLM → Sarvam TTS (or Deepgram's managed Voice Agent) on port 8080 |
| Voice (legacy) | `voice-agent/agent.py`: LiveKit Agents + Twilio SIP; kept for Hindi and Telugu |
| Messaging | SMTP email; Twilio SMS/WhatsApp with signed inbound replies |
| Payments | Razorpay (optional, with a mock mode) |
| Structure | 29 routers, 33 frontend routes (8 public, 14 clinic, 11 platform-admin) |

The Graphify code graph (`graphify-out/`, 7,755 nodes) was used to map structure. Its labels
still show the old product name; refresh with `/graphify . --update` if you keep using it.

---

## 3. Modules reviewed

All exist and are wired end to end: authentication and users; organizations (multi-tenant) and
super-admin console; doctors; patients (duplicate guard, phone matching, consent); appointments
(self-service link, waiting list, reminders, pre-visit forms); call logs, transcripts and AI
summaries; knowledge base (uploads + RAG); AI agent settings and escalation rules; notifications
(bell, toasts, email, SMS/WhatsApp); dashboard and analytics; call quality evaluation; billing
(plans, invoices, Razorpay); support and contact enquiries; EHR integration (generic, no named
vendor); security/compliance page; outbound webhooks; audit log.

**Not implemented (by design, per `CLAUDE.md`):** call **recordings** (calls are transcribed,
never recorded); named EHR vendor connectors; free trial; placing calls from the dashboard (the
button is intentionally disabled); session revocation on password reset; horizontal scaling
(needs Redis).

---

## 4. Bugs and issues found — and what was done

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | **Critical (data/ops)** | All **14** active clinic users in the `Medivoice2` database use the published demo password `password123` (incl. `admin@healthlinkclinic.com` and `voice-agent@healthlinkclinic.com`). Super admin does not | **Open, needs your action.** Not changed automatically: changing passwords alters your accounts. See section 8 |
| 2 | **Medium (security)** | Staff session JWTs and patient appointment-link tokens were written to the log in plain text (they travel in URL query strings; uvicorn logs every request line). Confirmed: 15 occurrences in one session's log | **Fixed** — `RedactTokensFilter` in `app/logging_config.py`; test added |
| 3 | **Low–medium (security)** | A suspended clinic's WebSocket was still accepted, so it kept receiving live call/appointment events (every HTTP path already rejected suspended clinics) | **Fixed** — `app/routers/ws.py`; test added |
| 4 | Medium (setup) | `.env.example` did not mention `GROQ_API_KEY` / `GROQ_MODEL`, which the working `.env` uses; a fresh setup silently lost AI summaries | **Fixed** — documented with placeholders, plus the scheduler switches |
| 5 | Medium (reproducibility) | The backend `venv` was copied from `D:\claude_pro\...`; its `activate` script and `.exe` launchers point there. `uvicorn.exe`/`pytest.exe` ran another folder's interpreter | **Worked around and documented**: always `.\venv\Scripts\python.exe -m ...`; clean-recreate steps in the run guide (not done automatically: needs a large download) |
| 6 | Low (tests) | `test_the_vocabulary_is_served_not_hardcoded` failed on every run: it read a frontend path from two renames ago | **Fixed** — path corrected; test passes |
| 7 | Low (docs) | `CLAUDE.md` command paths pointed at old `medicare-voice-ai-*-FIXED` folders, told you to use the broken venv launchers, and claimed a `.git` repository that this copy does not have | **Fixed** |
| 8 | Medium (UI, phones) | Dashboard "Recent calls" card overflowed the screen on phones (CSS grid min-width) | **Fixed** earlier today (Slice 1) |
| 9 | Low (UI) | Dashboard coloured every call outcome green (incl. "Cancelled"); outcome colours duplicated in three files | **Fixed** (Slice 1) |
| 10 | Low (a11y) | Header icon buttons were 32–34 px | **Fixed** to 40 px (Slice 1) |
| 11 | Low (privacy/perf) | Fonts loaded from Google Fonts (render-blocking, sends visitor IPs to a third party) | **Fixed**, fonts self-hosted (Slice 0) |

---

## 5. Security findings

**Verified sound (reviewed in code, and where noted, covered by passing tests):**
- **Tenant isolation.** Every by-ID lookup in the clinic routers was traced; each is either
  filtered by `org_id` or follows a parent row that already was (`tests/test_phase8_tenant_isolation.py` passes).
- **Access control.** Three JWT families are rejected by each other's decoders; admin-only
  routes use `require_admin`; suspended organizations are rejected on all HTTP paths.
- **Passwords.** bcrypt with the 72-byte limit enforced; per-IP and per-account login rate limits.
- **SQL injection.** No SQL is built from strings anywhere (ORM and bound parameters only).
- **CORS.** An explicit origin list with limited methods and headers. The backend refuses to
  start in production while it is still local-only.
- **Uploads.** Extension and content-type allowlist before reading, basename-only filenames,
  streamed with a size cap, stored outside the web root, per-organization folders.
- **Debug surface.** `/docs` and `/openapi.json` are hidden in production. The production
  startup gate lists every bad setting.
- **Webhooks.** Twilio (inbound SMS, voice) and Razorpay signatures are verified before parsing.
- **Secrets.** `SECRET_KEY` is not the default and is 64 characters. No credential values appear
  in any file created or edited today. All `.env` files are git-ignored.

**Open risks (not fixed; your decision or outside the code):**

| Risk | Severity | Note |
|---|---|---|
| Demo password on all 14 clinic accounts | Critical before deployment | section 8, step 1 |
| Credential rotation outstanding | High | recorded in `SECURITY-ROTATION.md`; five `.env` copies in `_backups\` hold old credentials |
| Session tokens valid 24 h (`ACCESS_TOKEN_EXPIRE_MINUTES=1440`), no revocation on password reset | Medium | shorten the expiry for production; revocation needs a design change |
| Tokens kept in browser `localStorage` | Low–medium | readable by any XSS; standard single-page-app trade-off |
| Python dependency advisories, **from knowledge, not scanned**: `python-jose 3.3.0` (CVE-2024-33663/33664; this app uses HS256 with a fixed algorithm list, so the algorithm-confusion issue does not apply), `python-multipart 0.0.9` and the Starlette version pinned by FastAPI 0.115.0 (multipart denial-of-service fixes in later versions) | Medium | install `pip-audit` and run it; upgrade FastAPI/python-multipart, then re-run the 966 tests |
| Single process only | Medium (availability) | by design until Redis-backed rate limits and fan-out exist |
| Knowledge-base uploads on local disk | Medium | back them up; they may contain patient information |

**Not a compliance statement.** These checks do not make MediCOR AI HIPAA-compliant or otherwise
legally compliant. That requires provider agreements (hosting, Twilio, Deepgram, Groq, Sarvam),
policies, access reviews, audit-log retention decisions and an independent assessment.

The installed **Claude Security** plugin was **not run**. It is launched by you with
`/claude-security` and uses a lot of tokens. The review above is a targeted manual review plus
the existing test suite.

---

## 6. Tests and builds actually executed (2026-09-21)

| Command | Result |
|---|---|
| `python -m pytest tests -q` (backend) | **966 passed, 0 failed** in 2 min 52 s (before today's fixes: 962 passed, 1 failed) |
| `python -m pytest tests/test_token_leak_and_ws_suspension.py` | 3 passed (new) |
| `python -m alembic current` / `heads` / `check` | at head `0036_payments`; no drift |
| `npm run lint` (oxlint) | 0 errors, 4 warnings (Fast Refresh export style; harmless) |
| `npm run smoke` | 61 of 61 pages and components render |
| `vite build` without `VITE_API_URL` | refuses, as designed |
| `vite build` with `VITE_API_URL` | builds; API URL printed |
| `npm audit` (production and dev) | 0 vulnerabilities |
| `scripts/stop_medicor_dev.ps1` | stopped backend (2 processes) and frontend; ports freed |
| `scripts/start_medicor_dev.ps1` | all pre-flight checks ok; backend and frontend healthy |
| Gateway `startup_problems()` | 3 problems (all credentials/URLs; see section 7) |
| Live phone call | **not run** (no Twilio auth token or public URL configured) |
| `pip-audit` | **not run** (not installed) |

---

## 7. AI, Twilio and Deepgram — what is actually there

- **Implemented:** Twilio webhook `POST /voice/incoming` with signature verification; audio over
  `WS /voice/stream`; Deepgram streaming STT (8 kHz mu-law, no resampling); Groq LLM with 16
  client-side tools (patient lookup, booking, rescheduling, cancelling, FAQ via the knowledge
  base, refills, transfer and so on); Sarvam TTS; barge-in, tool-call fillers, a call watchdog,
  single call finalisation, transcript persistence through the backend API, and a mock mode for
  credential-free testing (`simulate_call.py`).
- **Timeouts and retries:** backend client timeouts and retries are configurable
  (`BACKEND_API_TIMEOUT_SECONDS`, `BACKEND_API_MAX_RETRIES`); LLM calls have timeout and retry
  settings; a caller hears an apology rather than dead air on model failure.
- **Gateway self-check today:** `TWILIO_AUTH_TOKEN` not set (every call would be rejected);
  `VOICE_GATEWAY_PUBLIC_URL=http://127.0.0.1:8080` (Twilio cannot reach it);
  `DEFAULT_TRANSFER_NUMBER` not set.
- **Not verified:** that a real call works end to end. That needs your credentials and a public
  HTTPS URL. The voice stack is English only by design.

---

## 8. Manual actions required (in this order)

1. **Change or retire the demo accounts** before anyone else can reach this database. Change
   `admin@healthlinkclinic.com` and `voice-agent@healthlinkclinic.com` (and update
   `BACKEND_API_KEY`/the agent login if they depend on them), and deactivate
   `staff1`–`staff12@healthlinkclinic.com` in **Team**. For production, prefer a fresh database
   without `app.seed`.
2. **Rotate the credentials** listed in `SECURITY-ROTATION.md`, in each provider's console.
3. **Recreate the backend venv** (run guide section 3b) so it no longer depends on `D:\claude_pro`.
   Until then, do not delete `D:\claude_pro`.
4. **Put the project under version control:** `git init` in `D:\claude_pro_inspired\MediCOR_AI`,
   check `git status` shows no `.env` files, then commit.
5. **Voice:** fill in `TWILIO_AUTH_TOKEN`, `VOICE_GATEWAY_PUBLIC_URL` (ngrok for testing),
   `DEFAULT_TRANSFER_NUMBER`; have a clinician review `EMERGENCY_INSTRUCTION`; place a test call.
6. **Run `pip-audit`** and upgrade flagged Python packages, then re-run the tests.
7. **Production:** follow `FINAL_DEPLOYMENT_GUIDE.md` (hosting, HTTPS, env vars, backups).

---

## 9. Remaining UI/UX work (not done)

> **Superseded 2026-09-25:** the redesign below was completed for every clinic and admin
> page (landing and login deliberately left unchanged), and the defects listed were fixed.
> See `SUBMISSION_GUIDE.md` for the current state. The original note is kept for history.

A redesign ("Signal & Ink": keep teal/ink/crimson, colour means something, flat hairline cards,
no all-caps labels, Newsreader serif on public pages only) was started. **Done:** design tokens,
self-hosted fonts, the app shell and the Dashboard. **Not done:** Appointments, Patients, Call
Logs, Analytics, Settings, the 11 admin pages, and the landing/login pages (the landing page is
dark and the login page is light). Known small defects: dialogs focus the close button rather
than the first field; admin tables scroll sideways on phones; in dark mode the header avatar
loses its circle. None of these block use.

---

## 10. Files created or modified in this final pass

Created: `MEDICOR_AI_RUN_COMMANDS.md`, `FINAL_DEPLOYMENT_GUIDE.md`, `FINAL_AUDIT_REPORT.md`,
`scripts\start_medicor_dev.ps1`, `scripts\stop_medicor_dev.ps1`,
`MediCOR_AI-backend\MediCOR_AI-backend\tests\test_token_leak_and_ws_suspension.py`.

Modified: `MediCOR_AI-backend\MediCOR_AI-backend\app\logging_config.py`,
`...\app\routers\ws.py`, `...\tests\test_escalation_rules.py`, `...\.env.example`,
`CLAUDE.md`.

Earlier the same day (rename and UI slices 0–1): brand rename CortraSync → MediCOR across 31
text files, 3 `.docx` files, 6 file names and 2 regenerated PDFs; `index.html`, `src\index.css`,
`public\fonts\` (4 files), `src\lib\format.js`, `src\pages\app\Dashboard.jsx`, `CallLogs.jsx`,
`PatientProfile.jsx`, `src\components\AppShell.jsx`, `LiveCallBanner.jsx`.

**Not touched:** the `.env` files' values, the database (read-only checks only: no migration,
seed, reset or delete was run), `_backups\`, `_twilio-migration-backup\`, and the backup copies
elsewhere on `D:\`. The pre-rebrand backup zip is now at
`D:\claude_pro_frontend_pro\CortraSync_AI-twilio+deepgram.zip` (moved there at 15:45; no command in this Claude Code session moved it).
