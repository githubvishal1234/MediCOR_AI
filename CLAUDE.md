# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MediCOR AI — a multi-tenant SaaS AI voice receptionist for clinics. Three deployables:

| Part | Path (note the doubled folder names) | Stack |
|---|---|---|
| Backend API | `MediCOR_AI-backend/MediCOR_AI-backend/` | FastAPI + SQLAlchemy 2.0 + Alembic, SQL Server via pyodbc |
| Frontend | `MediCOR_AI-frontend/MediCOR_AI-frontend/` | React 19 + Vite + React Router 7 + Tailwind v4 |
| Voice gateway (**current**) | `…/voice-agent/twilio_deepgram/` | Twilio Media Streams → Deepgram Voice Agent, on FastAPI/uvicorn |
| Voice agent (legacy) | `…/voice-agent/agent.py` | LiveKit Agents worker (SIP telephony, Sarvam STT/TTS, Groq/OpenAI LLM) |

The voice agent lives *inside* the backend folder but is a separate process with its own `.env`, its own requirements file, and its own module namespace (it does `import config` / `import backend_client`, not `app.*`).

**There are two voice stacks and only one runs at a time.** They share
`voice-agent/.env`, `voice-agent/config.py` and `voice-agent/backend_client.py`,
and both reach the backend the same way — over HTTP as an authenticated
tenant, holding no business logic. Nothing in `app/` knows which is answering
the phone; the backend was not modified for the migration beyond two nullable
`call_logs` columns (`provider`, `provider_call_sid`, migration `0033`).
Running both against one clinic number produces two call logs for one call.
`VOICE_PROVIDER` in `voice-agent/.env` states which is intended, and the
gateway refuses to start if it disagrees. Full detail, including the rollback
procedure and the Hindi/Telugu limitation, is in `VOICE_STACK_MIGRATION.md`.

## Commands

Backend (from `MediCOR_AI-backend/MediCOR_AI-backend/`):

```bash
# Call the venv's python directly: this venv was copied from another folder, so its
# activate script and *.exe launchers (uvicorn.exe, pip.exe) still point at that folder.
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # docs at /docs (dev only)
.\venv\Scripts\python.exe -m app.seed      # demo org "HealthLink Clinic"; idempotent
# Full run guide: MEDICOR_AI_RUN_COMMANDS.md in the project root
```

Migrations (same directory — the DB URL comes from `app/config.py`/`.env`, never `alembic.ini`):

```bash
alembic stamp 0001_baseline   # EXISTING database, once only — do NOT run upgrade first
alembic upgrade head          # brand-new database needs only this
alembic revision --autogenerate -m "…"   # then READ the generated file before applying
```

Tests (`pytest` only; there is no `pytest.ini` or `pyproject.toml`. `tests/conftest.py` stubs the `DB_*` env vars `app/config.py` demands at import time and fixes `sys.path`; the older files still repeat that bootstrap themselves, harmlessly):

```bash
pytest tests/ -v
pytest tests/test_phase8_tenant_isolation.py -v          # single file
pytest tests/test_phase1_scheduling.py::test_name -v     # single test
```

Frontend (from `MediCOR_AI-frontend/MediCOR_AI-frontend/`):

```bash
npm install && npm run dev     # :5173; set VITE_API_URL if the API isn't on :8000
npm run build && npm run preview
npm run lint                   # oxlint, not eslint
npm run smoke                  # renders every page server-side; the only frontend test
```

Voice gateway — the current stack (from `voice-agent/`). Its requirements file
(`twilio_deepgram_requirements.txt`) deliberately installs **nothing new**: every line is already
satisfied by the backend's own `requirements.txt`, and there is no `deepgram-sdk` or `twilio`
package. It runs on the backend's `venv`, like the LiveKit worker, and shares `.env` with it.

```bash
..\venv\Scripts\python.exe -m twilio_deepgram.run   # serves :8080
curl http://localhost:8080/health                   # `problems` must be empty
```

Twilio reaches it over plain HTTPS/WSS — **no SIP trunk is involved**. Point the number's Voice
webhook at `POST {VOICE_GATEWAY_PUBLIC_URL}/voice/incoming`, which answers with
`<Connect><Stream>` naming the gateway's own websocket. `VOICE_GATEWAY_PUBLIC_URL` is the setting
worth checking first when anything is wrong: Twilio signs the URL it *called*, so behind ngrok or
a proxy a mismatch rejects every call in a way that looks exactly like a bad auth token.

Legacy voice agent (from `voice-agent/`). Still runnable, and still the only stack that speaks
Hindi and Telugu — read `VOICE_STACK_MIGRATION.md` before choosing between them. It has its own
`voiceagent_requirements.txt` and **not its own venv**, which is why the backend
`requirements.txt` duplicates every LiveKit line:

```bash
..\venv\Scripts\python.exe agent.py dev   # LiveKit agents CLI: dev | start | console
```

Its telephony is **Twilio Elastic SIP Trunking** (migrated from Vobiz). The carrier is a `.env`
value, not code: `config.SIP_TRUNK_ID` / `config.SIP_DOMAIN` are the only two carrier-bound
settings, read by `agent.py` for outbound dialling and for building the transfer URI. Inbound
never consults them — it is decided entirely by the LiveKit inbound trunk and its dispatch rule.

```bash
..\venv\Scripts\python.exe setup_twilio_trunks.py           # dry run, changes nothing
..\venv\Scripts\python.exe setup_twilio_trunks.py --apply   # create/extend LiveKit trunks
..\venv\Scripts\python.exe setup_twilio_trunks.py --restore
```

Full console walkthrough: `TWILIO-SETUP.md`. It replaced the hardcoded `fix_sip_trunk.py` /
`fix_sip_trunk_catchall.py`, which are retired to `_twilio-migration-backup/`. **That document
describes the LiveKit path only** — the new gateway needs none of it, and §8 of
`VOICE_STACK_MIGRATION.md` is its replacement. Leave the trunks provisioned anyway: they are what
a rollback needs.

`smoke_test.py` (backend root) walks every endpoint the dashboard calls against a **running**
server and real database, reporting pass/fail per screen. The pytest suite proves the logic on
SQLite; this is what proves the thing you are about to show someone actually works. Run it after
any change to config, schema, or deployment.

`merge_duplicate_patients.py` (backend root) merges patient records that are the same person recorded twice. Dry run by default, `--apply` to write, and it snapshots the rows to `_backups/` first. It **re-points all nine tables that reference `patients.id`** -- enumerated from the metadata, not listed by hand -- before archiving the loser, because the duplicates it was written for each carried half the history: one call on each Krishna row, one appointment on each smoke-test row. Archiving without merging would have left a real appointment attached to a record nobody looks at. The keeper is the oldest row and takes any field it is missing, so a merge cannot lose information; losers keep their id, so it is reversible from the snapshot.

Operational scripts in the backend root: `create_super_admin.py` (no self-registration endpoint exists for super admins — the first one must be made this way), `create_service_api_key.py`, `diagnose_api_key.py` (checks the agent's configured key against a live `api_keys` row without printing secrets).

## Architecture

### Tenancy is the organising principle

Every table except `TranscriptMessage` (nested under `CallLog`) and `Prescription`/`Interaction` (nested under `Patient`) carries `org_id`. There is **no automatic tenant filter** — every query must filter on the caller's org explicitly. The `_get_*_or_404(db, id, org_id)` helpers at the top of each router exist for this; use them rather than a bare `.get(id)`. The one confirmed cross-org IDOR in this codebase's history came from checking the parent's org but not the child's (`tests/test_phase8_tenant_isolation.py` pins it).

### Three JWT families that must never cross

`app/security.py` mints three token types, separated by a `typ` claim, and each decoder rejects the others:

- **staff** — no `typ` claim. `decode_access_token` rejects *any* typed token. Accepted by `get_current_user` / `require_admin`.
- **super admin** — `typ="super_admin"`, 8h. Only `decode_super_admin_token` / `get_current_super_admin`. Super admins are their own table (`SuperAdmin`), deliberately not a `User` row with a nullable `org_id`, so org-scoped query patterns never needed an `if`.
- **agent** — `typ="agent"`, carries `org_id`, minted by `POST /agent/resolve-tenant`. Accepted **only** by `get_org_context`.

When adding an endpoint, pick the dependency deliberately (`app/deps.py`):

- `get_current_user` — dashboard staff only.
- `require_admin` — staff with `role == admin`.
- `get_current_super_admin` — platform console (`/admin/*` routers).
- `get_org_context` — **dual auth**: `X-API-Key` (service) *or* staff JWT *or* agent token, returning `OrgContext(org_id, actor, user)`. This is what lets the voice agent and the dashboard share patient/appointment/call/knowledge-base endpoints. Anything the agent must reach has to use this.

A suspended org (`Organization.suspended`) is rejected in all four paths, so suspension cuts off in-progress calls too.

### Multi-tenant voice routing

One agent worker serves many clinics. Per call: agent extracts the **dialled** number (dispatch metadata, else the SIP participant's `sip.trunkPhoneNumber`, waiting up to `DIALED_NUMBER_WAIT_SECONDS`) → `POST /agent/resolve-tenant` with `X-Platform-Key` → backend maps it through the globally-unique `phone_numbers.e164` and returns org identity, timezone, greeting, voice profile, and a short-lived org token → agent builds a `TenantClient` **for that call only**.

The whole path is gated on `BACKEND_PLATFORM_KEY` being set in `voice-agent/.env`. Unset it and the agent reverts to single-tenant `BACKEND_API_KEY` behaviour — that is the deliberate rollback switch, and every failure mode (no number, unassigned number, backend down, resolution disabled) falls back the same way. **A call must never fail because routing is unconfigured.** Full runbook: `MULTI-TENANT-ROLLOUT.md`.

The agent holds **no business logic**. All slot math, validation, summarisation, and EHR decisions live in the backend; `voice-agent/backend_client.py` is transport only. Do not add rules there.

On the Twilio + Deepgram gateway the same routing runs, with one thing removed: the dialled
number arrives as Twilio's `To` parameter in the webhook, before the media stream opens, so the
polling `_await_dialed_number` does (`DIALED_NUMBER_WAIT_SECONDS`, which delayed the greeting on
every call) is not needed and not used. `From` likewise removes `_backfill_caller_phone`. The
fallback behaviour, and the reasoning for it, are unchanged.

### The Twilio + Deepgram + Sarvam voice gateway

`voice-agent/twilio_deepgram/` is the current voice stack: Twilio streams 8 kHz mulaw over a
websocket to a small FastAPI process, which runs the conversation and executes its tool calls
against this backend.

**It ships two engines and `VOICE_ENGINE` picks one.** They share the Twilio bridge, tenant
resolution, the tool layer, the prompt and the call record, so the choice changes how the caller
is heard and answered, not what the agent can do:

- **`pipeline` (default)** — Deepgram streaming STT -> an OpenAI-compatible LLM (Groq by
  default) -> Sarvam Bulbul TTS, orchestrated in `pipeline/orchestrator.py`.
- **`agent`** — Deepgram's managed Voice Agent: STT, turn detection, LLM and TTS behind one
  socket (`deepgram_agent.py`). Less to get wrong, but **it cannot use Sarvam.**

**Why the pipeline is the default, despite being more code.** Deepgram's Voice Agent accepts only
`deepgram` / `cartesia` / `eleven_labs` / `open_ai` / `aws_polly` as speak providers. Sarvam is
not on that list and cannot be added. Sarvam Bulbul is the only available voice that speaks
**Indian English** — Deepgram's Aura voices are American, British, Australian, Irish and
Filipino — and for a clinic whose callers are Indian that is worth orchestrating the pipeline
ourselves. `pipeline/__init__.py` states the trade-off.

**Audio crosses the whole pipeline without a resample.** Twilio sends 8 kHz mu-law, Deepgram STT
accepts it directly (`encoding=mulaw&sample_rate=8000`), and Sarvam emits it
(`output_audio_codec=mulaw`, `speech_sample_rate=8000`). `pipeline/audio.py` exists for the case
where an account cannot produce mu-law; its G.711 encoder is verified against `audioop` over all
65,536 sample values, and **it operates on 14-bit samples, not 16** — encoding at full width is
correct for positive samples and one off for negative ones, which is inaudible on speech and was
caught only by that comparison.

**`VOICE_MOCK_MODE=true` replaces exactly the three paid services** (STT, LLM, TTS) with local
fakes and leaves everything else real — webhook, signature check, media stream, tools, backend,
SQL Server, dashboard. It is how a deployment gets proved correct before anything is bought, and
`voice-agent/simulate_call.py` places a fake Twilio call to drive it. Mock mode is reported as a
startup problem and on `/health`, because a gateway left in it answers real calls with a script.

`bridge.py` holds everything difficult, and four rules there are load-bearing:

- **Barge-in is not free.** Twilio buffers agent audio and plays it at real time, so when
  Deepgram reports `UserStartedSpeaking` the sentence the agent was half-way through is still in
  Twilio's buffer. The gateway must send `{"event": "clear"}`. Without it interruption *appears*
  broken while every component upstream reports success, and nothing raises.
- **Tool calls run as their own tasks.** Awaiting a booking inside the receive loop stops agent
  audio for the duration of an HTTP round trip, which the caller hears as the line dying. The
  holding phrase (`InjectAgentMessage`, after `VOICE_TOOL_FILLER_DELAY_SECONDS`) is the same fix
  `with_filler` is on the LiveKit path, for the same measured 3–5 s of dead air.
- **Teardown drains before it finalises.** Twilio's `stop` and Deepgram's last events race, so
  cutting the agent socket the instant the caller hangs up drops the final turn from the
  transcript — silently, and the backend then summarises a conversation missing its ending. The
  drain ends on quiet (`_QUIET_SECONDS`) rather than on a timer, capped by `_DRAIN_SECONDS`.
- **The call ends exactly once.** Four routes reach teardown (caller hangs up, `end_call`, a
  transfer completes, the watchdog fires) and they must write one call record.

Two boundaries worth keeping:

- **Tools are client-side** — no `endpoint` on any schema in `tools.py`. A server-side tool would
  mean handing Deepgram the org token that reads and writes one clinic's patient records. For the
  same reason nothing secret travels in the TwiML `<Parameter>` children: the gateway resolves the
  tenant itself, from the number.
- **`tools.py` exposes 16 discrete functions, not one router.** That reverses the F9-era decision
  behind `clinic_action` deliberately, because its reason is gone: Groq counted every schema
  property against the same TPM budget as the reply, and Deepgram's managed LLMs are billed per
  minute of conversation. `tests/test_agent_clinic_action_wrapper.py` still guards the LiveKit
  path and must keep passing.

**This stack is English only, by decision, for now** — Hindi and Telugu come once English is
proven on real calls. A constraint points the same way: Deepgram's TTS cannot speak either, and
the Voice Agent API accepts only `deepgram` / `cartesia` / `eleven_labs` / `open_ai` /
`aws_polly` as speak providers, so Sarvam cannot be plugged in at all.

English-only lives in **three places that must move together** — `DEEPGRAM_STT_LANGUAGE=en`
(deliberately not `multi`, which buys code-switching the agent cannot answer in at the cost of
English accuracy), an Aura-2 `-en` voice, and a prompt whose language claim is *derived from the
TTS provider* so the two cannot disagree. `TestEnglishOnlyIsConfiguredEndToEnd` pins all three.
A caller speaking anything else is transferred to a person, by explicit prompt instruction:
attempting a language the voice cannot form produces noise, and carrying on in English at
someone who cannot follow is worse than a transfer.

Two consequences. `call_session.finalize` reports **no** `detected_language` — a value this
stack cannot honestly observe and could not act on, so sending a guess would write an unverified
fact onto a patient record (`speech_pace_requested` is still reported; that one is observed).
And `agent.py` remains the only stack that speaks Telugu, which is why it has not been deleted.
Adding the languages later is two `.env` changes and an ElevenLabs key, no code. Full detail:
`VOICE_STACK_MIGRATION.md` §17.

### Outbound webhooks (F14)

`app/webhook_service.py` POSTs clinic events to an endpoint the clinic configures on the EHR
Integration page.

**What this actually fixed.** The `webhooks` table, `GET`/`PUT /ehr/webhook`, the config screen
and a landing-page bullet reading *"Webhooks for custom infrastructure"* all existed —
and nothing ever delivered anything. `endpoint_url` was written by the clinic and then only ever
read back out in the same endpoint's response. The three events on offer
(`transcript.completed`, `intent.requires_action`, `agent.error`) were emitted by no part of the
codebase and never had been. Same class of defect as `call_logs.recording_url`: a working control
over a capability that does not exist.

Four rules:

- **`schemas.WEBHOOK_EVENTS` is the closed vocabulary**, and it is *served* to the UI as
  `available_events` rather than hardcoded there — hardcoding is exactly how the screen came to
  offer three events that did not exist. `schedule()` refuses an unknown event loudly, because a
  typo would otherwise never match a subscription and fail silently forever.
- **Every delivery is signed.** HMAC-SHA256 over the exact bytes sent, in `X-MedVoice-Signature`.
  The endpoint is public and clinic-chosen; unsigned, anyone who learned the URL could post
  fabricated appointments into their systems. Sign the raw body, never a re-serialised dict.
- **It never breaks the caller.** Delivery runs from `BackgroundTasks` after the response and
  catches everything — same posture as `ehr_service` and `notification_service`. The booking is
  already committed by then; a clinic's dead endpoint must not turn it into a 500, nor tell the
  voice agent its call update failed.
- **Retries are bounded, and only failures are stored (GAP-T4).** The original decision was one
  attempt and no queue, because a per-event delivery table would collect a row for every call a
  busy clinic takes — and it named the scheduler as the place to revisit that. It now has.
  `webhook_deliveries` holds **only deliveries that failed**: a successful send still writes
  nothing but `Webhook.last_delivery_status`, so the table is empty for a healthy endpoint and
  holds a short backlog for one that is briefly down. Settled rows are pruned.
  `jobs.retry_webhook_deliveries` drains it with backoff and gives up after
  `WEBHOOK_MAX_ATTEMPTS` — past roughly half an hour an endpoint is down rather than flapping,
  and retrying forever is the worse failure.
- **A retry resends the stored bytes verbatim.** Two reasons, both load-bearing: the signature
  is an HMAC over what went on the wire, so a re-serialised dict would fail verification; and
  the payload carries its own `delivery_id`, which a retry must reuse or a receiver cannot tell
  the third attempt from a third event. The signature itself is *not* stored — it is recomputed
  at send time, so a clinic that rotates its secret between attempts still gets something it can
  verify.

`Webhook.secret` is stored **recoverable**, unlike `APIKey.hashed_key`: signing needs the plaintext
here and the clinic needs the same value there. That is what a shared secret is; hashing it would
make signing impossible rather than safer. Minted lazily on first read, so a clinic that never uses
webhooks carries none. Migration `0018` also rewrites the fictional default — but only where the
row still holds that exact string, so a customised selection is left alone.

### Repeat-prescription requests (F9)

The agent can take a refill request by phone. **It records one; it never approves one.** That is
the entire safety argument, and it is enforced by the dependency on each route in
`routers/refills.py` rather than by prompt wording — a prompt is a request, a dependency is a rule:

| Route | Dependency | Reachable by the agent? |
|---|---|---|
| `POST /refills` | `get_org_context` | Yes — but it only ever writes `status="pending"`; there is no field on it that can approve anything |
| `GET /refills` | `get_current_user` | **No.** A list of other patients' medication requests is not the agent's business |
| `PATCH /refills/{id}` | `get_current_user` | **No.** This is the clinical decision. Do not widen it |

`PATCH` is deliberately **not** `require_admin`: in most practices the person working the
prescription queue is a nurse or receptionist acting on a standing instruction, and requiring an
admin would push the work back to paper. Who decided is recorded on the row and in the audit log
(`refill.requested` and `refill.handled` are separate codes, because "the patient asked" and "the
clinic agreed" are different facts).

Its own table rather than a status on `Prescription`, for the same reason. `medication` is free
text — what the caller *said*, not a validated drug code — and `prescription_id` is filled only on
a **case-insensitive exact match** against that patient's record. A fuzzy match would be a clinical
guess presented as a fact; null honestly says "they named something we have no record of", which
the queue surfaces as a "Not on file" chip.

The tool result handed back to the model is deliberately narrow
(`{recorded, medication, status: "pending clinic review"}`) so it has no material with which to
imply approval. Both prompts say the same thing explicitly. Migration `0016`;
`tests/test_refill_requests.py` pins the boundary.

**Adding a `clinic_action` parameter is a guarded change.**
`tests/test_agent_clinic_action_wrapper.py` freezes the schema property set, because every
property is sent to Groq on *every* call and counts against the same TPM budget as the reply. F9
added `medication` and had to argue for it: the precedent is `question`, already dedicated to the
`faq` action, and the alternative was overloading `query` — documented as the patient-search
term — on a clinical action. If that test fails, justify the addition in it rather than just
widening the set.

### Insurance was removed entirely (migration 0034)

**This product does not handle insurance.** Six columns were dropped across three tables --
`patients.insurance_provider` / `_member_id` / `_group_number`, `clinic_info.insurance_info`,
and `pre_visit_submissions.proposed_insurance_provider` / `_member_id` -- along with the API
fields, the patient form, the "Insurance & Docs" tab, the clinic-profile field, the agent's
tool and prompt wording, and an **"Insurance Verified" badge on the public landing page**.

Dropped rather than deprecated because a column the application no longer reads or writes is
the `call_logs.recording_url` shape this codebase treats as a defect -- and one of these was
read aloud to callers. Verified before dropping: **0 of 7 patients** and 0 pre-visit
submissions held any value; the single `clinic_info` row held US demo seed text describing no
real clinic. `downgrade()` restores the columns empty -- a schema rollback, not a data one.

Two consequences worth knowing. `PreVisitSubmission` still carries `proposed_phone` /
`proposed_email`, and **the load-bearing test in PF-13 now uses those** as its vehicle: a
submission must never touch the patient record, and that property is unchanged -- only the
field it is demonstrated with. And the "Insurance & Docs" tab was removed whole rather than
half-emptied, because per-patient documents were never built, so what remained would have been
a tab containing one empty state.

### Spoken-language memory (F5)

Sarvam STT detects the caller's language per turn and `agent.py` switches Sarvam TTS to match,
clamped to `en-IN | hi-IN | te-IN`. That detection used to be discarded when the call ended, so a
patient who had called ten times in Telugu was greeted in English on the eleventh.

Two columns close it: `patients.preferred_language` (the latest) and `call_logs.detected_language`
(per call — the evidence, and the basis for any future "which languages do our callers use"
breakdown). Both `String(8)`, both nullable, migration `0014`.

The flow, and the parts that are deliberate:

- **The agent reports, the backend decides.** `finalize_call` sends `detected_language`; the copy
  onto the patient happens in `update_call` (`routers/calls.py`). Which signal becomes a stored
  preference is a product rule, and the agent holds no business logic.
- **Last-spoken wins.** A relative calling in English flips it, and the patient speaking Telugu
  again flips it back. A sticky first-seen value could never recover from one wrong guess.
- **A silent call writes nothing.** `detected_language` is set only after a TTS switch actually
  succeeded, so a call where nobody spoke cannot overwrite a language we already know.
- **The value is validated against `schemas.SPOKEN_LANGUAGES`** on the way in, because it is fed
  straight back to Sarvam TTS on the next call.
- **`patient_lookup` strips it before the LLM sees it.** `PatientListOut` carries the field so the
  agent can switch TTS the moment a returning caller is identified, but that dict goes into a live
  prompt and every field costs Groq TPM budget — and the model has no use for it.

### Spoken accessibility accommodations (PF-16)

A caller can ask the agent to slow down, repeat itself, or fetch a person, and the first of
those is remembered. `patients.speech_pace_preference` / `call_logs.speech_pace_requested` are
the same pair as the spoken-language columns, in the same relationship: the call carries the
evidence, the patient carries the standing preference, and `routers/calls.py` decides which
becomes which — the agent only reports.

Four rules, all deliberate:

- **Only ever set because the patient asked.** Nothing infers it from age, call length, or how
  often someone asks for repetition. A system that guesses at disability guesses wrong in
  public, and `test_it_is_never_inferred_only_ever_asked_for` guards that.
- **Sticky, unlike the language preference.** Language is last-spoken-wins because a wrong
  guess must be recoverable; pace is not re-asserted on every later call, so a call that
  doesn't mention it must not silently speed the voice back up.
- **A closed vocabulary (`"slow"`), never a pace number.** The value is fed back into Sarvam
  TTS on the next call; an out-of-range figure is either a mid-call rejection or an
  unintelligible voice. The figure itself is `config.SLOW_TTS_PACE` in the agent, so it can be
  tuned without rewriting patient records.
- **The model never sees it.** It rides `PatientListOut` so TTS can be set up the moment a
  returning caller is recognised, and `patient_lookup` strips it — exactly as it already does
  for `preferred_language`.

`speak_slower` is a new *action value*, not a new tool property, so the frozen schema in
`tests/test_agent_clinic_action_wrapper.py` is untouched. Repetition and "I want a person" are
prompt instructions only — neither needs a tool.

### Self-service password reset (GAP-U1)

Before this there was no path at all — a locked-out staff member needed an administrator or a
direct database edit.

Two properties are load-bearing and easy to lose in a refactor:

- **The request endpoint answers identically whether or not the address is registered.** Not
  a different status, not a different message. Anything else turns it into a way to enumerate
  a clinic's staff — the same reasoning behind the single error `login` and `accept-invite`
  return. That is also why the work happens *after* the lookup rather than short-circuiting.
- **A token is short-lived and strictly single-use**, and cleared on use so a spent link is
  indistinguishable from one that never existed. It is a bearer credential for an existing
  account, where an invitation is a welcome; they deserve different lifetimes.

The reset link is derived from the first configured CORS origin, exactly as
`users._invite_url` does — the only place the backend is told where its frontend lives, and a
second setting answering the same question is a second thing to get wrong. Unset means the
email says to contact an administrator rather than carrying a dead link.

**Known limitation, stated rather than discovered:** resetting a password does *not* invalidate
existing sessions. The JWTs here are stateless with no revocation list, so a token already
issued stays valid until it expires. Tell anyone resetting because of a suspected compromise.
Fixing it means a database lookup on every request — an architectural decision, not one to make
inside a reset endpoint.

`auth.password_reset_requested` and `auth.password_reset` are separate audit codes because
"somebody asked" and "the password changed" are different facts, and a flood of the first with
none of the second is somebody probing addresses.

### Patient contact consent (PF-05)

`Patient.contact_consent` decides whether a patient may be messaged at all, and it is
**tri-state**: `"granted"`, `"declined"`, or `NULL` meaning nobody has ever asked. NULL is not
a missing value — it is what every row written before migration `0019` holds, and what the
voice agent leaves behind whenever it registers a caller without asking. Reading it as a
refusal would mute a clinic's entire existing roster the day messaging is switched on;
reading it as consent would message people who never agreed. So the reading is **not encoded
in the schema**: `notification_service.may_contact` resolves it from
`CONTACT_UNASKED_IS_CONSENT`, which defaults to the cautious answer.

**The gate lives inside `_send_patient_email` / `_send_patient_whatsapp`, not at the top of
each `notify_*` function.** That is the same reasoning as the plan gate inside
`ehr_service.get_active_integration`: a check a new caller has to remember to add is one that
eventually gets forgotten, and the failure mode — messaging somebody who asked not to be —
does not show up in testing. **Any new patient-facing message must go through those two
wrappers.** Staff mail (invitations, the weekly digest) deliberately calls the raw
`_send_email`: a receptionist is not a patient and their invitation is not marketing.

Both wrappers return **whether they actually sent**, because `send_appointment_reminder`'s
return value is what causes the job to write `reminder_sent_at`. A patient who has not
consented must read as `False` — exactly like one with no contact details. Nothing was
claimed on their behalf, so there is nothing to suppress next time.

`contact_consent_at` / `contact_consent_source` are derived in `routers/patients.py`
(`_stamp_consent`), never trusted from the request — same rule as the detected language:
which signal becomes a stored record is a product decision. The stamp only moves when the
answer actually changes, so re-saving a form does not restate when consent was given.
`contact_consent` is on `PatientDetailOut` but deliberately **not** on `PatientListOut`,
which goes straight into a live LLM prompt. `tests/test_patient_contact_consent.py` pins all
of it.

**Every outreach job reports `no_consent=N` in its run detail**, because the cautious default
plus a roster nobody has been asked about produces a correct `sent=0` that is indistinguishable
from a broken transport — the clinic then goes hunting a fault that does not exist. Same
reasoning as registering `evaluate_recent_calls` while it is switched off. The count uses
`notification_service.may_contact`, the very predicate the send wrappers enforce with, so the
figure cannot drift from the behaviour; **the gate itself has not moved** and must stay inside
`_send_patient_email` / `_send_patient_whatsapp`.
`tests/test_consent_is_a_reported_reason.py` pins both halves. Note the deployment now sets
`CONTACT_UNASKED_IS_CONSENT=false` explicitly in `.env` rather than inheriting it, so it reads
as a decision taken.

### Why there is no general "call summary" message (PF-09, scoped down)

PF-09 was originally *"send the patient a written summary of what was agreed on the call"*.
Once PF-01's confirmations existed, most of it became a second message repeating the first:
booking, rescheduling and cancelling each already send their own prompt confirmation, and
those are the actions a patient could plausibly have misheard. The rest of what the agent
records — answered a question, reviewed history, slowed its speech — is not worth a message.

So the architecture is **each fact tells the patient about itself, promptly**, rather than one
digest at the end. That is also better than a digest: a booking confirmation should not wait
for the call to finish.

What was genuinely uncovered was the refill request. The caller is told on the phone that it
has been passed to the clinic and then holds nothing until a decision arrives, possibly days
later. `notify_refill_recorded` is that receipt, and with `notify_refill_decision` (PF-11) the
loop is request → receipt → outcome.

**The receipt must not read as an approval.** That is F9's safety argument restated in another
medium: the agent records a request and can never approve one, and the tool result it sees is
deliberately narrow so it has no material with which to imply otherwise. A written
"your prescription is being prepared" would undo that from the other end. The message states
what was recorded, says a person will review it, tells the patient to keep following their
current prescription, and stops. `tests/test_missed_call_and_refill_outreach.py` asserts the
absence of every word that would imply approval.

### Post-visit follow-up (PF-14)

**The clinic writes the words, and nothing ships with a default.** That is the design, not a
gap in it: aftercare wording is a clinical statement — what to expect, when to worry, when to
come back — and a software vendor composing it to go out over a clinic's name is not a
defensible position. `ClinicInfo.follow_up_message` is NULL everywhere until a clinician fills
it in, and `jobs.send_post_visit_follow_ups` skips any clinic that has not. Same call as
`EMERGENCY_INSTRUCTION`, same reason.

`test_no_default_wording_exists_anywhere_in_the_code` guards it from both ends: the column must
carry no default, and the sender must refuse to invent content when handed none. If someone
later adds a "sensible default", that test fails.

`{name}` is the only substitution. A template language here would be a way for a settings
screen to raise exceptions inside a scheduled job — an unknown placeholder is left literal
rather than erroring.

The delay is the clinic's, counted from the appointment's **wall-clock** start rather than from
when the job runs, because `appointments.start_at` is clinic-local naive (app/timezones.py).
Everything else follows the pattern the other outreach jobs set: once per appointment, marked
on dispatch not delivery, consent respected through the usual wrappers, suspended clinics
skipped, and a bounded look-back so switching it on does not chase last month's visits.

### Pre-visit information (PF-13)

Everything reception would otherwise collect at the desk while other patients wait. Reached
through the **same appointment-link token as PF-06**, so there is one link, one page, and one
credential per appointment rather than a second token type.

**It is a submission, not an edit — and that is the whole design.** Nothing a patient types
reaches `patients`. Two independent reasons, either sufficient on its own:

- **Security.** The token is unauthenticated by design. If it could change a phone number, a
  link forwarded to a family group or lifted off a handset becomes an account-takeover path:
  redirect the messages, then use inbound replies (PF-07) to cancel or book at will.
  `test_a_submission_never_touches_the_patient_record` is the guard, and it is the most
  load-bearing test in the feature.
- **Clinical correctness.** A patient-reported medication list is not a verified one. Writing
  it into a medical record because somebody typed it into a web form puts unverified clinical
  text where verified text is expected. A nurse reading it beforehand is the value; the record
  is updated by the person who checks it.

Hence the `proposed_*` prefix on the contact columns — named so no future reader
mistakes them for the patient's actual details — and the warning on the staff panel saying
plainly that they have **not** been applied.

Three smaller rules: the patient view deliberately does **not** echo contact details back
(a forwarded link has no reason to read a phone number out); reading a submission is audited as
`patient.history_viewed`, because it contains what a patient said about their medication; and
the audit detail names the event only — never the values, or the log becomes a second copy of
clinical and financial text with longer retention.

Free-text columns are `UNICODE_TEXT`. This schema has already lost one set of transcripts to a
non-Unicode column (see 0012 and its repair in 0021), and a patient writing in Hindi or Telugu
is exactly the case that would repeat it.

### Waiting list and earlier-slot offers (PF-15)

A cancellation is pure lost capacity: the slot reopens and whoever wanted it has already been
told there was nothing and gone away. Availability was always exact and cancelling always
released the slot — nothing recorded who wanted one.

**The slot is deliberately NOT held.** `offered_start_at` / `offer_expires_at` make an offer
exclusive to one patient for a bounded time, but no placeholder appointment is created: a
"held" status would ripple into the calendar, the availability grid, conflict detection,
analytics and the stale-call reaper, and every one of those is a place for a phantom booking
to leak out. The cost is a real race — a walk-in can take an offered slot first — and it is
accepted knowingly: acceptance runs the ordinary `_validate_slot`, and on conflict the entry
returns to `waiting` rather than being lost. Losing a slot you were offered is disappointing;
an appointment nobody can see is worse. `test_losing_the_race_puts_the_patient_back_on_the_list`
asserts both halves, including that no double booking results.

**It is a sweep, not a cancellation hook.** Cancelling happens in three places — the
staff/agent endpoint, the self-service link, and an inbound reply — so a hook would have to be
remembered in all three, and the third did not exist when the first two were written. The
sweep has one call site and also catches capacity freed by everything that is not a
cancellation: hours widened, a no-show written off, an appointment moved earlier.

Other rules worth keeping: **oldest-first is a fairness rule**, not an ordering convenience;
one live entry per patient per clinician, so ringing three times does not buy three places;
`BOOK` is its own keyword because a patient may hold an offer *and* an appointment at once, and
"yes" would be genuinely ambiguous between them; and the offer message says
"first to reply gets it" rather than claiming the slot is reserved, because it is not.

Adding is `get_org_context` (the agent should be able to offer it at the end of a call where
nothing was free); reading and removing are `get_current_user` — the same split, and the same
reasoning, as repeat prescriptions.

### Patient feedback (PF-10)

The product was always thorough about its opinion of *itself* — `call_evaluations` scores every
call with a model as judge, flags hallucinations and policy breaches, and queues them for
review. Nothing ever asked the patient. `patient_feedback` closes that asymmetry, and the
number worth looking at is not the average but **the gap between what the judge thought and
what the person on the other end thought**, which is why `/quality/feedback` sits beside the
quality summary rather than replacing it.

- **The ask is one digit**, because a question needing a sentence goes unanswered — and
  because a digit cannot collide with CONFIRM / CANCEL / STOP. The inbound handler checks it
  *first*, so a "1" is never read as something else.
- **Free text they add is stored verbatim and never parsed.** Same rule as everything else in
  that webhook.
- **The cooldown is per patient, not per call**, counted from when we last *asked* — someone
  who ignored the question has told us something too. The job commits inside its loop so two
  calls from one patient in a single sweep produce one ask, not two.
- **A failed call is never surveyed.** That patient is already getting an apology from
  `contact_missed_callers`; asking them to rate it as well is close to rude.
- **Off by default**, unlike the other outreach jobs. Every other message is something the
  patient needs; this is something the clinic wants, and that is a decision to make rather than
  inherit.
- `average_rating` is `None` when nobody answered, never `0` — same rule as
  `resolution_rate_pct`. The distribution is returned alongside it, because two 1s and two 5s
  average to a 3 that describes nobody.

### Closing the loop with the patient (PF-11 / PF-12)

Two places where the product created an expectation and then went quiet.

**Refill outcomes (PF-11).** The agent tells a caller their request has been passed to the
clinic — true, and then nothing happened, so they telephoned to ask, which is exactly the call
the feature exists to remove. `notify_refill_decision` fires from the staff decision as a
background task. It carries **the outcome and nothing clinical**: an approval says where to
collect, a decline says the clinic will be in touch and stops. No reason, no dose, no advice —
the same boundary the voice agent works under, and whoever declined it can give the reason
properly.

**Missed calls (PF-12).** `jobs.contact_missed_callers` finally acts on the HV-7 callback
queue, which the reaper had been filling since F2 with nothing ever reading it. Four rules:

- **It must not touch `callback_status`.** That column is the *human* queue; a machine
  apologising is not a person having called back, and writing `"contacted"` there would
  quietly empty the list staff are meant to work. The marker is `callback_message_sent_at` —
  a different fact, in its own column.
- **A human gets the first chance** (`MISSED_CALL_OUTREACH_DELAY_MINUTES`). This catches what
  staff did not get to; it does not race them to the patient.
- **Only the recent past** (`..._MAX_AGE_HOURS`). Someone who rang four days ago has moved on.
- **The message promises nothing it cannot keep** — it apologises and invites them to call
  again. It never says anyone will call *them* back, because the queue is worked by people on
  their own schedule. `tests/test_missed_call_and_refill_outreach.py` asserts that wording.

Default **on**, matching the reminder job's reasoning rather than auto-evaluation's: the gate
that would make it act (a configured transport) is itself an opt-in, so nothing is sent until
someone sets up messaging.

### Patient messaging channels and inbound replies (PF-04 / PF-07)

**SMS is not a second integration.** It is the same Twilio Messages API, the same credentials
and the same payload as WhatsApp; the only difference is that neither number carries the
`whatsapp:` channel prefix. `_send_twilio_message(..., channel=)` is the one sender, and
`_channel_for(patient)` picks: the patient's own `message_channel`, else the deployment
default, else whichever channel is actually configured — a clinic that set up only SMS must not
silently send nothing because the default still says WhatsApp. Stripping a stray `whatsapp:`
prefix off an SMS recipient matters: Twilio accepts it and the message never arrives.

`routers/inbound_messages.py` is the **second unauthenticated router that acts on clinical
records** (the first is the self-service link). Four rules hold it:

- **Signature verification precedes reading anything.** Twilio's scheme is HMAC-SHA1 over the
  called URL plus every parameter in alphabetical order, compared with `compare_digest`. An
  unset auth token verifies nothing — it fails closed. Behind a proxy the URL this process
  sees is not the one Twilio signed, which is what `PUBLIC_BASE_URL` exists for.
- **One matching patient, or nothing happens.** An inbound message carries no tenant, so the
  phone number is the only identifier there is — which makes the single-match rule
  load-bearing rather than merely careful. It reaches only that patient's *nearest upcoming*
  appointment.
- **Free text is never guessed at.** Only CONFIRM / CANCEL / STOP, read from the first word.
  "I can't make it Friday" is a cancellation to a human and an ambiguity to a parser; acting on
  it would cancel real appointments. Same reasoning as the exact-match rule on refill
  medication names.
- **It always returns 200.** Twilio retries non-2xx, and a retry storm against an endpoint that
  cancels appointments is worse than a dropped message. Failures are logged, not signalled.

STOP writes `contact_consent="declined"` through the PF-05 fields, so the opt-out is honoured
by the same gate every outbound message already passes. `tests/test_inbound_messages.py` pins
all of it.

### Patient self-service link (PF-06 / PF-08)

`routers/appointment_link.py` is the **only unauthenticated router that acts on clinical
records**, so treat its boundary as load-bearing.

- **The token is scoped to one appointment, never to a patient.** A token resolving to a
  patient would, if forwarded to a family group or lifted off a phone, expose their whole
  record and every booking they have. `schemas.LinkAppointmentOut` is the complete list of
  what a holder can ever see, and it is an explicit field list rather than
  `from_attributes` — derived, a new `Appointment` column would start reaching anonymous
  callers the day it was added. `tests/test_appointment_self_service_link.py` asserts that.
- **First name only.** A full name lets a forwarded link confirm to a stranger that a
  specific named person is a patient of this clinic.
- **Every failure returns the same 404.** Unknown, expired, cancelled and suspended are
  indistinguishable, or the endpoint becomes an oracle for guessing tokens — the same
  reasoning as the identical error `accept-invite` returns.
- **It is not a shortcut past scheduling rules.** Rescheduling calls the same
  `_validate_slot` staff and the agent call. Do not reimplement the rules here.
- **Unlike an invite token it is not single-use** — a patient will open the link, think, and
  come back. `link_expires_at` (the appointment's own start plus a grace) does the bounding,
  and cancelling retires the token because it then has no remaining purpose.
- Actions are attributed to `audit_service.ACTOR_PATIENT`, not to staff and not to the agent.

`Appointment.confirmation_status` has no `"declined"` member on purpose: declining to attend
*is* cancelling, and `status` already records that. Rescheduling clears a confirmation — they
confirmed the old time. The column was deliberately withheld until PF-06 existed to write it;
`test_the_confirmation_column_still_has_a_writer` now guards the same rule from the other
side, so removing the router without dropping the column fails the build.

### Emergency handling in the voice prompt (PF-03)

The live prompt — built by `agent.build_instructions()`, which is now the *only* prompt in the
project — opens its rules with an `EMERGENCY` block that outranks every other instruction.
A `config.SYSTEM_PROMPT` fallback used to sit beside it and had drifted exactly as a duplicate
does: six tools that no longer existed, English and Hindi only, and no emergency block at all.
It was removed, the fallback now calls the real builder, and
`test_there_is_no_second_prompt_to_drift` fails if another prompt constant reappears. Before this, the
prompt contained no notion of urgency at all: a caller describing chest pain was taken into
the appointment-booking flow, and escalation existed only if a clinic had thought to write a
routing rule the model then happened to match.

The prompt is built by `agent.build_instructions()` at module level rather than inline in
`entrypoint()`, purely so it can be asserted on — `tests/test_agent_prompt_safety.py` pins the
emergency block's presence, its wording coming from configuration, that it precedes the booking
rules, and that the `EMERGENCY_LINE` placeholder is always substituted. Buried in a 400-line
async function needing a LiveKit room, the highest-stakes string in the product had no test at
all.

The spoken wording is **configuration, not code** (`EMERGENCY_NUMBER` /
`EMERGENCY_INSTRUCTION`): the number is territory-specific and the sentence is a clinical
decision belonging to the clinic. It deliberately does not triage, name conditions back to
the caller, or advise on what to do while waiting — that would be medical advice. **Have the
wording reviewed by someone clinically qualified before going live.**

Both are now **set for India in `voice-agent/.env`**: `EMERGENCY_NUMBER=112`, the unified
national number covering police, fire and ambulance. `108` is the alternative — a direct
medical ambulance line in most states including Telangana and AP — and is the right choice
only if a clinic prefers a dedicated ambulance line to national coverage. Both stacks read
these, so the legacy agent got the same value.

**`DEFAULT_TRANSFER_NUMBER` is deliberately unset on this machine**, so every transfer path
currently apologises rather than connecting: "I want a person", every escalation rule, the
emergency transfer offer, and the agent's own apology after a model failure. That is the safe
failure and `startup_problems()` reports it, but it is a real reduction and needs a staffed line
before going live.

When it is set, **it must not be the number patients dial to reach the AI.** That number reaches
this agent, so transferring to it loops the caller back in and the agent offers to transfer them
again -- unbounded, billed on both legs, ended only by the watchdog. A clinic's "main number" is
exactly what somebody would paste into a transfer field, so `_perform_transfer` refuses the case
outright via `twilio_api.same_number` (a suffix comparison on at least 7 digits, so formatting
and a missing country code still match). `TestTransferCannotLoop` pins it.

Two properties of that setting are easy to lose. The instruction is **quoted** in the prompt
rather than run inline, so the model can tell where the clinic's sentence ends; before that
it read as `Say This sounds like... to them, then offer to transfer`. And the prompt tells the
model to speak the number **one digit at a time**, because Sarvam's `enable_preprocessing`
normalises numbers to sound natural — right for a dosage, wrong for the number somebody dials
while having a heart attack, where "112" can become "one hundred and twelve". That is a
mitigation, not a guarantee: verify it by ear on the first real call.
`test_a_configured_emergency_number_is_actually_reachable_in_the_prompt` fails the build if
the placeholder ever comes back.

### Phone numbers are matched by digits, not by string

`GET /patients?search=` matches the phone column three ways: the original
`ILIKE '%term%'`, plus -- when the term is phone-shaped -- its **last ten digits** against the
stored number with formatting stripped (`_phone_suffix` / `_phone_digits_sql` in
`routers/patients.py`).

**This was a live bug, not a nicety.** Twilio delivers a caller in E.164 (`+918688460308`) and
every real patient in this deployment is stored as a receptionist types it (`8688460308`). The
substring never matched, so caller recognition matched nobody on any call — and worse,
`find_patient` reported returning patients as unregistered, after which the agent did the
reasonable thing and registered them again. The production database still holds two `Krishna`
rows, same number, no DOB, created eighty minutes apart. **A duplicate patient row splits
appointments, prescriptions and call history across two records.**

Three properties, all pinned by `tests/test_patient_phone_matching.py`:

- **Ten digits**, the national subscriber length in both India and the US, so every spelling of
  one line is the same question.
- **Seven-digit floor.** A shorter term stays an ordinary substring search; treating "86884" as
  a suffix would match unrelated people, and matching the *wrong* patient is far worse than
  matching none.
- **Additive and org-scoped.** It only widens what the previous clauses matched, and the org
  filter is asserted directly — widening a search is exactly the change that could reintroduce
  the one cross-org IDOR this codebase has had.

Nested `REPLACE` rather than a regex because SQL Server has none, and it is no less indexable
than the `ILIKE '%term%'` beside it, which cannot use an index either.

### Registering someone who already exists (duplicate guard)

`POST /patients` checks for the person before creating them, and **answers the two callers
differently** — the same split, and the same reasoning, as the plan limit a few lines below it in
`create_patient`:

| caller | answer |
|---|---|
| staff (`ctx.actor == "user"`) | **409** naming the existing record and its MRN, so it can be opened. They are at a screen and can act; `PatientFormModal` surfaces `ApiError.message`. |
| the agent (`service`) | **the existing patient**. Mid-call, a 4xx strands a caller who has just given their name and number. |

**A duplicate is the same name on the same line**, not the same number. Households share a
handset, so a clinic must be able to register a spouse or a child on one — `GET /patients`
deliberately returns both for one search, and the agent's "exactly one match" rule is built on
that being possible. Names are compared on case and spacing only, never fuzzily: "Krishna Kumar"
and "K. Kumar" may be one person, and deciding they are is a clinical guess presented as a fact
(the exact-match rule on refill medication names, restated).

**Archived patients are excluded.** An archived row was deliberately taken off the roster, and
`merge_duplicate_patients.py` archives the losing side of a merge — matching them would hand back
a record already merged away.

Checked *before* the plan limit, because an existing patient consumes no new capacity: a clinic at
its cap should be told the person is on file rather than sold an upgrade. Returning an existing
record writes `patient.viewed`, never `patient.created` — nothing was created, and the audit log
is the page a clinic would show an auditor. `tests/test_duplicate_patient_guard.py` pins all of it.

This closes the second half of the fault that produced two `Krishna` rows: the phone-format fix
stopped the agent *believing* returning callers were new, and this stops the registration
succeeding when it asks anyway. The prompt telling the model to look somebody up first is a
request; this is a rule.

### Caller recognition (PF-02)

The caller's number was already captured (and backfilled when it arrived late) but was
explicitly display-only, so a patient who had called ten times was still asked who they were
on the eleventh. `GET /patients` has always matched on the phone field; the two halves were
simply never joined. Three rules make it safe, and all three are load-bearing:

- **Exactly one match, or nothing happens.** Several matches means a shared handset.
- **It greets, it does not authenticate.** `patient_id` is not set from the number and the
  normal `patient_lookup` still runs. Treating a phone number as proof of identity would hand
  one family member another's appointments.
- **First name only, phrased as a question.** Reading a full name aloud to whoever picked up
  would disclose that a named person is a patient of this clinic — to someone not yet
  identified. The prompt is told the name is a guess.

### Datetime storage convention (read `app/timezones.py` before touching any date code)

Two kinds of naive datetime coexist and confusing them caused a real bug:

- `Appointment.start_at`/`end_at`, `PendingBooking.start_at` — **clinic wall-clock**, naive. Compare against `timezones.now_local(tz)`, never `datetime.utcnow()`.
- `occurred_at`, `created_at`, `started_at`, `ended_at` — **UTC**, naive (`datetime.utcnow()`). Filter "today at the clinic" with `timezones.local_day_bounds_utc(tz)`.

`tzdata` is a hard requirement on Windows; without it every clinic silently falls back to UTC. See `…/backend/docs/TIMEZONES.md`.

### Real-time

`app/realtime.py` is an in-process, org-scoped WebSocket fan-out. Routers stay synchronous and call the plain `manager.broadcast(org_id, event, data)` / `notify(...)`; it hands off to the bound event loop via `run_coroutine_threadsafe`. Browser side: `src/lib/ws.js` (token as a query param, since WS handshakes can't carry headers) wrapped by `src/lib/realtime.jsx`, mounted once in `AppShell`.

`broadcast()` is ephemeral — a disconnected client misses the event and picks up state on its next REST call. **`notify()` is not**: since HV-6 it writes a `notifications` row *before* broadcasting and puts that row's id in the payload, so the live copy and the fetched-history copy are the same object rather than duplicates. It is also deliberately non-fatal — every caller invokes it after committing, so an exception there would turn a succeeded booking into a 500.

Event names in use: `call.started`, `call.updated`, `call.ended`, `appointment.created|updated|booked|rescheduled|cancelled`, `notification`.

### Side-effect services never break the caller

`app/ehr_service.py` and `app/notification_service.py` share one philosophy: a booking must succeed even when the EHR or SMTP/Twilio is misconfigured or down. They return structured statuses (`synced` / `not_configured` / `unavailable` / `local_only`) instead of raising, and are invoked from `BackgroundTasks`. Preserve that when extending them. Routers must not make outbound EHR HTTP calls directly.

`app/call_intelligence.py` (summary, sentiment, outcome) is the rule-based **fallback**, invoked synchronously from `update_call` in `routers/calls.py` when a call reaches terminal status. `app/llm_service.py` (AI-2) then upgrades those same fields in a `BackgroundTasks` callback via Groq, gated on `GROQ_API_KEY` — unset it and only the rule-based path runs. Ordering is deliberate: a call always has *a* summary the instant it ends, and a live call never waits on a model. Neither path involves the voice agent, which has always delegated summarisation to the backend.

**Analysis is sampled deterministically; the spoken-answer path is not.** `_call_groq` sends
`temperature=groq_analysis_temperature` (0.0) plus a fixed `groq_analysis_seed`, while
`_call_groq_text` — the RAG answers a caller actually hears — keeps `groq_temperature` (0.2).
They are separate settings because the two jobs want opposite things: `outcome`, `sentiment` and
`intent` are picks from closed vocabularies over a fixed transcript, so variance is noise, and it
was real (one identical call analysed twice recorded `sentiment` as "Positive" then "Neutral").
Flattening the RAG path to 0 alongside it would have made every spoken reply robotic to fix a
dashboard column. Neither setting promises bit-reproducibility — inference is not deterministic
even at temperature 0 and Groq documents `seed` as best-effort — but the sampling variance that
caused the disagreement is gone (verified against the live API: five analyses of one transcript,
one distinct result).

Four constraints when touching this. `outcome` must stay inside `llm_service.ALLOWED_OUTCOMES`,
because the dashboard filters on that exact string. The Groq model name must exist on the
configured key (`GET {groq_base_url}/models`) — a 404 there is deliberately not retried, so every
call silently keeps the rule-based summary.

And **an outcome that claims an appointment changed requires `CallLog.appointment_id`.**
`call_intelligence.verified_outcome` is the one definition, applied by both derivation paths.
`Booked` / `Rescheduled` / `Cancelled` are assertions about rows in `appointments`, not
descriptions of a conversation — and both paths were willing to make them from talk alone: the
rule-based one by finding "book" in the transcript, the LLM one by reading that same transcript
and picking from a vocabulary containing the word. A caller who rang, asked to book, and got
nothing booked produced `outcome="Booked"` (observed on a real call here). The dashboard then
lists it as booked and nobody looks again, so the patient arrives for an appointment nobody made.

The fallback is deliberately the weaker true statement — `Completed`, a conversation happened —
rather than the next-best guess. `actions_taken` saying "Booked appointment" is *not* sufficient
on its own: it is the agent's own account, and if the appointment it names never reached the call
record then something went wrong between the two and the claim is not ours to repeat.
`Transferred to Nurse` survives unverified because it makes no claim about the appointment book.
`tests/test_outcome_needs_evidence.py` pins all of it.

### Knowledge base retrieval (AI-3)

`app/rag_service.py` answers `/knowledge-base/ask`: embed the question locally → cosine over that org's `kb_chunks` → synthesise an answer from **only** the retrieved text. `app/embedding_service.py` runs fastembed/ONNX (BAAI/bge-small-en-v1.5, 384-dim) on this machine — clinic documents and patient questions never leave it, which is deliberate and worth preserving.

**The threshold is not the safety gate.** `kb_min_similarity` (0.45) is a coarse filter only. Measured on a real handbook, the lowest answerable question scored 0.648 and the highest *unanswerable* one 0.645 — cosine cannot tell "related to this document" from "answered by this document". The **synthesiser** is the gate: it returns `INSUFFICIENT_CONTEXT`, which becomes `source: "none"`, which the agent already treats as "offer a transfer". Raising the threshold makes the system decline answerable questions without making it safer.

Vectors are `VARBINARY(MAX)` and similarity runs in numpy — this server is SQL Server 2022 Express, which has no native `VECTOR` type (2025+). There is deliberately no vector database: at clinic scale brute force is faster than a round trip, and a second datastore holding PHI-adjacent text is a liability.

`/knowledge-base/ask` returns `{answer, matched_question, source, confidence}`. The first three are the contract `voice-agent/` depends on — it speaks `answer` and keys on `source == "none"`. Adding fields is safe; renaming these breaks live calls.

### Call quality evaluation (AI-7)

`app/evaluation_service.py` scores finished calls with an LLM as judge; `/quality/*` exposes the summary, review queue and an admin-only batch run.

**Automatic evaluation exists but is OFF by default** (`QUALITY_AUTO_EVAL_ENABLED`, F10). The
scheduled `evaluate_recent_calls` job is registered either way — so `scheduled_job_runs` shows it
ticking and doing nothing, which distinguishes "switched off" from "broken" — but it returns
immediately unless the flag is set. That default is deliberate and should not be flipped casually:
every judged call is a paid model request, which is exactly why AI-7 shipped manual in the first
place. Once enabled, `quality_auto_eval_per_org_limit` caps each clinic **per run** (5 per hour at
the defaults), and suspended clinics are skipped so a suspended org cannot spend on the platform's
behalf.

Two things to preserve if you touch it. The judge is given `CallLog.actions_taken` alongside the transcript: **that is what makes hallucination detectable** — "I've booked you in" with no booking tool having run is invisible from the transcript alone. And `needs_review` triggers on a hallucination or policy violation **regardless of score**, because a call where the AI gave medical advice measured 70 with `goal_achieved=True`; a score threshold alone passes it.

Policy codes are a closed list (`evaluation_service.POLICY_CODES`) and unrecognised codes are dropped, so counts stay comparable across runs.

### The audit trail (HV-1)

`app/audit_service.py` is the **only** way to write `audit_log`. Never construct an
`AuditLogEntry` directly — the helper owns the closed action vocabulary (`audit_service.ACTIONS`,
which the Security page filters on) and the actor labelling, including impersonation.

```python
audit_service.record(db, ctx.org_id, "patient.viewed", ctx=ctx,
                     target_type="patient", target_id=patient.id,
                     status=audit_service.STATUS_LOGGED,
                     request=request, commit=True)
```

Five rules:

- **`commit=False` is the default, and it matters.** The row joins the caller's transaction so
  the entry and the change it describes land together — a rolled-back booking must not leave an
  entry claiming it happened. Pass `commit=True` only on reads, where nothing else is pending.
- **Record after the 404, before the mutation.** A failed lookup must write nothing (probing ids
  would otherwise manufacture "record accessed" rows), and a delete must be recorded *before* the
  row goes, or the erasure erases its own evidence.
- **Pass `ctx=` on dual-auth routes, `user=` on staff ones.** `ctx` is what attributes an action
  to the voice agent; hardcoding a user label on a dual-auth route silently credits the AI's work
  to a human.
- **Field names, never values.** Use `audit_service.changed_fields(before, after)`. An audit log
  holding old and new values is a second copy of the patient record, with longer retention and a
  wider audience.
- **It never raises.** Deliberately fail-open — an audit insert must not turn a booking into a
  500 — so failures surface in the application log, not the response.

Audit action codes and realtime event names look alike and are unrelated: `appointment.created`
is an `audit_service.ACTIONS` code, `appointment.booked` is a `realtime.broadcast` event. Neither
list accepts the other's strings.

Reads are logged at the **individual-record** level only (`GET /patients/{id}`, `GET /calls/{id}`
for staff); list endpoints are not, or a dashboard render would bury the reads that matter.

**Impersonation:** `POST /admin/organizations/{id}/impersonate` mints an ordinary staff token
carrying an `imp` claim; `deps._attach_impersonator` resolves it onto `user.impersonated_by`, and
entries then read `actor_type="platform"` naming both parties. `imp` is an extra claim on a staff
token, **not** a fourth token family — `decode_access_token` still rejects on `typ`, which is the
only barrier between the three.

### Plan entitlement and feature gates (HV-4)

`app/usage_service.py` holds two related things: usage metering (see the known-gaps note below)
and **what an org is allowed to do**. The rules are not obvious and getting one wrong is either
a live-call outage or free Enterprise.

Entitlement reads `Subscription` before `Plan`, and the three cases are deliberately distinct:

| State | Gated features |
|---|---|
| No `Subscription` row | **Allowed.** Most orgs have none; denying them would have locked out every existing clinic the day gating shipped. Absence of a plan is not a decision to withhold. |
| `status` in `active` / `past_due` | Whatever `Plan.ehr_access` / `knowledge_base_access` say. Both default to `False` on the model, so a plan grants them explicitly. `past_due` keeps access on purpose — cutting a clinic's phone AI off over a late invoice is a dunning decision and belongs with HV-5. |
| `status == "canceled"` | **Denied.** Not "no plan" — falling through to the grandfather rule would give a cancelled clinic more access than a paying Starter one. |

**How a gate refuses depends entirely on who is calling, and this is the part to preserve:**

- **Staff writes 403** with `usage_service.feature_error(...)`, which names the plan and what to
  do. Applied to `PATCH /ehr/integrations/{id}` and the three knowledge-base writes
  (`documents`, `sources`, `faqs`) via `_require_kb_plan`.
- **Anything the voice agent can reach degrades instead, never 403s.**
  `rag_service.answer_question` returns its ordinary `source: "none"` reply — the *same string*,
  `rag_service.NO_ANSWER_TEXT`, that a below-threshold match returns, because the agent speaks
  `answer` aloud and a caller must never hear about a billing problem.
  `ehr_service.get_active_integration` returns `None`, so sync/history/status all report the
  `local_only` state an unconfigured org already had. The EHR gate lives *inside* that one
  function specifically so a new EHR endpoint cannot forget it.
  `test_the_voice_agent_is_never_403d_by_a_plan_gate` is the guard.
- **`clinic-info` and `services` are never gated at all.** Opening hours and fees are what
  callers ask about most; an agent that cannot answer those is broken, not downgraded.

`/billing/plan` returns the real flags (`ehr_access`, `knowledge_base_access`, `user_limit`,
`patient_limit`, `subscription_status`, `over_limit`, `period_label`) so the Billing page can
describe the plan instead of asserting a hardcoded feature list. `POST /billing/upgrade` returns
**501 by design** — it used to grant Enterprise with no payment — so don't wire a button to it.

### Scheduled jobs (F2)

`app/scheduler.py` is the machinery, `app/jobs.py` is the policy. Before this, nothing could
happen on a schedule at all: every background task was a FastAPI `BackgroundTasks` callback, so
it only ran because a request arrived. SQL Server Express has no SQL Agent and there is no
separate worker service, so the scheduler lives in the API process.

**The important part is a table, not a timer.** A scheduling library keeps its timers in memory,
so a second uvicorn worker would fire every job twice — silently, since nothing errors. Instead a
job is claimed by INSERTing a row into `scheduled_job_runs`, which has a UNIQUE constraint on
`(job_name, run_key)`. Two processes racing the same tick both attempt the insert; exactly one
commits and the loser takes an `IntegrityError` and skips. The database is the leader election —
no Redis, no advisory lock, **no new dependency**.

Three things to preserve:

- **The claim must stay an INSERT race.** Turning it into SELECT-then-INSERT reopens the window
  the constraint exists to close.
- **`run_key` is a time bucket, not a timestamp** (epoch seconds ÷ interval). Re-ticking inside
  the same bucket after a restart or a slow tick is a no-op rather than a second send.
- **A failing job must not stop the loop.** Each job gets its own `Session` and its own
  try/except; the run row is marked `failed` and the next job still runs.

Register new jobs in `jobs.register_all()`. Three ship today:

| Job | Interval | Default | Gate |
|---|---|---|---|
| `reap_stale_calls` | 5 min | **on** | none — pure bookkeeping, costs nothing |
| `evaluate_recent_calls` | 1 hour | **off** | `QUALITY_AUTO_EVAL_ENABLED` — every judged call is a paid model request |
| `send_weekly_digests` | 7 days | **on** | SMTP being configured, which it is not |
| `send_appointment_reminders` | 1 hour | **on** | SMTP *or* Twilio WhatsApp being configured, neither of which is |

**`send_appointment_reminders` is where the timezone convention bites hardest.**
`Appointment.start_at` is clinic **wall-clock** naive, so "within the next 24 hours" must be
computed per clinic against `timezones.now_local(tz)` — never `datetime.utcnow()`. Getting it
wrong does not error; it shifts a UTC+5:30 clinic's window by five and a half hours, reminding
some patients twice and others not at all. `test_the_window_is_the_clinics_wall_clock_not_utc`
is the guard.

Two smaller rules there: the window starts at *now*, not just "before the cutoff", or a slot from
last week qualifies; and `reminder_sent_at` is set once a message is **dispatched**, not once
delivery is confirmed — the transports swallow their own failures by design, so arrival is not
knowable, and retrying forever against an address that will never accept mail is the worse
failure. A patient with no contact details at all is left unmarked, because nothing was claimed
and there is nothing to retry.

The reminder wording asks patients to **call back**, not to reply. Two-way confirmation needs an
inbound WhatsApp webhook this deployment does not have, and no `confirmation_status` column was
added for the same reason — a column nothing writes is the `recording_url` mistake again.

The two defaults differ for one reason: what the gate already is. Auto-evaluation's dependency
(`GROQ_API_KEY`) *is* configured, so defaulting it on would immediately start spending money and
would reverse a cost decision AI-7 had already made in writing. The digest's gate (SMTP) is *not*
configured, so nothing is sent until someone deliberately sets up mail — at which point a weekly
operations summary is expected behaviour, not a surprise. **Match that reasoning when adding a
job: default on only when the thing that would make it act is itself an opt-in.**

`reap_stale_calls` is the one that fixes a real bug: the voice
agent finalises a call from a LiveKit shutdown callback, which never runs if the worker is
killed, leaving the row `in_progress` forever — shown as live on the dashboard and counted as
*connected* by `analytics_service`, so it sits in the resolution-rate denominator permanently.
The reaper decides the outcome from evidence: a call with transcript messages becomes
`completed` (the conversation happened, only the bookkeeping was lost), one without becomes
`failed` and enters the HV-7 callback queue. **It never invents `duration_seconds`** — a
fabricated duration would flow straight into `ai_handled_minutes`.

### Call analytics (HV-9)

`app/analytics_service.py` + `routers/analytics.py` aggregate calls and appointments; nothing is
stored. Two rules:

- **Bucketing happens in the clinic's timezone, in Python, over a bounded range.** `occurred_at`
  is UTC, so a UTC axis puts a UTC+5:30 clinic's 9 AM rush at 03:30. The UTC→local conversion is
  dialect-specific and awkward in T-SQL, the range is capped, and `ix_call_logs_org_occurred`
  makes the fetch cheap — so don't "optimise" this into a `GROUP BY`.
- **`resolution_rate_pct` is `None` when nothing connected, which is not 0%.** Render it as an
  em dash. It also excludes `failed`/`no_answer` from the denominator; before HV-9 a day of
  entirely failed calls reported a healthy resolution rate. `staff_time_saved_hrs` is derived
  from AI-handled minutes, not the old `calls * 5 / 60` constant.

`/dashboard/stats` now delegates both of those figures here, so there is one definition of each.

### Frontend

Two entirely separate auth contexts sharing one browser: `lib/auth.jsx` + `lib/api.js` (`medvoice_token`) for clinics, `lib/adminAuth.jsx` + `lib/adminApi.js` (`medvoice_admin_token`) for the super-admin console. Keep them separate — an admin and a clinic session are expected to coexist in different tabs. All routes are declared in `src/App.jsx`; every API call goes through the wrappers in `lib/api.js`/`lib/adminApi.js`, not raw `fetch`.

Two conventions worth following:

- **A write handler needs a `catch`, not just a `finally`.** Several pages had `try/finally`
  around a mutation, so a rejected request cleared the spinner and showed nothing — which
  silently swallowed the KB upload allowlist's 415 and the size cap's 413 for as long as they
  have existed. `KnowledgeBase.jsx`'s single `writeError` banner is the pattern.
- **Nullable columns render nothing, not a placeholder.** The call detail panel's "Call origin"
  block (`provider` / `provider_call_sid`) is wrapped in a truthiness check because every call
  made before migration `0033` holds NULL — which means *this record predates the field*, not
  *unknown carrier*. Printing "Unknown" there would assert something nobody checked. Same reason
  `caller_phone` beside it is guarded. `npm run smoke` fails the build on a literal `undefined`
  reaching the DOM, which is the cruder version of the same rule.

- **Tab state that someone might link to belongs in the query string.** `/app/calls?tab=quality`
  and `/app/clinic?tab=doctors` are both reachable, which is what lets the dashboard's setup
  checklist deep-link to the section that fixes each outstanding step.

### The public pages may not assert what the product cannot do

`routers/public_catalog.py` (`GET /public/plans`) is the **third
unauthenticated router**, after `appointment_link.py` and
`inbound_messages.py`, and the only one that touches no tenant at all. It
exists because the landing page held its own price list, and every figure
in it had drifted from the catalogue a super admin maintains and the
platform bills against: **$199 / $499 against a real $99 / $299 / $799,
"500 minutes" against 1,000, a plan named "Professional" that does not
exist, and "1 EHR integration" listed on the one plan whose `ehr_access`
is False.** A visitor was quoted a price nobody had set.

Two boundaries on that endpoint: **only `is_active` plans** (deactivating
a plan is the platform deciding to stop selling it), and **only the
fields on `PublicPlanOut`**, which is an explicit field list rather than
`from_attributes` — derived, a new `Plan` column would start reaching
anonymous callers the day it was added. That is the same guard, for the
same reason, as `LinkAppointmentOut`.
`tests/test_public_plan_catalog.py` pins both, plus the rule that nothing
about any clinic may appear in the response.

`Pricing.jsx` **invents nothing when the catalogue cannot be read** — no
placeholder price, no "from $X". It says pricing could not be loaded and
points at a person, because a wrong price on a public page is worse than
no price. Every feature line is derived from a real column, and there is
deliberately **no "Best Value" or "Most popular" badge**: both are claims
and the catalogue carries no column supporting either.

The same audit removed a set of claims the application cannot back, all
of which are the `call_logs.recording_url` defect in prose form. Recorded
here because the pull to re-add them is constant:

- **"SOC 2 Type II"** on the trust strip. No such audit has been carried
  out. Same class as the "Insurance Verified" badge already removed, and
  the same rule as the Security page's `AES-256` / `TLS 1.3`, which are
  schema defaults in `ComplianceOut` rather than measurements.
- **Four named EHR vendors** (Epic, Oracle Cerner, athenahealth,
  Veradigm) across `Integration.jsx`, `HowItWorks.jsx` and `FAQ.jsx`, one
  with a cadence — "Scheduled sync every 15 min" — that no scheduled job
  performs. `app/ehr_service.py` says the opposite in its own docstring:
  *"No real EHR vendor is hardcoded or invented."* Three problems at
  once: partnerships that do not exist, other companies' trademarks used
  to imply them, and a connector that would not be there on the day a
  clinic on one of those systems signed.
- **"Multilingual by Default — fluent in English, Spanish, and more."**
  This stack is English only by decision, and Spanish was never on the
  list at all — the legacy stack speaks English, Hindi and Telugu.
- **A "tamper-evident audit trail."** It is an ordinary table: complete
  and attributed, which is worth saying, but with no hash chain and no
  write-once storage. The distinction is what a clinic could rely on it
  for in a dispute.
- **"Start 14-Day Free Trial."** There is no trial: no column, no expiry,
  and `POST /billing/upgrade` is 501 by design.
- **Three fabricated support statistics** — "typical response under 3
  minutes", "Avg. response: 12 min", and a green dot reading "Online now"
  that nothing checked.

**Dead controls found in the same pass**, all now working:
`Support.jsx`'s "Start Chat", "Browse Docs" and "Call Us" had no handlers
and nothing behind them, and four landing-page anchors pointed at
sections that did not exist (`#privacy`, `#terms`, and `#contact` twice).

Rather than delete the affordances, the things behind them were built:

- **`/privacy` and `/terms`** are real routes (`src/pages/marketing/`).
  Every factual claim in them was checked against the code, not copied
  from a template — a policy describing something the software does not
  do is a published commitment already being broken. Notably they state
  that **calls are transcribed and never recorded**, which is true:
  `call_logs.recording_url` has no writer anywhere. Both carry a
  `<ReviewNotice />` saying they are provisional; **remove it when a
  lawyer has been through them, and not before.**
- **`/contact`** posts to `POST /public/contact`, which records a row in
  `contact_enquiries` — **the only table in this schema with no
  `org_id`**, because whoever filled the form is not a customer, which is
  the whole reason they are writing. Attaching an org by matching
  `clinic_name` would be a guess presented as a fact. Super admins read
  them under Support → Contact enquiries; `source_ip` is stored for abuse
  triage and deliberately **never served** to that screen.
  `test_an_enquiry_that_is_written_can_actually_be_read` is the
  load-bearing test: a public write whose rows nobody reads is the
  `webhooks` defect, so **if you add another public write, add its
  reader in the same change.**
- **"Call us"** is back on the Support page and works. `SUPPORT_PHONE` /
  `SUPPORT_EMAIL` / `SUPPORT_HOURS` are blank by default and nothing
  invents a placeholder: the card renders a `tel:` link, a `mailto:`
  link, or a button to the message form saying no line is set up. Same
  reasoning as `EMERGENCY_NUMBER` — a phone number is a fact about the
  business, so it is configuration, not code.

One trap worth knowing: `CTAFooter`'s `Footer` now renders on `/privacy`
and `/terms` too, so its in-page anchor is `/#security` rather than
`#security`. A bare fragment on a route that does not own that id is the
very bug this set of pages was created to fix, and it is easy to
reintroduce by reusing a marketing component on a standalone page —
`PageShell` rewrites the nav links for the same reason.

The one control deliberately left inert is `PatientProfile.jsx`'s call
button. It is `disabled` with a title saying why, because placing a live
call from the dashboard needs an endpoint and gateway coordination that
do not exist — a feature, not a wiring gap. A disabled control that
explains itself is the honest state; do not "enable" it without building
the thing behind it.

### Taking payment (HV-5, Razorpay)

Before this there was no payment path at all: `POST /billing/upgrade`
once set `plan = "Enterprise"` and committed with **no money involved**,
so any clinic admin could grant themselves the top plan for free. That
was closed to a deliberate 501, and the Billing page told clinics to get
in touch. Meanwhile the page showed **"Visa ending in 4242"** and a next
billing date -- both seeded demo values -- so it read as though a card
were on file and a charge were scheduled. Neither was true.

**Two files, and the split is deliberate.** `app/payment_service.py` is
the Razorpay boundary and knows nothing about clinics;
`app/billing_service.py` knows nothing about Razorpay. A second provider
replaces the first and leaves the second alone.

**Three provider details were verified against Razorpay's own
documentation, not written from memory, because each fails silently when
wrong:**

| | |
|---|---|
| Orders | `POST https://api.razorpay.com/v1/orders`, Basic auth `key_id:key_secret`, `amount` in the **smallest sub-unit**, `receipt` **max 40 chars** |
| Checkout signature | HMAC-SHA256 of `order_id\|payment_id`, keyed by the **key secret** |
| Webhook signature | HMAC-SHA256 of the **raw body**, keyed by the **webhook secret** -- a *different* value |

Using one secret for the other fails every verification in a way that
looks like a broken integration rather than a swapped secret, which is
why they are separate arguments at the call site. Org ids are 64
characters, so the naive `f"org_{org_id}"` receipt is silently rejected
by the API -- `_receipt_for` truncates and salts.

**No `razorpay` SDK**, the same decision as the absent `twilio` one: this
codebase already speaks HTTP with `httpx` and already implements provider
HMAC schemes by hand.

Five rules hold the flow together, all pinned by `tests/test_payments.py`:

- **The price never comes from the client.** `CheckoutIn` carries a plan
  *name* and nothing else; the amount is read from
  `Plan.monthly_price_cents` server-side. A client that can name its own
  price is the oldest mistake in online payments, and the test asserts the
  request schema has no field through which one could arrive.
- **Fulfilment is idempotent, and that is not a nicety.** A successful
  payment reaches this application **at least twice by design** -- the
  webhook arrives at least once *and* the browser posts back. Both call
  `fulfil_payment`; the guard is the payment's own `status` on a row
  addressed by a UNIQUE `provider_order_id`. Without it a clinic gets two
  invoices for one payment.
- **The webhook is the authoritative path, not the browser.** A clinic
  that pays and closes the tab never posts back, and would be charged
  without being upgraded. `routers/payment_webhook.py` is the **fifth
  unauthenticated router**; it verifies the signature over the raw body
  **before parsing**, and returns 200 to everything afterwards because
  Razorpay retries a non-2xx and a retry storm against an endpoint that
  grants subscriptions is worse than a dropped event.
- **Verification checks ownership, not just the signature.** The
  signature is valid for whoever holds it, so without the `org_id` check
  an admin at clinic A could replay their own order id to upgrade clinic
  B. Unknown order and wrong-clinic order answer identically.
- **`payment.failed` never undoes a capture.** Razorpay can report a
  failed attempt on an order a later attempt paid; letting it through
  would strip a clinic of a plan it was charged for.

Fulfilment **mirrors `admin_plans.assign_subscription`** rather than
inventing a second path -- same Subscription write, and the same copy of
`plan.name` / `plan.voice_minutes_limit` onto the Organization, because
the clinic-facing Billing page reads those two columns. Paying online
must land a clinic exactly where a human assignment would.

`RAZORPAY_MOCK_MODE` replaces exactly the paid service and leaves the
order record, signature verification, fulfilment, the invoice and the
subscription real -- and **mock signatures use the same HMAC as live
ones**, so the verification code under test is the code that runs in
production. A verification path skipped in testing is one nobody has
tested. It is reported by `payment_service.startup_problems()` because a
deployment left in it grants plans **without taking any money**.

**Currency stopped being hardcoded.** `BILLING_CURRENCY` (INR) is served
on `PublicPlanOut` and used by the pricing page, the in-app checkout and
the invoice document, all of which printed "$" while the platform bills
in rupees. `Intl.NumberFormat` throws on an unknown ISO code rather than
guessing a symbol; the backend's `_format_money` falls back to printing
the code.

Everything blank by default is a **supported state**: `POST
/billing/checkout` answers 503 with a plain message and the page says
online payment is not switched on, which is what every clinic had before
this existed.

## Going to production

`app/deployment.py` answers one question at startup: was this process
*deployed* correctly? That is separate from whether it works, which the
test suite answers, and no test could catch it — the suite runs with
`ENVIRONMENT=development`, where every one of these values is right.

`ENVIRONMENT=production` is the **last** switch to flip, not the first.
Setting it turns on four checks, hides `/docs`, `/redoc` and
`/openapi.json`, and skips the demo-call reconciliation in `main.py`.

| Setting | Severity | What happens if it is wrong |
|---|---|---|
| `SECRET_KEY` | **fatal** | Every session token signed with a key published in this repository |
| `CORS_ORIGINS` | **fatal** | Browser blocks every API call; invitation and reset emails link to localhost |
| `PUBLIC_BASE_URL` | warning | Inbound `CONFIRM` / `CANCEL` / `STOP` all fail signature verification |
| `TRUSTED_PROXY_IPS` | warning | Every client shares one rate-limit bucket — only if a proxy is in front |

Four rules, all pinned by `tests/test_deployment_checks.py`:

- **Every problem is reported before any of them raises.** `problems()`
  returns a list and `check()` logs all of it first. A deployer who fixes
  CORS, restarts, discovers the secret key, restarts, and then discovers
  the public URL has been told three times what could have been said once.
  Same reasoning as `startup_problems()` in the voice gateway.
- **Fatal means the failure is invisible from outside.** A local-only
  `CORS_ORIGINS` does not error — the browser silently blocks the call and
  the dashboard shows a login that does nothing. It is fatal rather than a
  warning because it also breaks something that *leaves the building*:
  `users._invite_url` and the password-reset link are both built from the
  **first** origin in that list, so staff would be emailed a link to
  localhost in the clinic's name. `PUBLIC_BASE_URL` and
  `TRUSTED_PROXY_IPS` only warn because a deployment that sends no patient
  messages, or runs with no proxy, is legitimately finished without them.
- **`cors_is_local_only` compares the parsed host, never a substring.**
  `"//localhost" in origin` is the obvious way to write it and it refuses
  to start a clinic whose domain is `localhost-clinic.example.com`.
  Refusing to boot a correctly configured deployment is worse than the
  failure being prevented. `_host_of` is hand-rolled rather than
  `urlsplit` for one reason: `urlsplit` reads a schemeless
  `"localhost:5173"` as scheme `localhost` and returns no host at all, so
  the exact value this exists to catch would look like a remote domain.
- **The worker-count check stays in `app/instance.py`.** It reads argv
  rather than settings, owns the peer set the scheduler observes, and has
  an escape hatch of its own; two detectors for one problem is its whole
  design and splitting it across two files would split that argument.

**The frontend has the same failure and the backend cannot see it.**
`VITE_API_URL` is compiled *into* the bundle, not read at runtime, so a
build run without it ships a dashboard hardcoded to
`http://localhost:8000` -- deployed, every request goes to the visitor's
own machine. It presents identically to the CORS failure (a login that
does nothing) and setting the variable on the server afterwards does
nothing, because the value is already in the JavaScript. `vite.config.js`
therefore refuses to build on a local host, exempting two cases: the SSR
build behind `npm run smoke`, which renders to a string and never issues
a request, and `ALLOW_LOCAL_API_URL=true` for `build && preview` against
a local API -- named after `ALLOW_MULTIPLE_WORKERS`, and the same kind of
escape hatch, one that silences the check without making anything safe.
Every real build prints the URL it compiled in, including a correct one:
that value appears nowhere else, and a deployer should not have to unzip
a chunk to find it.

`.env.example` states the production value beside each of these in place.
There is deliberately **no** `.env.production.example`: a second template
answering the same question is a second thing to keep in sync, and this
codebase has already watched that go wrong twice (`config.SYSTEM_PROMPT`
drifting from the real prompt, and a settings screen hardcoding three
webhook events that did not exist).

## SQL Server / SQLAlchemy constraints

These are not stylistic — they are things SQL Server rejects or that break on Windows:

- **This deployment is SQL Server 2022 Express**, collation `SQL_Latin1_General_CP1_CI_AS`. No native `VECTOR` type (2025+), no SQL Agent (so a scheduler must be in-process), 10 GB per database.
- **Bare `Column(String)` compiles to `VARCHAR(MAX)`**, which SQL Server cannot index or put a `UNIQUE` constraint on. Any column that is unique, indexed, or filtered on needs an explicit length (see `Plan.name` = `String(120)`, `PhoneNumber.e164` = `String(32)`). Much of the existing schema is unbounded; new indexable columns must be bounded.
- **SQLAlchemy's generic LOB types map to deprecated SQL Server types**: `Text` → `TEXT`, `UnicodeText` → `NTEXT`, `LargeBinary` → `IMAGE`, all deprecated by Microsoft in 2005. Use the `UNICODE_TEXT` / `BINARY_BLOB` variants defined at the top of `app/models.py`, which pin `NVARCHAR(MAX)` / `VARBINARY(MAX)` on mssql while staying portable to the SQLite used in tests.
- **`== False` / `== True` in filters is intentional** (with `# noqa: E712`) for BIT columns. Don't "fix" them to `is False`.
- **`strftime("%-d")` / `"%-I"` are glibc-only** and raise on Windows. Build day/hour without them — see `_format_when` in `notification_service.py`, `_format_time_label` in `appointments.py`.
- **Schema evolution has two paths, and this deployment now uses only one.** The *code* default is still `AUTO_CREATE_SCHEMA=true` — a fresh clone keeps the safety net — but **this machine's `.env` sets it false**, so Alembic alone owns the schema here. Verified before switching: the live database already matched every declared model exactly (0 missing tables, columns or indexes), so `create_all()` and `sync_missing_columns()` were both no-ops; `smoke_test.py` then passed 53/53 against the running server with it off. **A brand-new database must now be created with `alembic upgrade head` before the app is first started** — it will no longer build its own tables. That is the point: `create_all()` racing Alembic twice produced a silently *incomplete* schema here (it built the new tables without their indexes, after which the migration had nothing left to create and was stamped as applied — `ix_appointments_link_token_hash` and `ix_users_reset_token_hash` both went missing that way). Where the flag is still on, `sync_missing_columns()` only ever *adds nullable columns* and never backfills, which is why pre-existing rows can hold `NULL` where the model says `default=False` (`tests/test_admin_org_suspended_null.py`). Anything beyond adding a nullable column requires an Alembic revision.
- **Two ways to authenticate to SQL Server.** `DB_USER`/`DB_PASSWORD` is the deployed path.
  `DB_TRUSTED_CONNECTION=true` switches to Windows auth and omits both — needed because a default
  SQL Express install is Windows-auth-only with `sa` disabled, so a SQL login cannot reach it at
  all. Windows auth only works where the app runs as a principal the server trusts.
- No SQLite or Postgres fallback exists. `app/config.py` builds an `mssql+pyodbc` URL from `DB_*` and nothing else. (The backend README's "Production notes" section still describes a `DATABASE_URL`/SQLite swap — that is stale.)

## Known gaps that affect how you write code here

Verified against the working tree on 2026-09-04. These are not backlog items — they change what correct code looks like in this repo.

- **RBAC is now enforced, and the line is deliberate.** Reads stay open to `medical_staff`; destructive actions and anything that changes what the AI says to callers are `require_admin`. When adding an endpoint to `patients`, `agent_settings` or `knowledge_base`, follow that split. **Never put `require_admin` on a `get_org_context` route** — that is how the voice agent authenticates, and gating one breaks live calls. `tests/test_qw7_rbac.py` is the guard.
- **101 `String` columns are still unbounded** (`VARCHAR(MAX)` on SQL Server), which cannot be indexed or carry a UNIQUE constraint. `org_id` itself *is* indexed now (17 indexes, migration `0005`), but give any new indexable/filterable column an explicit length. **None of the 101 is currently indexed, unique or a key**, so nothing is broken today — and retrofitting a length onto a column holding real data risks truncating it, which is why they were left alone. `tests/test_indexable_columns_are_bounded.py` fails the build the moment an unbounded column gains an index, a UNIQUE constraint, a primary key or a foreign key — catching the next one on this machine rather than on the server, the way `test_phase5_plan_column.py` had to learn the hard way.
- **The app cannot run more than one worker — and it now says so.** `rate_limit._attempts` is a module-level dict and `realtime.ConnectionManager` holds sockets in-process, so a second worker gets its own copy of both: login limits become N× more permissive than configured and dashboards miss events broadcast by the others. Redis is still the prerequisite for horizontal scaling and is not built. What changed is that the failure is no longer silent. `app/instance.py` has **two detectors, because neither is sufficient alone** — `configured_worker_count()` reads argv and `WEB_CONCURRENCY`/`GUNICORN_CMD_ARGS` at startup (fatal in production, loud in development, `ALLOW_MULTIPLE_WORKERS` suppresses the check without making anything safe); and the scheduler stamps `scheduled_job_runs.instance_id`, so a process that **loses** a claim can read who beat it — a different id is direct evidence of a second process, which argv can never see. **Two conditions, and the second was learned from a false positive:** the winning id must differ from ours, *and* the claim must have been made after this process booted. `INSTANCE_ID` is per-process, so a restart gets a new one and loses the bucket to its own predecessor — a `uvicorn --reload` server reported two "peers" against an empty machine within minutes of this shipping. A concurrent process keeps claiming *after* we start; a dead predecessor cannot. An alarm that cries wolf is worse than no alarm. `/health` carries the numbers without ever flipping `status`, because a load balancer reading "degraded" would pull the only process out of rotation. **The scheduler remains the one component that is genuinely multi-process-safe** (see *Scheduled jobs* below). If either in-process structure ever moves to Redis, delete the guard — `tests/test_single_worker_guard.py` asserts both still exist, so it fails rather than lying.
- **Copy the KB upload pattern for any new upload.** `routers/knowledge_base.py` now checks extension and content-type against an allowlist *before reading a byte*, then streams in 64 KiB chunks and aborts the moment `max_upload_bytes` is crossed. Patient documents are PHI; don't buffer a whole upload before validating it. (Storage is still local disk, which breaks on multi-instance deploys — object storage is outstanding.)
- **There are two audit logs and both are live.** `SuperAdminAuditLog` records cross-org
  platform actions (`_log` in `routers/admin.py` / `admin_plans.py` — add a call to any new one,
  before the `db.commit()` that saves the change). `AuditLogEntry` is the clinic's own, written
  through `audit_service.record` — see *The audit trail* above. They are not interchangeable: the
  first is invisible to clinics, the second is the page a clinic would show an auditor.
- **Escalation is two layers now, and the difference between them matters.** A rule carries a
  `condition_type` (GAP-T9, migration `0032`). `model` — the default, and what every rule
  written before `0032` still is — is prose in the system prompt, matched by the LLM: wide,
  paraphrase- and language-tolerant, and **not deterministic**; don't describe it as such.
  `keyword` / `unknown_caller` / `outside_hours` are evaluated by `app/escalation_service.py`
  and reached through `POST /agent/escalation-check`, which the agent consults on each caller
  turn and which returns the rule that fired. **The two coexist by design** — a clinic's English
  keyword list will not catch a caller saying the Telugu for "chest pain", so the prompt layer is
  still carrying the wider case. Deleting it to "simplify" loses calls. Server-evaluated rules
  are deliberately kept *out* of the prompt: they would cost TPM on every turn and a
  half-match could escalate a call the rule explicitly did not fire for.
  Everything here fails **open** (towards reaching a person) except `outside_hours`, which
  cannot fire for a clinic with no doctors — a fail-open reading there would transfer every call
  at every hour to a line the rule itself presumes is unstaffed. Keep prompt rule text short: it
  is in the system prompt of every call, billed against the same TPM budget as output.
  (`AuditLogEntry` and `voice_minutes_used` were both once surfaces showing data nothing wrote;
  HV-1 and HV-4 closed them.)
- **The Security page's encryption values are not measurements.** `AES-256` / `TLS 1.3` are
  schema defaults in `ComplianceOut`; nothing checks whether TDE is enabled or which TLS version
  terminated the request. The page states them as intended configuration, without ticks. Don't
  re-present them as verified, and don't add compliance claims the app cannot check —
  `hipaa_verified` and `baa_status` were both asserted rather than known until HV-1.
- **Call-log CSV export is server-side now** (`POST /calls/export`), and audits itself with
  `call_log.exported`. Two corrections to what this note used to say: the browser-built version
  did *not* leave "no audit entry" — every `GET /calls/{id}` it fetched wrote a
  `call.transcript_viewed` row — but nothing recorded that N of them were one bulk pull rather
  than a day of ordinary lookups, which is the distinction an investigator needs. The endpoint is
  `get_current_user`, not `require_admin`, because medical staff could already export from that
  page and silently narrowing it would be a regression. The Time column is the clinic's wall
  clock, not the viewer's locale, so two people's exports of the same call agree.
  `tests/test_call_export_audit.py` is the guard. Any *new* bulk export must go through an
  endpoint the same way.
- **Invoices are issued now, and the document says which kind it is.**
  `GET /billing/invoices/{id}/document` (GAP-B2) renders one row as a
  self-contained printable HTML page -- no PDF library, because a browser's
  Print-to-PDF already does that from this markup, and `Content-Disposition` is
  `inline` for the same reason. It audits itself as `invoice.downloaded`.
  **What changed with HV-5 is that there are now two kinds of row and the
  document must not describe one as the other.** A row with `payment_id`
  set is backed by a captured Razorpay payment and reads as a receipt,
  quoting the provider reference. A row without one came from `app/seed.py`,
  describes a charge this application never took, and keeps the original
  wording: charges are not processed here, this is a copy of your record
  rather than a payment receipt. **Neither ever claims to be a tax
  document** -- no tax is calculated, withheld or reported anywhere in this
  codebase. `tests/test_invoice_document.py` pins both notes and still
  watches the set of invoice writers, now `["billing_service.py",
  "seed.py"]`; a third appearing is the signal to ask the same question
  again. Also note the filename is sanitised separately from the HTML
  escaping: `html.escape` makes `<` safe and leaves a newline intact, and a
  newline in a response header is header injection. And the amount is
  rendered in the payment's own currency (falling back to
  `BILLING_CURRENCY`) -- it used to hardcode "$" while the platform bills
  in rupees, on the one page a clinic forwards to their accountant.
- **Plan limits are enforced now, and the refusal style is not uniform.** Staff writes 403;
  anything the agent can reach degrades. See *Plan entitlement and feature gates* above before
  adding a gate — a 403 on an agent-reachable route surfaces mid-call as a failed tool call.
- **Voice-minute usage is computed, never stored.** `app/usage_service.py` derives it from `CallLog` per billing month. `Organization.voice_minutes_used` is a **stale legacy column read by nothing** — don't wire anything to it, or the clinic and super-admin views drift apart again (that was finding C8). The billing period is a calendar month in the *clinic's* timezone, not UTC.
- **Free text is Unicode now — keep it that way.** All 21 `Column(Text)` declarations became `UNICODE_TEXT` (migration `0012_unicode_text_columns`), so `transcript_messages.text`, `agent_settings.greeting_script` and the rest are `NVARCHAR(MAX)` instead of the non-Unicode `TEXT` that silently stored `?` for anything outside the collation codepage. **Text written before that migration is not recoverable** — the original bytes are gone, so early Hindi/Telugu transcripts still hold `?`. Use `UNICODE_TEXT` (or `Unicode`) for any new column that can hold text a human typed or spoke; `tests/test_unicode_text_columns.py` fails the build if a plain `Text`, `NTEXT` or `IMAGE` column reappears, which is the one dialect trap SQLite cannot surface.

## Tests

All 53 test files run against throwaway **in-memory SQLite** with `poolclass=StaticPool` (TestClient uses a threadpool; without StaticPool each thread gets its own empty `:memory:` database) and a `get_db` dependency override. They never touch the configured SQL Server. `rate_limit._attempts` is module-level and in-process — clear it in fixtures or one test's logins trip another's 429.

**The frontend has one automated check: `npm run smoke`.** There is no test runner installed
and it deliberately does not add one — `smoke/render-smoke.jsx` renders all 31 pages plus the
shared primitives with `react-dom/server` (already a dependency) via Vite's SSR build (already
how the app compiles), and exits non-zero on a failure.

It is narrow: effects do not run, so nothing talks to the API, and it asserts that pages render
rather than that they render *correctly*. But it catches what a successful build does not — a
component that throws on its first frame, a missing provider, a hook used against its contract,
and text that was never meant to be rendered. It also fails on `[object Object]`, a literal
`undefined`/`NaN`, and a stray `className=` in the output; that last one is why it exists,
having found eleven attributes stranded outside their tags, one of them inside the landing
page's main headline.

**A green test run does not prove SQL Server compatibility.** Every constraint in the section above — `VARCHAR(MAX)` indexing limits, BIT-column `== False` comparisons — is invisible under SQLite. `test_phase5_plan_column.py` exists precisely because a `UNIQUE` on an unbounded `String` passed the suite and then failed at table creation on SQL Server. Reason about the dialect explicitly; the tests won't catch it for you.

`tests/test_phase2_agent_client.py` runs the real `voice-agent/backend_client.py` against a real uvicorn server; `tests/test_phase2_agent_module.py` imports the real `agent.py` with only the vendor plugin modules stubbed.

**Nothing in the suite may touch the configured database — including code that opens its own
session.** Several services deliberately open a `SessionLocal` of their own rather than taking the
request's (`realtime._persist_notification`, `llm_service`, `evaluation_service`, `ehr_service`,
`notification_service`, `scheduler`), which is right in production but bypasses the `get_db`
override here. The autouse `_no_real_database` fixture in `conftest.py` rebinds `SessionLocal` —
both the `app.database` attribute and the module-level copies in the three services that imported
it eagerly — to an **empty** SQLite engine.

Empty on purpose, and bound rather than stubbed: `SessionLocal()` does not connect (sessions are
lazy), so `ehr_service` puts `db = SessionLocal()` *outside* its try block. A stub that raised on
construction would escape handlers written to contain a failure that only ever happens on the
first query. An empty database puts it back there, as an ordinary `SQLAlchemyError` the services
already swallow. A test that needs one of these to genuinely succeed rebinds `SessionLocal` to its
own factory — see `test_hv6_hv7_notifications_callbacks.py`.

That fixture is also why the suite is fast. Each such call previously waited out a ~30-second ODBC
login timeout against `DB_SERVER=unused`; a full run took **20 minutes** and one file that
finalises a handful of calls took seven on its own.

**A full run takes roughly three minutes.** Current count: 966 tests, all passing (verified 2026-09-21).

## Repo notes

- `SECURITY-ROTATION.md` — live credentials were once committed in plaintext; rotation is **still outstanding** and can only be done by the owner in provider consoles. Read before touching anything credential-related.
- `MediCOR-AI-Roadmap.md` — a large analysis/planning document, carrying a **status pass verified against the working tree on 2026-09-04**. Every `### ` item heading is tagged `DONE` / `PARTIAL` / `OPEN`, and the "Status of this document" section near the top has an evidence table. **Read the tag before the body**: item bodies are the original 2026 prose describing the problem *as it was*, with inline `**[SUPERSEDED]**` notes where a claim has since become false. 16 of 32 feature items are done, 2 are partial (HV-4 and AI-7 — in both cases what remains is a decision, not typing), 14 are untouched; fixes C2/C3/C8/C9 are closed and **C12 credential rotation is not**. If you implement something from it, update its tag in the same change — the tags are the authority, the prose counts drift.
- The frontend README is **no longer stale** — it was corrected. Its "Notes" section now states
  outright that the dashboard runs on live API data and that `src/lib/data.js` was removed once
  the pages were wired to the backend. Verified: that file does not exist.
- **This copy (`D:\claude_pro_inspired\MediCOR_AI`, 2026-09-21) has NO `.git` folder** -- it was restored from a zip. The history described next lived in an earlier copy. Before `git init` here, repeat the secret checks below. Originally: this tree was a git repository as of the initial import, rooted at the parent of both projects (not inside either). It has no remote: pushing it anywhere is the owner's decision and belongs *after* the rotation in `SECURITY-ROTATION.md`, not before. `.gitignore` files exist at the root and in both projects covering `.env`, `uploads/` (may contain PHI), `venv/`, `node_modules/`, `dist/`, and `*.bak-*` / `_backups/` in all three (the per-project ones previously covered only `.env.*`, so a `git init` inside either project would have tracked `agent.py.bak-filler`). **The first commit was verified clean before it was made**, not audited afterwards, because git history is hard to purge: every secret-named value in the two live `.env` files was searched for across all 290 staged files, the staged files were scanned for provider-shaped credentials, and the two committed PDFs were decompressed stream by stream first — a plaintext grep cannot see inside one. Repeat that before adding any remote.
- **`_backups/` holds every ad-hoc backup copy**, collected from four different naming
  conventions scattered across both projects — which is why searching for one kind kept missing
  the others. Nothing loads from it. Two things make it worth knowing about: each `agent.py.bak-*`
  was verified to be a strict ancestor of the live file before moving (nothing is stranded there),
  and **five of the files are copies of `.env`**. Those five hold two credential classes that
  appear in **no live configuration** — the retired Vobiz SIP login, and a `DB_USER`/`DB_PASSWORD`
  pair the live backend does not use because it authenticates with `DB_TRUSTED_CONNECTION`. A
  rotation pass built around reading the live `.env` files would conclude there was nothing to do.
  `SECURITY-ROTATION.md` now carries the full inventory; it previously cited two paths that were
  wrong and omitted one file entirely. Deleting these is the owner's call and belongs *after*
  rotation, not before — and one of them, `agent.py.bak-filler`, is what `agent.py` was recovered
  from after a patch script corrupted it. Version control is the actual fix.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
