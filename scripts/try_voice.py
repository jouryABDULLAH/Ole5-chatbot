"""The voice pipeline against the real services, for the checks unit tests cannot make (VOICE_PLAN.md section 8.3).

  python scripts/try_voice.py stt [RECORDINGS]   the mic-lab speech segments through Groq Whisper and the voice rules
  python scripts/try_voice.py tts                the fixed lines synthesized into .voice_cache, and two long replies
                                                 spoken and heard back by Whisper to catch dropped words
  python scripts/try_voice.py kb [REPEATS]       the demo questions through the real graph and knowledge base, timed,
                                                 on the voice channel and the chat channel
  python scripts/try_voice.py flow [NAME]        whole conversations (the demo scenarios) through the real voice
                                                 session: the user's recordings, real speech to text, the real graph,
                                                 knowledge base and speech, played in real time by a simulated
                                                 browser; every step's stages and audible timeline printed, with its
                                                 silences after the customer stopped (CP3: none over 1.5 s)

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
if os.environ.get("VOICE_LOG"):  # the voice modules' own timing lines (stt, tts, turn), printed among the steps
    import logging
    logging.basicConfig(format="      log %(message)s", stream=sys.stdout)
    logging.getLogger("ole5_chat.voice").setLevel(logging.INFO)

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


# Demo scenarios (VOICE_PLAN.md section 10) as steps: a recording (by mic-lab phrase id) or typed text, and the phase
# expected after it (None: not checked). Typed text stands in where no recording exists. "during": a recording said
# while the step before it is still being answered, a second after its first filler (it is held, then answered).
FLOWS = {
    "ar_kb": [("rec", "s1_kb_password", None)],
    "en_ticket": [("rec", "s2_en_crash", "offer_ticket"), ("rec", "w_yes", "collect"), ("rec", "s2_en_detail", "collect"),
                  ("typed", "That's all I know.", "confirm"), ("rec", "w_ok", "confirm"),
                  ("rec", "s2_en_yes", "none")],
    "ar_urgent": [("rec", "s3_urgent", "confirm"), ("rec", "s3_send", "none")],
    "held": [("rec", "s1_kb_archive", None), ("during", "s1_kb_password", None)],
}
REDEMPTION_S = 0.85  # the browser sends an utterance this long after the customer stops (web/voice.js, D30)
LEAD_S = 0.12  # a silent player starts this long after its first frame arrives (web/voice.js)
WHOLE_LEAD_S = 0.02  # ... or this long, for audio sent complete in one piece
TAIL_S = 0.6  # speech starting in the assistant's last 0.6 s is not over it (web/voice.js)
OVER_REDEMPTION_S = 0.5  # the detector's wait for speech that began over the assistant (web/voice.js)
PRE_PAD_S = 0.3  # each recorded clip starts this long before the voice (the detector's pre-speech pad)
DEAD_AIR_S = 1.5  # the CP3 target: no silence longer than this after the customer stops
REPLY_GAP_S = 0.4  # the customer starts speaking this long after the assistant stops


class Page:
    """The browser, played in real time: frames go on a gapless player, an item is reported played when its last frame
    has been heard, and stop_audio silences it. sound: one [start, end, kind, text] per item."""

    def __init__(self):
        self.out, self.session = [], None
        self.end = 0.0  # when everything queued so far will have played
        self.sound: list[list] = []
        self.whole = False
        self._played: list[asyncio.TimerHandle] = []

    async def json(self, data):
        now = time.perf_counter()
        self.out.append({**data, "at": now})
        if data["type"] == "tts_start":
            self.sound.append([None, None, data["kind"], data["text"], data["id"]])
            self.whole = bool(data.get("whole"))
        elif data["type"] == "tts_end" and not data["cancelled"]:
            ends = self.sound[-1][1] or now
            self._played.append(asyncio.get_running_loop().call_later(max(0.0, ends - now), self.session.played,
                                                                      data["id"]))
        elif data["type"] == "stop_audio":
            for handle in self._played:
                handle.cancel()
            self.end = min(self.end, now)
            for item in self.sound:
                if item[0] is not None and item[1] > now:
                    item[1] = max(item[0], now)

    async def audio(self, data):
        now = time.perf_counter()
        start = self.end if self.end > now else now + (WHOLE_LEAD_S if self.whole else LEAD_S)
        self.end = start + len(data) / 2 / 24000
        item = self.sound[-1]
        item[0] = start if item[0] is None else item[0]
        item[1] = self.end

    def audible(self, at: float) -> str | None:
        """The id of the item heard at that moment, if any."""
        return next((s[4] for s in self.sound if s[0] is not None and s[0] <= at < s[1]), None)

    def last_end(self, at: float) -> float | None:
        return max((s[1] for s in self.sound if s[1] is not None and s[1] <= at), default=None)


def timeline(page: Page, mark: int, spoke_at: list[float], began: float) -> list[str]:
    """The step's audible stretches and its silences after the customer stopped: lines to print."""
    out = page.out[mark:]
    stopped = spoke_at[0]
    lines = []
    marks = [("heard", next((m["at"] for m in out if m["type"] == "heard"), None)),
             ("turn", next((m["at"] for m in out if m["type"] == "turn"), None)),
             ("search", next((m["at"] for m in out if m["type"] == "status" and m.get("stage") == "searching"), None)),
             ("ole5 done", next((m["at"] for m in out if m["type"] == "view"), None))]
    lines.append("    stages after the customer stopped: " + ", ".join(
        f"{name} {at - stopped:.1f}s" for name, at in marks if at))
    sounds = sorted((s for s in page.sound if s[0] is not None and s[1] > s[0] and s[0] >= stopped - 0.05),
                    key=lambda s: s[0])  # what started after the customer stopped (5 ms timer slack)
    cursor, gaps = stopped, []
    for start, end, kind, text, _ in sounds:
        if start - cursor > 0:
            gaps.append((start - cursor, cursor, kind, text))
        lines.append(f"    {start - stopped:5.1f}-{end - stopped:5.1f}s [{kind}] {text[:70]}")
        cursor = max(cursor, end)
    first = sounds[0][0] - stopped if sounds else None
    worst = max(gaps, default=(0.0, stopped, "-", ""))
    long = [g for g in gaps if g[0] > DEAD_AIR_S]
    lines.append(f"    first sound {first:.1f}s after the customer stopped; longest silence "
                 f"{worst[0]:.1f}s (before [{worst[2]}] {worst[3][:40]}); silences over {DEAD_AIR_S}s: {len(long)}"
                 if sounds else "    no sound")
    return lines


async def run_flow(name: str, steps: list, folder: Path, email: str) -> dict:
    from ole5_chat import sessions
    from ole5_chat.voice import fillers
    from ole5_chat.voice.api import _touch, _warm
    from ole5_chat.voice.session import Backend, VoiceSession

    entries = json.loads((folder / "recordings.json").read_text(encoding="utf-8"))
    files, seconds = {}, {}
    for e in entries:
        if e["kind"].startswith("vad"):
            files.setdefault(e["phrase"], folder / e["file"])
    from ole5_chat.voice import stt, tts
    stt._client = tts._client = None  # each flow runs in its own event loop; a client (and its pool) belongs to one
    await fillers.warm()  # new fixed lines are made and heard back before the flow needs them
    sid, _ = sessions.start(email)
    page = Page()
    voice = VoiceSession(sid, page.json, page.audio, Backend(warm=_warm, touch=_touch))  # as the server makes it
    page.session = voice
    await voice.start()
    while voice.speaker.busy:  # the greeting is heard out before the customer speaks
        await asyncio.sleep(0.1)
    ok, stats = True, {"first": [], "worst": [], "long": 0}
    print(f"\n=== {name} ===")
    group = None
    try:
        for i, (kind, what, want) in enumerate(steps):
            if kind == "typed":
                while page.end > time.perf_counter():
                    await asyncio.sleep(0.05)
                await asyncio.sleep(REPLY_GAP_S)
            if kind in ("rec", "during"):
                if kind == "rec":  # the customer answers once the assistant stops
                    await asyncio.sleep(REPLY_GAP_S)
                else:  # asked while the turn before still runs: a second after its first filler started
                    for _ in range(600):
                        if any(m["type"] == "tts_start" and m["kind"] == "filler" for m in page.out[group[0]:]):
                            break
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(1.0)
                voice.speech(True)  # the browser reports the voice starting
                started_over = page.end - time.perf_counter() > TAIL_S  # judged at speech start, as voice.js does
                # The clip holds the 0.3 s before the voice and the detector's 0.85 s after it: it is sent at its end,
                # or 0.35 s sooner over the assistant (the detector then waits 500 ms).
                await asyncio.sleep((len(files[what].read_bytes()) - 44) / 32000 - PRE_PAD_S
                                    - (REDEMPTION_S - OVER_REDEMPTION_S if started_over else 0.0))
            now = time.perf_counter()
            if group is None:
                stop_wait = 0.0 if kind == "typed" else OVER_REDEMPTION_S if started_over else REDEMPTION_S
                group = (len(page.out), [now - stop_wait], now, what)
            if kind == "typed":
                voice.typed(what)
            else:
                wav = files[what].read_bytes()
                over = kind != "typed" and started_over
                spoke = now - (len(wav) - 44) / 32000 + PRE_PAD_S + (REDEMPTION_S - OVER_REDEMPTION_S if over else 0.0)
                playing = page.audible(spoke)
                last = page.last_end(spoke)
                since = 0 if playing else None if last is None or spoke - last > 5 else (spoke - last) * 1000
                voice.utterance({"over_bot": over, "over_id": playing if over else "", "since_bot_ms": since}, wav)
            for _ in range(300):  # the step has been heard (or, typed, has started a turn or been held)
                if any(m["type"] in ("heard", "ignored", "turn") for m in page.out[group[0]:]) or voice.held:
                    break
                await asyncio.sleep(0.1)
            if i + 1 < len(steps) and steps[i + 1][0] == "during":
                continue
            await asyncio.sleep(0.3)
            for _ in range(1800):  # up to 180 s for the turn and what it says, played out
                if voice.turn is None and voice._inbox.empty() and not voice.speaker.busy and voice.partial is None:
                    break
                await asyncio.sleep(0.1)
            mark, spoke_at, began, first_what = group
            group = None
            seen = page.out[mark:]
            heard = [m["text"] for m in seen if m["type"] == "heard"] or [first_what if kind == "typed" else "?"]
            phase = sessions.get(sid)["phase"]
            tickets = [t["ticket_number"] for t in sessions.get(sid)["created_tickets"]]
            good = want is None or phase == want
            ok &= good
            print(f"\n{'ok ' if good else 'BAD'} customer ({kind}): {' / '.join(heard)}   "
                  f"[{time.perf_counter() - began:.1f}s, phase {phase}, want {want}"
                  f"{', tickets ' + str(tickets) if tickets else ''}]")
            report = timeline(page, mark, spoke_at, began)
            print("\n".join(report))
            sounds = sorted((s for s in page.sound if s[0] is not None and s[1] > s[0] and s[0] >= spoke_at[0] - 0.05),
                            key=lambda s: s[0])
            if sounds:
                stats["first"].append(sounds[0][0] - spoke_at[0])
                cursor, worst = spoke_at[0], 0.0
                for start, end, *_ in sounds:
                    worst = max(worst, start - cursor)
                    stats["long"] += start - cursor > DEAD_AIR_S
                    cursor = max(cursor, end)
                stats["worst"].append(worst)
    finally:
        await voice.close()
    stats["ok"] = ok
    return stats


def check_flow(name: str | None, folder: Path) -> None:
    temp = Path(tempfile.mkdtemp(prefix="ole5-voice-"))
    shutil.copytree(ROOT / "sheets", temp / "sheets")
    os.environ.update(SHEETS_DIR=str(temp / "sheets"), TICKET_API_URL="", ATTACHMENTS_DIR=str(temp / "uploads"))
    from ole5_chat import sheets
    from ole5_chat.config import get_settings
    from ole5_chat.knowledge import retriever
    get_settings.cache_clear()
    s = get_settings()
    if s.kb_backend == "ragent2":
        began = time.perf_counter()
        retriever.warm(sorted(set(s.kb_tenants.values())))
        print(f"knowledge base ready in {time.perf_counter() - began:.0f} s", flush=True)
    email = next(str(r["email"]) for r in sheets.customers()
                 if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")
    chosen = {name: FLOWS[name]} if name else FLOWS
    results = {n: asyncio.run(run_flow(n, steps, folder, email)) for n, steps in chosen.items()}
    print("\n" + "  ".join(f"{n}: {'pass' if r['ok'] else 'FAIL'}" for n, r in results.items()))
    first = sorted(x for r in results.values() for x in r["first"])
    worst = sorted(x for r in results.values() for x in r["worst"])
    if first:
        print(f"first sound after the customer stopped: median {first[len(first) // 2]:.1f}s, max {first[-1]:.1f}s; "
              f"longest silence per step: median {worst[len(worst) // 2]:.1f}s, max {worst[-1]:.1f}s; silences over "
              f"{DEAD_AIR_S}s: {sum(r['long'] for r in results.values())} in {len(first)} steps")


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
                             "phase": state["phase"], "sources": state["sources"], "reply": reply,
                             "corrections": state["corrections"]})
                print(f"[{rep}] {channel:5} {total:5.1f}s search@{marks.get('search_started', 0):4.1f}s "
                      f"searches={searches} phase={state['phase']:12} sources={len(state['sources'])}  {text[:40]}\n"
                      f"        {reply[:160]!r}", flush=True)
    out = ROOT / ".voice_cache" / "kb_timings.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved {out}")
    for channel in ("voice", "chat"):
        times = sorted(r["seconds"] for r in rows if r["channel"] == channel)
        mine = [r for r in rows if r["channel"] == channel]
        offers = sum(r["phase"] == "offer_ticket" for r in mine)
        answered = sum(bool(r["sources"]) for r in mine)
        sorry = sum(r["reply"].startswith(("Sorry", "عذراً")) for r in mine)
        retries = sum(sum(c.startswith("retried") for c in r["corrections"]) for r in mine)
        ungrounded = sum("reply not grounded" in r["corrections"] for r in mine)
        print(f"{channel}: median {times[len(times) // 2]} s, max {times[-1]} s, answered {answered}, "
              f"offers {offers}, apologies {sorry}, grounding retries {retries}, not grounded {ungrounded} "
              f"of {len(mine)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stt"
    if mode == "stt":
        asyncio.run(check_stt(Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT.parent / "voice-lab" / "recordings"))
    elif mode == "tts":
        asyncio.run(check_tts())
    elif mode == "kb":
        check_kb(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif mode == "flow":
        check_flow(sys.argv[2] if len(sys.argv) > 2 else None, ROOT.parent / "voice-lab" / "recordings")
    else:
        sys.exit(__doc__)
