"""
Outbound webhooks to a clinic's own systems (F14).

WHAT WAS ACTUALLY WRONG

The `webhooks` table, `GET`/`PUT /ehr/webhook`, the config screen on the
EHR Integration page and a landing-page bullet reading "Webhooks for
custom infrastructure" all existed. **Nothing ever delivered anything.**
`endpoint_url` was written by the clinic and then only ever read back out
in the same endpoint's response. The three offered events —
`transcript.completed`, `intent.requires_action`, `agent.error` — were not
emitted anywhere in the codebase either; they had never been real.

So this is not a new capability so much as closing a promise the product
was already making, in the same class as `call_logs.recording_url` — a
column nothing writes, with playback code and an API field built around
it. The webhook case is the worse of the two: its config screen really
did save, and the landing page really did advertise it, whereas the
recording control was gated on a value that is always null and so never
rendered.

DESIGN, AND WHY IT MATCHES THE NEIGHBOURS

`ehr_service` and `notification_service` share one rule: a side effect
must never break the thing it is a side effect of. A booking has already
been committed by the time we try to tell anyone about it, so a slow or
dead endpoint at a clinic must not turn a succeeded booking into a 500,
and must not make the voice agent believe its call update failed.

This module follows that exactly:

  * delivery runs from `BackgroundTasks`, after the response;
  * every failure is caught and recorded, never raised;
  * the outcome is written to `Webhook.last_delivery_status` so the
    config screen can say something true about whether it works.

WHY SIGNED

The endpoint URL is chosen by the clinic and will sit on the public
internet. Without a signature any party that learns or guesses it could
post fabricated appointments and refill requests into their systems.
Every delivery carries an HMAC-SHA256 of the exact body under the
webhook's `secret`, so the receiver can verify it came from us.

ONE ATTEMPT, DELIBERATELY

There is no retry queue. Retrying needs somewhere durable to hold the
attempt, and a per-event delivery table would collect a row for every
call a busy clinic takes. `ehr_service` made the same call for the same
reason. If retries are wanted later, the scheduler (F2) is the place —
its `scheduled_job_runs` claim already solves the double-send problem
that makes retries dangerous.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks

from . import models
from .config import settings

logger = logging.getLogger("webhook-service")

SIGNATURE_HEADER = "X-MedVoice-Signature"
EVENT_HEADER = "X-MedVoice-Event"
DELIVERY_HEADER = "X-MedVoice-Delivery"


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def sign(secret: str, body: bytes) -> str:
    """
    `sha256=<hex>` over the exact bytes sent.

    Over the raw body, not a re-serialised dict: the receiver verifies
    what arrived on the wire, and any difference in key order or spacing
    between our two serialisations would make every signature fail.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _subscribed(hook: models.Webhook, event: str) -> bool:
    events = [e.strip() for e in (hook.events_csv or "").split(",") if e.strip()]
    return event in events


def backoff_minutes() -> list[int]:
    """Parsed `WEBHOOK_RETRY_BACKOFF_MINUTES`. A bad value falls back."""
    try:
        out = [int(x) for x in settings.webhook_retry_backoff_minutes.split(",") if x.strip()]
        return out or [1, 5, 30]
    except ValueError:
        logger.warning("WEBHOOK_RETRY_BACKOFF_MINUTES is unparseable; using the default")
        return [1, 5, 30]


def blocked_target(url: str) -> Optional[str]:
    """
    Why a webhook URL must not be called, or None if it may be.

    The endpoint is clinic-chosen and anyone can register a clinic, so
    without this a webhook is a way to make this server POST into its own
    network -- the database host, the voice gateway, anything on the LAN --
    and read back whether the port answered from `last_delivery_status`.
    Only http(s) to globally routable addresses is allowed. Checked when
    the URL is saved and again before every send, since DNS can change.

    An unresolvable name is let through: the request itself then fails and
    reports it, which is the honest status for a typo.

    ponytail: httpx resolves the name again after this check, so DNS
    rebinding between the two is not closed; pin the resolved IP in the
    transport if that ever matters.
    """
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "must be an http:// or https:// URL"
    host = parts.hostname
    if host == "localhost" or host.endswith(".localhost"):
        return "must not point at this server"
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(info[4][0].split("%")[0])
                for info in socket.getaddrinfo(host, None)
            }
        except (OSError, UnicodeError, ValueError):
            return None
    if any(not a.is_global for a in addresses):
        return "must be a public address, not a private or internal one"
    return None


def post_body(hook: models.Webhook, event: str, delivery_id: str, body: bytes) -> tuple[bool, str]:
    """
    Send one payload, exactly as given. Returns (delivered, status).

    Takes the body as bytes rather than a dict because the signature is an
    HMAC over what goes on the wire -- a retry must send the identical
    bytes, and re-serialising a dict could change key order or spacing and
    break every signature.

    The signature IS recomputed here rather than stored, so a clinic that
    rotates its secret between attempts still gets something it can
    verify.

    Never raises: a clinic's endpoint is not our uptime.
    """
    reason = blocked_target(hook.endpoint_url)
    if reason:
        logger.warning("Webhook %s -> %s refused: URL %s", event, hook.endpoint_url, reason)
        return False, "refused: not a public address"
    try:
        resp = httpx.post(
            hook.endpoint_url,
            content=body,
            headers={
                "Content-Type": "application/json",
                EVENT_HEADER: event,
                DELIVERY_HEADER: delivery_id,
                SIGNATURE_HEADER: sign(hook.secret, body),
            },
            timeout=settings.webhook_timeout_seconds,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Webhook %s -> %s returned %s", event, hook.endpoint_url, resp.status_code
            )
            return False, str(resp.status_code)
        return True, str(resp.status_code)
    except httpx.TimeoutException:
        logger.warning("Webhook %s -> %s timed out", event, hook.endpoint_url)
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001 - a clinic's endpoint is not our uptime
        logger.warning("Webhook %s -> %s failed: %s", event, hook.endpoint_url, exc)
        return False, f"error: {type(exc).__name__}"


def _queue_retry(
    db, hook: models.Webhook, org_id: str, event: str,
    delivery_id: str, body: bytes, status: str,
) -> None:
    """
    Put a failed delivery in the retry queue.

    **Only failures get a row.** A delivery that succeeds writes nothing
    here, which is what keeps this table empty for a clinic whose endpoint
    is healthy -- the objection that kept retries out of F14.
    """
    if not settings.webhook_retry_enabled:
        return
    schedule = backoff_minutes()
    db.add(
        models.WebhookDelivery(
            org_id=org_id,
            webhook_id=hook.id,
            event=event,
            delivery_id=delivery_id,
            body=body.decode("utf-8"),
            status="pending",
            attempts=1,  # the immediate one just failed
            next_attempt_at=datetime.utcnow() + timedelta(minutes=schedule[0]),
            last_status=status[:120],
        )
    )


def _deliver(org_id: str, event: str, data: dict[str, Any]) -> None:
    """
    The background task. Opens its own session — the request's is closed
    by the time this runs — and never raises.
    """
    from .database import SessionLocal

    db = None
    try:
        db = SessionLocal()
        hook = (
            db.query(models.Webhook)
            .filter(models.Webhook.org_id == org_id)
            .first()
        )
        if hook is None or not (hook.endpoint_url or "").strip():
            return
        if not _subscribed(hook, event):
            return

        if not hook.secret:
            # A webhook configured before signing existed. Mint one now
            # rather than sending unsigned — an unsigned delivery is the
            # thing the signature exists to prevent.
            hook.secret = generate_secret()
            db.commit()

        delivery_id = secrets.token_hex(12)
        body = json.dumps(
            {
                "event": event,
                "delivery_id": delivery_id,
                # UTC, ISO-8601 with an explicit Z so a receiver in
                # another zone cannot read it as local time.
                "occurred_at": datetime.utcnow().isoformat() + "Z",
                "org_id": org_id,
                "data": data,
            },
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")

        delivered, status = post_body(hook, event, delivery_id, body)
        if not delivered:
            _queue_retry(db, hook, org_id, event, delivery_id, body, status)

        hook.last_delivery_at = datetime.utcnow()
        hook.last_delivery_status = status[:120]
        db.commit()
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.exception("Webhook delivery failed for org=%s event=%s", org_id, event)
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()


def schedule(
    background_tasks: Optional[BackgroundTasks],
    org_id: str,
    event: str,
    data: dict[str, Any],
) -> bool:
    """
    Queue a delivery for after the response is sent.

    Returns whether anything was queued, which is False in the ordinary
    case that this clinic has no webhook configured.

    Only ids and plain values should go in `data` — never an ORM
    instance. The session it was loaded from is closed by the time the
    task runs, and a lazy attribute access would then raise inside the
    background task. Same rule as `notification_service`.
    """
    if background_tasks is None:
        return False
    if event not in WEBHOOK_EVENTS:
        # Loud, because a typo here fails silently forever: the event
        # simply never matches a subscription and nothing is delivered.
        logger.error("Refusing to send unknown webhook event %r", event)
        return False
    background_tasks.add_task(_deliver, org_id, event, data)
    return True


# Imported at the bottom to avoid a cycle: schemas imports security, which
# has no dependency on this module, but keeping the import here documents
# that the vocabulary is owned by schemas and shared with the API surface.
from .schemas import WEBHOOK_EVENTS  # noqa: E402
