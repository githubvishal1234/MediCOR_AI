"""
Minimal in-process rate limiting — Phase 8, hardened in Phase 0.

A single-process, in-memory sliding-window limiter for sensitive,
unauthenticated endpoints (login, register) to blunt brute-force /
credential-stuffing and registration-spam attempts. This is
intentionally simple (no Redis dependency) — good enough for a single
backend instance; if the app is later scaled horizontally, swap the
in-memory store for a shared one (Redis, etc.) without changing the
call sites below.

Two independent limiters live here, because they stop different attacks:

  * `enforce_rate_limit`      — keyed on client IP. Stops one host
                               hammering an endpoint.
  * `enforce_account_rate_limit` / `record_account_failure` — keyed on
                               the submitted account identity (email).
                               Stops a *distributed* attack against one
                               known account, which per-IP limiting
                               cannot see at all. Only failed attempts
                               are counted, and a successful login
                               clears the counter, so an ordinary user
                               who mistypes a password a few times and
                               then succeeds is never penalised.

Client IP resolution (`client_ip`) deliberately does NOT trust
X-Forwarded-For unless the direct peer is a configured trusted proxy.
Before Phase 0 the header was honoured unconditionally, which meant any
client could send a fresh random value on every request and bypass this
limiter entirely.

Not a substitute for a WAF/edge rate limiter in front of the API, but
stops naive automated abuse from exhausting login attempts directly
against this service.
"""

import ipaddress
import time
from collections import defaultdict
from threading import Lock
from typing import Optional

from fastapi import HTTPException, Request, status

from .config import settings

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


# ---------------------------------------------------------------------
# Client IP resolution
# ---------------------------------------------------------------------

def client_ip(request: Optional[Request]) -> str:
    """
    The caller's IP address, as trustworthy as this deployment allows.

    Returns the socket peer address unless that peer is a configured
    trusted proxy (settings.trusted_proxy_ips), in which case the
    right-most X-Forwarded-For entry that is *not itself* a trusted
    proxy is used — that is the address the outermost trusted proxy
    actually observed, and the left-most value a client can no longer
    forge past.

    With no trusted proxies configured (the default), X-Forwarded-For is
    ignored completely.
    """
    if request is None:
        return "unknown"

    peer = request.client.host if request.client else None
    if not peer:
        return "unknown"

    trusted = settings.trusted_proxy_networks
    if not trusted or not _is_trusted(peer, trusted):
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer

    # Walk right-to-left; the first address that isn't one of our own
    # proxies is the earliest hop we can still vouch for.
    for raw in reversed(forwarded.split(",")):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            # Garbage in the chain — stop trusting anything further left.
            return peer
        if not _is_trusted(candidate, trusted):
            return candidate

    # Every hop in the chain was one of our proxies.
    return peer


def _is_trusted(address: str, trusted: list) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in trusted)


# ---------------------------------------------------------------------
# Shared sliding-window core
# ---------------------------------------------------------------------

def _prune(key: str, window_seconds: int) -> list[float]:
    """Drop entries outside the window. Caller must hold `_lock`."""
    cutoff = time.monotonic() - window_seconds
    recent = [t for t in _attempts[key] if t > cutoff]
    if recent:
        _attempts[key] = recent
    else:
        _attempts.pop(key, None)
    return recent


def _too_many(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
    )


# ---------------------------------------------------------------------
# Per-IP limiter (pre-existing behaviour, unchanged signature)
# ---------------------------------------------------------------------

def _client_key(request: Request, bucket: str) -> str:
    return f"{bucket}:{client_ip(request)}"


def enforce_rate_limit(request: Request, bucket: str, max_attempts: int, window_seconds: int) -> None:
    """
    Raises 429 if the caller has exceeded `max_attempts` within the
    trailing `window_seconds` for the given `bucket` (e.g. "login").
    Call this once per attempt, including failed ones — callers should
    invoke it before doing the expensive/sensitive work.
    """
    key = _client_key(request, bucket)

    with _lock:
        recent = _prune(key, window_seconds)
        if len(recent) >= max_attempts:
            _attempts[key] = recent
            raise _too_many("Too many attempts. Please try again later.")
        recent.append(time.monotonic())
        _attempts[key] = recent


# ---------------------------------------------------------------------
# Per-account limiter (Phase 0)
# ---------------------------------------------------------------------

def _account_key(identity: str, bucket: str) -> str:
    # Case-folded so "Admin@x.com" and "admin@x.com" share one counter.
    return f"{bucket}:account:{(identity or '').strip().lower()}"


def enforce_account_rate_limit(
    identity: str, bucket: str, max_attempts: int, window_seconds: int
) -> None:
    """
    Raises 429 if this account identity has accumulated `max_attempts`
    failures within the window. Checks only — it does not record. Call
    it *before* verifying the password, then call
    `record_account_failure` if verification fails.

    Keyed on the submitted identity rather than on a resolved user row,
    so an unknown email is throttled identically to a real one and this
    never becomes a user-enumeration oracle.
    """
    key = _account_key(identity, bucket)
    with _lock:
        recent = _prune(key, window_seconds)
        if len(recent) >= max_attempts:
            _attempts[key] = recent
            raise _too_many(
                "Too many failed sign-in attempts for this account. "
                "Please wait a few minutes and try again."
            )


def record_account_failure(identity: str, bucket: str, window_seconds: int) -> None:
    """Records one failed attempt against an account identity."""
    key = _account_key(identity, bucket)
    with _lock:
        recent = _prune(key, window_seconds)
        recent.append(time.monotonic())
        _attempts[key] = recent


def clear_account_failures(identity: str, bucket: str) -> None:
    """
    Clears an account's failure counter after a successful sign-in, so a
    legitimate user who mistyped a couple of times starts clean.
    """
    with _lock:
        _attempts.pop(_account_key(identity, bucket), None)
