"""
Audio format plumbing between Twilio, Deepgram and Sarvam.

Telephony is 8 kHz G.711 mu-law, mono. Twilio sends it and accepts it;
Deepgram's streaming STT takes it directly (`encoding=mulaw`,
`sample_rate=8000`); and Sarvam's streaming TTS can emit it. When all
three agree, this module does almost nothing, which is the intent --
every sample rate conversion in a voice path costs latency and quality.

It exists for the case where they do not agree. Sarvam's REST
documentation lists `wav` as the default codec and does not enumerate
`mulaw`, while its WebSocket documentation does; rather than depend on
which is right for a given account, the TTS client asks for mu-law and
this module converts whatever actually arrives. A deployment that finds
mu-law unavailable sets `SARVAM_TTS_CODEC=linear16` and everything still
works.

WHY NOT `audioop`

The standard library's `audioop.lin2ulaw` would do the conversion in C,
and this project runs Python 3.12 where it still exists. It was removed
in 3.13. A pure-Python encoder costs nothing measurable at 8000 samples
per second -- a whole minute of speech is 480,000 samples, and the table
below turns each into one dict lookup -- and it means a Python upgrade
cannot silently break the audio path. `audioop` is used when present
purely because it is faster, with this as the fallback and the reference.
"""

import audioop  # type: ignore[import-not-found]  # noqa: F401 - see _lin2ulaw
import logging
import struct
from typing import Iterator, Optional

logger = logging.getLogger("twilio-deepgram.audio")

# Telephony constants. Not configurable: this is what the PSTN is.
SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2  # bytes per linear16 sample
# 20 ms of mu-law at 8 kHz. Twilio sends this size and expects roughly it
# back; larger frames arrive as audible chunking, smaller ones waste
# websocket overhead.
FRAME_BYTES = 160

_HAVE_AUDIOOP = True
try:  # pragma: no cover - depends on the interpreter, not on the tests
    import audioop as _audioop
except ImportError:  # pragma: no cover - Python 3.13+
    _audioop = None
    _HAVE_AUDIOOP = False


# --- G.711 mu-law, the reference implementation ----------------------
#
# ITU-T G.711, following the Sun reference that CPython's `audioop` also
# implements. Kept explicit rather than clever: this is the one function
# where an off-by-one produces a call that sounds like static, and a
# reader needs to be able to check it line by line against the standard.
#
# The subtlety worth pointing at, because getting it wrong is silent and
# was got wrong here first: **the algorithm operates on 14-bit samples,
# not 16.** The input is shifted right by two before anything else, the
# bias is scaled to match (0x84 >> 2), and the clip is the 14-bit 8159
# rather than 32635. Encoding at full 16-bit width produces output that
# is correct for positive samples and one off for negative ones -- which
# is inaudible on speech and therefore would never have been noticed
# without checking against `audioop`. `test_mulaw_matches_the_stdlib`
# does exactly that, over the full sample range.

_ULAW_BIAS = 0x84 >> 2
_ULAW_CLIP = 8159
# Upper bound of each mu-law segment, in 14-bit units.
_SEG_UEND = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)


def _encode_sample(sample: int) -> int:
    """One signed 16-bit sample to one mu-law byte."""
    value = sample >> 2  # 16-bit to 14-bit; Python's >> is arithmetic
    if value < 0:
        value = -value
        mask = 0x7F  # mu-law inverts all bits; sign lives in the mask
    else:
        mask = 0xFF
    if value > _ULAW_CLIP:
        value = _ULAW_CLIP
    value += _ULAW_BIAS

    segment = len(_SEG_UEND)
    for index, upper in enumerate(_SEG_UEND):
        if value <= upper:
            segment = index
            break
    if segment >= 8:
        return 0x7F ^ mask

    return ((segment << 4) | ((value >> (segment + 1)) & 0x0F)) ^ mask


# Precomputed for every 16-bit sample value, so the per-sample cost is an
# index rather than a loop. 65536 entries, built once at import.
_ULAW_TABLE = bytes(_encode_sample(s if s < 32768 else s - 65536) for s in range(65536))


def pcm16_to_mulaw(pcm: bytes) -> bytes:
    """Signed 16-bit little-endian PCM to G.711 mu-law."""
    if not pcm:
        return b""
    if _HAVE_AUDIOOP:
        return _audioop.lin2ulaw(pcm, SAMPLE_WIDTH)
    # Trim a stray odd byte rather than raising: a truncated final frame
    # must not end a call.
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return bytes(_ULAW_TABLE[s & 0xFFFF] for s in samples)


def resample_pcm16(pcm: bytes, from_rate: int, to_rate: int = SAMPLE_RATE) -> bytes:
    """
    Change the sample rate of linear16 audio.

    Only reached when a deployment configures Sarvam at something other
    than 8000 Hz. Uses `audioop.ratecv` when available; the fallback is
    nearest-neighbour, which is audibly worse than a real filter and is
    deliberately a fallback rather than the design -- the correct fix is
    to ask Sarvam for 8000 Hz in the first place, which the default does.
    """
    if from_rate == to_rate or not pcm:
        return pcm
    if _HAVE_AUDIOOP:
        converted, _ = _audioop.ratecv(pcm, SAMPLE_WIDTH, 1, from_rate, to_rate, None)
        return converted

    logger.warning(
        "Resampling %d Hz -> %d Hz without audioop; set SARVAM_TTS_SAMPLE_RATE=8000 "
        "to avoid this entirely.",
        from_rate,
        to_rate,
    )
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    ratio = from_rate / to_rate
    out = [samples[min(int(i * ratio), len(samples) - 1)] for i in range(int(len(samples) / ratio))]
    return struct.pack(f"<{len(out)}h", *out)


def strip_wav_header(data: bytes) -> tuple[bytes, Optional[int]]:
    """
    Return (pcm_payload, sample_rate) for a RIFF/WAVE buffer.

    Sarvam's REST default codec is `wav`, and a WAV header fed to Twilio
    as if it were audio is 44 bytes of noise at the start of every
    utterance -- quiet enough to be mistaken for a glitch rather than a
    bug. Parsed properly rather than by skipping a fixed 44 bytes,
    because the header is not always 44 bytes.

    Returns the input unchanged if it is not a WAV, so this is safe to
    call on anything.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data, None

    sample_rate: Optional[int] = None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        (chunk_size,) = struct.unpack("<I", data[offset + 4:offset + 8])
        body = offset + 8
        if chunk_id == b"fmt " and body + 16 <= len(data):
            sample_rate = struct.unpack("<I", data[body + 4:body + 8])[0]
        elif chunk_id == b"data":
            return data[body:body + chunk_size], sample_rate
        offset = body + chunk_size + (chunk_size % 2)
    return data, sample_rate


def to_twilio_mulaw(
    payload: bytes, codec: str, sample_rate: int = SAMPLE_RATE
) -> bytes:
    """
    Whatever the TTS produced, as 8 kHz mu-law for Twilio.

    `codec` is what we asked Sarvam for. The mu-law path is a pass-through
    and is the configured default; the others exist so that a deployment
    whose account cannot produce mu-law still gets a working call rather
    than silence.
    """
    codec = (codec or "").lower()

    if codec in ("mulaw", "ulaw", "mu-law"):
        return payload

    if codec == "wav":
        pcm, wav_rate = strip_wav_header(payload)
        return pcm16_to_mulaw(resample_pcm16(pcm, wav_rate or sample_rate))

    if codec in ("linear16", "pcm", "l16"):
        return pcm16_to_mulaw(resample_pcm16(payload, sample_rate))

    # mp3, opus, aac, flac. Decoding these needs a codec library this
    # project deliberately does not carry, and a phone call is the wrong
    # place to discover that. Refuse clearly instead of emitting noise.
    raise ValueError(
        f"Cannot convert {codec!r} to telephony audio. Set SARVAM_TTS_CODEC to "
        "mulaw (best), linear16, or wav."
    )


def frames(payload: bytes, size: int = FRAME_BYTES) -> Iterator[bytes]:
    """
    Split mu-law audio into Twilio-sized frames.

    Twilio accepts larger writes, but pacing the stream in 20 ms units is
    what lets a barge-in `clear` take effect promptly: audio already
    handed over is audio the caller will hear regardless.
    """
    for start in range(0, len(payload), size):
        yield payload[start:start + size]


def silence(milliseconds: int) -> bytes:
    """
    mu-law silence. 0xFF is the encoding of zero, not 0x00.

    Used by the mock TTS and to pad the moment before a hang-up.
    """
    return b"\xff" * int(SAMPLE_RATE * milliseconds / 1000)
