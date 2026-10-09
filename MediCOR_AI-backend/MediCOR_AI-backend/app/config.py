import ipaddress
import logging
from typing import Optional, Union

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

logger = logging.getLogger("config")

DEFAULT_SECRET_KEY = "change-this-to-a-random-secret-in-production"

_IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

# Hostnames that mean "this machine". Used by `cors_is_local_only` to
# decide whether a deployment has been told where its frontend actually
# lives. Compared against the parsed host, never as a substring: a real
# clinic domain could contain any of these words.
_LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", ""}
)


def _host_of(origin: str) -> str:
    """
    The hostname out of a CORS origin, lowercased, without scheme or port.

    Deliberately hand-rolled rather than `urlsplit`, which cannot parse a
    schemeless "localhost:5173" -- it reads "localhost" as the scheme and
    returns no host at all, so the very value this is checking for would
    look like a remote domain. IPv6 literals keep their brackets, which is
    how they are written in an origin and how `_LOCAL_HOSTS` lists them.
    """
    o = origin.strip().lower()
    _, _, o = o.rpartition("://")
    o = o.split("/", 1)[0]
    o = o.rpartition("@")[2]
    if o.startswith("["):
        return o.split("]", 1)[0] + "]"
    return o.split(":", 1)[0]



class Settings(BaseSettings):
    # ============================================================
    # SQL SERVER ONLY
    # ============================================================

    db_server: str
    db_port: int
    db_name: str
    # Empty when DB_TRUSTED_CONNECTION is on — Windows authentication
    # carries no username or password.
    db_user: str = ""
    db_password: str = ""

    db_driver: str = "ODBC Driver 18 for SQL Server"
    db_encrypt: bool = True
    db_trust_server_certificate: bool = True

    # Windows authentication instead of a SQL login.
    #
    # A SQL Server Express instance installed with the defaults is
    # Windows-auth-only with `sa` disabled, so DB_USER/DB_PASSWORD cannot
    # connect to it at all. Without this flag the only way to run against
    # such an instance is to switch the server to mixed mode and enable
    # `sa` — a server-wide security change, and a stored password, both
    # to run locally.
    #
    # Off by default: the deployed configuration uses a SQL login, and
    # Windows auth only works where the app runs as a principal the
    # server trusts (so: a local or domain machine, never a container on
    # another host).
    db_trusted_connection: bool = False

    # ============================================================
    # AUTH
    # ============================================================

    secret_key: str = DEFAULT_SECRET_KEY
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ============================================================
    # ENVIRONMENT / LOGGING
    # ============================================================

    environment: str = "development"
    log_level: str = "INFO"

    # GAP-T1. This app cannot run more than one process safely: rate-limit
    # counters and dashboard WebSockets both live in memory, so a second
    # worker silently halves the effective login limit and drops live
    # events. `app/instance.py` detects it and refuses to start in
    # production; setting this true downgrades that to a warning.
    #
    # It suppresses the check. It does not make multiple workers safe --
    # Redis for both is the actual prerequisite, and it is not built.
    allow_multiple_workers: bool = False

    # ============================================================
    # DATABASE POOL
    # ============================================================

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30

    # Legacy bootstrap: run Base.metadata.create_all() + sync_missing_columns()
    # on startup. Kept enabled by default so existing deployments behave
    # exactly as before Alembic was introduced. Set to false once Alembic
    # owns the schema in every environment (see alembic/README.md).
    auto_create_schema: bool = True

    # ============================================================
    # REVERSE PROXY / CLIENT IP RESOLUTION
    # ============================================================
    # Comma-separated IPs or CIDR blocks of reverse proxies actually
    # deployed in front of this API. X-Forwarded-For is honoured ONLY
    # when the direct peer is in this list; otherwise the header is
    # ignored entirely and the socket peer address is used.
    #
    # Empty (the default) means "no proxy" — the safe posture, because an
    # unvalidated X-Forwarded-For lets any client rotate its apparent IP
    # per request and bypass the login/register rate limiter completely.

    trusted_proxy_ips: str = ""

    # ============================================================
    # VOICE AGENT — MULTI-TENANT RUNTIME (Phase 2)
    # ============================================================
    # Shared secret the voice-agent worker presents (as X-Platform-Key)
    # to POST /agent/resolve-tenant, which maps a dialled number to the
    # clinic that owns it and returns a short-lived, org-scoped token.
    #
    # This is a PLATFORM credential, not a tenant one: it can resolve any
    # clinic, so it must be long and random. Leave it unset to disable
    # tenant resolution entirely — the agent then falls back to its
    # single-tenant BACKEND_API_KEY exactly as before Phase 2.
    agent_platform_key: str = ""

    # Lifetime of the org-scoped token handed to the agent. Only has to
    # outlive a phone call; the agent re-resolves on the next one.
    agent_token_expire_minutes: int = 120

    # Per-IP throttle on tenant resolution. Generous enough for a busy
    # worker (one call per resolution) but not a brute-force oracle.
    agent_resolve_rate_limit_attempts: int = 120
    agent_resolve_rate_limit_window_seconds: int = 60

    # ============================================================
    # SCHEDULER
    # ============================================================
    # In-process only, because this deployment is SQL Server Express —
    # no SQL Agent, and no separate worker service. See app/scheduler.py
    # for why a DB row, not a timer, is what makes it safe.
    scheduler_enabled: bool = True

    # How often the loop wakes. Jobs declare their own interval; this is
    # just the resolution at which "is anything due" is asked, so it does
    # not need to be small.
    scheduler_tick_seconds: int = 60

    # A call still `in_progress` this long after it started is treated as
    # abandoned. The voice agent finalises on shutdown, but a killed
    # process runs no shutdown callback, and the row then stays "live" on
    # the dashboard forever while still counting as a connected call.
    #
    # Well above any real call length on purpose: reaping a call that is
    # merely long would end a conversation in the UI while the patient is
    # still talking.
    stale_call_timeout_minutes: int = 30
    stale_call_sweep_interval_seconds: int = 300

    # --- Automatic call quality evaluation (F10) ---
    #
    # OFF by default, and that is a deliberate reversal-guard rather than
    # timidity. Each judged call is a paid model request, and the design
    # note on AI-7 says so explicitly: judging every call doubles the
    # per-call LLM cost for a signal nobody reads live. Turning that on
    # without being asked would spend the owner's money to reverse a
    # decision they had already made and written down.
    #
    # Set QUALITY_AUTO_EVAL_ENABLED=true to sample automatically. The
    # per-org cap applies per run, so hourly ticks at the default give at
    # most 5 judged calls per clinic per hour — bounded even if a clinic
    # suddenly takes a thousand calls.
    quality_auto_eval_enabled: bool = False
    quality_auto_eval_interval_seconds: int = 3600
    quality_auto_eval_per_org_limit: int = 5

    # --- Weekly performance digest (F13) ---
    #
    # ON by default, unlike auto-evaluation above — the difference is what
    # the gate already is. Auto-evaluation's dependency (GROQ_API_KEY) is
    # configured, so defaulting it on would immediately start spending
    # money. This one is gated on SMTP, which is NOT configured, so
    # nothing is sent until someone deliberately sets up mail. At that
    # point a weekly operations summary to clinic admins is the expected
    # behaviour rather than a surprise, and costs nothing per send.
    weekly_digest_enabled: bool = True
    weekly_digest_interval_seconds: int = 604800  # 7 days
    weekly_digest_days: int = 7

    # --- Pre-appointment reminders (F3) ---
    #
    # ON by default, gated on a channel being configured — same reasoning
    # as the digest above, and the same gate: neither SMTP nor Twilio
    # WhatsApp is set here, so nothing is sent until someone deliberately
    # configures one. Costs nothing per send.
    #
    # The lead time is how far ahead of the appointment a reminder goes
    # out. 24 hours is the usual clinic default: long enough that the slot
    # can be re-offered if they cancel, short enough that they have not
    # forgotten again by the time it arrives.
    #
    # The sweep runs hourly rather than at a fixed hour because the
    # scheduler is interval-based, and because clinics sit in different
    # timezones — an hourly pass catches each one's window without the job
    # needing to know what "9am" means for any of them.
    appointment_reminders_enabled: bool = True
    appointment_reminder_lead_hours: int = 24
    appointment_reminder_interval_seconds: int = 3600

    # ============================================================
    # AUTH HARDENING
    # ============================================================

    min_password_length: int = 8

    # Per-IP: every login attempt, successful or not.
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    # Per-account: consecutive FAILED attempts for one email address,
    # counted across every source IP, cleared on a successful login.
    # This is what stops distributed credential stuffing against a single
    # known account, which per-IP limiting cannot see.
    login_account_rate_limit_attempts: int = 8
    login_account_rate_limit_window_seconds: int = 900

    register_rate_limit_attempts: int = 5
    register_rate_limit_window_seconds: int = 3600

    # ============================================================
    # STAFF INVITATIONS (HV-2)
    # ============================================================
    # How long a staff invite link stays valid. Long enough to survive a
    # weekend and a spam folder; short enough that a link forwarded or
    # left in an inbox does not stay usable indefinitely. An expired
    # invite is re-issued (with a fresh token) from the Team page.
    invite_expire_days: int = 7

    # --- Password reset (GAP-U1) ---
    #
    # Short, deliberately. A reset token is a bearer credential for an
    # existing account; an invitation is a welcome. One hour is long
    # enough to walk to a computer and short enough that a link left in an
    # inbox stops being useful.
    password_reset_expire_minutes: int = 60
    # Per IP and per account. The endpoint deliberately reveals nothing
    # about whether an address is registered, so the only thing left to
    # abuse is volume.
    password_reset_rate_limit_attempts: int = 5
    password_reset_rate_limit_window_seconds: int = 900

    # Per-IP throttle on POST /auth/accept-invite. The token is 32 random
    # bytes and unguessable, so this is not the primary defence — it just
    # stops the endpoint being used as a fast oracle.
    # ------------------------------------------------------------
    # Payments (Razorpay) -- HV-5
    # ------------------------------------------------------------
    # All three blank by default. Blank is a supported state, not a
    # broken one: `POST /billing/checkout` answers 503 with a plain
    # message and the Billing page says online payment is not switched on
    # yet. Nothing anywhere claims a card is on file.
    #
    # NEVER commit real values. Test keys (rzp_test_...) and live keys
    # (rzp_live_...) have the same shape, so the only thing separating a
    # sandbox from real money is which one is pasted here.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    # A SEPARATE secret from the key secret above, set when the webhook is
    # created in the Razorpay dashboard. Checkout signatures are keyed by
    # the key secret; webhook signatures are keyed by this. Using one for
    # the other fails every verification in a way that looks like a bad
    # integration rather than a wrong secret.
    razorpay_webhook_secret: str = ""

    # ISO 4217. `monthly_price_cents` is minor units, so 9900 is $99.00.
    #
    # Set to USD by the owner on 2026-09-10. It had defaulted to INR, which
    # disagreed with the catalogue it labelled: the plans are 9900 / 29900 /
    # 79900 minor units, and those are the $99 / $299 / $799 tiers the
    # platform actually sells. Read as rupees they quoted roughly an
    # eightieth of the intended price.
    #
    # Changing this does NOT reprice anything -- the stored minor-unit
    # amounts are untouched; only the currency they are denominated in and
    # the symbol rendered from it change. Historical payments are also
    # unaffected: `Payment.currency` is a per-row column and the invoice
    # document prefers it over this setting (routers/billing.py).
    #
    # It is served to the browser and formatted with Intl.NumberFormat
    # rather than a hardcoded symbol, which is why the pricing page,
    # checkout and invoice all follow it without further edits.
    billing_currency: str = "USD"

    # The same idea as VOICE_MOCK_MODE: replaces exactly the paid service
    # (Razorpay) with a local fake and leaves everything else real --
    # order records, signature verification, fulfilment, invoices,
    # subscription activation. It is how this is proved correct before
    # anything is bought. Reported as a startup warning, because a
    # deployment left in it takes no money while appearing to.
    razorpay_mock_mode: bool = False

    # ------------------------------------------------------------
    # How a prospect or a clinic reaches a person
    # ------------------------------------------------------------
    # Both blank by default and both are DELIBERATELY not invented here.
    # A phone number and an address are facts about a company, the way
    # EMERGENCY_NUMBER and DEFAULT_TRANSFER_NUMBER are facts about a
    # clinic -- configuration, not code. Blank is handled everywhere it
    # is read: the public contact page falls back to its form, and the
    # Support page's "Call us" card does not offer a number it does not
    # have. Nothing renders a placeholder.
    support_phone: str = ""
    support_email: str = ""
    # Shown beside the number so nobody rings at 3am expecting an answer.
    support_hours: str = ""

    # Public contact form. Unauthenticated and world-reachable, so it is
    # rate limited per IP like every other public write. Five an hour is
    # generous for a human and useless for a spammer.
    contact_rate_limit_attempts: int = 5
    contact_rate_limit_window_seconds: int = 3600

    accept_invite_rate_limit_attempts: int = 10
    accept_invite_rate_limit_window_seconds: int = 900

    # ============================================================
    # SCHEDULING
    # ============================================================
    # Upper bound on open slots returned for one doctor-day by
    # GET /appointments/availability. Must comfortably exceed a full
    # working day at the doctor's slot length, otherwise later slots are
    # never offered to callers or staff.

    availability_max_slots: int = 32

    # ============================================================
    # CALL INTELLIGENCE — LLM (AI-2)
    # ============================================================
    # Groq, via its OpenAI-compatible REST API. Deliberately called with
    # plain httpx (already a dependency, already used by ehr_service and
    # notification_service) rather than adding an SDK for one endpoint.
    #
    # Leave `groq_api_key` unset to disable LLM summarisation entirely —
    # `call_intelligence` then keeps using its existing rule-based path,
    # exactly as before AI-2. That is the default and the rollback switch.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # The agent's worker uses openai/gpt-oss-20b for latency during a live
    # call. Summarisation runs *after* the call in a background task, so
    # it can afford the larger 120b for materially better extraction —
    # nobody is waiting on it.
    #
    # Verify against GET {groq_base_url}/models before changing this: a
    # model name this key cannot access returns a 404 that llm_service
    # correctly refuses to retry, and every call then silently keeps the
    # rule-based summary.
    groq_model: str = "openai/gpt-oss-120b"

    # Low but not zero: leaves room for natural phrasing in text a
    # caller actually hears. This is the RAG answer path
    # (`_call_groq_text`), where a slightly different wording of the same
    # fact is fine and a robotic one is not.
    groq_temperature: float = 0.2

    # Call analysis is a different job and wants the opposite thing.
    #
    # `outcome`, `sentiment` and `intent` are picks from closed
    # vocabularies -- they are classifications of a fixed transcript, not
    # writing. Any variance there is noise, and it was observed: two runs
    # over one identical mock call recorded `sentiment` as "Positive" and
    # then "Neutral". A dashboard column that disagrees with itself about
    # a call nobody touched is one a clinic learns to ignore.
    #
    # Zero, with a fixed seed below. Note what this does NOT promise:
    # LLM inference is not bit-reproducible even at temperature 0 --
    # batching and floating-point ordering still move results, and Groq
    # documents `seed` as best-effort. This removes the sampling variance,
    # which is the part that was actually causing the disagreement.
    groq_analysis_temperature: float = 0.0

    # Any fixed value works; it exists to be stable, not to be special.
    groq_analysis_seed: int = 42

    # Cost guard. A transcript is the only unbounded input here, so it is
    # truncated to this many characters before being sent — a very long
    # call must not turn into an unbounded bill. ~12k chars is a
    # comfortably long consultation.
    llm_max_transcript_chars: int = 12000
    llm_max_completion_tokens: int = 700
    llm_timeout_seconds: float = 20.0

    # Transient failures only (timeouts, connection errors, 5xx). A 4xx is
    # never retried — the request itself is wrong.
    llm_max_retries: int = 1
    llm_retry_backoff_seconds: float = 0.75

    # ============================================================
    # KNOWLEDGE BASE — RETRIEVAL (AI-3)
    # ============================================================
    # Embeddings are computed LOCALLY (fastembed / ONNX). Nothing about a
    # clinic's documents or a patient's question leaves this machine —
    # which is the point: uploaded handbooks and the questions asked of
    # them are PHI-adjacent, and shipping them to a hosted embedding API
    # would be a new data-processor relationship to justify.
    #
    # BAAI/bge-small-en-v1.5: 384 dims, ~1.5 KB per vector, quantised
    # ONNX — no torch dependency. At clinic scale (hundreds to low
    # thousands of chunks per org) brute-force cosine in numpy is
    # sub-millisecond, so there is no vector database to operate. This
    # deployment is SQL Server 2022 Express, which has no native VECTOR
    # type (2025+ only), so vectors are stored as VARBINARY.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Where the ONNX weights are cached. Defaults to a directory beside
    # the app rather than the system temp folder, so the model is not
    # re-downloaded on every reboot.
    embedding_cache_dir: str = ""

    # Chunking. Tuned empirically against a clinic handbook, not guessed:
    # at 800 chars the whole document collapsed into ONE chunk whose
    # embedding averaged hours + fees + parking together, and every
    # question scored a mushy 0.56-0.64. Splitting to ~280 lifted real
    # questions to 0.63-0.82 while pushing an off-topic question down to
    # 0.41. Small, topically-focused chunks are what make retrieval
    # discriminate at all.
    kb_chunk_chars: int = 280
    kb_chunk_overlap_chars: int = 60

    # How many chunks are retrieved and handed to the synthesiser.
    kb_retrieval_top_k: int = 4

    # A COARSE FILTER — deliberately NOT the safety gate.
    #
    # The tempting design is a high threshold that only lets confident
    # matches through. Measured on a real handbook, that does not work:
    # the lowest *answerable* question scored 0.648 and the highest
    # *unanswerable* one 0.645 — a 0.003 window. "Can I get a repeat
    # prescription?" and "What are your COVID vaccination times?" both
    # retrieve the hours/policy text with high similarity precisely
    # because they are plausible clinic questions. Cosine distance cannot
    # tell "related to this document" from "answered by this document".
    #
    # So the threshold only keeps obvious garbage out of the context
    # window, and **the synthesiser is the actual safety gate**: it is
    # instructed to return INSUFFICIENT_CONTEXT when the retrieved text
    # does not contain the answer, which it does correctly for exactly
    # the cases above. Raising this value does not make the system safer;
    # it makes it decline questions it could have answered.
    kb_min_similarity: float = 0.45

    # Cap on how much retrieved text is sent for synthesis.
    kb_max_context_chars: int = 4000

    # ============================================================
    # EHR HTTP CLIENT
    # ============================================================

    ehr_http_timeout_seconds: float = 8.0
    ehr_http_max_retries: int = 2
    ehr_http_retry_backoff_seconds: float = 0.5

    # ============================================================
    # PATIENT NOTIFICATIONS (appointment confirmations)
    # ============================================================
    # All optional — if a channel's settings are left unset, that channel
    # is silently skipped (same "not_configured, never break the caller's
    # primary action" philosophy as the EHR sync client below).

    # Email — sent via any SMTP provider (Gmail, SES, SendGrid SMTP relay, etc.)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: str = "MediCOR Clinic"
    smtp_use_tls: bool = True

    # WhatsApp — sent via Twilio's WhatsApp Business API.
    # whatsapp_from_number is the Twilio-provisioned sender, e.g. "whatsapp:+14155238886".
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    whatsapp_from_number: Optional[str] = None
    # SMS uses the very same Twilio Messages API and the same credentials
    # as WhatsApp above -- the only difference is that neither number
    # carries the "whatsapp:" channel prefix. That is why this arrives as
    # a handful of lines rather than a new integration.
    sms_from_number: Optional[str] = None

    # Which channel a patient gets when their record does not say.
    # "whatsapp" preserves the behaviour of every deployment that existed
    # before SMS was an option; "sms" is the right default for a
    # population unlikely to have WhatsApp.
    default_message_channel: str = "whatsapp"

    # --- Automatic callback outreach (PF-12) ---
    #
    # Default ON, matching the reasoning the reminder job set: the gate
    # that would make it act (a configured message transport) is itself an
    # opt-in, so nothing is sent until someone deliberately sets up
    # messaging. Contrast auto-evaluation, which defaults OFF because its
    # gate is already configured and enabling it would start spending.
    missed_call_outreach_enabled: bool = True
    missed_call_outreach_interval_seconds: int = 3600
    # Only calls from the recent past. A patient who rang four days ago
    # and gave up has moved on; an apology then is noise, not service.
    missed_call_outreach_max_age_hours: int = 24
    # Give a human the first chance. Staff work the callback queue, and
    # this exists to catch what they did not get to, not to race them.
    missed_call_outreach_delay_minutes: int = 30

    # --- Patient feedback (PF-10) ---
    #
    # Default OFF, unlike the other outreach jobs. Every other message
    # this product sends is something the patient needs -- a confirmation,
    # a reminder, an outcome. This one is something the *clinic* wants,
    # and messaging patients to ask them a favour is a decision a clinic
    # should make deliberately rather than inherit.
    feedback_requests_enabled: bool = False
    feedback_request_interval_seconds: int = 3600
    # Wait until the call is well finished, so the message does not arrive
    # while they are still putting the phone down.
    feedback_request_delay_minutes: int = 60
    # And not so late that they have forgotten the call.
    feedback_request_max_age_hours: int = 24
    # A frequent caller must not be asked every time. This is per patient,
    # not per call -- being surveyed after every contact is how a clinic
    # teaches people to ignore its messages.
    feedback_request_cooldown_days: int = 30

    # --- Waiting list (PF-15) ---
    #
    # On by default, gated the same way the reminder job is: the thing
    # that would make it act (a configured transport) is itself an opt-in,
    # so nothing is offered until someone sets up messaging.
    waiting_list_enabled: bool = True
    waiting_list_interval_seconds: int = 900
    # How long an offer is exclusive to one patient before it passes on.
    # Long enough for someone to see a message and reply; short enough
    # that a slot is not parked for a day.
    waiting_list_offer_ttl_minutes: int = 120
    # How far ahead to look for a freed slot.
    waiting_list_horizon_days: int = 14
    # Cap the work one sweep does, so a large backlog cannot turn a
    # scheduled tick into a long-running job.
    waiting_list_max_offers_per_run: int = 25

    # --- Post-visit follow-up (PF-14) ---
    #
    # On by default and yet inert by default, which is the point: the job
    # sends nothing until a clinic has written its own follow-up wording.
    # Aftercare text is a clinical statement and belongs to the clinic.
    post_visit_follow_up_enabled: bool = True
    post_visit_follow_up_interval_seconds: int = 3600
    # Don't chase an appointment from last month if the job was off.
    post_visit_follow_up_max_age_days: int = 30

    # --- Inbound patient messages (PF-07) ---
    #
    # The public URL Twilio signed. Signature verification hashes the URL
    # Twilio *called*, which behind a proxy is not the URL this process
    # sees -- so a deployment states it here rather than having every
    # signature silently fail. Unset falls back to the request URL, which
    # is correct when nothing rewrites the host.
    public_base_url: str = ""
    # Public and unauthenticated, so limited per client like login is.
    inbound_message_rate_limit_attempts: int = 60
    inbound_message_rate_limit_window_seconds: int = 300

    notification_http_timeout_seconds: float = 8.0

    # What an unrecorded contact consent means (PF-05).
    #
    # `Patient.contact_consent` is tri-state: granted, declined, or NULL
    # for "nobody ever asked". NULL is not an edge case -- it is what every
    # patient row created before this feature holds, and what the voice
    # agent leaves behind whenever a caller is registered without being
    # asked. So the reading has to be a deliberate choice rather than an
    # accident of implementation.
    #
    # False (the default) is the cautious reading: an unasked patient is
    # not messaged. A clinic that has collected consent by other means --
    # on a paper registration form, say -- can flip this to True and have
    # its existing roster reachable immediately.
    #
    # Deliberately global rather than per-clinic. Making it per-clinic is
    # a reasonable future change, but it is a data-protection decision
    # that ought to be made once and visibly, not buried in a settings
    # screen where it can be toggled without thought.
    contact_unasked_is_consent: bool = False

    # --- Patient self-service link (PF-06) ---
    #
    # The link stays valid until shortly after the appointment it belongs
    # to; its purpose ends when the appointment does. The grace period
    # exists so a patient running late can still cancel rather than simply
    # not arriving, which is the outcome the clinic most wants to avoid.
    appointment_link_grace_hours: int = 2
    # Fallback bound for an appointment with no start time (a staff-entered
    # slot carrying only a label). Prevents an immortal token.
    appointment_link_max_days: int = 30
    # This is a public, unauthenticated endpoint reachable by anyone who
    # can reach the API, so it is rate limited per client the same way
    # login is. Generous enough for a patient tapping around a page,
    # tight enough that guessing tokens is not viable.
    appointment_link_rate_limit_attempts: int = 30
    appointment_link_rate_limit_window_seconds: int = 300

    # Base URL of the patient-facing site, used to build the link that
    # goes in a message. Unset means no link is included -- the message
    # still sends, it simply asks the patient to call instead. Never
    # invent a host: a dead link is worse than no link.
    patient_link_base_url: str = ""

    # Outbound webhooks (F14). Short on purpose: this runs in a background
    # task after the response, but a clinic's endpoint being slow must not
    # keep a worker thread parked. One attempt, then the outcome is
    # recorded on the webhook row.
    webhook_timeout_seconds: float = 8.0

    # --- Webhook retry (GAP-T4) ---
    #
    # F14 shipped one attempt and said plainly that if retries were wanted
    # the scheduler was the place, because its claim row already solves
    # the double-send problem that makes retries dangerous. The scheduler
    # now exists.
    #
    # Only failed deliveries are queued, so the table is empty while a
    # clinic's endpoint is healthy -- which was the objection to a
    # per-event delivery table in the first place.
    webhook_retry_enabled: bool = True
    webhook_retry_interval_seconds: int = 300
    # Total attempts INCLUDING the first, immediate one. Four means the
    # last try lands roughly half an hour out; beyond that a clinic's
    # endpoint is down rather than flapping, and a human should look.
    webhook_max_attempts: int = 4
    # Backoff in minutes between queued retries, indexed by attempt.
    webhook_retry_backoff_minutes: str = "1,5,30"
    # How long a settled row is kept before pruning, so the table stays
    # bounded without losing the recent record staff might ask about.
    webhook_delivery_retention_days: int = 7
    # Cap the work one sweep does.
    webhook_retry_max_per_run: int = 50

    # ============================================================
    # UPLOAD LIMIT
    # ============================================================

    max_upload_bytes: int = 20 * 1024 * 1024

    # ============================================================
    # ENVIRONMENT FILE
    # ============================================================

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # ============================================================
    # HELPERS
    # ============================================================

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def uses_default_secret_key(self) -> bool:
        return self.secret_key == DEFAULT_SECRET_KEY

    @property
    def cors_is_local_only(self) -> bool:
        """
        Whether every configured CORS origin points at this machine.

        The shipped default is four localhost ports, which is correct for
        development and useless in production: the browser blocks every
        call from the deployed dashboard, and the failure surfaces as a
        login that silently does nothing rather than as an error anybody
        can act on.

        It is also **not only a CORS problem**. `users._invite_url` and
        the password-reset link both derive from the FIRST origin in this
        list -- it is the only place the backend is told where its
        frontend lives -- so a localhost value emails staff a link to
        their own laptop.
        """
        origins = self.cors_origin_list
        if not origins:
            return True
        return all(_host_of(o) in _LOCAL_HOSTS for o in origins)

    @property
    def trusted_proxy_networks(self) -> list[_IPNetwork]:
        """
        Parsed `trusted_proxy_ips`. Bare addresses ("10.0.0.4") are treated
        as single-host networks; CIDR blocks ("10.0.0.0/8") are used as
        given. Unparseable entries are dropped with a warning rather than
        crashing startup — a typo here must never take the API down, and
        dropping the entry fails closed (that proxy simply isn't trusted).
        """
        networks: list[_IPNetwork] = []
        for raw in self.trusted_proxy_ips.split(","):
            entry = raw.strip()
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning(
                    "Ignoring unparseable TRUSTED_PROXY_IPS entry %r — "
                    "X-Forwarded-For from that proxy will NOT be trusted.",
                    entry,
                )
        return networks

    # ============================================================
    # SQL SERVER CONNECTION
    # ============================================================

    @property
    def resolved_database_url(self):
        """
        SQL Server is the ONLY supported database.

        No SQLite fallback.
        """

        server = self.db_server

        if self.db_port:
            server = f"{server},{self.db_port}"

        query = {
            "driver": self.db_driver,
            "Encrypt": "yes" if self.db_encrypt else "no",
            "TrustServerCertificate": (
                "yes"
                if self.db_trust_server_certificate
                else "no"
            ),
        }

        if self.db_trusted_connection:
            # Windows auth: the credentials come from the process token,
            # so username and password must be absent rather than empty —
            # pyodbc rejects a connection string carrying both
            # Trusted_Connection and UID.
            query["Trusted_Connection"] = "yes"
            return URL.create(
                "mssql+pyodbc",
                host=server,
                database=self.db_name,
                query=query,
            )

        return URL.create(
            "mssql+pyodbc",
            username=self.db_user,
            password=self.db_password,
            host=server,
            database=self.db_name,
            query=query,
        )


settings = Settings()