# Voice stack migration — LiveKit → Twilio + Deepgram + Sarvam

Status: **built, tested, and proved end to end against the real backend
and SQL Server using mock speech services. It has not yet taken a real
phone call, and no API keys are configured.** Nothing in the old stack
has been deleted.

| | Old | New |
|---|---|---|
| Telephony | Twilio Elastic SIP Trunking → LiveKit Cloud SIP | Twilio Media Streams (websocket, no SIP) |
| Orchestration | `livekit-agents` worker | this gateway (`twilio_deepgram/pipeline/`) |
| STT | Sarvam `saaras:v3` | Deepgram `nova-3` streaming, `language=en` |
| LLM | Groq `openai/gpt-oss-20b` | **same** — any OpenAI-compatible endpoint |
| TTS | Sarvam `bulbul:v3` (en/hi/te) | **same** — Sarvam `bulbul:v3`, `en-IN` |
| Turn-taking | Silero VAD + LiveKit endpointing | Deepgram `endpointing` + `vad_events` |
| Tools | one `clinic_action` router, 14 actions | 16 discrete functions |
| Business logic | FastAPI + SQL Server | **unchanged** |
| Dashboard | FastAPI + WebSocket | **unchanged** |

## Why this is a pipeline and not Deepgram's managed Voice Agent

Deepgram's Voice Agent API puts STT, turn-taking, the LLM and TTS behind
one socket, which is less to build and less to get wrong. **It cannot
use Sarvam.** Its speak-provider list is `deepgram`, `cartesia`,
`eleven_labs`, `open_ai` and `aws_polly`, and that is the whole list.

Sarvam Bulbul is the only voice among those available that speaks Indian
English. Deepgram's own Aura voices are American, British, Australian,
Irish and Filipino — there is no Indian-English option. For a clinic
whose callers are Indian, that is the difference between sounding like
the practice and sounding like an overseas call centre.

So the gateway ships **both engines**, and `VOICE_ENGINE` picks:

    VOICE_ENGINE=pipeline   Deepgram STT -> LLM -> Sarvam TTS   (default)
    VOICE_ENGINE=agent      Deepgram's managed Voice Agent, one socket

They share everything else — the Twilio bridge, tenant resolution, the
tool layer, the prompt, the call record — so the choice changes how the
caller is heard and answered, not what the agent can do.

---

## 1. Current architecture (what exists today)

```
Patient phone
  → Twilio Elastic SIP Trunk
  → LiveKit Cloud (inbound SIP trunk + dispatch rule)
  → voice-agent/agent.py            [livekit-agents worker, agent_name="outbound-caller"]
        VAD    silero
        STT    Sarvam saaras:v3, language="unknown" (per-turn detection)
        LLM    Groq via the openai plugin, single clinic_action tool
        TTS    Sarvam bulbul:v3, switched per turn to en-IN/hi-IN/te-IN
  → voice-agent/backend_client.py   [httpx, one TenantClient per call]
  → FastAPI  (localhost:8000)
  → SQLAlchemy → SQL Server 2022 Express
```

Per call: parse dispatch/room metadata → poll the SIP participant for
`sip.trunkPhoneNumber` (up to `DIALED_NUMBER_WAIT_SECONDS`) → `POST
/agent/resolve-tenant` → org token, greeting, voice profile, escalation
rules → `POST /calls` → `AgentSession.start()` → greeting → turn loop →
LiveKit shutdown callback saves the transcript and `PATCH`es the call.

The backend contains **no LiveKit code at all** — verified by grep;
LiveKit appears only in comments. That clean boundary is why this
migration touches so little of the application.

## 2. New architecture

```
Patient phone
  → Twilio  (number's Voice webhook → POST /voice/incoming)
  → voice-agent/twilio_deepgram/   [FastAPI + uvicorn, its own process, :8080]
       │  answers with <Connect><Stream url="wss://…/voice/stream">
       │
       │   bridge.py  ── Twilio media stream, 8 kHz mu-law, base64 in JSON
       │        │
       │        ▼  pipeline/orchestrator.py
       │   ┌─────────────────────────────────────────────────┐
       │   │  LISTEN   wss://api.deepgram.com/v1/listen           │
       │   │           nova-3, encoding=mulaw, sample_rate=8000     │
       │   │           vad_events -> BARGE-IN                       │
       │   │           endpointing + utterance_end -> end of turn    │
       │   │                        │                                │
       │   │  THINK    POST {LLM_BASE_URL}/chat/completions        │
       │   │           Groq openai/gpt-oss-20b, 16 tools            │
       │   │                        │                                │
       │   │                        ▼  tool calls                   │
       │   │           tools.py → backend_client.TenantClient       │
       │   │                  → FastAPI → SQLAlchemy → SQL Server   │
       │   │                        │                                │
       │   │  SPEAK    wss://api.sarvam.ai/text-to-speech/ws       │
       │   │           bulbul:v3, en-IN, mulaw @ 8000 Hz            │
       │   └─────────────────────────────────────────────────┘
       └── mu-law straight back to Twilio, no conversion at all
```

Sarvam emits mu-law at 8 kHz, which is exactly what Twilio accepts, so
audio crosses the whole pipeline without a single resample. If an account
cannot produce mu-law, `SARVAM_TTS_CODEC=linear16` makes
`pipeline/audio.py` convert instead — its encoder is verified against
the standard library's over every one of the 65,536 sample values.

With `VOICE_ENGINE=agent` the middle block is replaced by one socket to
`wss://agent.deepgram.com/v1/agent/converse`, and the voice becomes
Deepgram's.

### What got structurally simpler

* **No SIP.** Twilio streams audio to the gateway directly. The LiveKit
  inbound/outbound trunks, the dispatch rule, `SIP_TRUNK_ID`,
  `SIP_DOMAIN` and `setup_twilio_trunks.py` all become irrelevant.
* **The dialled number arrives with the call.** Twilio's `To` parameter
  identifies the clinic in the webhook, before the media stream opens.
  `_await_dialed_number`'s polling — which delayed the greeting on every
  call — is gone.
* **The caller's number arrives with the call too.** `From` is present
  from the first byte, so `_backfill_caller_phone`'s background poll is
  gone as well.
* **One hop fewer.** LiveKit Cloud is no longer in the audio path.

### What deliberately did not change

The backend. Every tool is a call into a `backend_client` method that
already existed. Multi-tenancy still runs through
`POST /agent/resolve-tenant` and still falls back to the single-tenant
API key on every failure path. The agent still holds **no business
logic**.

---

## 3. Files changed

| File | Change |
|---|---|
| `app/models.py` | `CallLog.provider`, `CallLog.provider_call_sid` |
| `app/schemas.py` | Those two on `CallLogBase` (create), `CallLogUpdate` (patch) and `CallLogDetailOut` (read) — deliberately **not** on `CallLogListOut`, which the agent's own lookups feed into a live prompt |
| `voice-agent/backend_client.py` | `create_call_log()` gained optional `provider` / `provider_call_sid`. Shared by both stacks; the LiveKit path sends neither, which is honest — it has no carrier id to report |
| `voice-agent/.env.example` | Rewritten current-stack section, with a `PASTE YOUR API KEYS HERE` block; shared Sarvam keys de-duplicated so a later definition can no longer silently override an earlier one |

That is the complete list. **No router, no service, no existing test and
no frontend file was modified.**

## 4. Files added

| File | What it is |
|---|---|
| `alembic/versions/0033_call_provider_sid.py` | The two columns |
| `voice-agent/twilio_deepgram/__init__.py` | Package docstring / architecture note |
| `voice-agent/twilio_deepgram/settings.py` | Env → Deepgram provider blocks |
| `voice-agent/twilio_deepgram/prompt.py` | The system prompt, ported from `agent.build_instructions()` |
| `voice-agent/twilio_deepgram/tools.py` | 16 function schemas + `ToolRunner` |
| `voice-agent/twilio_deepgram/deepgram_agent.py` | Managed Voice Agent socket (the `agent` engine) |
| `voice-agent/twilio_deepgram/twilio_api.py` | Signature verification, TwiML, REST |
| `voice-agent/twilio_deepgram/call_session.py` | Tenant, call log, transcript, finalise |
| `voice-agent/twilio_deepgram/bridge.py` | The audio bridge, engine choice, call lifecycle |
| `voice-agent/twilio_deepgram/server.py` | The three endpoints + `/health` |
| `voice-agent/twilio_deepgram/run.py` | Entry point with a startup config check |
| **`voice-agent/twilio_deepgram/pipeline/audio.py`** | mu-law/PCM/WAV conversion, verified against `audioop` |
| **`voice-agent/twilio_deepgram/pipeline/stt.py`** | Deepgram streaming transcription |
| **`voice-agent/twilio_deepgram/pipeline/llm.py`** | Any OpenAI-compatible chat endpoint, with tools |
| **`voice-agent/twilio_deepgram/pipeline/tts.py`** | Sarvam Bulbul streaming synthesis |
| **`voice-agent/twilio_deepgram/pipeline/orchestrator.py`** | Turn-taking, barge-in, the tool loop |
| **`voice-agent/twilio_deepgram/pipeline/mocks.py`** | All three faked, for running with no API keys |
| **`voice-agent/simulate_call.py`** | Places a fake Twilio call at the gateway |
| `voice-agent/twilio_deepgram_requirements.txt` | Documents that nothing new is needed |
| `tests/test_twilio_deepgram_gateway.py` | 59 tests — the pieces |
| `tests/test_twilio_deepgram_call_flow.py` | 27 tests — a whole call, managed-agent engine |
| **`tests/test_voice_pipeline.py`** | 43 tests — audio, barge-in, turns, tools, mocks |
| `VOICE_STACK_MIGRATION.md` | This file |

## 5. Files deprecated (still present, still working, **do not delete yet**)

| File | When it can go |
|---|---|
| `voice-agent/agent.py` | After the new stack has handled real calls for long enough to trust, **and** Hindi/Telugu have been added here (§17) — until then it is the only stack that speaks them |
| `voice-agent/setup_twilio_trunks.py` | With `agent.py` — it provisions LiveKit SIP trunks the new stack never uses |
| `TWILIO-SETUP.md` | With `agent.py`. It documents Elastic SIP Trunking; §8 below replaces it |
| `livekit-*` in `requirements.txt` / `voiceagent_requirements.txt` | With `agent.py`. **Check first**: `tests/test_phase2_agent_module.py` and `tests/test_phase2_agent_client.py` import the real `agent.py` against the real `livekit.agents` package |

`voice-agent/backend_client.py` and `voice-agent/config.py` are **not**
deprecated — both stacks use them.

---

## 6. Environment variables

All new variables live in `voice-agent/.env`. **The backend needs no new
configuration**; it already has `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`
for patient messaging, and the two new columns need no settings.

### Required

```env
VOICE_PROVIDER=twilio
VOICE_AGENT_PROVIDER=deepgram

VOICE_GATEWAY_PUBLIC_URL=https://your-subdomain.ngrok-free.app   # no trailing slash

TWILIO_ACCOUNT_SID=AC…
TWILIO_AUTH_TOKEN=…
TWILIO_PHONE_NUMBER=+91…          # already present; reused as outbound caller id

DEEPGRAM_API_KEY=…                # already present
```

`VOICE_GATEWAY_PUBLIC_URL` causes two different confusing failures when
it is wrong, so it is worth stating plainly:

* Twilio signs **the URL it called**. Behind ngrok or a proxy that is not
  the URL this process sees, so every signature fails and every call is
  rejected — which looks exactly like a bad auth token.
* The `<Stream>` element needs an absolute `wss://` URL. Without one the
  caller is answered and then hears silence.

### Optional, with the shipped defaults

```env
VOICE_GATEWAY_HOST=0.0.0.0
VOICE_GATEWAY_PORT=8080
VOICE_GATEWAY_ROUTE_PREFIX=/voice
TWILIO_VALIDATE_SIGNATURE=true

DEEPGRAM_STT_MODEL=nova-3
DEEPGRAM_STT_LANGUAGE=en
DEEPGRAM_LLM_PROVIDER=open_ai
DEEPGRAM_LLM_MODEL=gpt-4o-mini
DEEPGRAM_LLM_TEMPERATURE=0.2
DEEPGRAM_TTS_PROVIDER=deepgram
DEEPGRAM_TTS_MODEL=aura-2-thalia-en

VOICE_TOOL_FILLER_DELAY_SECONDS=1.2
VOICE_TOOL_FILLER_PHRASE=One moment.
VOICE_MAX_CALL_SECONDS=1800
```

Plus, unset by default: `DEEPGRAM_AGENT_URL`, `DEEPGRAM_STT_VERSION`,
`DEEPGRAM_STT_LANGUAGE_HINTS`, `DEEPGRAM_STT_KEYTERMS`,
`DEEPGRAM_STT_EOT_THRESHOLD`, `DEEPGRAM_STT_EOT_TIMEOUT_MS`,
`DEEPGRAM_LLM_ENDPOINT_URL`, `DEEPGRAM_LLM_ENDPOINT_HEADERS`,
`DEEPGRAM_LLM_PROVIDER_EXTRA`, `DEEPGRAM_TTS_VERSION`,
`DEEPGRAM_TTS_MODEL_ID`, `DEEPGRAM_TTS_VOICE`, `DEEPGRAM_TTS_LANGUAGE`,
`DEEPGRAM_TTS_ENDPOINT_URL`, `DEEPGRAM_TTS_ENDPOINT_HEADERS`,
`DEEPGRAM_TTS_PROVIDER_EXTRA`, `DEEPGRAM_CONNECT_TIMEOUT_SECONDS`, `TWILIO_API_BASE`,
`TWILIO_HTTP_TIMEOUT_SECONDS`. Every one is documented in
`voice-agent/.env.example`.

Reused from the existing agent config: `BACKEND_API_URL`,
`BACKEND_API_KEY`, `BACKEND_PLATFORM_KEY`, `BACKEND_API_TIMEOUT_SECONDS`,
`BACKEND_API_MAX_RETRIES`, `BACKEND_API_RETRY_BACKOFF_SECONDS`,
`DEFAULT_TRANSFER_NUMBER`, `EMERGENCY_NUMBER`, `EMERGENCY_INSTRUCTION`.

`.gitignore` already covers `.env` at the root and in both projects — no
change needed.

## 7. Installation

Python **3.12.10**, the project's existing version. Not upgraded: the
tree has had dependency trouble on newer Python, and nothing here needs
it.

```bash
cd medicare-voice-ai-backend-FIXED/medicare-voice-ai-backend
venv\Scripts\activate

# Installs NOTHING NEW — every requirement is already satisfied by the
# backend's own requirements.txt. Run it to prove that, not to change it.
pip install -r voice-agent/twilio_deepgram_requirements.txt

# The two new columns. Required: this deployment has
# AUTO_CREATE_SCHEMA=false, so Alembic alone owns the schema.
alembic upgrade head
```

No new dependency is added, and none is removed yet. §5 says when the
LiveKit packages can go and what to check first.

## 8. Twilio setup

**Elastic SIP Trunking is not used by the new stack.** Do not run
`setup_twilio_trunks.py`; the existing trunks can stay untouched (they
are what a rollback needs).

1. **Twilio Console → Phone Numbers → Manage → Active numbers**, click
   your clinic number.
2. Under **Voice Configuration**:
   * *A call comes in*: **Webhook**
   * URL: `https://<VOICE_GATEWAY_PUBLIC_URL>/voice/incoming`
   * HTTP: **POST**
3. Save. Nothing else in Twilio needs changing — no trunk, no credential
   list, no origination URI.
4. Note the **Auth Token** from the Console dashboard into
   `TWILIO_AUTH_TOKEN`. Signature verification uses it and fails closed
   without it.

Multi-tenant: each clinic's number points at the same URL. The gateway
identifies the clinic from Twilio's `To` parameter via
`POST /agent/resolve-tenant`, exactly as before. Assign numbers in
**Super Admin → Phone Numbers**.

## 9. Deepgram setup

1. A Deepgram API key with Voice Agent access → `DEEPGRAM_API_KEY`
   (already set for this project).
2. Nothing else. `open_ai/gpt-4o-mini` and `aura-2-thalia-en` are
   Deepgram-managed: no OpenAI key, no endpoint, no separate contract.

To bring your own LLM (`groq`, `aws_bedrock`, or your own OpenAI
contract), set `DEEPGRAM_LLM_ENDPOINT_URL` and
`DEEPGRAM_LLM_ENDPOINT_HEADERS`. `groq` and `aws_bedrock` **require**
the endpoint; the managed providers accept one.

## 10. Local development

Four terminals.

```bash
# 1. Backend
cd medicare-voice-ai-backend-FIXED/medicare-voice-ai-backend
venv\Scripts\activate
uvicorn app.main:app --reload --port 8000

# 2. Voice gateway
cd medicare-voice-ai-backend-FIXED/medicare-voice-ai-backend/voice-agent
..\venv\Scripts\python.exe -m twilio_deepgram.run

# 3. Public tunnel (Twilio must reach the gateway, not the backend)
ngrok http 8080

# 4. Frontend, if you want the dashboard
cd medicare-voice-ai-frontend-FIXED/medicare-voice-ai-frontend
npm run dev
```

Put the ngrok https URL in `VOICE_GATEWAY_PUBLIC_URL`, restart the
gateway, and set the same URL + `/voice/incoming` on the Twilio number.
**The ngrok URL changes every restart on the free tier**, and both places
must be updated together.

Check before dialling:

```bash
curl http://localhost:8080/health
```

`problems` must be empty. A non-empty list is the reason the next call
will fail, named.

## 11. Webhook configuration

| Endpoint | Method | Set where | Purpose |
|---|---|---|---|
| `/voice/incoming` | POST | Twilio number's Voice webhook | Answers with `<Connect><Stream>` |
| `/voice/stream` | WSS | Named in that TwiML, not configured | The call's audio |
| `/voice/status` | POST | Set automatically via `statusCallback` | Informational logging only |
| `/health` | GET | Your monitoring | Liveness + configuration |

Every POST verifies `X-Twilio-Signature` (HMAC-SHA1 over the called URL
plus every parameter in alphabetical order, `compare_digest`) — the same
scheme and the same implementation shape as
`app/routers/inbound_messages.py`.

`/voice/status` deliberately **never rewrites a finalised call record**.
It can arrive after the bridge has written the call, and the bridge knows
what happened in the conversation; letting the callback win would replace
"booked an appointment" with "completed".

## 12. Testing a phone call

### Automated (no phone, no keys, no network)

```bash
cd medicare-voice-ai-backend-FIXED/medicare-voice-ai-backend
pytest tests/test_voice_pipeline.py -v            # 43  the pipeline engine
pytest tests/test_twilio_deepgram_gateway.py -v   # 71  the pieces
pytest tests/test_twilio_deepgram_call_flow.py -v # 27  a whole call
pytest tests/ -q                                  # 884 everything
```

### Mock mode — a whole call with no API keys at all

This is the step to do **before buying anything**. It replaces exactly
three things — speech recognition, the model, and the voice — with local
fakes. Everything else is real: the Twilio webhook and its signature
check, the media stream, tenant resolution, the tool layer, the FastAPI
backend, SQL Server, and the dashboard.

```bash
# terminal 1 - the backend
cd medicare-voice-ai-backend-FIXED/medicare-voice-ai-backend
venv\Scripts\activate
alembic upgrade head                      # once; adds the two call_logs columns
uvicorn app.main:app --reload --port 8000

# terminal 2 - the gateway, services faked
cd medicare-voice-ai-backend-FIXED/medicare-voice-ai-backend/voice-agent
set VOICE_MOCK_MODE=true
..\venv\Scripts\python.exe -m twilio_deepgram.run

# terminal 3 - place a fake call
..\venv\Scripts\python.exe simulate_call.py
```

A good run ends with something like:

```
  agent audio received : 185,096 bytes (23.1s of speech)
  media frames         : 1,163
  barge-in clears      : 0
```

and the gateway log shows the tool calls going through:

```
Call CAsim... started (stream=MZsim..., to=+919000000000)
Call CAsim... -> <TenantContext single-tenant fallback>
Tool call: list_doctors()
Turn handled in 0.64s
Tool call: find_patient(query)
Tool call: end_call()
```

Then check the dashboard's Call Logs, or the database directly — the row
should carry `provider='twilio'`, the CallSid, a duration, a transcript,
and an AI summary generated by the backend.

**What this proves:** if the keys were the only untested thing before,
they are the only untested thing now. **What it cannot tell you:**
anything about how the call sounds, how fast it feels, or whether
interruption works — there is no microphone and no speaker. That needs a
real phone.

Mock mode is announced loudly at startup and on `/health`, because a
gateway left in it answers real calls with a script.

### Manual, with a real phone

Prerequisites: backend running with a seeded clinic
(`python -m app.seed`), gateway running, ngrok up, the Twilio number's
webhook pointing at it, and the number assigned to that clinic in Super
Admin → Phone Numbers.

| # | Do this | Expect |
|---|---|---|
| 1 | Call the Twilio number | Ringing stops within ~2 s and you hear the clinic's greeting |
| 2 | Watch the gateway log | `Incoming inbound call CA…`, `Call CA… -> <TenantContext …>`, `Deepgram settings: …`, `Deepgram settings applied` |
| 3 | Open the dashboard | The call appears **live** on Active Calls (proves `POST /calls` and the WebSocket broadcast) |
| 4 | **Interrupt it.** Start talking over the greeting, mid-word | It stops **immediately**. This is the single most important check; if it finishes its sentence, see §13 |
| 5 | "What time do you close?" | Answered from clinic info. Log: `Tool call: clinic_info()` |
| 6 | "I'd like to book an appointment" | It asks what for / when — one question at a time |
| 7 | Give a doctor and a day | Log: `list_doctors`, then `check_available_slots`; it offers 2–3 real times |
| 8 | Pick one | It repeats the day and time back and asks you to confirm |
| 9 | Say yes | Log: `book_appointment`; **only then** does it say it is booked |
| 10 | Check the dashboard | The appointment exists, on the right patient, at the time you agreed |
| 11 | "Can I speak to a person?" | It says it is transferring, then the call actually redirects to `DEFAULT_TRANSFER_NUMBER` |
| 12 | On a fresh call, say goodbye | It says goodbye and hangs up cleanly — no clipped word, no dead air |
| 13 | Open the call in Call Logs | Full transcript, an AI summary, sentiment, outcome, duration, and `actions_taken` listing what really ran |

**Test the emergency path deliberately, before going live.** Say "I'm
having chest pain" and confirm the agent abandons whatever it was doing,
reads the configured line, and offers a transfer — no triage, no
questions, no advice.

It is now configured for India:

```env
EMERGENCY_NUMBER=112
EMERGENCY_INSTRUCTION=This sounds like a medical emergency. Please hang up now and call {number}, or go straight to the nearest hospital emergency department. I can also put you through to the clinic right away if you prefer.
```

`112` is India's unified national emergency number (police, fire and
ambulance). `108` reaches a medical ambulance directly in most states,
including Telangana and Andhra Pradesh, and is the alternative if the
clinic prefers a dedicated ambulance line over national coverage.

Two things still to do on that setting, and neither is optional:

* **Have the wording signed off by someone clinically qualified.** It is
  a clinical statement going out over the clinic's name. The default
  deliberately does not triage, name conditions back to the caller, or
  advise on what to do while waiting — all three would be medical advice
  the agent is forbidden to give and not competent to give.

* **Listen to how the number is pronounced on the first real call.**
  Sarvam's `enable_preprocessing` normalises numbers so that dosages and
  appointment times sound natural, and the same feature can render "112"
  as "one hundred and twelve". The prompt instructs the model to say it
  one digit at a time; if that is not enough, set
  `EMERGENCY_NUMBER=1 1 2`.

Also worth doing once: **stop the backend mid-call** and ask it to book
something. It must apologise and offer a transfer. It must not say
anything was booked.

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every call rejected, log says "unsigned or mis-signed" | `VOICE_GATEWAY_PUBLIC_URL` ≠ the URL Twilio called | Set it to the exact ngrok/public https URL, no trailing slash |
| Call connects, then silence | No `wss://` stream URL, or Deepgram unreachable | `curl /health` — `problems` names it. Check `DEEPGRAM_API_KEY` |
| Twilio plays "an application error has occurred" | The gateway is not reachable at that URL | Is ngrok up? Is the port right? Twilio Console → Monitor → Errors |
| **The agent talks over the caller** | The `clear` is not reaching Twilio | `stream_sid` must be set from the `start` event. `test_user_started_speaking_clears_twilio_playback` covers this |
| Agent offers to transfer a Hindi/Telugu caller | Correct, and deliberate. See §17 | Nothing to fix. To serve them here, switch to `eleven_labs` |
| Telugu/Hindi not transcribed | Expected: `DEEPGRAM_STT_LANGUAGE=en` | Only change it together with the TTS provider — see §17 |
| Long silence during booking | Backend slow | Expected up to `VOICE_TOOL_FILLER_DELAY_SECONDS`, then it says "One moment." Check backend logs |
| It claims a booking that did not happen | Prompt regression | `pytest tests/test_twilio_deepgram_gateway.py -k claim` |
| Transcript missing its last line | Teardown drain too short | `_QUIET_SECONDS` / `_DRAIN_SECONDS` in `bridge.py`, and read why they are two numbers |
| Calls appear twice in the dashboard | Both stacks running | Stop one. `agent.py` and the gateway both answer, and each writes its own call log |
| `no such column: provider` | Migration not applied | `alembic upgrade head` |

## 14. Rollback

The old stack was never modified, so rollback is: stop one process,
start the other.

```bash
# 1. Stop the gateway (Ctrl-C).

# 2. Twilio Console -> your number -> Voice Configuration:
#    point "A call comes in" back at the LiveKit SIP trunk origination
#    (its Elastic SIP Trunk configuration is untouched).

# 3. voice-agent/.env
VOICE_PROVIDER=livekit

# 4. Start the LiveKit worker
cd voice-agent
..\venv\Scripts\python.exe agent.py dev
```

Nothing needs reverting in the database. Migration `0033` adds two
nullable columns that the LiveKit path simply never writes; leaving it
applied is correct, and `alembic downgrade 0032_rule_conditions` is
available if you want it gone.

Partial rollback is also possible — run both stacks against *different*
Twilio numbers and move clinics over one at a time. Do **not** point one
number at both.

## 15. Production deployment

* **Run the gateway as its own service**, not inside the API process.
  `app/instance.py` makes multiple API workers fatal in production
  because rate-limit counters and dashboard websockets live in process
  memory; live audio bridges in that same single worker would put one
  clinic's call load on the same event loop as every clinic's dashboard.
* **One gateway worker per process**, as `run.py` enforces. Scale by
  running more processes behind a load balancer, not with `--workers`.
* **TLS is required.** Twilio will not open a `ws://` stream. Terminate
  TLS at your proxy and set `VOICE_GATEWAY_PUBLIC_URL` to the public
  https name — the signature check depends on it matching exactly.
* **Websockets must survive your proxy.** nginx needs
  `proxy_http_version 1.1`, `Upgrade`/`Connection` headers, and a
  `proxy_read_timeout` longer than your longest call
  (`VOICE_MAX_CALL_SECONDS` is 1800 by default).
* **Keep `TWILIO_VALIDATE_SIGNATURE=true`.** These endpoints answer the
  phone and start a paid conversation.
* **Health check `/health`** and alert on a non-empty `problems`.
  `status` stays `"ok"` deliberately, so a load balancer does not pull
  the only process out of rotation over a configuration warning.
* **Outbound egress**: `agent.deepgram.com:443` and `api.twilio.com:443`.
* Only one stack answers a given number. Two would produce two call logs
  for one call.

## 16. Cost per connected minute

### Verified, from the providers' own current pricing

| Component | Price | Source |
|---|---|---|
| Deepgram Voice Agent, Standard (STT + managed LLM + TTS) | **$0.056/min** PAYG, $0.051/min on Growth | deepgram.com/pricing |
| Deepgram Voice Agent, Standard **BYO TTS** | **$0.065/min** PAYG | same |
| Deepgram Voice Agent, **BYO LLM** | **$0.050/min** PAYG | same |
| Twilio inbound, Indian local number | **≈ ₹0.35/min** | third-party guides — **confirm in your Twilio console**, and note India voice requires regulatory registration |
| Twilio Indian local number rental | ≈ $2/month | same caveat |

### The arithmetic

At an assumed **₹88 / USD** (substitute today's rate):

**Default configuration — Deepgram English TTS:**

```
Deepgram Voice Agent, Standard   $0.056/min  =  ₹4.93/min
Twilio inbound (India, local)                   ₹0.35/min
                                             ───────────
                                                ₹5.28/min
```

**With ElevenLabs for Hindi/Telugu:**

```
Deepgram Voice Agent, BYO TTS    $0.065/min  =  ₹5.72/min
ElevenLabs Flash v2.5                           ₹?  /min   ← from your ElevenLabs plan;
                                                             the agent speaks roughly
                                                             400–600 characters per
                                                             connected minute
Twilio inbound (India, local)                   ₹0.35/min
                                             ───────────
                                                ₹6.07/min + ElevenLabs
```

**Against your ₹9–12/min target:** the default configuration lands at
roughly **₹5.3/min**, comfortably under. The ElevenLabs configuration
starts at ₹6.07 and stays inside the target unless ElevenLabs costs more
than about ₹4/min at your usage — check your plan.

Note that Deepgram bills **websocket connection time**, not speech time,
so an idle open socket costs the same as an active conversation. That is
what `VOICE_MAX_CALL_SECONDS` exists for.

FastAPI and SQL Server are infrastructure you already run; no per-minute
figure is invented for them.

### Future optimisations — do NOT apply these before the stack is proven

1. **BYO LLM** ($0.050 vs $0.056/min) — you already have a Groq key, and
   Deepgram supports `groq` with an endpoint. Saves ~₹0.5/min and
   reintroduces the TPM-budget pressure that forced the single-tool
   schema on the old stack. Measure tool-calling reliability first.
2. **Growth plan** ($0.051 vs $0.056) — pure discount, no behaviour
   change. The obvious first move once volume justifies it.
3. **Sarvam for Indian-language TTS** — cheaper than ElevenLabs and
   better on Indian voices, but **not supported by the Voice Agent API**.
   It would mean leaving the managed agent for a hand-built
   STT→LLM→TTS pipeline, which is the architecture this migration
   deliberately left behind. Only worth revisiting if Deepgram adds a
   generic TTS endpoint type.
4. **Shorter calls** — the largest lever, and free. Prompt work that gets
   a booking done in four turns instead of seven saves more than any
   provider swap.

---

## 17. English only — the decision, and the way back

**This stack ships English only, deliberately, for now.** Hindi and
Telugu come once English is proven on real calls.

Two separate things point the same way here, and conflating them makes
the later decision harder than it needs to be:

* **The decision** is to do one language properly first.
* **The constraint** is that Deepgram's TTS voices do not speak Hindi or
  Telugu, and the Voice Agent API accepts only `deepgram`, `cartesia`,
  `eleven_labs`, `open_ai` and `aws_polly` as speak providers — so the
  Sarvam voices the LiveKit stack used cannot be plugged into it at all.

The decision would stand on its own; the constraint means acting on it
later costs an ElevenLabs account rather than a config change alone.

### What English-only actually means, in three places

All three move together, and any one drifting alone breaks the agent in a
way that is hard to see:

| Layer | Setting | Why |
|---|---|---|
| Listen | `DEEPGRAM_STT_LANGUAGE=en` | **Not `multi`.** `multi` spreads the model across ten languages for code-switching, which is worth paying for only when the agent can *answer* in them. It cannot — so `multi` would buy a slightly worse transcription of the English it is going to reply to anyway. Indian-accented English is handled by `en`; that is acoustic modelling, not language selection. |
| Speak | `aura-2-thalia-en` | Aura-2 has American, British, Australian, Irish and Filipino English voices and **no Indian-English one**. `aura-2-draco-en` / `aura-2-pandora-en` are the British pair if those suit your callers better. |
| Prompt | derived from the TTS provider | Says "Speak English only", so it cannot disagree with the voice. |

`TestEnglishOnlyIsConfiguredEndToEnd` in
`tests/test_twilio_deepgram_gateway.py` pins all three together.

### A caller who does not speak English

They are **transferred to a person**, and the prompt says so explicitly.
The two alternatives are both worse: a model attempting a few words of
Hindi through a voice that cannot form them produces noise, and a model
ploughing on in English at somebody who has already shown they cannot
follow it wastes their time and the clinic's. Nothing else in the system
can detect this situation, so it has to be an instruction.

This is also, for any language outside en/hi/te, better than the LiveKit
stack — which clamps everything to English and carries on.

### One thing the LiveKit stack does that this one deliberately does not

`call_session.finalize` reports **no** `detected_language`. The LiveKit
path sends one because Sarvam detects a language per turn and the value
is fed back to Sarvam TTS on the patient's next call. Deepgram's agent
socket does not report a per-turn language, and an English-only stack
could not act on one anyway — so sending a guess would write an
unverified fact onto a patient record. `speech_pace_requested` **is**
still reported; that one is observed rather than inferred.

### Adding Hindi and Telugu later

Two `.env` changes and an ElevenLabs key. No code:

```env
DEEPGRAM_TTS_PROVIDER=eleven_labs
DEEPGRAM_TTS_MODEL_ID=eleven_flash_v2_5
DEEPGRAM_TTS_VOICE=<an ElevenLabs voice id>
DEEPGRAM_TTS_ENDPOINT_URL=https://api.elevenlabs.io/v1/text-to-speech
DEEPGRAM_TTS_ENDPOINT_HEADERS={"xi-api-key": "…"}

DEEPGRAM_STT_LANGUAGE=multi     # English + Hindi, code-switching
#DEEPGRAM_STT_LANGUAGE=te       # Telugu-first line; excludes English
```

The prompt follows the TTS provider on its own. Note the Telugu wrinkle:
`nova-3` supports it only as a dedicated language, never inside `multi`,
so a Telugu line and an English line are two different numbers, not one
bilingual agent. Costs are in §16.

Until then, `agent.py` remains the only stack that speaks Telugu — which
is why it has not been deleted.
