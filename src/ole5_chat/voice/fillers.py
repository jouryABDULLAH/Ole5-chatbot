"""The fixed lines (rules.LINES and the greetings) as audio, kept on disk so they play at once and still play when Groq is
slow or refusing. A file is named by a hash of the model, voice and text, so a changed line or voice is synthesized
again. Orpheus sometimes garbles a short English line ("Still checking, thank you for waiting" came back as noise), so
each new file is heard back by Whisper and synthesized again, up to ATTEMPTS times, until it matches."""

import asyncio
import hashlib
import io
import logging
import uuid
import wave
from difflib import SequenceMatcher

from ole5_chat.config import get_settings
from ole5_chat.voice import rules, speakable, stt, tts

log = logging.getLogger(__name__)
_memory: dict[str, bytes] = {}
ATTEMPTS = 4
GOOD_MATCH = 0.85
FORMAT = "trim1"  # part of the file name: changing how audio is made (the silence trim) makes new files


def _key(text: str, language: str) -> str:
    model, voice = tts.voice_for(text, language)
    return hashlib.sha1(f"{FORMAT}|{model}|{voice}|{text}".encode()).hexdigest()[:20]


def _wav(pcm_data: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.RATE)
        w.writeframes(pcm_data)
    return out.getvalue()


async def _checked(text: str, language: str, api, hearing) -> bytes:
    """Synthesized until Whisper hears the text back (hearing: an STT client, or None to skip the check)."""
    best, best_score = b"", -1.0
    for attempt in range(1, ATTEMPTS + 1):
        data = await tts.synthesize(text, language, api)
        if hearing is None:
            return data
        take = await stt._whisper(_wav(data), language, None, hearing)
        score = SequenceMatcher(None, rules.normalize(take.raw), rules.normalize(text)).ratio()
        if score > best_score:
            best, best_score = data, score
        if score >= GOOD_MATCH:
            break
        log.warning("voice  fixed line heard back as %r (%.2f), attempt %d: %r", take.raw, score, attempt, text)
    if best_score < GOOD_MATCH:
        log.error("voice  fixed line never heard back right (best %.2f): %r", best_score, text)
    return best


async def pcm(text: str, language: str, api=None, hearing=None) -> bytes:
    """The line's audio: from memory, from disk, or synthesized (and checked, when hearing is given) and saved."""
    key = _key(text, language)
    if key in _memory:
        return _memory[key]
    path = get_settings().voice_cache_dir / f"{key}.pcm"
    if path.exists():
        data = path.read_bytes()
    else:
        data = await _checked(text, language, api, hearing)
        if len(data) < tts.FRAME_BYTES:
            raise tts.BadAudio(f"no audio for {text!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.part")  # the warm-up thread may write the same line
        part.write_bytes(data)
        part.replace(path)
    _memory[key] = data
    return data


def fixed_texts() -> list[tuple[str, str, str]]:
    """(name, language, text) of every line said word for word: the rules' lines and each product's greeting."""
    from ole5_chat import products
    texts = [(key, language, text) for key, by_language in rules.LINES.items() for language, text in by_language.items()]
    for folder in sorted(p for p in get_settings().products_dir.iterdir() if p.is_dir()):
        try:
            product = products.load(folder.name.upper())
        except Exception:
            continue
        texts += [(f"greet:{folder.name}", language, speakable.speakable(product.greet(language), language))
                  for language in ("ar", "en")]
    return texts


async def warm() -> None:
    """Every fixed line in both languages, synthesized once, before the first caller needs it. Runs in its own thread
    and event loop, so it has its own client."""
    api, hearing = tts.new_client(), stt.new_client()
    try:
        for key, language, text in fixed_texts():
            try:
                await pcm(text, language, api, hearing)
            except Exception:
                log.exception("voice  fixed line %s/%s not cached", key, language)
    finally:
        await api.close()
        await hearing.close()
    log.info("voice  fixed lines ready: %d", len(_memory))


def warm_in_background() -> None:
    import threading
    threading.Thread(target=lambda: asyncio.run(warm()), daemon=True, name="voice-warm").start()
