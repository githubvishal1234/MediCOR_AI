# MediCOR AI — Run Commands (Windows PowerShell)

Everything here was checked against this repository on **2026-09-21**. Commands are
copy-paste ready for **Windows PowerShell** (not `cmd`, not Git Bash).

Related documents: `FINAL_AUDIT_REPORT.md` (what was checked and what is still open) and
`FINAL_DEPLOYMENT_GUIDE.md` (putting it on a server).

---

## 0. The shape of the project

| Part | Folder | Port | Needed for |
|---|---|---|---|
| Backend API (FastAPI) | `MediCOR_AI-backend\MediCOR_AI-backend` | **8000** | everything |
| Frontend (React + Vite) | `MediCOR_AI-frontend\MediCOR_AI-frontend` | **5173** | the dashboard |
| Database | Microsoft SQL Server 2022 Express, database `Medivoice2` | **1433** | everything |
| Voice gateway (Twilio + Deepgram) | `MediCOR_AI-backend\MediCOR_AI-backend\voice-agent` | **8080** | phone calls only |

There is **no Redis, no Celery and no separate worker**. Scheduled jobs (reminders,
stale-call cleanup, digests) run inside the backend process. Run **exactly one** backend
process — the app refuses to start with more than one worker.

The folders are doubled on purpose (`MediCOR_AI-backend\MediCOR_AI-backend`). Do not
flatten them: `voice-agent\setup_twilio_trunks.py` relies on that depth.

---

## 1. Prerequisites

| Software | Version on this machine | Check |
|---|---|---|
| Python | 3.12 (the backend venv is 3.12.10) | `py -3.12 --version` |
| Node.js | 24.x (Vite 8 needs Node 20.19+ or 22.12+) | `node --version` |
| SQL Server | 2022 Express, instance `SQLEXPRESS`, TCP on 1433 | `Get-Service 'MSSQL$SQLEXPRESS'` |
| ODBC driver | "ODBC Driver 18 for SQL Server" | `Get-OdbcDriver -Platform 64-bit \| Where-Object Name -like '*SQL Server*'` |
| sqlcmd | only for creating a brand-new database | `sqlcmd -?` |

Voice calls additionally need: a Twilio account and number, a Deepgram API key, a Sarvam
API key (Indian-English voice), a Groq API key, and a public HTTPS URL (ngrok for testing).

---

## 2. Open the project

```powershell
$Root = "D:\claude_pro_inspired\MediCOR_AI"
Set-Location $Root
```

Every later block assumes `$Root` is set. If you open a new window, run that line again.

Allow the helper scripts to run **for one command only** (this changes no system setting):
`powershell -ExecutionPolicy Bypass -File <script>` — used below.

---

## 3. Python virtual environment

### 3a. Use the existing venv (works today)

The existing `venv` works **when you call its `python.exe` directly**:

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe --version
```

> **Important:** this venv was copied from `D:\claude_pro\medicare-voice-ai\...`. Its
> `activate` script and its `.exe` launchers (`uvicorn.exe`, `pip.exe`, `pytest.exe`,
> `alembic.exe`) still point at that old folder. So **always use
> `.\venv\Scripts\python.exe -m <tool>`**, never `uvicorn ...` or `pip ...` on their own.
> Every command in this file follows that rule.

### 3b. Recreate a clean venv (recommended once; nothing is deleted)

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
Rename-Item venv venv_old_copy          # keep the old one until the new one works
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

After this, `.\venv\Scripts\Activate.ps1` also works normally. Once the app starts and
tests pass (section 16), you may delete `venv_old_copy` yourself.

The voice gateway runs on this **same** venv. `voice-agent\twilio_deepgram_requirements.txt`
adds nothing beyond `requirements.txt`.

---

## 4. Backend dependencies

Already installed in the existing venv. To (re)install into the venv:

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

Frontend dependencies:

```powershell
Set-Location "$Root\MediCOR_AI-frontend\MediCOR_AI-frontend"
npm install
```

---

## 5. Configure the `.env` files

Your working `.env` files already exist. **Never overwrite them.** For a new machine only:

```powershell
# Backend (API)
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# Voice gateway
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend\voice-agent"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# Frontend (optional in development)
Set-Location "$Root\MediCOR_AI-frontend\MediCOR_AI-frontend"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Then open each `.env` in a text editor and fill in the values. Generate a strong `SECRET_KEY`:

```powershell
& "$Root\MediCOR_AI-backend\MediCOR_AI-backend\venv\Scripts\python.exe" -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Backend `.env` — values you must provide**

| Key | This machine uses | Notes |
|---|---|---|
| `SECRET_KEY` | set (64 chars) | signs every login. Never share it; never commit it |
| `DB_SERVER`, `DB_PORT`, `DB_NAME` | `localhost`, `1433`, `Medivoice2` | |
| `DB_TRUSTED_CONNECTION` | `true` | Windows login. Set `false` and add `DB_USER`/`DB_PASSWORD` for a SQL login |
| `DB_DRIVER` | `ODBC Driver 18 for SQL Server` | must match an installed driver exactly |
| `DB_ENCRYPT`, `DB_TRUST_SERVER_CERTIFICATE` | `true`, `true` | local self-signed certificate |
| `CORS_ORIGINS` | localhost ports | in production: your real frontend URL(s) |
| `AUTO_CREATE_SCHEMA` | `false` | the schema is managed by Alembic (section 7) |
| `GROQ_API_KEY`, `GROQ_MODEL` | set | optional; empty = rule-based call summaries only |

**Voice gateway `voice-agent\.env` — needed for real phone calls**

`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `DEEPGRAM_API_KEY`,
`SARVAM_API_KEY`, `GROQ_API_KEY`, `BACKEND_API_URL` (e.g. `http://localhost:8000`),
`BACKEND_API_KEY` and/or `BACKEND_PLATFORM_KEY`, `VOICE_GATEWAY_PUBLIC_URL` (public HTTPS URL),
`VOICE_PROVIDER`, `VOICE_ENGINE`, `EMERGENCY_NUMBER`, and a `DEFAULT_TRANSFER_NUMBER`.

As of 2026-09-21 the gateway reports three gaps in this machine's `voice-agent\.env`:
`TWILIO_AUTH_TOKEN` is not set, `VOICE_GATEWAY_PUBLIC_URL` is a local address, and
`DEFAULT_TRANSFER_NUMBER` is not set. Real calls will not work until these are filled in.

`BACKEND_API_KEY` is created with:
```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe create_service_api_key.py
```

**Frontend `.env`** — only `VITE_API_URL`. Leave it unset in development (defaults to
`http://localhost:8000`). It is **required** for a production build (section 17).

Never paste keys into source files or chat. Put them only in these `.env` files.

---

## 6. SQL Server

```powershell
Get-Service 'MSSQL$SQLEXPRESS'                     # should say Running
Start-Service 'MSSQL$SQLEXPRESS'                   # run PowerShell as Administrator if stopped
Test-NetConnection localhost -Port 1433            # TcpTestSucceeded : True
```

Single quotes matter: `$SQLEXPRESS` inside double quotes would be read as a variable.

**Brand-new machine only** — create the empty database (does nothing if it already exists):

```powershell
sqlcmd -S localhost -E -C -Q "IF DB_ID('Medivoice2') IS NULL CREATE DATABASE Medivoice2"
```

---

## 7. Database migrations (Alembic)

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe -m alembic current     # which revision the database is on
.\venv\Scripts\python.exe -m alembic heads       # latest revision in the code
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m alembic check       # "No new upgrade operations detected." = in sync
```

On 2026-09-21 this database was at `0036_payments (head)` and `alembic check` reported no drift,
so `upgrade head` is a no-op today.

- **Brand-new empty database:** `alembic upgrade head` builds every table.
- **An old database that has tables but no `alembic_version` table:** run
  `alembic stamp 0001_baseline` **once**, then `alembic upgrade head`. Do not run `upgrade`
  first on such a database.
- Never run `alembic downgrade` against real data without a backup.

Optional, **development only** — demo clinic "HealthLink Clinic" (idempotent). It creates
users whose password is `password123`, so **never run it on a production database**:

```powershell
.\venv\Scripts\python.exe -m app.seed
```

Create the first platform super-admin (asks for email and password interactively):

```powershell
.\venv\Scripts\python.exe create_super_admin.py
```

---

## 8. Start the backend (Terminal 1)

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Wait for `Application startup complete`. Leave this window open. Do not add `--workers`.

## 9. Start the frontend (Terminal 2)

```powershell
Set-Location "$Root\MediCOR_AI-frontend\MediCOR_AI-frontend"
npm run dev
```

Open http://localhost:5173/login

## 10. Start the voice gateway (Terminal 3 — only for phone calls)

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend\voice-agent"
..\venv\Scripts\python.exe -m twilio_deepgram.run
```

Check http://localhost:8080/health — its `problems` list must be empty before real calls work.
For Twilio to reach it you need a public HTTPS URL (for testing: `ngrok http 8080`, then put
the `https://...` address in `VOICE_GATEWAY_PUBLIC_URL` and restart the gateway).

**Test without buying anything** (fake speech, fake AI, fake voice; the backend and database
are real, so a test call record is written to the database): set `VOICE_MOCK_MODE=true` in
`voice-agent\.env`, restart the gateway, then in another terminal:

```powershell
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend\voice-agent"
..\venv\Scripts\python.exe simulate_call.py
```

Set `VOICE_MOCK_MODE=false` again afterwards — a gateway left in mock mode answers real calls
with a script.

Legacy LiveKit agent (only if you switch back to it; see `VOICE_STACK_MIGRATION.md`):
`..\venv\Scripts\python.exe agent.py dev`. Never run both voice stacks for one number.

## 11. Redis / Celery

Not used by this project. Nothing to start.

---

## 12. Start everything with one command

```powershell
Set-Location $Root
powershell -ExecutionPolicy Bypass -File .\scripts\start_medicor_dev.ps1
# with the voice gateway as well:
powershell -ExecutionPolicy Bypass -File .\scripts\start_medicor_dev.ps1 -WithVoiceGateway
```

The script checks the venv, `.env`, `node_modules`, SQL Server and free ports; opens the backend
and frontend (and optionally the gateway) in their **own windows**; waits for their health
checks; and prints the URLs. It never migrates, seeds or deletes anything. Tested 2026-09-21.

## 13. Verify it is running

```powershell
Invoke-RestMethod http://localhost:8000/health          # status : ok
(Invoke-WebRequest -UseBasicParsing http://localhost:5173).StatusCode   # 200
```

- Clinic app: http://localhost:5173/login
- Platform admin: http://localhost:5173/admin/login
- API docs (development only): http://localhost:8000/docs

End-to-end check of every dashboard endpoint against the running server (expects the demo
seed data and a super admin; edit the credentials at the top of the file first):
`.\venv\Scripts\python.exe smoke_test.py` (from the backend folder).

## 14. Stop the application

```powershell
Set-Location $Root
powershell -ExecutionPolicy Bypass -File .\scripts\stop_medicor_dev.ps1
```

Or press **Ctrl+C** in each service window. The stop script only stops MediCOR's backend,
frontend and gateway processes; it never touches SQL Server or other programs. Tested 2026-09-21.

---

## 15. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Fatal error in launcher: Unable to create process using "D:\claude_pro\..."` | You ran `uvicorn`/`pip`/`pytest` directly. Use `.\venv\Scripts\python.exe -m uvicorn ...`, or recreate the venv (3b). |
| `...cannot be loaded because running scripts is disabled` | Run scripts with `powershell -ExecutionPolicy Bypass -File ...`. |
| `Port 8000 is already in use` | Run the stop script, or find the owner: `Get-NetTCPConnection -LocalPort 8000 -State Listen`. |
| `Data source name not found and no default driver specified` | `DB_DRIVER` does not match an installed ODBC driver name exactly. |
| `Login failed for user` / cannot connect to SQL Server | Service stopped (section 6), TCP/IP disabled in SQL Server Configuration Manager, or wrong `DB_TRUSTED_CONNECTION`. |
| `SSL Provider: The certificate chain was issued by an authority that is not trusted` | Keep `DB_TRUST_SERVER_CERTIFICATE=true` locally. |
| Login page does nothing / CORS error in the browser console | The frontend's address is missing from `CORS_ORIGINS` in the backend `.env`. Restart the backend after editing. |
| `Refusing to build: VITE_API_URL is unset` | Intended. Set `VITE_API_URL` for production builds (section 17). |
| Backend refuses to start in production mode | Read the log: it lists every bad setting (e.g. `CORS_ORIGINS` still local). |
| Voice calls connect to silence | `VOICE_GATEWAY_PUBLIC_URL` is not a public HTTPS address Twilio can reach. |
| Every inbound call rejected | `TWILIO_AUTH_TOKEN` is missing or wrong (signature check fails). |
| Every clinic falls back to UTC times | `tzdata` missing: reinstall `requirements.txt`. |

---

## 16. Run the tests

```powershell
# Backend: 966 tests, in-memory SQLite, never touches your real database (~3 minutes)
Set-Location "$Root\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe -m pytest tests -q

# One file / one test
.\venv\Scripts\python.exe -m pytest tests/test_phase8_tenant_isolation.py -v

# Frontend: lint, then render every page server-side
Set-Location "$Root\MediCOR_AI-frontend\MediCOR_AI-frontend"
npm run lint
npm run smoke
```

Results on 2026-09-21: backend **966 passed, 0 failed**; lint **0 errors** (4 warnings);
smoke **61 of 61** pages rendered; `npm audit` **0 vulnerabilities**.

## 17. Frontend production build

```powershell
Set-Location "$Root\MediCOR_AI-frontend\MediCOR_AI-frontend"
$env:VITE_API_URL = "https://api.your-domain.com"     # your real public API address
npm run build                                          # output: dist\
npm run preview                                        # local look at the built site
Remove-Item Env:VITE_API_URL
```

`VITE_API_URL` is compiled into the JavaScript. Changing it later means rebuilding.
For a local-only preview against `localhost:8000`: `$env:ALLOW_LOCAL_API_URL = "true"` before
`npm run build`.

## 18. Run in production (summary)

Full steps are in `FINAL_DEPLOYMENT_GUIDE.md`. In short, on the server:

```powershell
# backend .env: ENVIRONMENT=production, real CORS_ORIGINS, PUBLIC_BASE_URL, new SECRET_KEY
Set-Location "<server path>\MediCOR_AI-backend\MediCOR_AI-backend"
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

No `--reload` and no `--workers`. Put an HTTPS reverse proxy in front, and serve the frontend
`dist\` folder as static files with every unknown path falling back to `index.html`.
