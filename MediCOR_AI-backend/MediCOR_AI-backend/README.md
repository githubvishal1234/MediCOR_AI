# MediCOR AI — Backend

FastAPI + SQLAlchemy backend for the `medicor-ai` React frontend
(marketing site + product dashboard for an AI voice receptionist for clinics),
and for the voice agent in `voice-agent/`.

**There are two voice stacks in `voice-agent/`, and only one runs at a time.**
Both talk to this backend the same way — over HTTP, through
`voice-agent/backend_client.py`, holding no business logic of their own — so
nothing in `app/` knows or cares which one is answering the phone:

| | Where | Telephony | Run it with |
|---|---|---|---|
| **New** | `voice-agent/twilio_deepgram/` | Twilio Media Streams → Deepgram Voice Agent | `..\venv\Scripts\python.exe -m twilio_deepgram.run` |
| Old | `voice-agent/agent.py` | Twilio Elastic SIP → LiveKit Cloud | `..\venv\Scripts\python.exe agent.py dev` |

Running both against the same clinic number produces two call logs for one
call. `VOICE_PROVIDER` in `voice-agent/.env` states which is intended, and the
gateway refuses to start if it disagrees. See `VOICE_STACK_MIGRATION.md` at the
repository root for the whole picture, including rollback.

## Stack

- FastAPI + Uvicorn
- SQLAlchemy 2.0 against **SQL Server only**, via pyodbc. There is no SQLite
  or Postgres fallback — `app/config.py` builds an `mssql+pyodbc` URL from the
  `DB_*` settings.
- Alembic for schema migrations (see `alembic/README.md`)
- JWT auth (python-jose) + bcrypt password hashing
- Multi-tenant: every resource is scoped to an `Organization` (`org_id`)

## Setup

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env   # fill in DB_* and SECRET_KEY — see SECURITY-ROTATION.md
```

Then bring the schema up:

```bash
alembic stamp 0001_baseline   # existing database only, once
alembic upgrade head
```

A brand-new database just needs `alembic upgrade head`.

```bash
python -m app.seed      # optional: demo org "HealthLink Clinic" + sample data
uvicorn app.main:app --reload --port 8000
```

### Timezones

Each clinic has an IANA timezone (`organizations.timezone`, default `UTC`) and
every appointment time is a clinic wall-clock reading. **`tzdata` is required
on Windows** — without it every clinic silently falls back to UTC. See
`docs/TIMEZONES.md`.

API docs: http://localhost:8000/docs

Demo login (after seeding): `admin@healthlinkclinic.com` / `password123`

## Auth

OAuth2 password flow. Get a token:

```bash
curl -X POST http://localhost:8000/auth/login \
  -d "username=admin@healthlinkclinic.com&password=password123"
```

Send it on every other request: `Authorization: Bearer <token>`.

New clinics self-serve via `POST /auth/register` (creates an
`Organization` + admin `User` in one call).

Three roles: `admin`, `medical_staff`, `ai_agent` (mirrors the "Access
Control" cards in Security & Compliance). Admin-only endpoints are marked
below.

The voice agent authenticates separately, with a per-org service API key sent
as `X-API-Key` (see `get_org_context` in `app/deps.py`). Generate one from
EHR Integration -> API Keys, or `python create_service_api_key.py`.

## Endpoint map (→ which frontend page uses it)

| Router | Base path | Frontend page |
|---|---|---|
| auth | `/auth` | login/register (not in current mockups, needed for real auth) |
| dashboard | `/dashboard` | Overview |
| patients | `/patients` | Patients, Patient Profile |
| calls | `/calls` | Call Logs & Transcripts |
| appointments | `/appointments` | Appointment Manager |
| knowledge-base | `/knowledge-base` | Knowledge Base, AI Agent Settings (docs section) |
| agent-settings | `/agent-settings` | AI Agent Settings |
| ehr | `/ehr` | EHR Integration Hub |
| security | `/security` | Security & Compliance |
| billing | `/billing` | Billing & Usage |
| support | `/support` | Support |

Full request/response schemas are in `/docs` (Swagger UI) once running.

### Notable behaviors

- `GET /dashboard/stats` — `calls_handled_today`, `appointments_booked_today`,
  `resolution_rate_pct`, `staff_time_saved_hrs`, computed live from today's
  `CallLog` rows (not stored counters).
- `GET /dashboard/call-volume` — 24 hourly buckets computed from `CallLog.occurred_at`,
  for the Overview bar chart.
- `GET/POST /dashboard/live-calls` — in-progress calls. This is naturally a
  websocket/polling feed in production (telephony webhook pushes a row in,
  deletes it when the call ends); wired here as plain REST for simplicity.
- `POST /appointments/pending/bookings/{id}/verify` — approves an AI-proposed
  booking and creates the real `Appointment` row.
- `POST /knowledge-base/documents` — multipart file upload, stored under
  `uploads/<org_id>/`. Indexing is stubbed to complete synchronously; wire to
  a real embedding/indexing job queue in production.
- `POST /ehr/api-keys` — returns the plaintext key **once**; only a masked
  prefix is stored/returned afterward. Admin-only.
- Admin-only: EHR integration management, API keys, webhook config, plan
  upgrade, support ticket list.

## Data model

See `app/models.py`. Every table (except `TranscriptMessage`, which nests
under `CallLog`) carries `org_id` for tenant isolation — all router queries
filter on `current_user.org_id`, so one org can never see another's data.

## Seeding

`app/seed.py` recreates the exact demo data the frontend was designed
against (Sarah Jenkins / Michael Chen patients, the sample call + transcript,
EHR integrations, invoices, etc). Safe to re-run — it's a no-op if
"HealthLink Clinic" already exists.

## Production notes

- **There is no `DATABASE_URL` and no SQLite/Postgres option.** `app/config.py`
  builds an `mssql+pyodbc` URL from the `DB_*` settings and nothing else.
  (An earlier version of this section described swapping `DATABASE_URL` to
  Postgres — that has not been true since the SQL Server-only change.)
- Set a real `SECRET_KEY` and shorten `ACCESS_TOKEN_EXPIRE_MINUTES`, or move
  to refresh tokens. With `ENVIRONMENT=production` the app **refuses to start**
  on the default placeholder key.
- Set `TRUSTED_PROXY_IPS` if anything sits in front of the API. Left empty,
  `X-Forwarded-For` is ignored entirely (the safe default) — but behind a
  proxy that means every client shares one rate-limit bucket.
- Set `AUTO_CREATE_SCHEMA=false` once every environment has been stamped and
  upgraded with Alembic, so the schema has exactly one owner. See
  `alembic/README.md`.
- **The API cannot yet run more than one worker.** `app/rate_limit.py` keeps
  attempt counters in a module-level dict and `app/realtime.py` holds
  WebSocket connections in-process, so a second worker silently produces
  per-worker rate limits and dashboards that miss events broadcast by the
  other worker. Redis is the prerequisite for scaling out.
- `POST /knowledge-base/documents` writes to local disk, which breaks on any
  multi-instance deploy — move to S3/GCS. Indexing is stubbed to complete
  synchronously; wire it to a real job queue.
- Wire real MRN/EHR sync instead of `EHRIntegration.connected` being a
  manually-flipped boolean. `EHRIntegration.api_credentials_json` is stored
  in plaintext and needs encryption at rest before any real credential goes in.
- Rotate every credential before going live — see `../SECURITY-ROTATION.md`.
