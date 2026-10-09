r"""
Place a fake phone call at the gateway, with no Twilio and no API keys.

    # terminal 1 - the backend
    uvicorn app.main:app --reload --port 8000

    # terminal 2 - the gateway, with the services faked
    cd voice-agent
    set VOICE_MOCK_MODE=true
    ..\venv\Scripts\python.exe -m twilio_deepgram.run

    # terminal 3 - ring it
    ..\venv\Scripts\python.exe simulate_call.py

WHAT THIS ACTUALLY PROVES

It speaks Twilio's Media Streams protocol exactly as Twilio does -- the
same `connected` / `start` / `media` / `stop` frames, the same base64
mu-law, the same `customParameters` -- so everything downstream of the
websocket is the real thing: tenant resolution, the call log, the tool
layer, the backend, SQL Server and the dashboard broadcast.

In mock mode the three paid services are scripted, so what this checks is
the **wiring**, not the conversation. A green run here means that when
the API keys arrive, the only untested things left are the keys.

With `VOICE_MOCK_MODE=false` and real keys, the same script drives a real
conversation -- it sends silence as caller audio, so the agent will greet
you and then wait, which is enough to confirm STT, the model and the
voice are all reachable and that audio is flowing back.

WHAT IT CANNOT TELL YOU

Nothing about how the call sounds, how fast it feels, or whether
interruption works, because there is no microphone and no speaker. Those
need a real phone; the manual test script in VOICE_STACK_MIGRATION.md
covers them.
"""

import argparse
import asyncio
import base64
import json
import sys
import time

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("websockets is not installed. Run: pip install -r twilio_deepgram_requirements.txt")


# 20 ms of mu-law silence, which is what Twilio sends when nobody is
# talking. 0xFF is mu-law zero, not 0x00.
SILENT_FRAME = b"\xff" * 160


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


async def place_call(
    url: str,
    to_number: str,
    from_number: str,
    seconds: float,
    call_sid: str,
    stream_sid: str,
) -> int:
    """
    Run one simulated call. Returns a process exit code.

    Audio received from the gateway is counted rather than played --
    there is no audio device here, and the number is the useful signal:
    zero bytes back means the agent never spoke, which is the failure
    this script exists to catch.
    """
    print(f"-> connecting to {url}")
    try:
        async with websockets.connect(url, max_size=None) as ws:
            print("[ok] connected\n")

            await ws.send(json.dumps({
                "event": "connected", "protocol": "Call", "version": "1.0.0",
            }))
            await ws.send(json.dumps({
                "event": "start",
                "sequenceNumber": "1",
                "streamSid": stream_sid,
                "start": {
                    "accountSid": "ACsimulated",
                    "streamSid": stream_sid,
                    "callSid": call_sid,
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1,
                    },
                    # Exactly what twilio_api.connect_stream puts in the
                    # TwiML: the dialled number is what identifies the
                    # clinic, and the caller's number is what recognises
                    # a returning patient.
                    "customParameters": {
                        "dialed_number": to_number,
                        "caller_phone": from_number,
                        "direction": "inbound",
                    },
                },
            }))
            print(f"[ok] start sent   callSid={call_sid}  {from_number} -> {to_number}\n")

            state = {"audio_bytes": 0, "media_frames": 0, "clears": 0, "marks": 0}

            async def receive() -> None:
                async for raw in ws:
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    event = message.get("event")
                    if event == "media":
                        payload = (message.get("media") or {}).get("payload") or ""
                        state["audio_bytes"] += len(base64.b64decode(payload))
                        state["media_frames"] += 1
                    elif event == "clear":
                        state["clears"] += 1
                        print("  <- clear      (agent stopped talking: barge-in)")
                    elif event == "mark":
                        state["marks"] += 1

            async def send_silence() -> None:
                """
                Caller audio, at real time.

                Pacing matters: sending a minute of audio instantly would
                make the STT endpointer see the whole call as one turn.
                """
                deadline = _now_ms() + int(seconds * 1000)
                chunk = 0
                while _now_ms() < deadline:
                    await ws.send(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {
                            "track": "inbound",
                            "chunk": str(chunk),
                            "timestamp": str(chunk * 20),
                            "payload": base64.b64encode(SILENT_FRAME).decode(),
                        },
                    }))
                    chunk += 1
                    await asyncio.sleep(0.02)

            receiver = asyncio.ensure_future(receive())
            print(f"-> streaming {seconds:.0f}s of caller audio (silence)...\n")
            hung_up_on_us = False
            try:
                await send_silence()
            except websockets.exceptions.ConnectionClosed:
                # The AGENT ended the call -- it said goodbye and called
                # `end_call`, and closing the stream is how a hang-up
                # works on a `<Connect><Stream>`. That is a successful
                # call, not an error, and Twilio treats it the same way.
                hung_up_on_us = True
                print("\n[ok] the agent ended the call (goodbye -> end_call)")
            finally:
                if not hung_up_on_us:
                    try:
                        await ws.send(json.dumps({
                            "event": "stop",
                            "streamSid": stream_sid,
                            "stop": {"accountSid": "ACsimulated", "callSid": call_sid},
                        }))
                        print("\n[ok] stop sent (caller hung up)")
                    except websockets.exceptions.ConnectionClosed:
                        hung_up_on_us = True
                await asyncio.sleep(1.5)  # let the gateway finalise
                receiver.cancel()

            seconds_of_audio = state["audio_bytes"] / 8000
            print("\n" + "-" * 58)
            print(f"  agent audio received : {state['audio_bytes']:,} bytes "
                  f"({seconds_of_audio:.1f}s of speech)")
            print(f"  media frames         : {state['media_frames']:,}")
            print(f"  barge-in clears      : {state['clears']}")
            print("-" * 58)

            if state["audio_bytes"] == 0:
                print("\n[FAIL] THE AGENT NEVER SPOKE.")
                print("  Check the gateway log. Usual causes: the greeting failed,")
                print("  the TTS could not connect, or the backend was unreachable.")
                return 1

            print("\n[ok] Call completed. Check the gateway log for the tool calls,")
            print("  and the dashboard Call Logs for the record and transcript.")
            return 0

    except OSError as e:
        print(f"\n[FAIL] could not connect: {e}")
        print("  Is the gateway running?  python -m twilio_deepgram.run")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate a Twilio Media Streams call against the voice gateway."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8080/voice/stream",
                        help="gateway media-stream websocket")
    parser.add_argument("--to", dest="to_number", default="+919000000000",
                        help="the number dialled; identifies the clinic")
    parser.add_argument("--from", dest="from_number", default="+919111111111",
                        help="the caller's number")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="how long the caller stays on the line")
    parser.add_argument("--call-sid", default=f"CAsim{int(time.time())}")
    parser.add_argument("--stream-sid", default=f"MZsim{int(time.time())}")
    args = parser.parse_args()

    return asyncio.run(place_call(
        args.url, args.to_number, args.from_number,
        args.seconds, args.call_sid, args.stream_sid,
    ))


if __name__ == "__main__":
    sys.exit(main())
