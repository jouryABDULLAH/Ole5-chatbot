"""Text to speech: Groq Orpheus, streamed and cut into 40 ms frames of raw PCM for the browser's player.

Groq streams a WAV file: a 78-byte header (RIFF, fmt and a LIST chunk) and then 24 kHz mono PCM16, in chunks that can
split anywhere, even in the middle of a sample. The voice follows the letters of the text: Arabic by the Saudi model,
anything else by the English one (spike, VOICE_PLAN.md section 12)."""

import logging
import math
import struct
import time
from array import array
from collections.abc import AsyncIterator

from groq import AsyncGroq

from ole5_chat.agent.validate import script_language
from ole5_chat.config import get_settings

log = logging.getLogger(__name__)

RATE = 24000
FRAME_BYTES = RATE // 25 * 2  # 40 ms of mono PCM16
CHARS_PER_SECOND_MAX = 35


class BadAudio(ValueError):
    """The speech service sent something that is not 24 kHz mono 16-bit WAV."""


class WavStream:
    """Feeds on WAV bytes as they arrive and gives back the PCM samples, skipping the header chunks."""

    def __init__(self):
        self._head = bytearray()
        self._in_data = False
        self.format: tuple[int, int, int] | None = None  # channels, rate, bits

    def feed(self, chunk: bytes) -> bytes:
        if self._in_data:
            return chunk
        self._head += chunk
        if len(self._head) < 12:
            return b""
        if self._head[:4] != b"RIFF" or self._head[8:12] != b"WAVE":
            raise BadAudio(f"not a WAV stream: {bytes(self._head[:12])!r}")
        at = 12
        while len(self._head) >= at + 8:
            kind, size = bytes(self._head[at:at + 4]), struct.unpack("<I", self._head[at + 4:at + 8])[0]
            if kind == b"data":
                self._in_data = True
                pcm = bytes(self._head[at + 8:])
                self._head.clear()
                self._check()
                return pcm
            if len(self._head) < at + 8 + size:
                return b""
            if kind == b"fmt ":
                _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", self._head[at + 8:at + 24])
                self.format = (channels, rate, bits)
            at += 8 + size + (size & 1)
        return b""

    def _check(self) -> None:
        if self.format != (1, RATE, 16):
            raise BadAudio(f"expected mono {RATE} Hz 16-bit audio, got {self.format}")


class Framer:
    """Cuts PCM into equal frames; the last one may be shorter, but always a whole number of samples."""

    def __init__(self, size: int = FRAME_BYTES):
        self.size = size
        self._buf = bytearray()

    def push(self, pcm: bytes) -> list[bytes]:
        self._buf += pcm
        cut = len(self._buf) - len(self._buf) % self.size
        frames = [bytes(self._buf[i:i + self.size]) for i in range(0, cut, self.size)]
        del self._buf[:cut]
        return frames

    def flush(self) -> bytes:
        tail = bytes(self._buf[:len(self._buf) - len(self._buf) % 2])
        self._buf.clear()
        return tail


SILENT_RMS = 0.006  # about -44 dBFS: Orpheus' pauses are near-digital silence, its quietest speech is far above
KEEP_BEFORE, KEEP_PAUSE, KEEP_AFTER = 1, 10, 2  # frames: 40 ms before speech, pauses cut to 400 ms, 80 ms after


def loud(frame: bytes) -> bool:
    samples = array("h", frame[:len(frame) - len(frame) % 2])
    return bool(samples) and math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768 > SILENT_RMS


class Trimmer:
    """Drops the silence Orpheus leaves around its speech (0.4-0.7 s before, up to 0.6 s after) and shortens the pauses
    inside it (up to 2 s were measured inside one short sentence)."""

    def __init__(self):
        self._started = False
        self._silent: list[bytes] = []

    def push(self, frame: bytes) -> list[bytes]:
        if not loud(frame):
            self._silent.append(frame)
            return []
        if not self._started:
            before = self._silent[-KEEP_BEFORE:]
        elif len(self._silent) > KEEP_PAUSE:
            before = self._silent[:KEEP_PAUSE - 2] + self._silent[-2:]
        else:
            before = self._silent
        self._started, self._silent = True, []
        return [*before, frame]

    def flush(self) -> list[bytes]:
        tail, self._silent = (self._silent[:KEEP_AFTER] if self._started else []), []
        return tail


def frames_of(pcm: bytes) -> list[bytes]:
    framer = Framer()
    return [*framer.push(pcm), *filter(None, [framer.flush()])]


def voice_for(text: str, language: str) -> tuple[str, str]:
    """The model and voice for a text: by its letters, else by the conversation's language."""
    s = get_settings()
    if (script_language(text) or language) == "ar":
        return s.voice_tts_model_ar, s.voice_tts_voice_ar
    return s.voice_tts_model_en, s.voice_tts_voice_en


_client: AsyncGroq | None = None


def new_client() -> AsyncGroq:
    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return AsyncGroq(api_key=s.groq_api_key.get_secret_value(), timeout=15.0, max_retries=1)


def client() -> AsyncGroq:
    """The client of the server's event loop. A client is tied to the loop it first ran in, so code running in another
    loop (the warm-up thread) passes its own."""
    global _client
    if _client is None:
        _client = new_client()
    return _client


async def warm() -> None:
    try:
        await client().models.list()
    except Exception:
        log.warning("voice  speech warm-up failed", exc_info=True)


async def stream(text: str, language: str, api: AsyncGroq | None = None) -> AsyncIterator[bytes]:
    """The text spoken, as 40 ms PCM frames while Groq generates them. Closing the iterator closes the request."""
    model, voice = voice_for(text, language)
    started, first, total = time.perf_counter(), None, 0
    wav, framer, trimmer = WavStream(), Framer(), Trimmer()
    async with (api or client()).audio.speech.with_streaming_response.create(model=model, voice=voice, input=text,
                                                                    response_format="wav") as response:
        async for chunk in response.iter_bytes():
            for frame in (kept for raw in framer.push(wav.feed(chunk)) for kept in trimmer.push(raw)):
                if first is None:
                    first = int((time.perf_counter() - started) * 1000)
                total += len(frame)
                yield frame
    tail = framer.flush()
    for frame in [*(trimmer.push(tail) if tail else []), *trimmer.flush()]:
        total += len(frame)
        yield frame
    seconds = total / 2 / RATE
    log.info("voice  tts voice=%s chars=%d first_ms=%s audio_s=%.1f ms=%d", voice, len(text), first, seconds,
             int((time.perf_counter() - started) * 1000))
    if seconds < len(text) / CHARS_PER_SECOND_MAX:
        # Measured speech runs 12-20 characters a second; a take far below that lost words (seen on 2026-10-26).
        log.warning("voice  speech looks cut short: %.1f s for %d characters: %r", seconds, len(text), text[:80])


async def synthesize(text: str, language: str, api: AsyncGroq | None = None) -> bytes:
    """The whole text as one PCM buffer, for the fixed lines kept on disk."""
    return b"".join([frame async for frame in stream(text, language, api)])
