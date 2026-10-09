"""
Everything that speaks Twilio: signature verification, TwiML, and the
handful of REST calls this gateway makes.

Twilio-specific knowledge is confined to this module on purpose. The
bridge asks for "transfer this call" and "hang up"; how that is spelled
in TwiML, and which REST verb performs it, does not leak into the parts
of the system that reason about a conversation. The project has changed
telephony carrier once already.

NO `twilio` SDK. The backend already calls Twilio's Messages API with
plain `httpx` (`notification_service._send_twilio_message`) and already
implements Twilio's request-signature scheme by hand
(`routers/inbound_messages.verify_twilio_signature`). Adding the SDK here
would introduce `requests`, `PyJWT` and `aiohttp` to a venv shared with
the API process in order to do two things this codebase already does. The
signature check below is deliberately the same algorithm as the backend's
-- if one is ever found wrong, both are wrong in the same way and are
found together.
"""

import hmac
import logging
from base64 import b64encode
from hashlib import sha1
from typing import Mapping, Optional
from xml.sax.saxutils import escape, quoteattr

import httpx

from . import settings

logger = logging.getLogger("twilio-deepgram.twilio")


# ---------------------------------------------------------------------
# Request signatures
# ---------------------------------------------------------------------


def verify_signature(url: str, form: Mapping[str, str], signature: Optional[str]) -> bool:
    """
    Twilio's request signature: HMAC-SHA1 over the full URL with every
    POST parameter appended in alphabetical order, keyed by the account
    auth token, base64 encoded.

    Compared with `hmac.compare_digest` rather than `==`, and an unset
    auth token verifies nothing rather than everything -- it fails
    closed. These endpoints answer the phone; without this, anyone who
    learned the URL could make the clinic's agent place and hold
    conversations at the clinic's expense.
    """
    token = settings.TWILIO_AUTH_TOKEN
    if not token or not signature:
        return False
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    expected = b64encode(
        hmac.new(token.encode("utf-8"), payload.encode("utf-8"), sha1).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def digits_of(number: Optional[str]) -> str:
    """
    A phone number reduced to comparable digits.

    The same line arrives in several shapes depending on where it came
    from -- "+91 80 7158 3192" typed into a settings screen, "+918071583192"
    from Twilio, "008918071583192" from a dial plan. Comparing the raw
    strings would call two spellings of one number different, which for
    the loop check below would mean not catching the loop.

    Trailing digits are what identify a line, so the comparison in
    `same_number` uses a suffix rather than equality: a clinic that
    stores its number without the country code still matches.
    """
    return "".join(ch for ch in (number or "") if ch.isdigit())


def same_number(a: Optional[str], b: Optional[str]) -> bool:
    """
    Whether two numbers are the same line, allowing for formatting.

    Deliberately a suffix comparison on at least 7 digits: a national
    subscriber number is enough to identify a line, and demanding full
    E.164 equality would miss exactly the mismatch this exists to catch.
    Seven, not fewer, so a short extension cannot match everything.
    """
    x, y = digits_of(a), digits_of(b)
    if not x or not y:
        return False
    shortest = min(len(x), len(y))
    if shortest < 7:
        return x == y
    return x[-shortest:] == y[-shortest:]


def public_url_for(path: str, request_url: str) -> str:
    """
    The URL Twilio signed, which is the one it called -- not necessarily
    the one this process sees behind a tunnel or a proxy.

    `VOICE_GATEWAY_PUBLIC_URL` exists so a deployment can state it;
    without it we fall back to the request's own URL, which is correct
    only when nothing rewrites the host. In development, behind ngrok,
    it is never correct, which is why every signature fails until that
    variable is set -- and why the error message says so.
    """
    base = settings.PUBLIC_BASE_URL
    if base:
        return f"{base}{path}"
    return request_url


# ---------------------------------------------------------------------
# TwiML
# ---------------------------------------------------------------------


def connect_stream(
    stream_url: str,
    parameters: Optional[Mapping[str, str]] = None,
    status_callback: Optional[str] = None,
) -> str:
    """
    The TwiML that hands a live call to this gateway's websocket.

    `<Connect><Stream>` rather than `<Start><Stream>` because this is a
    conversation, not a recording: Connect is bidirectional (we can send
    audio back) and it *blocks* -- Twilio runs no further TwiML until our
    websocket closes. That is what makes closing the socket a hang-up,
    which is how `end_call` works.

    Custom `<Parameter>` children are how the dialled and calling numbers
    reach the websocket handler, which otherwise receives only a stream
    id. Deliberately only non-secret facts: the org token minted by
    `/agent/resolve-tenant` is NOT passed this way. It is a credential
    that can read and write one clinic's patient records, and routing it
    out through a third party and back for convenience would be a poor
    trade. The gateway resolves the tenant itself, from the number.
    """
    params = "".join(
        f"<Parameter name={quoteattr(str(k))} value={quoteattr(str(v))}/>"
        for k, v in (parameters or {}).items()
        if v is not None
    )
    callback = f" statusCallback={quoteattr(status_callback)}" if status_callback else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Connect><Stream url={quoteattr(stream_url)}{callback}>{params}</Stream></Connect>"
        "</Response>"
    )


def say_and_hangup(message: str) -> str:
    """
    Spoken by Twilio itself, not by the agent.

    The fallback for a call we cannot handle at all -- no Deepgram key,
    no tenant, the gateway misconfigured. A caller must hear a sentence
    and a clean disconnect rather than ringing into silence or a Twilio
    error tone, and this is the only path available before the media
    stream exists.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"
    )


def dial(destination: str, caller_id: Optional[str] = None) -> str:
    """TwiML that connects the caller to a person."""
    number = destination.replace("tel:", "").strip()
    attrs = f" callerId={quoteattr(caller_id)}" if caller_id else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Dial{attrs}>{escape(number)}</Dial></Response>"
    )


# ---------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------


def _calls_url(call_sid: str) -> str:
    return (
        f"{settings.TWILIO_API_BASE}/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/Calls/{call_sid}.json"
    )


async def _post(url: str, data: dict[str, str]) -> Optional[dict]:
    """
    One authenticated Twilio REST call.

    Never raises: every caller is on the live-call path, where an
    exception would end a conversation that was otherwise fine. Failures
    are logged and reported as a return value the caller can act on.
    """
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio credentials are not configured; REST call skipped")
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.TWILIO_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                data=data,
            )
        if resp.status_code >= 400:
            # `resp.text` is Twilio's error body. It carries no
            # credential -- the account SID is in the URL, the token is
            # only ever in the Authorization header httpx builds.
            logger.warning("Twilio REST %s -> %s %s", url, resp.status_code, resp.text)
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("Twilio REST call failed: %s", e)
        return None


async def redirect_to_dial(
    call_sid: str, destination: str, caller_id: Optional[str] = None
) -> bool:
    """
    Transfer a live call to a person.

    This replaces LiveKit's `transfer_sip_participant`. The mechanism is
    different -- we rewrite the in-progress call's TwiML rather than
    re-pointing a SIP participant -- but the contract the rest of the
    system sees is identical: the caller ends up talking to a human and
    our media stream ends.

    A consequence worth knowing: redirecting closes the `<Connect>`, so
    the websocket receives a `stop` and the call is finalised through the
    normal path. Nothing extra is needed to record that the call ended.
    """
    # `Twiml`, not `TwiML`. That is Twilio's own casing for this form
    # parameter, verified against their Call resource documentation --
    # the brand is written TwiML everywhere else, so this looks like a
    # typo and correcting it would silently break every transfer.
    result = await _post(
        _calls_url(call_sid), {"Twiml": dial(destination, caller_id=caller_id)}
    )
    return result is not None


async def hangup(call_sid: str) -> bool:
    """
    End a call from our side.

    Used only as a backstop. The ordinary hang-up is closing the
    websocket, which ends the blocking `<Connect>` with no further TwiML
    to run -- one fewer round trip, and it cannot race the audio still
    being played out.
    """
    result = await _post(_calls_url(call_sid), {"Status": "completed"})
    return result is not None


async def place_call(to: str, from_: Optional[str] = None) -> Optional[str]:
    """
    Place an outbound call that connects to this gateway on answer.

    Preserved because the old stack could dial out (`agent.py` creates a
    SIP participant when handed a `phone_number`). Returns the new
    CallSid, or None if Twilio refused -- there is no partial success
    here to report.

    Note what the answering caller reaches: the same `/voice/incoming`
    webhook an inbound call reaches, so an outbound call gets the same
    agent, the same tools and the same tenant resolution. Two code paths
    for one conversation is how the two drift apart.
    """
    sender = from_ or settings.TWILIO_PHONE_NUMBER
    if not sender:
        logger.warning("No TWILIO_PHONE_NUMBER configured; cannot place a call")
        return None
    if not settings.PUBLIC_BASE_URL:
        logger.warning("No VOICE_GATEWAY_PUBLIC_URL configured; cannot place a call")
        return None

    url = (
        f"{settings.TWILIO_API_BASE}/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/Calls.json"
    )
    result = await _post(
        url,
        {
            "To": to,
            "From": sender,
            "Url": f"{settings.PUBLIC_BASE_URL}{settings.ROUTE_PREFIX}/incoming",
            "StatusCallback": f"{settings.PUBLIC_BASE_URL}{settings.ROUTE_PREFIX}/status",
            "StatusCallbackEvent": "completed",
        },
    )
    return (result or {}).get("sid")
