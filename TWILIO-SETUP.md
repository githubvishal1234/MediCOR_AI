# Twilio setup runbook

Everything needed to bring the voice agent up on **Twilio Elastic SIP Trunking**,
from an empty Twilio account to a call the agent answers.

The application code is already migrated. Nothing below requires a code change —
it is console configuration, four `.env` values, and one command.

---

## Before you buy anything

Two questions decide whether this plan works as written. Answer them first.

### 1. Can Twilio actually serve your number?

The old carrier's DID was `+91 8071583192`. Twilio's India voice service carries
heavy KYC / regulatory-bundle requirements, and Indian regulation restricts
terminating domestic PSTN calls onto VoIP.

- If you need an **Indian** DID, confirm with Twilio sales *before* purchasing
  that they will provision one for your entity, for both inbound and outbound.
- If this is for **testing only**, buy a US/UK DID instead. It sidesteps the
  regulatory question entirely and validates the whole path in an afternoon.

**This is the single biggest risk in the migration and it is not a code problem.**

### 2. Does your trunk support SIP REFER?

The agent transfers callers with `transfer_sip_participant`, which emits a SIP
REFER. On Elastic SIP Trunking this must be **explicitly enabled**. If it cannot
be, transfer needs reworking as a dial-out-and-bridge — a real code change, and
the only one this migration might still force.

Escalation rules (AI-5) also resolve to `action="transfer"`, so they depend on
this too.

---

## Step 1 — Get your LiveKit SIP URI

LiveKit Cloud → your project → **Settings → SIP**. Copy the project SIP URI.
It looks like:

```
sip:<project-id>.sip.livekit.cloud
```

Twilio sends inbound calls here. You need it in Step 3.

## Step 2 — Create the Elastic SIP Trunk

Twilio Console → **Elastic SIP Trunking → Trunks → Create new trunk**.

Name it something you will recognise later, e.g. `medicor-ai`.

Note the **Termination SIP URI** Twilio assigns:

```
<your-subdomain>.pstn.twilio.com
```

That value becomes `SIP_DOMAIN` and `TWILIO_TERMINATION_URI`.

## Step 3 — Origination (Twilio → LiveKit, inbound)

On the trunk, **Origination → Add Origination URI**:

```
sip:<project-id>.sip.livekit.cloud;transport=tls
```

Priority `10`, weight `10`. This is how a call to your DID reaches LiveKit.

> Drop `;transport=tls` if you have not enabled Secure Trunking — see Step 6.

## Step 4 — Termination auth (LiveKit → Twilio, outbound)

On the trunk, **Termination**:

1. Set a Termination SIP URI subdomain if you have not already.
2. Under **Authentication**, create a **Credential List** with a username and
   password.

**Use a Credential List, not an IP Access Control List.** LiveKit Cloud's egress
addresses are not fixed, so an IP allowlist will work until it silently doesn't.

These two values become `TWILIO_TRUNK_AUTH_USERNAME` / `TWILIO_TRUNK_AUTH_PASSWORD`.

## Step 5 — Buy and attach the number

**Phone Numbers → Buy a number** (see the regulatory note above), then on the
trunk: **Numbers → Add an existing number** and attach it.

Note it in E.164 (`+15551234567`). It becomes `TWILIO_PHONE_NUMBER`.

## Step 6 — Enable SIP REFER, and decide on TLS

Still on the trunk:

- **Enable SIP REFER.** Transfer does not work without it.
- **Secure Trunking (TLS + SRTP)** is optional. If you enable it, keep
  `;transport=tls` in Step 3 and set `TWILIO_SIP_TRANSPORT=tls`. If you do not,
  remove it and leave the transport at its default (`auto`).

Do not set `tls` blind — if Secure Trunking is off, TLS will simply fail to
connect.

---

## Step 7 — Fill in `.env`

In `voice-agent/.env`, uncomment and complete the Twilio provisioning block:

```dotenv
TWILIO_TERMINATION_URI=<your-subdomain>.pstn.twilio.com
TWILIO_TRUNK_AUTH_USERNAME=<credential list username>
TWILIO_TRUNK_AUTH_PASSWORD=<credential list password>
TWILIO_PHONE_NUMBER=+15551234567
LIVEKIT_INBOUND_TRUNK_ID=ST_LZcuAoR4y6xQ
```

`LIVEKIT_INBOUND_TRUNK_ID` points at the existing inbound trunk. Setting it means
the Twilio DID is **added** to that trunk, preserving what is already there and
keeping the existing dispatch rule working. Leave it unset only if you want a
brand-new trunk — and then read the warning the script prints.

Leave `SIP_TRUNK_ID` and `SIP_DOMAIN` commented for now. Step 8 produces them.

## Step 8 — Provision the LiveKit trunks

From `voice-agent/`:

```bash
..\venv\Scripts\python.exe setup_twilio_trunks.py
```

Dry run. Changes nothing; prints exactly what it would do. Read it.

```bash
..\venv\Scripts\python.exe setup_twilio_trunks.py --apply
```

This creates the **outbound** trunk (none exists today, which is why outbound
dialling currently fails) and adds your DID to the **inbound** trunk. It backs up
the inbound trunk's prior state to `_twilio-migration-backup/` first.

It finishes by printing two lines. Paste them into `voice-agent/.env`,
uncommented:

```dotenv
SIP_TRUNK_ID=ST_xxxxxxxxxxxx
SIP_DOMAIN=<your-subdomain>.pstn.twilio.com
```

## Step 9 — Restart and test

```bash
..\venv\Scripts\python.exe agent.py dev
```

Then work through these in order. Each isolates a different failure.

| # | Test | What it proves | If it fails |
|---|---|---|---|
| 1 | Call the Twilio DID | Origination URI, inbound trunk `numbers`, dispatch rule | Twilio **Debugger** shows whether the INVITE left Twilio and what LiveKit replied |
| 2 | Agent greets you | Worker registered, dispatch rule targets `outbound-caller` | Check the worker log for a job |
| 3 | Say "book an appointment" | Sarvam STT → Groq → Sarvam TTS, and the backend call | Backend must be running on `BACKEND_API_URL` |
| 4 | Check the dashboard | Call log row, live `call.started` / `call.ended` | Unrelated to telephony if 1–3 passed |
| 5 | Ask for a human | **SIP REFER** | Almost always the REFER toggle in Step 6 |
| 6 | Outbound dispatch | Outbound trunk, termination auth, caller ID | Twilio rejects a `From` you do not own |

---

## Rollback

```bash
..\venv\Scripts\python.exe setup_twilio_trunks.py --restore --apply
```

Restores the inbound trunk's `numbers` to its pre-migration state. It does **not**
delete the outbound trunk it created — remove that from the LiveKit dashboard if
you want it gone.

To roll the code back as well, the pre-migration copies are in
`_twilio-migration-backup/`: `config.py.pre-twilio`, `env.pre-twilio`,
`env.example.pre-twilio`, `agent.py.pre-twilio`.

---

## Things that will bite you

- **Caller ID.** Twilio rejects an outbound INVITE whose `From` is not a number
  you own. The script sets the trunk's `numbers` to your DID for this reason.
- **A new inbound trunk is not enough.** The SIP dispatch rule is scoped to
  specific `trunk_ids`. Create a new inbound trunk and calls will arrive at
  LiveKit and then go nowhere, because no rule covers it. The script warns you
  when this happens. Extending the existing trunk avoids it entirely.
- **The inbound trunk's only constraint is `numbers`.** No IP allowlist, no SIP
  auth. Anyone who knows the LiveKit SIP URI and a matching number can inject a
  call. Consider adding `auth_username` / `auth_password` and a matching Twilio
  credential list once the basic path works.
- **`normalize_e164` is forgiving, but not psychic.** A bare 10-digit number with
  no country code normalises to `+<those digits>` — a different number. If tenant
  routing is on, that silently routes to the wrong clinic rather than erroring.
- **Tenant routing is currently off.** `BACKEND_PLATFORM_KEY` and
  `AGENT_PLATFORM_KEY` are both unset, so every call is handled by whichever org
  owns `BACKEND_API_KEY`. To run Twilio and the old carrier side by side against
  different clinics, enable it first — see `MULTI-TENANT-ROLLOUT.md`.
- **Removing the Vobiz variables was not credential rotation.** Cancel the trunk
  with the carrier and check its CDRs. See `SECURITY-ROTATION.md`.
