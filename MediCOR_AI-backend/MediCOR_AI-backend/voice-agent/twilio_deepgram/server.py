"""
The gateway's HTTP surface: three endpoints and a health check.

    POST /voice/incoming   Twilio asks what to do with a ringing call.
                           Answers with TwiML handing it to the stream.
    WS   /voice/stream     The call's audio, both directions.
    POST /voice/status     Twilio reports a call's final state.
    GET  /health           Is this process alive and configured.

WHY THIS IS A SEPARATE PROCESS FROM THE API

It would be less deployment to fold these routes into the FastAPI backend
next door, and it would be wrong. Two reasons:

  * **The API cannot run more than one worker.** `app/instance.py` makes
    that fatal in production, because rate-limit counters and dashboard
    websockets are held in process memory. Putting live audio bridges in
    that same single worker means one clinic's phone traffic and every
    clinic's dashboard share one event loop, and a busy call degrades the
    dashboard for everyone. The gateway is stateless per call and can be
    scaled on its own.

  * **The architecture already draws this line.** The voice agent holds
    no business logic and reaches the backend over HTTP as an
    authenticated tenant. Moving it inside the API would put it one
    import away from the ORM, and that boundary is the reason a bug in
    the agent cannot become a bug in a patient record.

WHY THESE ENDPOINTS ARE PUBLIC, AND WHAT MAKES THAT SAFE

They are unauthenticated because Twilio calls them, not a user -- the
same position `routers/inbound_messages.py` is in, and it is held the
same way: every request's signature is verified against the account auth
token before anything in it is read, and an unset token verifies nothing
rather than everything.
"""

import logging

from fastapi import FastAPI, Request, Response, WebSocket

from . import settings, twilio_api
from .bridge import CallBridge

logger = logging.getLogger("twilio-deepgram.server")


def create_app() -> FastAPI:
    app = FastAPI(
        title="MediCOR Twilio + Deepgram gateway",
        version="1.0.0",
        # No interactive docs. This service has no human users; the only
        # thing a schema page here would do is describe the endpoints
        # that answer a clinic's phone to whoever finds them.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    prefix = settings.ROUTE_PREFIX

    @app.post(f"{prefix}/incoming")
    async def incoming_call(request: Request) -> Response:
        """
        Answer a ringing call with TwiML that opens the media stream.

        The dialled and calling numbers are passed through as `<Parameter>`
        children, because a media stream otherwise arrives knowing only
        its own id. That is enough to identify the clinic and to greet a
        returning caller, and it is deliberately all that travels this
        way -- the org token stays in this process (see
        twilio_api.connect_stream).
        """
        form = dict(await request.form())

        if settings.TWILIO_VALIDATE_SIGNATURE:
            url = twilio_api.public_url_for(request.url.path, str(request.url))
            if not twilio_api.verify_signature(
                url, form, request.headers.get("X-Twilio-Signature")
            ):
                logger.warning(
                    "Rejected an unsigned or mis-signed call webhook for %s. If this "
                    "is a real Twilio request, VOICE_GATEWAY_PUBLIC_URL probably does "
                    "not match the URL Twilio called.",
                    url,
                )
                return Response(status_code=403, content="Invalid signature")

        stream_url = settings.stream_websocket_url()
        if not stream_url:
            # There is no point connecting a caller to a stream we cannot
            # name. Say a sentence and hang up: a caller must never be
            # left holding an open, silent line.
            logger.error("VOICE_GATEWAY_PUBLIC_URL is not set; cannot open a stream")
            return Response(
                content=twilio_api.say_and_hangup(
                    "Sorry, our booking line is unavailable right now. "
                    "Please call back shortly."
                ),
                media_type="application/xml",
            )

        # Twilio's own field names. `To` is the clinic's number that was
        # rung, which is what identifies the tenant; `From` is the
        # caller. `Direction` distinguishes a call we placed from one we
        # received -- an outbound call reaches this same webhook on
        # answer, deliberately, so both directions get one agent.
        direction = (
            "outbound"
            if str(form.get("Direction", "")).startswith("outbound")
            else "inbound"
        )
        twiml = twilio_api.connect_stream(
            stream_url,
            parameters={
                "dialed_number": form.get("To"),
                "caller_phone": form.get("From"),
                "direction": direction,
            },
            status_callback=(
                f"{settings.PUBLIC_BASE_URL}{prefix}/status"
                if settings.PUBLIC_BASE_URL
                else None
            ),
        )
        logger.info(
            "Incoming %s call %s: %s -> %s",
            direction,
            form.get("CallSid"),
            form.get("From"),
            form.get("To"),
        )
        return Response(content=twiml, media_type="application/xml")

    @app.websocket(f"{prefix}/stream")
    async def media_stream(websocket: WebSocket) -> None:
        """
        One call's audio.

        Deliberately thin: accept, hand the socket to a `CallBridge`, and
        let it own everything from Twilio's `start` event to finalising
        the call record. A bridge that raises has already recorded the
        call as failed and closed its own resources; this only has to
        avoid taking the process down with it.
        """
        await websocket.accept()
        bridge = CallBridge(websocket)
        try:
            await bridge.run()
        except Exception:  # noqa: BLE001 - one bad call must not stop the gateway
            logger.exception("Media stream handler failed")

    @app.post(f"{prefix}/status")
    async def call_status(request: Request) -> Response:
        """
        Twilio's report of how a call ended.

        Informational only, and that is a decision rather than an
        omission: the call record is written by the bridge, which knows
        what actually happened in the conversation. This callback can
        arrive after that, and letting it overwrite a finalised record
        would replace "booked an appointment" with "completed".

        Always 200. Twilio retries a non-2xx, and there is nothing here
        worth a retry storm.
        """
        form = dict(await request.form())
        if settings.TWILIO_VALIDATE_SIGNATURE:
            url = twilio_api.public_url_for(request.url.path, str(request.url))
            if not twilio_api.verify_signature(
                url, form, request.headers.get("X-Twilio-Signature")
            ):
                logger.warning("Rejected an unsigned status callback")
                return Response(status_code=403, content="Invalid signature")

        logger.info(
            "Call %s ended: status=%s duration=%s",
            form.get("CallSid"),
            form.get("CallStatus"),
            form.get("CallDuration"),
        )
        return Response(status_code=204)

    @app.get("/health")
    def health() -> dict:
        """
        Liveness, plus whatever is misconfigured.

        `status` stays "ok" while configuration is wrong, for the same
        reason the backend's health check does: a load balancer reading
        "degraded" would pull the only process out of rotation and turn a
        misconfiguration into an outage. The problems are here to be
        alerted on; the verdict is not.
        """
        return {
            "status": "ok",
            "provider": settings.VOICE_PROVIDER,
            "agent": settings.VOICE_AGENT_PROVIDER,
            "stream_url": settings.stream_websocket_url() or None,
            "problems": settings.startup_problems(),
        }

    return app


app = create_app()
