"""Speech to text: one speech segment (cut by the browser's speech detector) through Groq Whisper, then cleaned.

What the user's recordings showed (VOICE_LOG.md, CP0 and CP1): trimmed segments transcribe well; Whisper's detected
language is unreliable (Arabic speech tagged Spanish, French, Turkish) and an Arabic hint turns short English into
Arabic ("Yes, submit" → "نعم، اشتركوا في القناة"), while the language forced with a hint in that language was right on
32 of 35 segments. So each segment is transcribed twice at once, as Arabic with the Arabic hint and as English with the
English hint, and one is kept: when both come back in the same script, the call for that script; otherwise the more
confident one, with a lead for the conversation's language. One-word clips go without the hint, which leaked into
them ("ودعم RiCH"); a transcript made only of hint words is dropped.
"""

import io
import logging
import re
import time
import wave
import asyncio
from dataclasses import dataclass

from groq import AsyncGroq

from ole5_chat.agent.validate import script_language
from ole5_chat.config import get_settings
from ole5_chat.voice.rules import TASHKEEL

log = logging.getLogger(__name__)

# Lines Whisper writes on silence or at the end of a clip, never said to a support assistant. Removed wherever they appear.
NEVER_SAID = [
    r"[،,]?\s*(?:و\s*)?(?:لا\s*تنسوا\s*)?(?:ال)?اشتراك\s*(?:في|ب)\s*(?:ال)?قنا[ةه]",
    r"[،,]?\s*(?:و\s*)?(?:لا\s*تنس[وى]ا?\s*)?اشترك(?:وا|ي)?\s*(?:في|ب)\s*(?:ال)?قنا[ةه]",
    r"ترجم[ةه]\s*نانسي\s*قنقر",
    r"شكرا\s*(?:لكم\s*)?(?:على|لل)\s*(?:ال)?مشاهد[ةه]",
    r"thanks?\s*(?:you\s*)?for\s*watching[.!]?",
    r"(?:please\s*)?subscribe\s*to\s*(?:the|my|our)\s*channel[.!]?",
    r"subtitles?\s*by\s*[^.]*\.?",
    r"amara\.org[^.]*\.?",
]
NEVER_SAID_RE = re.compile("|".join(f"(?:{p})" for p in NEVER_SAID), re.IGNORECASE)
ONLY_MARKS = re.compile(r"^[\W_]*$")
HINTS = {"ar": "نظام Ole5 ودعم RiCH", "en": "Ole5, RiCH"}
HINT_WORDS = {"نظام", "ole5", "ودعم", "دعم", "rich", "و"}
SESSION_LEAD = 0.3  # added to the conversation language's confidence (mean log-probability) when the scripts differ


@dataclass
class Heard:
    text: str  # cleaned; empty when nothing usable was said
    language: str | None  # "ar" or "en" from the letters of the text; None when too short to tell
    raw: str
    seconds: float
    ms: int
    forced: str | None  # the language of the call that was kept
    confidence: float = 0.0  # its mean log-probability per token, by segment length


_client: AsyncGroq | None = None


def new_client() -> AsyncGroq:
    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return AsyncGroq(api_key=s.groq_api_key.get_secret_value(), timeout=10.0, max_retries=1)


def client() -> AsyncGroq:
    """The client of the server's event loop (see tts.client)."""
    global _client
    if _client is None:
        _client = new_client()
    return _client


async def warm() -> None:
    """Opens the connection before the first utterance: the first request of a client took ~1.4 s instead of ~0.3."""
    try:
        await client().models.list()
    except Exception:
        log.warning("voice  speech-to-text warm-up failed", exc_info=True)


def seconds_of(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / float(w.getframerate() or 1)


def clean(text: str, hint: str) -> str:
    """The transcript without diacritics (Whisper adds them to short words: "تَمَامُ"), the known invented lines, the
    hint copied into it, and nothing at all when only hint words are left."""
    text = NEVER_SAID_RE.sub(" ", TASHKEEL.sub("", text))
    if hint:
        text = text.replace(hint, " ")
    text = " ".join(text.split()).strip(" ،,")
    if ONLY_MARKS.match(text) or set(re.sub(r"[^\w\s]", " ", text).lower().split()) <= HINT_WORDS:
        return ""
    return text


@dataclass
class _Take:
    language: str
    raw: str
    text: str
    confidence: float


async def _whisper(wav: bytes, language: str, hint: str | None, api: AsyncGroq | None = None) -> _Take:
    s = get_settings()
    extra = {"prompt": hint} if hint else {}
    result = await (api or client()).audio.transcriptions.create(model=s.voice_stt_model, file=("speech.wav", wav),
                                                        language=language, response_format="verbose_json",
                                                        temperature=0, **extra)
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    raw = str(data.get("text") or "").strip()
    parts = [(max(1e-3, float(p.get("end", 0)) - float(p.get("start", 0))), float(p.get("avg_logprob", -1.0)))
             for p in data.get("segments") or []]
    total = sum(length for length, _ in parts)
    confidence = sum(length * lp for length, lp in parts) / total if total else -1.0
    return _Take(language, raw, clean(raw, hint or ""), confidence)


def choose(takes: list[_Take], session_language: str) -> _Take:
    """The take to keep (see the module docstring)."""
    usable = [t for t in takes if t.text]
    if len(usable) < 2:
        return usable[0] if usable else takes[0]
    scripts = {script_language(t.text) for t in usable}
    if len(scripts) == 1 and None not in scripts:
        script = scripts.pop()
        return next(t for t in usable if t.language == script)
    return max(usable, key=lambda t: t.confidence + (SESSION_LEAD if t.language == session_language else 0.0))


async def transcribe(wav: bytes, session_language: str) -> Heard:
    """One segment to text, as Arabic and as English at the same time; the better take is kept."""
    s = get_settings()
    started = time.perf_counter()
    seconds = seconds_of(wav)
    short = seconds < s.voice_short_clip_s
    results = await asyncio.gather(*(_whisper(wav, language, None if short else HINTS[language])
                                     for language in HINTS), return_exceptions=True)
    takes = [r for r in results if isinstance(r, _Take)]
    for failed in (r for r in results if isinstance(r, BaseException)):
        log.warning("voice  one speech-to-text call failed: %s", failed)
    if not takes:
        raise results[0]
    kept = choose(takes, session_language)
    ms = int((time.perf_counter() - started) * 1000)
    log.info("voice  stt seconds=%.2f ms=%d kept=%s %s", seconds, ms, kept.language,
             " | ".join(f"{t.language}:{t.confidence:.2f}:{t.raw!r}" for t in takes))
    return Heard(text=kept.text, language=script_language(kept.text), raw=kept.raw, seconds=round(seconds, 2), ms=ms,
                 forced=kept.language, confidence=round(kept.confidence, 3))
