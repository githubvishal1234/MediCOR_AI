# MediCOR AI — Deployment Guide

Status on **2026-09-21**: MediCOR AI is **not deployed anywhere**. This guide describes what a
deployment needs, based on the actual code. Nothing in it has been tried on a server yet.
Day-to-day commands are in `MEDICOR_AI_RUN_COMMANDS.md`; open issues in `FINAL_AUDIT_REPORT.md`.

---

## 1. Deployment architecture

```
                   HTTPS (443)                               ┌────────────────────┐
  Browser ───────► Reverse proxy ──► /  (static files) ─────►│ frontend dist\     │
  (clinic staff,     (TLS, one       /api (or api.domain) ──►│ FastAPI :8000      │──► SQL Server
   super admin)       domain or       /ws/dashboard (WSS) ──►│ ONE process        │    (Medivoice2)
                      two)                                   └────────▲───────────┘
  Twilio ──HTTPS──► Reverse proxy ──► /voice/incoming ──────►┌────────┴───────────┐
          ──WSS───►                   /voice/stream  ───────►│ voice gateway :8080│──► Deepgram STT
                                                             │ (twilio_deepgram)  │──► Groq LLM
                                                             └────────────────────┘──► Sarvam TTS
```

| Component | Required | Notes |
|---|---|---|
| Frontend (static `dist\`) | yes | any static host; unknown paths must fall back to `index.html` |
| FastAPI backend | yes | **exactly one process**. It holds WebSocket connections and rate limits in memory, and runs the scheduled jobs itself |
| SQL Server | yes | 2022 Express works (10 GB database limit). Backups are your responsibility |
| WebSockets through the proxy | yes | `/ws/dashboard` (backend) and `/voice/stream` (gateway) |
| Voice gateway | for phone calls | separate process, same Python venv as the backend |
| Persistent disk | yes | the backend's `uploads\` folder holds knowledge-base documents (may contain patient information) |
| Redis / Celery / queue | **no** | not used |
| Call recording storage | **no** | calls are transcribed, never recorded |

---

## 2. Prerequisites

- A server (Windows Server or Windows 10/11, or Linux; the commands in this repository are
  Windows commands) with Python 3.12, Node.js 20.19+ (for building only) and the
  "ODBC Driver 18 for SQL Server".
- SQL Server reachable from that server.
- A domain name and HTTPS certificates (for example IIS with a certificate, Caddy or nginx).
- Accounts you create and pay for yourself: Twilio (number and SMS/WhatsApp), Deepgram,
  Sarvam, Groq; optionally an SMTP mail provider and Razorpay.

---

## 3. Before you deploy — mandatory security steps

These are not optional. Each was found during the 2026-09-21 audit.

1. **Do not deploy the demo accounts.** All 14 active users in the current `Medivoice2`
   database use the published seed password `password123`, including
   `admin@healthlinkclinic.com` and `voice-agent@healthlinkclinic.com`. Either start from a
   **fresh database** (`alembic upgrade head`, **no** `app.seed`) or change every one of those
   passwords and deactivate the `staff*@healthlinkclinic.com` demo users first.
2. **Rotate credentials.** `SECURITY-ROTATION.md` records that live credentials were once
   committed in plain text and that rotation is still outstanding. Rotate them in each
   provider's console before going live.
3. **Generate a new `SECRET_KEY`** for production (section 4). Changing it signs out every user.
4. **Set `ENVIRONMENT=production` last**, after everything else in section 4 is set. The
   backend then refuses to start while `SECRET_KEY` is the default or `CORS_ORIGINS` is still
   local, and hides `/docs`.
5. Nothing here makes the product HIPAA- or otherwise legally compliant. Compliance needs
   agreements with providers (Twilio, Deepgram, Groq, Sarvam, hosting), policies, access
   reviews and audits outside this code.

---

## 4. Environment variables (production)

Backend `MediCOR_AI-backend\MediCOR_AI-backend\.env`:

| Key | Production value |
|---|---|
| `ENVIRONMENT` | `production` (set last) |
| `SECRET_KEY` | new random value: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CORS_ORIGINS` | your frontend URL(s), e.g. `https://app.your-domain.com`. The **first** one is used in invitation and password-reset email links |
| `PUBLIC_BASE_URL` | the public HTTPS URL of the backend. Needed so Twilio signatures on inbound SMS verify |
| `TRUSTED_PROXY_IPS` | your reverse proxy's IP address, so rate limits see real client IPs |
| `DB_SERVER`, `DB_PORT`, `DB_NAME`, `DB_DRIVER`, `DB_TRUSTED_CONNECTION` or `DB_USER`/`DB_PASSWORD`, `DB_ENCRYPT`, `DB_TRUST_SERVER_CERTIFICATE` | your server. Use `DB_TRUST_SERVER_CERTIFICATE=false` with a real certificate |
| `AUTO_CREATE_SCHEMA` | `false` (Alembic owns the schema) |
| `GROQ_API_KEY`, `GROQ_MODEL` | AI call summaries and knowledge-base answers (optional) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`, `SMTP_USE_TLS` | staff invitations, password resets, email reminders |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `SMS_FROM_NUMBER`, `WHATSAPP_FROM_NUMBER`, `DEFAULT_MESSAGE_CHANNEL` | patient SMS/WhatsApp messages and inbound replies |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `BILLING_CURRENCY` | online payment (optional). `RAZORPAY_MOCK_MODE` must be `false` in production |
| `SUPPORT_PHONE`, `SUPPORT_EMAIL`, `SUPPORT_HOURS` | shown on the Support page (optional) |

Voice gateway `voice-agent\.env`: see `MEDICOR_AI_RUN_COMMANDS.md` section 5. In production:
`VOICE_GATEWAY_PUBLIC_URL=https://voice.your-domain.com`, `VOICE_MOCK_MODE=false`,
`BACKEND_API_URL` pointing at the backend, a real `DEFAULT_TRANSFER_NUMBER` that is **not** the
number patients dial, and `EMERGENCY_NUMBER` / `EMERGENCY_INSTRUCTION` reviewed by a clinician.

Frontend: `VITE_API_URL` at **build time** (section 7).

Every `.env` file is ignored by `.gitignore`. Keep them out of version control and backups
that other people can read.

---

## 5. Database setup

```powershell
# Empty database (skip if it exists)
sqlcmd -S <server> -E -C -Q "IF DB_ID('Medivoice2') IS NULL CREATE DATABASE Medivoice2"

# Schema (from the backend folder)
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m alembic check        # must say: No new upgrade operations detected.

# First platform administrator (interactive)
.\venv\Scripts\python.exe create_super_admin.py
```

Do **not** run `python -m app.seed` in production. Set up automated SQL Server backups
(Express has no SQL Agent, so use Windows Task Scheduler with `sqlcmd` `BACKUP DATABASE`, or
your host's backup service) and test a restore.

---

## 6. Backend deployment

```powershell
Set-Location "<server path>\MediCOR_AI-backend\MediCOR_AI-backend"
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- One process only: no `--workers`, no second copy behind a load balancer. Scaling out needs
  Redis-backed rate limits and WebSocket fan-out, which are **not built**.
- Keep it running as a service, for example with Windows Task Scheduler ("run whether user is
  logged on or not", restart on failure) or a service wrapper such as NSSM. This has not been
  set up or tested here.
- The first knowledge-base upload or question downloads the embedding model (about 66 MB) into
  `.model_cache\`; allow outbound internet once, or copy that folder from a machine that has it.
- Health check: `GET https://<backend>/health` returns `{"status":"ok",...}`.

---

## 7. Frontend deployment

```powershell
Set-Location "<repo>\MediCOR_AI-frontend\MediCOR_AI-frontend"
npm install
$env:VITE_API_URL = "https://api.your-domain.com"
npm run build
```

Upload `dist\` to any static host (IIS site, nginx, Netlify, S3 + CloudFront, and so on).
Configure **SPA fallback**: every path that is not a file serves `index.html`. Without it,
reloading `/app/calls` returns 404. The fonts are self-hosted in `dist\fonts\`, so nothing is
loaded from Google.

---

## 8. Twilio and Deepgram setup

1. Start the gateway (`..\venv\Scripts\python.exe -m twilio_deepgram.run` from `voice-agent\`)
   behind HTTPS at `VOICE_GATEWAY_PUBLIC_URL`.
2. Open `https://<gateway>/health`. The `problems` list must be empty.
3. In the Twilio console, set your number's **Voice → A call comes in** webhook to
   `POST https://<gateway>/voice/incoming`. Twilio then opens the audio WebSocket
   `wss://<gateway>/voice/stream` itself. Your proxy must allow WebSockets on that path.
4. For patient SMS/WhatsApp replies (CONFIRM / CANCEL / STOP), set the number's **Messaging**
   webhook to `POST https://<backend>/messages/inbound`, and set `PUBLIC_BASE_URL`.
5. Razorpay (optional): webhook URL `https://<backend>/webhooks/razorpay`.
6. Assign the Twilio number to a clinic in the super-admin console (**Phone numbers**) so calls
   are routed to the right tenant.
7. Place a real test call and confirm a call record, transcript and summary appear in Call Logs.

Signatures matter: Twilio signs the exact URL it called. If the URL in `.env` differs from the
real public URL (http vs https, a different host, a trailing slash), every call or message is
rejected in a way that looks like a wrong auth token.

The voice stack is **English only** by design (see `VOICE_STACK_MIGRATION.md` §17). The legacy
LiveKit agent (`agent.py`) is the only stack that speaks Hindi and Telugu.

---

## 9. Migrations on later releases

```powershell
# 1. back up the database
# 2. stop the backend
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m alembic check
# 3. start the backend, check /health
```

Read every new file in `alembic\versions\` before applying it to production data.

---

## 10. Verification after deployment

| Check | Expected |
|---|---|
| `GET /health` (backend) | `status: ok`, `workers.configured: 1` |
| Backend startup log | no "fatal" deployment problems |
| `GET /health` (gateway) | `problems: []` |
| Log in at the frontend | dashboard loads, "Live" indicator shows connected |
| Browser dev tools → Network | API calls go to `VITE_API_URL`, not `localhost` |
| Test call | call appears live, then in Call Logs with transcript |
| Password reset email | link points at your real frontend domain |
| `/docs` on the backend | 404 (hidden in production) |

---

## 11. Common deployment failures

| Symptom | Cause |
|---|---|
| Login does nothing, CORS error in console | frontend domain missing from `CORS_ORIGINS` |
| Dashboard calls `localhost:8000` | frontend built without `VITE_API_URL` |
| Reloading a page gives 404 | SPA fallback not configured |
| "Live" indicator never connects | reverse proxy is not forwarding WebSockets on `/ws/dashboard` |
| Backend exits at startup in production | read the log: it lists every problem at once |
| Calls connect to silence | gateway WebSocket `/voice/stream` not reachable over WSS |
| All calls or SMS rejected | `TWILIO_AUTH_TOKEN` wrong, or public URL mismatch (section 8) |
| Rate limiting blocks everyone at once | proxy in front but `TRUSTED_PROXY_IPS` not set |
| Every clinic shows UTC times | `tzdata` not installed |

More local troubleshooting: `MEDICOR_AI_RUN_COMMANDS.md` section 15.
