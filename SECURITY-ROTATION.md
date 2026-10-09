# Credential rotation runbook

**Status: NOT DONE. This is the one Phase 0 item that cannot be completed
from the codebase — every step below requires signing in to a provider
console or database, which only you can do.**

## Why

Live credentials were committed into the working tree in plaintext:

| File | Secrets it held |
|---|---|
| `medicare-voice-ai-backend/.env` | SQL Server host, user, password; `SECRET_KEY` |
| `medicare-voice-ai-backend/.env.example` | a real 44-character `SECRET_KEY`; a full connection string in a comment |
| `medicare-voice-ai-backend/voice-agent/.env` | LiveKit API key + secret, Deepgram, Groq, Sarvam, Vobiz SIP username/password/trunk id, backend service API key |

There was no `.gitignore` anywhere in either project, so any `git init`,
any archive, and any handover would have shipped all of it.

Phase 0 fixed the *exposure path*: `.gitignore` files now exist at the repo
root and in both projects, and both `.env.example` files are placeholders
only. **It did not and could not rotate the secrets themselves.** Treat
every value in the table above as compromised until rotated.

---

## Order of operations

Do these in order. Steps 1–5 are independent of each other; step 6 is the
disruptive one and should go last, during a quiet window.

### 1. Third-party API keys

For each provider: issue a new key, update `voice-agent/.env`, restart the
agent worker, confirm a test call still works, **then revoke the old key.**
Issue-then-revoke (not revoke-then-issue) keeps calls working throughout.

- **LiveKit** — new API key + secret. Update `LIVEKIT_API_KEY`,
  `LIVEKIT_API_SECRET`.
- **Deepgram** — `DEEPGRAM_API_KEY`.
- **Groq** — `GROQ_API_KEY`.
- **Sarvam** — `SARVAM_API_KEY`.

### 2. SIP trunk

**Carrier migrated to Twilio Elastic SIP Trunking.** The `VOBIZ_*`
variables no longer exist in `voice-agent/.env`; the live equivalents are
`TWILIO_TRUNK_AUTH_USERNAME` / `TWILIO_TRUNK_AUTH_PASSWORD` (the
Termination Credential List), read only by `setup_twilio_trunks.py`.

SIP credentials carry direct financial exposure — a leaked one can be
used to place calls billed to your account — so this section still
applies, to whichever carrier is current.

**Vobiz, still outstanding.** The old credentials were on disk in
plaintext and are unrotated. Removing them from `.env` is not rotation:

- Cancel the Vobiz trunk with the carrier rather than merely abandoning
  it, and confirm the cancellation.
- Check Vobiz call detail records for unrecognised outbound calls since
  the credentials were first written to disk.
- Stale copies still hold the Vobiz secrets. They were scattered across
  both projects under four different naming conventions; they are now
  collected in `_backups/` (see the inventory below and that directory's
  README). None is gitignored *by accident* and none is read at runtime,
  but **rotating the live `.env` files does not touch any of them** —
  which is the point, because `VOBIZ_USERNAME` and `VOBIZ_PASSWORD` no
  longer appear in a live `.env` at all. A rotation pass that walks only
  the live configuration would conclude there is nothing to do.
  Delete them once the Vobiz trunk is confirmed cancelled.

**Twilio, going forward.** Prefer a Termination Credential List over an
IP ACL — LiveKit Cloud egress addresses are not fixed, so an IP allowlist
will silently break. Restrict the trunk's geographic permissions to the
countries you actually call.

### 3. Database

Rotate the SQL Server password for `DB_USER`, then update `DB_SERVER`,
`DB_USER`, `DB_PASSWORD` in `backend/.env`.

While you are there: confirm the account is not `sa` and holds only the
rights the app needs (DML on the app schema, plus DDL only if you keep
`AUTO_CREATE_SCHEMA=true`). Also set `DB_TRUST_SERVER_CERTIFICATE=false`
once a CA-signed certificate is in place — it is `true` today, which
disables certificate validation on the database connection.

### 4. Backend service API key (voice agent)

1. Dashboard → **EHR Integration → API Keys → Create API Key** (label it
   e.g. `Voice Agent 2026-08`). Or run `python create_service_api_key.py`
   from the backend directory.
2. Copy the plaintext key — it is shown exactly once.
3. Replace the value on the existing `BACKEND_API_KEY` line in
   `voice-agent/.env`. **Replace it; do not add a second line** — the agent
   reads the last one and would keep sending a stale key.
4. Restart the agent, make a test call, confirm patient lookup works.
5. Revoke the old key in the same dashboard screen.

`python diagnose_api_key.py` verifies the configured key matches a live,
non-revoked row without printing any secret.

### 5. Move secrets out of `.env` files entirely

`.env` on disk is a stopgap. For the actual deployment, use whatever secret
store the hosting platform provides (Azure Key Vault, AWS Secrets Manager,
Docker/Kubernetes secrets). `pydantic-settings` reads real environment
variables with no code change — the `.env` file is only a local-dev
convenience.

### 6. `SECRET_KEY` — do this last

**Rotating `SECRET_KEY` invalidates every issued JWT.** Everyone signed
into the clinic dashboard and the super-admin console is logged out the
moment the API restarts. It does **not** affect voice calls: the agent
authenticates with `X-API-Key`, not a JWT.

Generate:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then set `SECRET_KEY` in `backend/.env` (or the secret store), restart the
API, and tell users to sign in again. Pick a low-traffic window.

---

## Inventory: every copy of a secret on disk

Kept accurate because the previous version of this runbook cited two
paths that were wrong and omitted a third file entirely — and an
inventory that is subtly wrong is worse than none, since it is read as
complete.

Verified by comparing keys and value hashes against the live `.env`
files (no secret was printed to do it).

| File | Holds | Present in the live config? |
|---|---|---|
| `_backups/backend/.env.bak-cors` | an **older `SECRET_KEY`** | No — superseded. Tokens signed with it are already rejected |
| `_backups/backend/.env.remote-backup` | **`DB_USER` / `DB_PASSWORD`** — a SQL Server login | **Commented out** in the live `.env`, alongside the remote `DB_SERVER`/`DB_PORT`. The backend authenticates with `DB_TRUSTED_CONNECTION` (Windows auth) and never reads them — but they are still on disk in plaintext, in the live file, and a `#` is not redaction |
| `_backups/backend/voice-agent/.env.backup` | 6 × `VOBIZ_*`, incl. `VOBIZ_USERNAME` / `VOBIZ_PASSWORD` | **No** — retired carrier |
| `_backups/backend/voice-agent/.env.bak-pace` | same 6 × `VOBIZ_*` | **No** |
| `_backups/backend/voice-agent/.env.bak-tts` | same 6 × `VOBIZ_*` | **No** |
| `_twilio-migration-backup/env.pre-twilio` | same 6 × `VOBIZ_*` | **No** |
| `_twilio-migration-backup/backend-env.pre-secretkey` | a pre-rotation `SECRET_KEY` | No |
| `medicare-voice-ai-backend-FIXED/…/.env` | the live backend configuration | — |
| `medicare-voice-ai-backend-FIXED/…/voice-agent/.env` | the live agent configuration | — |

Two things follow from the "Present in the live config?" column, and
they are the reason this table exists:

- **The Vobiz SIP login survives only in backups.** It appears in no
  live `.env` at all, so a rotation pass built around reading the live
  files would not find it. The trunk needs **cancelling with the
  carrier**, not merely deleting from a file.

- **The SQL Server login is in the live `.env`, commented out.**
  Correcting an earlier version of this table, which said it was only in
  a backup: it is in both. The app does not read it (Windows auth is
  configured instead), but a commented credential is still a credential
  on disk — and it is the one most likely to be uncommented later by
  somebody restoring the remote database block. **Disable the login on
  the server**; deleting the line is not rotation.

- **`_twilio-migration-backup/` is deliberate**, unlike `_backups/`. It
  is referenced from `CLAUDE.md` as where the retired `fix_sip_trunk.py`
  scripts live, so it stays where it is — but it holds secrets too, and
  the same deletion applies to its `env.pre-twilio` and
  `backend-env.pre-secretkey` once rotation is done.

---

## After rotating

- [ ] Confirm `git status` shows no `.env` file as tracked or untracked-but-stageable. The new `.gitignore` files should hide them.
- [ ] If this tree was **ever** pushed to a remote, rotating is not enough — the old values remain in history. Purge with `git filter-repo` (or BFG) and force-push, then rotate again afterwards.
- [ ] Confirm `.env.example` files still contain only `REPLACE_ME` placeholders.
- [ ] Check provider dashboards for unexpected usage during the exposure window.
- [ ] Set a calendar reminder to rotate on a schedule from here on.

---

## What Phase 0 already did

- Added `.gitignore` at the repo root and in both projects, covering `.env`,
  `*.pem`/`*.key`, `uploads/` (knowledge-base documents may contain PHI),
  `*.db`, `node_modules/`, `dist/`, and log directories.
- Rewrote `backend/.env.example` as placeholders only, matching the real
  `Settings` class (the previous version documented a `DATABASE_URL`/SQLite
  option the code no longer supports, and contained a real secret).
- Added `voice-agent/.env.example`, which did not exist.
