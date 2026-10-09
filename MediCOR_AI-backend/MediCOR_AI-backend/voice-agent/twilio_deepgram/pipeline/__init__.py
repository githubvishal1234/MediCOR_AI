"""
The Deepgram STT + LLM + Sarvam TTS conversation pipeline.

This is the second of the gateway's two voice engines, and the default
one. The other (`twilio_deepgram/deepgram_agent.py`) uses Deepgram's
managed Voice Agent socket, which does STT, turn-taking, the LLM and TTS
behind a single connection.

WHY BOTH EXIST

Deepgram's managed agent accepts only `deepgram`, `cartesia`,
`eleven_labs`, `open_ai` and `aws_polly` as speak providers. **Sarvam is
not on that list**, so a Sarvam voice is impossible inside it. Sarvam's
Bulbul is the only one of the available voices that speaks Indian English
natively -- Deepgram's Aura voices offer American, British, Australian,
Irish and Filipino and nothing closer -- and for a clinic whose callers
are Indian that is worth orchestrating the pipeline ourselves.

So:

    VOICE_ENGINE=pipeline   Deepgram STT -> LLM -> Sarvam TTS   (default)
    VOICE_ENGINE=agent      Deepgram Voice Agent, one socket

Both share everything else in the gateway: the Twilio bridge, tenant
resolution, the tool layer, the prompt, the call record. Swapping engines
changes how the caller is heard and answered, not what the agent can do.

    audio.py         mu-law/PCM conversion. Verified against `audioop`.
    stt.py           Deepgram streaming transcription.
    llm.py           Any OpenAI-compatible chat endpoint, with tools.
    tts.py           Sarvam Bulbul streaming synthesis.
    orchestrator.py  Turn-taking, barge-in, the tool loop.
    mocks.py         All three, faked, for running without API keys.
"""
