# Frontend → Backend Dependencies

**Status:** documented, **not implemented**. No backend file has been modified.
**Raised by:** the Voice AI explainer (decision #4) during Phase 0 planning.

Per the brief: where a frontend requirement appears to need a backend change, work
stops and the requirement is documented here for approval rather than built.

**One dependency was found. It is not a blocker** — the explainer ships without
it, in an honest reduced form described in §1.5. Two further items are recorded
as non-blocking observations.

---

## 1. DEPENDENCY — live agent state for the Voice AI explainer

### 1.1 What was asked for

Decision #4 requests a visual status representation with five states:

```
Ready · Listening · Processing · Taking Action · Confirming
```

### 1.2 Why it cannot be built as live telemetry today

The dashboard WebSocket carries exactly **nine** event types. Enumerated from
every `manager.broadcast(...)` and `notify(...)` call site in `app/`:

| Event | Emitted from |
|---|---|
| `call.started` | `routers/calls.py:124` |
| `call.updated` | `routers/calls.py:387`, `:573` |
| `call.ended` | `routers/calls.py:389`, `jobs.py:136` |
| `call.transcript_message` | `routers/calls.py:456` |
| `appointment.created` | `routers/appointments.py:194` |
| `appointment.updated` | `routers/appointments.py:227`, `:618`, `appointment_link.py:199`, `:314` |
| `appointment.booked` | `routers/appointments.py:394`, `:767`, `inbound_messages.py:300` |
| `appointment.rescheduled` | `routers/appointments.py:504`, `appointment_link.py:396` |
| `appointment.cancelled` | `routers/appointments.py:550`, `appointment_link.py:433` |
| `notification` | `realtime.py:196` |

**None of these carries the agent's per-turn state.** There is no
`agent.listening`, no `agent.thinking`, no tool-call event. `GET /agent-settings`
returns only `voice_profile` and `greeting_script` (`schemas.py:1176`), and no
endpoint anywhere exposes the voice stack, engine, or supported languages.

The voice gateway *knows* these states internally — Deepgram's
`UserStartedSpeaking`, the tool-dispatch task, the filler-phrase timer — but it
never reports them to the backend, and the backend has no channel to relay them.

### 1.3 What building it anyway would mean

Rendering an animated "Listening → Processing → Taking Action" indicator driven
by nothing would be a fabricated status display. That is precisely the defect
class this audit is removing in Phase 5 — `PatientProfile`'s green
"Live agent available" dot, which checks nothing — and the same class as the
"Online now" indicator and "Insurance Verified" badge already removed from this
product. **It will not be built.**

### 1.4 Proposed backend change (NOT implemented — awaiting approval)

If a genuine live indicator is wanted later:

| | |
|---|---|
| **New event** | `call.agent_state` |
| **Payload** | `{ call_id, state, at }` where `state ∈ {listening, processing, acting, speaking}` |
| **Emitted by** | The voice gateway, via a new authenticated endpoint on the existing agent-facing surface — e.g. `POST /calls/{id}/agent-state`, guarded by `get_org_context` like every other agent route |
| **Relayed by** | `manager.broadcast(org_id, "call.agent_state", …)` — the existing fan-out, no new machinery |
| **Frontend** | `LiveCallBanner` and the explainer subscribe through the existing `useRealtime().subscribe`; no socket change |

**Impact if approved:** one new nullable-free event name, one new agent-reachable
route, and per-turn traffic on an existing socket. **Cost:** it adds a
network round trip inside the turn loop, on a path where `CLAUDE.md` records that
3–5 seconds of dead air was already a measured problem. The gateway would need to
fire-and-forget it, never await it.

**Recommendation: do not build this.** The value is a status light; the risk is
latency on the call path. §1.5 delivers the same user understanding for free.

### 1.5 What ships instead — no backend change

The explainer separates two things that the request conflates:

**(a) The five stages become an explanatory diagram, not a status light.**
A labelled flow of what happens on *every* call, which is true independent of
whether a call is happening now. This is what actually helps a clinic manager
understand the product — the brief's own goal.

**(b) A genuine two-state indicator, from data that already exists.**
`useRealtime().liveCalls` is real, already populated, already reconciled every
10s against `GET /calls`:

| Condition | Shown |
|---|---|
| `liveCalls.length === 0` | **Ready** — "No calls in progress" |
| `liveCalls.length > 0` | **On a call** — "1 call in progress", linking to `/app/calls?call={id}` |

Both are observed facts. Neither claims anything the frontend cannot see.

If §1.4 is later approved, stage (a) can be upgraded to reflect real per-turn
state with no rework — the diagram's stage components take a prop that is
currently never set.

---

## 2. OBSERVATION — no date-range filter on `GET /calls`

`CallLogs.jsx` filters Today / Yesterday / Last 7 Days **client-side**, over the
already-loaded list, because the endpoint exposes no date parameter. Its own
comment says so.

**Not requesting a change.** At clinic scale the list is small and the filter is
instant. Recorded only so that if call volume grows, the fix is known: a
`from`/`to` query parameter on `GET /calls`, not a frontend workaround.

**No frontend change is planned here.**

---

## 3. OBSERVATION — `Interaction.audio_url` has no writer

`Interaction.has_audio` is set **only in `app/seed.py`** (lines 345, 419).
`audio_url` is **never written anywhere in `app/`** — not even by the seed.

The frontend guard `it.has_audio && it.audio_url` means the play button already
cannot render. Phase 5 removes the dead control and the `new Audio()` machinery
behind it, on the frontend only.

**No backend change requested.** The columns are the backend's to keep or drop;
`CLAUDE.md` documents the identical `call_logs.recording_url` case as a known
defect of the same shape. Flagged here so the decision is visible, not to ask for
one.

---

## 4. Summary

| # | Item | Blocks Phase? | Backend change requested? |
|---|---|---|---|
| 1 | Live agent state | **No** — §1.5 ships instead | **No** — documented for a future decision |
| 2 | Call date-range filter | No | No |
| 3 | `audio_url` has no writer | No | No |

**Nothing in the 13-phase plan is blocked. No backend change is requested at this
time.**
