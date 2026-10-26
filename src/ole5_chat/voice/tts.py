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
from ole5_chat.voice import groqhttp

log = logging.getLogger(__name__)

RATE = 24000
FRAME_BYTES = RATE // 25 * 2  # 40 ms of mono PCM16
# Faster than this, a take lost words. The cached fixed lines run 10-17 characters a second in Arabic and 13-22 in
# English (2026-10-26); a take that drops half its words runs about twice its normal rate.
CHARS_PER_SECOND_MAX = {"ar": 24, "en": 30}
SHORT_TEXT = 30  # a text this short has too few characters for its rate to say anything


def cut_short(text: str, language: str | None, seconds: float) -> bool:
    """The take is far shorter than the text needs: Groq ended the speech early (seen on 2026-10-26: 3.7 s for 163
    characters, the question at its end missing)."""
    if len(text) < SHORT_TEXT:
        return False
    ceiling = CHARS_PER_SECOND_MAX["ar" if (language or script_language(text)) == "ar" else "en"]
    return seconds <= 0 or len(text) / seconds > ceiling


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


def voice_for(text: str, language: str | None) -> tuple[str, str]:
    """The model and voice for a text: by the language it is said in, else by its letters. A reply is said in one
    language, so all of it gets one voice (D35): choosing by each sentence's letters gave a sentence that is mostly an
    Arabic screen name, in an English reply, the Arabic voice, and it sounded like someone else talking."""
    s = get_settings()
    if (language or script_language(text) or "en") == "ar":
        return s.voice_tts_model_ar, s.voice_tts_voice_ar
    return s.voice_tts_model_en, s.voice_tts_voice_en


_client: AsyncGroq | None = None


def new_client() -> AsyncGroq:
    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return AsyncGroq(api_key=s.groq_api_key.get_secret_value(), timeout=15.0, max_retries=1,
                     http_client=groqhttp.http_client())


def client() -> AsyncGroq:
    """The client of the server's event loop. A client is tied to the loop it first ran in, so code running in another
    loop (the warm-up thread) passes its own."""
    global _client
    if _client is None:
        _client = new_client()
    return _client


_used = 0.0  # when a request last went out on the client
STALE_S = 40.0  # see stt.STALE_S


async def warm() -> None:
    global _used
    try:
        _used = time.monotonic()
        await client().models.list()
    except Exception:
        log.warning("voice  speech warm-up failed", exc_info=True)


async def touch() -> None:
    """The customer started talking: a connection idle long enough to have been closed is opened again, so the reply
    to what they say does not pay for it (see groqhttp)."""
    if time.monotonic() - _used > STALE_S:
        await warm()


async def stream(text: str, language: str, api: AsyncGroq | None = None) -> AsyncIterator[bytes]:
    """The text spoken, as 40 ms PCM frames while Groq generates them. Closing the iterator closes the request."""
    global _used
    if api is None:
        _used = time.monotonic()
    model, voice = voice_for(text, language)
    started, first, total = time.perf_counter(), None, 0
    wav, framer, trimmer = WavStream(), Framer(), Trimmer()
    done = False
    try:
        async with (api or client()).audio.speech.with_streaming_response.create(model=model, voice=voice, input=text,
                                                                        response_format="wav") as response:
            async for chunk in response.iter_bytes():
                for frame in (kept for raw in framer.push(wav.feed(chunk)) for kept in trimmer.push(raw)):
                    if first is None:
                        first = int((time.perf_counter() - started) * 1000)
                    total += len(frame)
                    yield frame
        done = True
    finally:
        if not done and api is None:
            _used = 0.0  # a stream cut off mid-body closes its connection: the next touch() opens one again
    tail = framer.flush()
    for frame in [*(trimmer.push(tail) if tail else []), *trimmer.flush()]:
        total += len(frame)
        yield frame
    seconds = total / 2 / RATE
    log.info("voice  tts voice=%s chars=%d first_ms=%s audio_s=%.1f ms=%d", voice, len(text), first, seconds,
             int((time.perf_counter() - started) * 1000))
    if cut_short(text, language, seconds):
        log.warning("voice  speech looks cut short: %.1f s for %d characters: %r", seconds, len(text), text[:80])


async def synthesize(text: str, language: str, api: AsyncGroq | None = None) -> bytes:
    """The whole text as one PCM buffer, for the fixed lines kept on disk."""
    return b"".join([frame async for frame in stream(text, language, api)])
