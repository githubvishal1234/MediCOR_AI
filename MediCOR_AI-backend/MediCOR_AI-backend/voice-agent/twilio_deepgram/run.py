r"""
Start the gateway.

    cd voice-agent
    ..\venv\Scripts\python.exe -m twilio_deepgram.run

Deliberately one worker. Nothing here holds cross-call state, so a second
worker would be safe in principle -- but Twilio addresses a call to
whichever process answered its webhook only by URL, and the media stream
is a long-lived socket. Scaling this is a load-balancer question, not a
`--workers` one, and putting the flag here would invite the wrong answer.

Also deliberately NOT `uvicorn twilio_deepgram.server:app`: that path
skips the configuration check below, and the failure it catches -- no
Deepgram key -- otherwise surfaces as a caller connecting to silence.
"""

import logging
import sys

import uvicorn

from . import settings


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logger = logging.getLogger("twilio-deepgram")

    problems = settings.startup_problems()
    fatal = [p for p in problems if "DEEPGRAM_API_KEY" in p]

    for problem in problems:
        logger.error("CONFIGURATION: %s", problem)

    if fatal:
        # There is no agent without a Deepgram key, and a process that
        # answers the phone and then cannot speak is worse than one that
        # refused to start. Everything else is loud but survivable: a
        # developer exercising /health or the TwiML endpoint should be
        # able to.
        logger.error("Refusing to start. Fix the above and try again.")
        return 1

    logger.info(
        "MediCOR voice gateway starting on %s:%s (stream=%s)",
        settings.GATEWAY_HOST,
        settings.GATEWAY_PORT,
        settings.stream_websocket_url() or "UNCONFIGURED",
    )
    # Describe the engine that is actually going to run. Logging the
    # managed agent's settings while the pipeline is running is how a
    # misconfiguration hides in plain sight in a support thread.
    if settings.VOICE_MOCK_MODE:
        logger.warning(
            "ENGINE: mock - scripted speech, rule-based replies, silent audio. "
            "Tools, backend and database are REAL."
        )
    elif settings.VOICE_ENGINE == "pipeline":
        logger.info(
            "ENGINE: pipeline | listen=deepgram/%s (%s) | think=%s | speak=sarvam/%s "
            "%s %s @ %sHz %s",
            settings.DEEPGRAM_STT_MODEL,
            settings.DEEPGRAM_STT_LANGUAGE,
            settings.LLM_MODEL,
            settings.SARVAM_TTS_MODEL,
            settings.SARVAM_TTS_SPEAKER,
            settings.SARVAM_TTS_LANGUAGE,
            settings.SARVAM_TTS_SAMPLE_RATE,
            settings.SARVAM_TTS_CODEC,
        )
    else:
        logger.info(
            "ENGINE: managed agent | listen=%s | think=%s/%s | speak=%s",
            settings.DEEPGRAM_STT_MODEL,
            settings.DEEPGRAM_LLM_PROVIDER,
            settings.DEEPGRAM_LLM_MODEL,
            settings.DEEPGRAM_TTS_PROVIDER,
        )

    uvicorn.run(
        "twilio_deepgram.server:app",
        host=settings.GATEWAY_HOST,
        port=settings.GATEWAY_PORT,
        workers=1,
        log_level="info",
        # Twilio's media stream is a plain websocket; `websockets` is
        # already present via uvicorn[standard] and is what the Deepgram
        # client uses too, so both ends of the bridge speak through one
        # implementation.
        ws="websockets",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
