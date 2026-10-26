"""The voice pipeline against the real services, for the checks unit tests cannot make (VOICE_PLAN.md section 8.3).

  python scripts/try_voice.py stt [RECORDINGS]   the mic-lab speech segments through Groq Whisper and the voice rules
  python scripts/try_voice.py tts                the fixed lines synthesized into .voice_cache, and two long replies
                                                 spoken and heard back by Whisper to catch dropped words
  python scripts/try_voice.py kb [REPEATS]       the demo questions through the real graph and knowledge base, timed,
                                                 on the voice channel and the chat channel

RECORDINGS defaults to ../voice-lab/recordings (recordings.json and the WAV files the lab saved). The kb check runs
on a temporary copy of sheets/ with the mock ticket API, and traces are off.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TRACING"] = "false"
sys.stdout.reconfigure(encoding="utf-8")

from ole5_chat.voice import rules  # noqa: E402

# What each recorded phrase should do: ("command", name), ("confirm", kind) or ("text", None) for a sentence.
PHRASES = {
    "s1_kb_password": ("مرحبا، نسيت كلمة المرور كيف أستعيدها؟", "ar", ("text", None)),
    "s1_kb_archive": ("كيف أؤرشف مراسلة في النظام؟", "ar", ("text", None)),
    "s2_en_crash": ("The mobile app closes by itself when I open the inbox.", "en", ("text", None)),
    "s2_en_detail": ("It started yesterday, on Android.", "en", ("text", None)),
    "s2_en_yes": ("Yes, submit.", "en", ("confirm", "submit")),
    "s3_urgent": ("ولا موظف عندنا يقدر يسجل دخول من الصبح", "ar", ("text", None)),
    "s3_send": ("نعم أرسلها", "ar", ("confirm", "submit")),
    "w_naam": ("نعم", "ar", ("confirm", "submit")),
    "w_eh": ("ايه", "ar", ("confirm", "submit")),
    "w_la": ("لا", "ar", ("confirm", "other")),
    "w_tamam": ("تمام", "ar", ("confirm", "ack")),
    "w_okay_ar": ("اوكي", "ar", ("confirm", "ack")),
    "w_irsilha": ("ارسلها", "ar", ("confirm", "submit")),
    "w_wagif": ("وقف", "ar", ("command", "stop")),
    "w_eid": ("عيد", "ar", ("command", "repeat")),
    "w_lahza": ("لحظة", "ar", ("command", "wait")),
    "w_maak": ("معي؟", "ar", ("command", "there")),
    "w_yes": ("yes", "en", ("confirm", "submit")),
    "w_ok": ("ok", "en", ("confirm", "ack")),
    "w_stop": ("stop", "en", ("command", "stop")),
    "w_repeat": ("repeat that", "en", ("command", "repeat")),
    "p_pause": ("عندي مشكلة في... يعني لما أفتح البريد الوارد يطلع لي خطأ", "ar", ("text", None)),
    "c_mixed": ("عندي سؤال عن Ole5 ونظام RiCH", "ar", ("text", None)),
    "c_error": ("يطلع لي خطأ رقم خمسمية", "ar", ("text", None)),
    "t_thanks_ar": ("شكراً", "ar", ("text", None)),
    "t_thanks_en": ("thank you", "en", ("text", None)),
    "free": ("", "ar", ("text", None)),
}


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, rules.normalize(a), rules.normalize(b)).ratio()


def outcome(text: str, kind: str) -> str | None:
    if kind == "command":
        return rules.command(text)
    if kind == "confirm":
        return rules.command(text) and "command" or rules.confirm_kind(text)
    return None


async def check_stt(folder: Path) -> None:
    from ole5_chat.voice import stt
    entries = [e for e in json.loads((folder / "recordings.json").read_text(encoding="utf-8"))
               if e["kind"].startswith("vad")]
    ok = total = 0
    times = []
    for e in entries:
        expected, language, (kind, want) = PHRASES.get(e["phrase"], ("", "ar", ("text", None)))
        heard = await stt.transcribe((folder / e["file"]).read_bytes(), language)
        times.append(heard.ms)
        if kind == "text":
            score = similar(heard.text, expected) if expected else None
            good = score is None or score >= 0.8
            verdict = "-" if score is None else f"{score:.2f}"
        else:
            got = outcome(heard.text, kind)
            good = got == want or (e["phrase"] == "w_eh" and got == "unclear")  # "E" for ايه asks again: acceptable
            verdict = f"{got} (want {want})"
        ok += good
        total += 1
        print(f"{'ok ' if good else 'BAD'} {e['phrase']:15} {heard.seconds:4.2f}s forced={heard.forced or '-':2} "
              f"lang={heard.language or '-':2} {heard.ms:4d}ms  {heard.text!r:45}  {verdict}")
    times.sort()
    print(f"\n{ok}/{total} as expected; speech-to-text median {times[len(times) // 2]} ms, max {times[-1]} ms")


async def check_tts() -> None:
    from ole5_chat.voice import fillers, speakable, stt, tts
    started = time.perf_counter()
    await fillers.warm()
    print(f"fixed lines cached in {time.perf_counter() - started:.1f} s:")
    for key, language, text in fillers.fixed_texts():
        pcm = await fillers.pcm(text, language)
        print(f"  {key:18} {language} {len(pcm) / 2 / tts.RATE:5.1f}s  {text[:60]}")
    replies = {
        "ar": "لاستعادة كلمة المرور، افتح صفحة تسجيل الدخول واضغط على نسيت كلمة المرور. ثم أدخل بريدك الإلكتروني "
              "المسجل في النظام، وستصلك رسالة فيها رابط لتعيين كلمة مرور جديدة. إذا لم تصلك الرسالة خلال دقائق، "
              "تحقق من مجلد الرسائل غير المرغوب فيها، أو تواصل مع مسؤول النظام في جهتك.",
        "en": "To archive a correspondence, open it from your inbox, then choose Archive from the actions at the top of "
              "the screen. The correspondence moves to the archive and stays searchable. If the Archive action is "
              "missing, your role does not have the archive permission; ask your system administrator to add it.",
    }
    for language, text in replies.items():
        for part in speakable.segments(speakable.speakable(text, language)):
            began = time.perf_counter()
            first, chunks = None, []
            async for frame in tts.stream(part, language):
                first = first or time.perf_counter() - began
                chunks.append(frame)
            pcm = b"".join(chunks)
            wav = wav_of(pcm, tts.RATE)
            back = await stt.transcribe(wav, language)
            print(f"{language} {len(part):3d} chars  first sound {first * 1000:4.0f} ms  {len(pcm) / 2 / tts.RATE:4.1f}s "
                  f"audio  heard back {similar(back.text, part):.2f}\n   said:  {part}\n   heard: {back.text}")


def wav_of(pcm: bytes, rate: int) -> bytes:
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


QUESTIONS = [
    ("مرحبا، نسيت كلمة المرور كيف أستعيدها؟", "ar"),
    ("كيف أؤرشف مراسلة في النظام؟", "ar"),
    ("كيف أضيف توقيعاً إلى المراسلات؟", "ar"),
    ("How do I export a correspondence as PDF?", "en"),
    ("The mobile app closes by itself when I open the inbox.", "en"),
]


def check_kb(repeats: int) -> None:
    temp = Path(tempfile.mkdtemp(prefix="ole5-voice-"))
    shutil.copytree(ROOT / "sheets", temp / "sheets")
    os.environ.update(SHEETS_DIR=str(temp / "sheets"), TICKET_API_URL="", ATTACHMENTS_DIR=str(temp / "uploads"))
    from ole5_chat import sessions, sheets
    from ole5_chat.config import get_settings
    from ole5_chat.knowledge import retriever
    get_settings.cache_clear()
    s = get_settings()
    print(f"knowledge base: {s.kb_backend}; loading...", flush=True)
    began = time.perf_counter()
    if s.kb_backend == "ragent2":
        retriever.warm(sorted(set(s.kb_tenants.values())))
    print(f"ready in {time.perf_counter() - began:.0f} s\n", flush=True)
    email = next(str(r["email"]) for r in sheets.customers()
                 if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")
    rows = []
    for rep in range(1, repeats + 1):
        for text, language in QUESTIONS:
            for channel in ("voice", "chat"):
                sid, _ = sessions.start(email)
                events, marks = [], {}
                began = time.perf_counter()

                def on_event(event, began=began, marks=marks):
                    name = event.get("node") or event["type"]
                    marks.setdefault(name, time.perf_counter() - began)
                    events.append(event)

                options = {"channel": "voice", "heard_language": language, "on_event": on_event} if channel == "voice" else {}
                state = sessions.send(sid, text, **options)
                total = time.perf_counter() - began
                reply = state["messages"][-1].text
                searches = next((e["searches"] for e in events if e["type"] == "search_done"), None)
                rows.append({"rep": rep, "channel": channel, "question": text, "seconds": round(total, 1),
                             "search_started": round(marks.get("search_started", 0), 1), "searches": searches,
                             "phase": state["phase"], "sources": state["sources"], "reply": reply})
                print(f"[{rep}] {channel:5} {total:5.1f}s search@{marks.get('search_started', 0):4.1f}s "
                      f"searches={searches} phase={state['phase']:12} sources={len(state['sources'])}  {text[:40]}\n"
                      f"        {reply[:160]!r}", flush=True)
    out = ROOT / ".voice_cache" / "kb_timings.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved {out}")
    for channel in ("voice", "chat"):
        times = sorted(r["seconds"] for r in rows if r["channel"] == channel)
        offers = sum(r["phase"] == "offer_ticket" for r in rows if r["channel"] == channel)
        print(f"{channel}: median {times[len(times) // 2]} s, max {times[-1]} s, offers {offers}/{len(times)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stt"
    if mode == "stt":
        asyncio.run(check_stt(Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT.parent / "voice-lab" / "recordings"))
    elif mode == "tts":
        asyncio.run(check_tts())
    elif mode == "kb":
        check_kb(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        sys.exit(__doc__)
