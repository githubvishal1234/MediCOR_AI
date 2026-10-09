# Multi-tenant rollout (Phase 2)

One voice-agent deployment can now answer for many clinics. This is the
cutover runbook.

## What changed

Before, the agent worker held a single `BACKEND_API_KEY` in its `.env`,
and `backend_client` baked it into one module-global HTTP client. Whichever
number a patient dialled, their record was written into that one
organization. One deployment meant one clinic.

Now:

1. A `phone_numbers` table maps each number to the clinic that owns it.
   Super admins assign numbers at **Platform Admin → Phone Numbers**.
2. At the start of a call the agent works out which number was dialled and
   asks `POST /agent/resolve-tenant`, authenticating with a platform key.
3. The backend returns that clinic's identity, timezone, greeting and a
   short-lived **org-scoped token**.
4. The agent builds a client **per call** from that token. Two concurrent
   calls for two clinics cannot mix, because no tenant state is shared.

The agent still holds no business logic. It resolves who it is, then uses
the same patient/appointment/call endpoints as before.

## The rollback switch

Everything above is gated on `BACKEND_PLATFORM_KEY` being set in
`voice-agent/.env`.

**Unset it and the agent behaves exactly as it did before Phase 2** — it
uses `BACKEND_API_KEY` for every call, does no resolution, and does not
even wait for the dialled number. Same fallback applies per call whenever
a number is unassigned, resolution fails, or the backend is unreachable.
A call is never lost to a routing problem; it is handled by the default
clinic instead.

---

## Cutover

### 1. Generate the platform key

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

This one credential can resolve **any** clinic. Treat it like `SECRET_KEY`.

Set it in **both** places — they must match:

- backend `.env`: `AGENT_PLATFORM_KEY=<value>`
- `voice-agent/.env`: `BACKEND_PLATFORM_KEY=<value>`

Leave `BACKEND_PLATFORM_KEY` unset until step 3 is done.

### 2. Apply the migration

```bash
alembic upgrade head
```

Adds `phone_numbers` (revision `0004_phone_numbers`).

### 3. Assign your numbers

Platform Admin → **Phone Numbers** → *Assign number*, once per clinic
line. Spacing and the leading `+` don't matter — numbers are normalised,
so `+91 98765 43210`, `919876543210` and `sip:+919876543210@trunk` all
resolve to the same clinic.

Do this for **every** number you serve before step 4. Any number left
unassigned falls back to the `BACKEND_API_KEY` clinic.

### 4. Enable routing

Set `BACKEND_PLATFORM_KEY` in `voice-agent/.env` and restart the worker.

### 5. Verify with a real call

Call one clinic's number and confirm in the worker log:

```
Tenant for this call: <TenantContext Clinic Name (org-id)>
```

`<TenantContext single-tenant fallback>` means resolution didn't happen —
see troubleshooting below. Then check the call appears under that clinic's
Call Logs, and that a patient registered on the call lands in that clinic.

Repeat for a second clinic before considering the cutover done.

---

## Which number identifies the tenant

The number the caller **dialled** — not the number they called from.
Routing on caller ID would put a patient through to whichever clinic
happened to match their own number.

Sources, in order:

1. Dispatch metadata: `dialed_number`, `to_number`, `called_number`, or
   `trunk_phone_number`. A LiveKit SIP dispatch rule can set this, and
   it needs no waiting — **prefer it**.
2. The SIP participant's `sip.trunkPhoneNumber` attribute.

For inbound calls the participant often joins a moment after the agent
job starts, so the agent waits up to `DIALED_NUMBER_WAIT_SECONDS` (1.5s
default) for that attribute. This runs before the greeting, so it delays
answering slightly. Setting the number in dispatch metadata avoids the
wait entirely.

---

## Per-clinic behaviour now live

Both were configurable at `/app/agent` long before Phase 2 and were read
by nothing — every clinic got the same hardcoded greeting and voice.

- **Greeting** — `AgentSettings.greeting_script` is spoken at the start of
  the call. Falls back to the previous hardcoded line when unset.
- **Voice** — `AgentSettings.voice_profile` is matched against known TTS
  voice names (anushka, aravind, amartya, dhruv, alloy, echo, shimmer).
  Anything unrecognised leaves the existing env/default voice alone, so a
  clinic cannot break its own audio by typing free text.

Explicit dispatch metadata (`voice_id`, `model_provider`) still wins.

---

## Onboarding a new clinic

Fully self-serve now, except step 3:

1. The clinic signs up at `/login` → **Create one** (name, admin, timezone).
2. They add doctors, opening hours, contact details and services in the
   dashboard. `GET /onboarding/status` drives a checklist of what's still
   missing.
3. **You** assign their phone number in Platform Admin.

Registration deliberately creates an **empty** clinic — no placeholder
doctors, services or fees. The agent reads those fields out to patients,
so seeded filler would have it quoting a price nobody set or offering
appointments with a doctor who doesn't exist.

---

## Troubleshooting

**Log says `single-tenant fallback`.** Resolution didn't happen. Check:
`BACKEND_PLATFORM_KEY` set in `voice-agent/.env` and matching
`AGENT_PLATFORM_KEY`; the number assigned and *Routing* (not *Paused*);
the dialled number actually reaching the agent (raise
`DIALED_NUMBER_WAIT_SECONDS`, or set it in dispatch metadata). The worker
logs the reason at WARNING for each case.

**`503` from `/agent/resolve-tenant`.** `AGENT_PLATFORM_KEY` is unset on
the backend. The agent treats this as "use the fallback".

**`401`.** The two keys don't match.

**Calls land in the wrong clinic.** Check for a duplicate assignment —
`e164` is globally unique, so the API refuses a second assignment and
names the current owner.

**Suspending a clinic** cuts off calls immediately, including ones already
in progress: resolution refuses, and the org-scoped token stops working
mid-call.

---

## Security notes

- The platform key is **not** a tenant credential. It cannot read any
  clinic's data — it only resolves numbers and mints tokens. Presenting it
  as `X-API-Key` is rejected.
- Agent tokens carry `typ=agent` and are accepted **only** by
  `get_org_context`. They cannot authenticate `/auth/me`, doctor
  management, or anything else behind `get_current_user` / `require_admin`
  — the same barrier that keeps super-admin tokens out of clinic routes.
- Tokens are short-lived (`AGENT_TOKEN_EXPIRE_MINUTES`, default 120) and
  scoped to one org. Nothing long-lived is handed to the worker.
- `/agent/resolve-tenant` is rate-limited, compares the key in constant
  time, and returns a flat 404 for unknown numbers so it cannot be used to
  enumerate which clinics a deployment serves.
