"""The turn manager and the speaker of one voice socket, with fake speech and a fake Ole5: event order, fillers,
stop and repeat, held utterances, the deadline, the confirm table, speech over the assistant, and failures."""

import asyncio
import threading
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat.agent.validate import script_language
from ole5_chat.voice import rules, session as voice_session
from ole5_chat.voice.session import Backend, VoiceSession
from ole5_chat.voice.stt import Heard

WAV = b"RIFF" + b"\x00" * 40
FRAME = b"\x01\x00" * 960


class FakeOle5:
    """Stands in for speech-to-text, speech and the graph. heard: what each utterance is transcribed as (or an
    exception). replies: each turn's reply (or an exception). gate: when set, a turn waits for it."""

    def __init__(self, phase="none", language="ar", heard=(), replies=()):
        self.state = {"identity": SimpleNamespace(language=language), "messages": [AIMessage(content="أهلاً بك")],
                      "phase": phase, "created_tickets": []}
        self.heard, self.replies = list(heard), list(replies)
        self.sent, self.submitted, self.notes, self.spoken = [], [], [], []
        self.events: list[dict] = []
        self.gate: threading.Event | None = None
        self.frame_delay = 0.0  # seconds per speech frame, to catch an item while it is being said
        self.stale: str | None = None  # an older turn's reply landing just before this turn's words
        self.after: dict = {}  # state fields the next turn sets (e.g. reconfirmed by the graph's guard)
        self.refuse_submit = False
        self.speech_fails: set[str] = set()  # texts whose speech request fails

    def backend(self) -> Backend:
        return Backend(transcribe=self.transcribe, speech=self.speech, cached=self.cached, send=self.send,
                       submit=self.submit, note=self.note, get=lambda sid: self.state, view=self.view)

    async def transcribe(self, wav, language):
        text = self.heard.pop(0)
        if isinstance(text, Exception):
            raise text
        seconds = 1.0 if len(text.split()) <= 2 else 2.5  # a word or two is a short segment; a sentence is longer
        return Heard(text=text, language=script_language(text), raw=text, seconds=seconds, ms=5, forced=None)

    async def speech(self, text, language):
        self.spoken.append(text)
        if text in self.speech_fails:
            raise RuntimeError("Groq said 429")
        for _ in range(2):
            await asyncio.sleep(self.frame_delay)
            yield FRAME

    async def cached(self, text, language):
        return FRAME * 3

    def _turn(self, said, on_event):
        for event in self.events:
            on_event(event)
        if self.gate:
            assert self.gate.wait(5)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        added = ([AIMessage(content=self.stale)] if self.stale else []) + \
            ([HumanMessage(content=said)] if said else []) + [AIMessage(content=reply)]
        self.state = {**self.state, "messages": [*self.state["messages"], *added], **self.after}
        self.after = {}
        return self.state

    def send(self, sid, text, *, channel, heard_language, on_event):
        self.sent.append((text, heard_language, channel))
        return self._turn(text, on_event)

    def submit(self, sid, *, channel, said, heard_language, on_event):
        if self.refuse_submit:
            from ole5_chat.tickets.submit import SubmitRefused
            raise SubmitRefused("there is no ticket summary to submit")
        self.submitted.append((said, channel))
        self.submit_languages = [*getattr(self, "submit_languages", []), heard_language]
        return self._turn(said, on_event)

    def note(self, sid, said, answer):
        self.notes.append((said, answer))
        self.state = {**self.state, "messages": [*self.state["messages"], HumanMessage(content=said),
                                                 AIMessage(content=answer)]}
        return self.state

    def view(self, sid, state):
        return {"messages": [m.text for m in state["messages"]], "phase": state["phase"]}


class Browser:
    """What the page receives. With autoplay it reports every item played as soon as it ends, like a page whose
    audio finished."""

    def __init__(self, autoplay=True):
        self.out: list = []
        self.autoplay = autoplay
        self.session: VoiceSession | None = None
        self.on_json = None

    async def json(self, data):
        self.out.append(data)
        if self.on_json:
            await self.on_json(data)
        if self.autoplay and data["type"] == "tts_end" and not data["cancelled"]:
            asyncio.get_running_loop().call_soon(self.session.played, data["id"])

    async def audio(self, data):
        self.out.append(("audio", len(data)))

    def events(self, kind=None):
        return [m for m in self.out if isinstance(m, dict) and (kind is None or m["type"] == kind)]

    def said(self, kind=None):
        return [m["text"] for m in self.events("tts_start") if kind is None or m["kind"] == kind]

    def types(self):
        return [m["type"] if isinstance(m, dict) else "audio" for m in self.out]


async def until(check, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting")


def run(fake, scenario, autoplay=True, **options):
    async def main():
        page = Browser(autoplay)
        voice = VoiceSession("s1", page.json, page.audio, fake.backend(), **options)
        page.session = voice
        try:
            await scenario(voice, page)
        finally:
            await voice.close()
        return page
    return asyncio.run(main())


async def idle(voice, page):
    await until(lambda: voice.turn is None and not voice.speaker.busy and voice._inbox.empty())


def line(key, language="ar"):
    return rules.line(key, language)


def test_a_question_is_heard_answered_and_spoken_in_order():
    fake = FakeOle5(heard=["كيف أستعيد كلمة المرور؟"], replies=["افتح صفحة الدخول. ثم اضغط نسيت كلمة المرور."])

    async def scenario(voice, page):
        voice.utterance({"over_bot": False}, WAV)
        await until(lambda: page.events("view"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert fake.sent == [("كيف أستعيد كلمة المرور؟", "ar", "voice")]
    kinds = page.types()
    # the acknowledgement as soon as the turn starts (D38), the reply after the view
    assert kinds.index("heard") < kinds.index("turn") < kinds.index("tts_start") < kinds.index("view")
    assert page.said() == [line("ack_ok"), "افتح صفحة الدخول. ثم اضغط نسيت كلمة المرور."]
    assert page.events("turn")[0]["text"] == "كيف أستعيد كلمة المرور؟"
    assert page.events("status")[-1]["stage"] == "listening"
    assert kinds.count("audio") == 1 + 2 and page.events("tts_end")[-1]["cancelled"] is False  # cached: one piece


def test_a_search_says_let_me_check_before_the_reply():
    fake = FakeOle5(heard=["كيف أستعيد كلمة المرور؟"], replies=["اضغط نسيت كلمة المرور."])
    fake.events = [{"type": "search_started"}]
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("filler"))
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("ack_ok"), line("searching"), "اضغط نسيت كلمة المرور."]
    assert {"type": "status", "stage": "searching"} in page.out


def test_a_long_turn_gets_progress_lines_none_twice_then_slower():
    fake = FakeOle5(heard=["سؤال طويل عن النظام"], replies=["الجواب."])
    fake.events = [{"type": "search_started"}]
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("filler")) == 7)
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, progress_every=0.05)
    lines = [line(k) for k in ("ack_ok", "searching", "still_looking", "still_checking", "long_wait", "not_done",
                               "still_looking")]
    assert page.said()[:7] == lines and page.said()[-1] == "الجواب."


def test_a_turn_that_did_not_search_says_nothing_about_the_documentation():
    fake = FakeOle5(phase="collect", heard=["على أندرويد"], replies=["هل تظهر رسالة خطأ؟"])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("filler")) == 4)
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, progress_every=0.05)
    assert page.said("filler")[:4] == [line(k) for k in ("ack_noted", "still_checking", "long_wait", "still_checking")]


def test_got_it_after_just_to_confirm_is_not_a_yes():
    fake = FakeOle5(phase="confirm", heard=["تمام", "فهمت"], replies=["هل تريد إرسالها؟"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: fake.sent or fake.submitted)
        await idle(voice, page)

    run(fake, scenario)
    assert not fake.submitted and [t for t, _, _ in fake.sent] == ["فهمت"]


def test_repeat_makes_a_cut_short_part_again_instead_of_replaying_it():
    text = "افتح صفحة الدخول، ثم اضغط على نسيت كلمة المرور، وأدخل بريدك الإلكتروني المسجل في النظام."
    fake = FakeOle5(heard=["عيد"])

    async def scenario(voice, page):
        voice.last_reply = [voice.speaker.say("reply", text, "ar")]  # streams 2 frames: far too short for the text
        await until(lambda: page.events("tts_end"))
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: len(page.events("tts_end")) == 2)

    run(fake, scenario)
    assert fake.spoken.count(text) == 2


def test_a_progress_line_waits_for_silence_after_what_is_being_said():
    fake = FakeOle5(heard=["شكراً جزيلاً"], replies=["الجواب."])  # thanks: no acknowledgement first
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.speaker.say("filler", "لحظات من فضلك، أتحقق من ذلك.", "ar")  # still in the browser: not played yet
        await asyncio.sleep(0.3)
        assert page.said("filler") == ["لحظات من فضلك، أتحقق من ذلك."]  # no progress line over it
        voice.played(page.events("tts_start")[-1]["id"])
        await until(lambda: len(page.said("filler")) == 2)
        page.autoplay = True
        await until(lambda: page.events("tts_end")[-1]["id"] == page.events("tts_start")[-1]["id"])
        voice.played(page.events("tts_start")[-1]["id"])
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, autoplay=False, progress_every=0.1)
    assert page.said("filler")[1] == line("still_checking")  # no search in this turn


def test_a_reply_drops_the_fillers_still_waiting():
    fake = FakeOle5(heard=["سؤال"], replies=["الجواب."])

    fake.frame_delay = 0.1

    async def scenario(voice, page):
        voice.speaker.say("filler", "one", "ar")
        voice.speaker.say("filler", "two", "ar")
        await until(lambda: page.said())
        voice._speak_reply("الجواب.", "ar", [], turn=None)
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == ["one", "الجواب."]


def test_stop_during_a_turn_shows_the_reply_without_speaking_it():
    fake = FakeOle5(heard=["كيف أصدر المراسلة؟", "وقف"], replies=["اضغط تصدير."])
    fake.events = [{"type": "search_started"}]
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("filler"))
        voice.utterance({}, WAV)
        await until(lambda: voice.muted)
        fake.gate.set()
        await until(lambda: page.events("view"))
        await idle(voice, page)

    page = run(fake, scenario, autoplay=False)
    assert page.said("reply") == [] and page.events("stop_audio")
    assert page.events("view")[-1]["session"]["messages"][-1] == "اضغط تصدير."


def test_the_stop_button_cuts_the_reply_being_spoken():
    fake = FakeOle5()
    fake.frame_delay = 0.2
    first = "جملة أولى طويلة بما يكفي لتكون مقطعاً وحدها، وفيها كلام كثير عن الإعدادات والقوائم والشاشات."

    async def scenario(voice, page):
        voice._speak_reply(f"{first} ثم جملة ثانية.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        await voice.stop()
        await idle(voice, page)

    page = run(fake, scenario, autoplay=False)
    assert page.said("reply") == [first]  # the second segment never started
    assert page.events("tts_end") == [{"type": "tts_end", "id": page.events("tts_start")[0]["id"], "cancelled": True}]
    assert page.events("stop_audio") and page.events("status")[-1]["stage"] == "listening"


def test_repeat_says_the_last_reply_again_from_its_audio():
    fake = FakeOle5(heard=["كيف أضيف توقيعاً؟", "عيد"], replies=["افتح الإعدادات ثم التوقيع."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said("reply") == ["افتح الإعدادات ثم التوقيع."] * 2
    assert fake.spoken == ["افتح الإعدادات ثم التوقيع."]  # said again from the kept audio
    assert len(fake.sent) == 1


def test_repeat_with_nothing_said_yet():
    fake = FakeOle5(heard=["عيد"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)

    assert run(fake, scenario).said() == [line("nothing_to_repeat")]


def test_during_a_turn_a_backchannel_is_dropped_and_a_question_is_held_then_answered():
    fake = FakeOle5(heard=["كيف أستعيد كلمة المرور؟", "تمام", "وكيف أغير البريد الإلكتروني؟"],
                    replies=["اضغط نسيت كلمة المرور.", "افتح الملف الشخصي."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.utterance({}, WAV)
        voice.utterance({}, WAV)
        await until(lambda: voice.held is not None)
        fake.gate.set()
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["كيف أستعيد كلمة المرور؟", "وكيف أغير البريد الإلكتروني؟"]
    assert {"type": "ignored", "reason": "backchannel"} in page.out
    # the second answer opens with a bridge, so it does not run straight on from the first
    assert page.said() == [line("ack_ok"), line("held"), "اضغط نسيت كلمة المرور.", line("next_question"),
                           "افتح الملف الشخصي."]


def test_something_added_during_a_turn_is_answered_after_a_bridge_that_says_so():
    fake = FakeOle5(heard=["التطبيق يغلق عند فتح البريد", "وصار هذا من أمس على أندرويد"],
                    replies=["هل تظهر رسالة خطأ؟", "شكراً، هل هناك شيء آخر؟"])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.utterance({}, WAV)
        await until(lambda: voice.held is not None)
        fake.gate.set()
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("ack_ok"), line("held"), "هل تظهر رسالة خطأ؟", line("next_added"),
                           "شكراً، هل هناك شيء آخر؟"]


def test_are_you_there_during_a_turn():
    fake = FakeOle5(heard=["سؤال عن النظام", "هل أنت معي؟"], replies=["الجواب."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    assert run(fake, scenario).said() == [line("ack_ok"), line("there_busy"), "الجواب."]


def test_a_turn_past_the_deadline_says_so_and_its_late_reply_is_shown_not_spoken():
    fake = FakeOle5(heard=["سؤال بطيء", "سؤال آخر عن البريد"], replies=["رد متأخر.", "رد سريع."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: line("late") in page.said())
        voice._graph_event(voice.turn_no, {"type": "search_started"})  # a search starting after the deadline
        voice.utterance({}, WAV)  # asked again while the late turn still runs
        await until(lambda: voice.held is not None)
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, deadline=0.1)
    assert page.said() == [line("ack_ok"), line("late"), line("held"), line("ack_ok"), "رد سريع."]  # held, no bridge
    assert page.events("view")[0]["session"]["messages"][-1] == "رد متأخر."


def test_a_failed_turn_gets_the_apology_and_the_page_stops_waiting():
    fake = FakeOle5(heard=["سؤال"], replies=[RuntimeError("graph broke")])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.events("view"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("ack_ok"), line("apology")] and page.events("view")


def test_speech_to_text_failing_asks_to_say_it_again():
    fake = FakeOle5(heard=[RuntimeError("groq down")])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("unclear")] and {"type": "ignored", "reason": "stt_failed"} in page.out


def test_a_fragment_is_asked_again_and_not_sent_to_ole5():
    fake = FakeOle5(heard=["E"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)

    assert run(fake, scenario).said() == [line("unclear")] and fake.sent == []


def test_audio_that_is_not_a_wav_is_ignored():
    fake = FakeOle5()

    async def scenario(voice, page):
        voice.utterance({}, b"not audio")
        await until(lambda: page.events("ignored"))

    assert run(fake, scenario).events("ignored") == [{"type": "ignored", "reason": "bad_audio"}]


# ---- the confirm step ----

def test_confirm_a_bare_acknowledgement_is_asked_again_and_kept_in_the_history():
    fake = FakeOle5(phase="confirm", heard=["تمام"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)

    page = run(fake, scenario)
    assert fake.notes == [("تمام", line("just_confirm"))] and fake.sent == [] and fake.submitted == []
    assert page.said() == [line("just_confirm")]
    assert page.events("view")[-1]["session"]["messages"][-2:] == ["تمام", line("just_confirm")]


def test_confirm_a_second_acknowledgement_right_after_submits():
    fake = FakeOle5(phase="confirm", heard=["تمام", "اوكي"], replies=["تم إنشاء التذكرة."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: fake.notes)
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert fake.submitted == [("اوكي", "voice")] and fake.sent == []
    assert page.events("turn")[-1]["text"] == "اوكي"


def test_confirm_an_explicit_yes_goes_to_ole5():
    fake = FakeOle5(phase="confirm", heard=["نعم أرسلها"], replies=["تم إنشاء التذكرة."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario)
    assert fake.sent == [("نعم أرسلها", "ar", "voice")] and fake.notes == []


def test_confirm_a_fragment_asks_for_the_explicit_answer():
    fake = FakeOle5(phase="confirm", heard=["y"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)

    assert run(fake, scenario).said() == [line("just_confirm")] and fake.notes == [] and fake.sent == []


def test_confirm_content_goes_to_ole5():
    fake = FakeOle5(phase="confirm", heard=["نعم بس غير الأولوية إلى عالية"], replies=["تم تحديث الملخص."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario)
    assert fake.sent[0][0] == "نعم بس غير الأولوية إلى عالية"


def test_the_submit_button_in_voice_mode_runs_a_spoken_turn():
    fake = FakeOle5(phase="confirm", replies=["تم إنشاء التذكرة."])

    async def scenario(voice, page):
        voice.submit()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert fake.submitted == [(None, "voice")] and page.events("turn")[0]["text"] is None
    assert fake.submit_languages == ["ar"]


# ---- speech over the assistant ----

def test_over_the_assistant_a_backchannel_and_an_echo_are_ignored_and_stop_stops():
    fake = FakeOle5(heard=["تمام", "افتح إعدادات المستخدم ثم التوقيع", "وقف"])

    async def scenario(voice, page):
        voice._speak_reply("افتح إعدادات المستخدم ثم التوقيع.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({"over_bot": True}, WAV)
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: len(page.events("ignored")) == 2)
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: page.events("stop_audio"))

    page = run(fake, scenario, autoplay=False)
    assert [m["reason"] for m in page.events("ignored")] == ["backchannel", "echo"] and fake.sent == []


def test_over_the_assistant_real_words_stop_it_and_are_answered():
    fake = FakeOle5(heard=["لا أقصد تطبيق الجوال وليس الموقع"], replies=["في تطبيق الجوال افتح القائمة."])

    async def scenario(voice, page):
        voice._speak_reply("في الموقع افتح الإعدادات.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: len(page.said("reply")) == 2)

    page = run(fake, scenario, autoplay=False)
    assert page.events("stop_audio") and fake.sent[0][0] == "لا أقصد تطبيق الجوال وليس الموقع"


# ---- language, typing and the greeting ----

def test_the_language_is_sticky_against_short_words_in_the_other_script():
    fake = FakeOle5(heard=["Outlook", "How do I sign my emails?"], replies=["رد.", "Open Settings."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    run(fake, scenario)
    assert [lang for _, lang, _ in fake.sent] == ["ar", "en"]


def test_a_typed_message_in_voice_mode_is_answered_aloud_and_ole5_judges_its_language():
    fake = FakeOle5(replies=["رد."])

    async def scenario(voice, page):
        voice.typed("me@example.com")
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        assert voice.language == "ar"

    run(fake, scenario)
    assert fake.sent == [("me@example.com", None, "voice")]


def test_the_greeting_is_spoken_once_for_a_new_conversation():
    voice_session.GREETED.clear()
    fake = FakeOle5()

    async def scenario(voice, page):
        await voice.start()
        await idle(voice, page)
        await voice.start()
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == ["أهلاً بك."] and page.events("ready")[0]["language"] == "ar"


# ---- found in the CP1 review ----

def test_stop_wins_over_a_turn_finishing_while_the_stop_is_sent():
    fake = FakeOle5(heard=["كيف أصدر المراسلة؟"], replies=["اضغط تصدير."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        async def finish_turn_meanwhile(data):
            if data["type"] == "stop_audio":
                fake.gate.set()
                await asyncio.sleep(0.2)  # the turn ends while stop() is still sending
        page.on_json = finish_turn_meanwhile
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        await voice.stop()
        await until(lambda: page.events("view"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said("reply") == [] and page.events("view")[-1]["session"]["messages"][-1] == "اضغط تصدير."


def test_a_sentence_split_by_a_pause_is_sent_as_one():
    fake = FakeOle5(heard=["عندي مشكلة في", "يعني لما أفتح البريد يطلع لي خطأ"], replies=["أي خطأ يظهر لك؟"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        voice.speech(True)  # the customer goes on speaking right after the pause
        await until(lambda: voice.partial is not None)
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["عندي مشكلة في يعني لما أفتح البريد يطلع لي خطأ"]


def test_a_partial_sentence_is_sent_alone_when_the_rest_was_not_speech():
    fake = FakeOle5(heard=["عندي مشكلة في البريد"], replies=["ما المشكلة؟"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        voice.speech(True)
        await until(lambda: voice.partial is not None)
        voice.speech(False)  # a cough: the detector gave up
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["عندي مشكلة في البريد"]


def test_several_utterances_during_a_turn_are_held_together():
    fake = FakeOle5(heard=["سؤال أول عن النظام", "وعندي سؤال ثاني", "عن البريد الوارد"], replies=["رد أول.", "رد ثان."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.utterance({}, WAV)
        voice.utterance({}, WAV)
        await until(lambda: voice.held and voice.held[0].endswith("الوارد"))
        fake.gate.set()
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert fake.sent[1][0] == "وعندي سؤال ثاني عن البريد الوارد"
    assert page.said().count(line("held")) == 1


def test_an_answer_that_began_in_the_assistants_question_counts():
    fake = FakeOle5(heard=["نعم"], replies=["ممتاز."])

    async def scenario(voice, page):
        voice._speak_reply("هل تريد أن أفتح لك تذكرة؟", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({"over_bot": True, "over_id": page.events("tts_start")[-1]["id"]}, WAV)
        await until(lambda: len(page.said("reply")) == 2)

    run(fake, scenario)
    assert fake.sent[0][0] == "نعم"


def test_a_yes_over_the_confirm_read_back_is_asked_again():
    fake = FakeOle5(phase="confirm", heard=["نعم أرسلها"])

    async def scenario(voice, page):
        voice._speak_reply("هل أرسل التذكرة الآن؟", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: len(page.said()) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said()[-1] == line("just_confirm") and fake.sent == [] and fake.submitted == []


def test_a_reply_that_cannot_be_spoken_says_it_is_on_the_screen():
    fake = FakeOle5(heard=["كيف أضيف توقيعاً؟"], replies=["افتح الإعدادات. ثم اختر التوقيع من القائمة الجانبية في الشاشة."])
    fake.speech_fails = {"افتح الإعدادات. ثم اختر التوقيع من القائمة الجانبية في الشاشة."}

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: line("speech_failed") in page.said())
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said()[-1] == line("speech_failed")


def test_only_this_turns_reply_is_spoken_when_an_older_one_lands_first():
    fake = FakeOle5(heard=["كيف أؤرشف مراسلة؟"], replies=["افتح المراسلة ثم اختر أرشفة."])
    fake.stale = "رد قديم من اتصال سابق."

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    assert run(fake, scenario).said("reply") == ["افتح المراسلة ثم اختر أرشفة."]


def test_khalas_stops_the_assistant_only_while_it_talks():
    fake = FakeOle5(heard=["خلاص", "خلاص"], replies=["حسناً."])
    fake.frame_delay = 0.2

    async def scenario(voice, page):
        voice._speak_reply("شرح طويل عن الإعدادات والقوائم والشاشات المختلفة في النظام.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({}, WAV)
        await until(lambda: page.events("stop_audio"))
        await idle(voice, page)
        voice.utterance({}, WAV)  # in silence it means "that's all": Ole5 gets it
        await until(lambda: fake.sent)

    run(fake, scenario, autoplay=False)
    assert [t for t, _, _ in fake.sent] == ["خلاص"]


def test_stop_said_over_the_assistant_with_its_echo_stops_it():
    fake = FakeOle5(heard=["افتح إعدادات المستخدم وقف"])
    fake.frame_delay = 0.2

    async def scenario(voice, page):
        voice._speak_reply("افتح إعدادات المستخدم، ثم اختر التوقيع من القائمة، ثم اضغط حفظ في أسفل الشاشة.", "ar", [],
                           turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: page.events("stop_audio"))
        await idle(voice, page)

    page = run(fake, scenario, autoplay=False)
    assert fake.sent == [] and page.events("tts_end")[-1]["cancelled"] is True


def test_wait_said_over_the_assistant_stops_it_and_waits():
    fake = FakeOle5(heard=["لحظة لحظة"])
    fake.frame_delay = 0.2

    async def scenario(voice, page):
        voice._speak_reply("شرح طويل عن الإعدادات والقوائم والشاشات المختلفة في النظام.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: line("wait") in page.said())

    page = run(fake, scenario, autoplay=False)
    assert page.events("stop_audio") and fake.sent == []


def test_a_short_unknown_sound_over_the_assistant_is_ignored_and_a_no_interrupts():
    fake = FakeOle5(heard=["تاما", "لا"], replies=["حسناً."])
    fake.frame_delay = 0.2

    async def scenario(voice, page):
        voice._speak_reply("شرح طويل عن الإعدادات والقوائم والشاشات المختلفة في النظام.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: page.events("ignored"))
        assert not page.events("stop_audio")
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: fake.sent)

    page = run(fake, scenario, autoplay=False)
    assert page.events("ignored")[0]["reason"] == "backchannel" and page.events("stop_audio")
    assert [t for t, _, _ in fake.sent] == ["لا"]


def test_a_yes_said_before_the_summary_existed_is_not_applied_to_it():
    fake = FakeOle5(heard=["ولا موظف عندنا يقدر يسجل دخول", "نعم أرسلها"], replies=["الملخص جاهز. هل أرسله؟"])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.utterance({}, WAV)  # said while the urgent turn still runs
        await until(lambda: voice.held is not None)
        fake.state = {**fake.state, "phase": "confirm"}  # the turn prepared the summary
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["ولا موظف عندنا يقدر يسجل دخول"] and fake.submitted == []


def test_a_question_held_across_the_summary_is_still_answered():
    fake = FakeOle5(heard=["ولا موظف عندنا يقدر يسجل دخول", "كم ياخذ الرد عادة؟"], replies=["الملخص جاهز.", "خلال يوم."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.utterance({}, WAV)
        await until(lambda: voice.held is not None)
        fake.state = {**fake.state, "phase": "confirm"}
        fake.gate.set()
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    run(fake, scenario)
    assert [t for t, _, _ in fake.sent][1] == "كم ياخذ الرد عادة؟"


# ---- the confirm step, CP2 ----

READ_BACK = ("تذكرتك عن تعطل التطبيق عند فتح صندوق الوارد، وأولويتها متوسطة. إن أردت تعديل شيء فأخبرني، ولإرسالها قل: "
             "نعم أرسلها. هل تريد إرسالها الآن؟")


async def read_back(voice, page):
    voice._speak_reply(READ_BACK, "ar", [], turn=None)
    await until(lambda: len(page.said("reply")) >= 2)


def test_over_the_read_back_an_acknowledgement_is_ignored_and_does_not_count_for_d15():
    fake = FakeOle5(phase="confirm", heard=["تمام"])
    fake.frame_delay = 0.2

    async def scenario(voice, page):
        await read_back(voice, page)
        first = page.events("tts_start")[0]["id"]
        voice.utterance({"over_bot": True, "over_id": first}, WAV)
        await until(lambda: page.events("ignored"))

    page = run(fake, scenario, autoplay=False)
    assert not page.events("stop_audio") and fake.notes == [] and fake.sent == []


def test_a_yes_over_the_read_back_is_asked_again_even_over_its_closing_question():
    fake = FakeOle5(phase="confirm", heard=["ايوه تمام ارسلها"])

    async def scenario(voice, page):
        await read_back(voice, page)
        last = page.events("tts_start")[-1]["id"]
        voice.utterance({"over_bot": True, "over_id": last}, WAV)
        await until(lambda: line("just_confirm") in page.said())

    page = run(fake, scenario, autoplay=False)
    assert page.events("stop_audio") and fake.sent == [] and fake.submitted == []


def test_the_read_backs_own_submit_phrase_heard_over_it_is_ignored_not_asked_again():
    # Laptop speakers: the microphone catches "قل: نعم أرسلها". Asking again would say the phrase again, in a loop.
    fake = FakeOle5(phase="confirm", heard=["نعم أرسلها", "نعم أرسلها"])

    async def scenario(voice, page):
        await read_back(voice, page)
        last = page.events("tts_start")[-1]["id"]
        voice.utterance({"over_bot": True, "over_id": last}, WAV)
        voice.utterance({"over_bot": True, "over_id": last}, WAV)
        await until(lambda: len(page.events("ignored")) == 2)

    page = run(fake, scenario, autoplay=False)
    assert not page.events("stop_audio") and line("just_confirm") not in page.said() and fake.sent == []


def test_a_prompt_yes_just_after_the_read_back_is_an_answer_not_echo():
    fake = FakeOle5(phase="confirm", heard=["نعم أرسلها"], replies=["تم إنشاء التذكرة."])

    async def scenario(voice, page):
        await read_back(voice, page)
        await idle(voice, page)
        voice.utterance({"over_bot": False, "since_bot_ms": 300}, WAV)
        await until(lambda: fake.sent)

    run(fake, scenario)
    assert fake.sent[0][0] == "نعم أرسلها"


def test_stop_disarms_d15():
    fake = FakeOle5(phase="confirm", heard=["تمام", "وقف", "تمام"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: line("just_confirm") in page.said())
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: page.events("stop_audio"))
        await asyncio.sleep(0.2)
        voice.utterance({}, WAV)
        await asyncio.sleep(0.3)
        await idle(voice, page)

    page = run(fake, scenario)
    assert fake.submitted == [] and page.said().count(line("just_confirm")) == 2


def test_an_apology_after_the_guard_does_not_arm_d15():
    fake = FakeOle5(phase="confirm", heard=["صحيح", "تمام"],
                    replies=["عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى."])
    fake.after = {"reconfirmed": True}

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({}, WAV)
        await asyncio.sleep(0.3)
        await idle(voice, page)

    run(fake, scenario)
    assert fake.submitted == [] and len(fake.notes) == 1  # the "تمام" got "just to confirm", not a submit


def test_a_yes_over_a_filler_during_a_turn_at_confirm_is_not_asked_mid_turn():
    fake = FakeOle5(phase="confirm", heard=["غير الأولوية إلى عالية", "ايوه تمام ارسلها"], replies=["تم التعديل."])
    fake.events = [{"type": "search_started"}]
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("filler"))
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: page.events("ignored"))
        fake.gate.set()
        await until(lambda: page.said("reply"))

    page = run(fake, scenario, autoplay=False)
    assert line("just_confirm") not in page.said() and fake.submitted == []


def test_d15_does_not_survive_wait_or_repeat():
    for command in ("لحظة", "عيد"):
        fake = FakeOle5(phase="confirm", heard=["تمام", command, "تمام"])

        async def scenario(voice, page):
            voice.last_reply = [voice.speaker.say("reply", READ_BACK, "ar")]
            await idle(voice, page)
            for _ in range(3):
                voice.utterance({}, WAV)
                await asyncio.sleep(0.2)
                await idle(voice, page)

        run(fake, scenario)
        assert fake.submitted == [] and len(fake.notes) == 2, command


def test_thanks_twice_at_confirm_goes_to_ole5_and_submits_nothing():
    fake = FakeOle5(phase="confirm", heard=["شكراً", "شكراً"], replies=["عفواً. هل تريد إرسالها الآن؟", "عفواً."])

    async def scenario(voice, page):
        for _ in range(2):
            voice.utterance({}, WAV)
            await asyncio.sleep(0.2)
            await idle(voice, page)

    run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["شكراً", "شكراً"] and fake.submitted == []


def test_after_the_guard_asked_again_an_acknowledgement_submits():
    fake = FakeOle5(phase="confirm", heard=["صحيح", "تمام"], replies=[line("just_confirm"), "تم إنشاء التذكرة."])
    fake.after = {"reconfirmed": True}

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: fake.submitted)

    run(fake, scenario)
    assert fake.submitted == [("تمام", "voice")]


def test_a_yes_split_by_a_pause_is_joined_and_sent():
    fake = FakeOle5(phase="confirm", heard=["نعم", "أرسلها"], replies=["تم إنشاء التذكرة."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        voice.speech(True)
        await until(lambda: voice.partial is not None)
        voice.utterance({}, WAV)
        await until(lambda: fake.sent)

    run(fake, scenario)
    assert fake.sent[0][0] == "نعم أرسلها"


def test_stop_with_more_words_over_the_assistant_answers_the_rest():
    fake = FakeOle5(heard=["وقف، الأولوية غلط"], replies=["ما الأولوية الصحيحة؟"])
    fake.frame_delay = 0.2

    async def scenario(voice, page):
        voice._speak_reply("شرح طويل عن الإعدادات والقوائم والشاشات المختلفة في النظام.", "ar", [], turn=None)
        await until(lambda: page.said("reply"))
        voice.utterance({"over_bot": True}, WAV)
        await until(lambda: fake.sent)

    page = run(fake, scenario, autoplay=False)
    assert page.events("stop_audio") and fake.sent[0][0] == "الأولوية غلط"  # as said, not normalized


def test_the_assistants_echo_just_after_it_spoke_is_ignored():
    fake = FakeOle5(heard=["افتح إعدادات المستخدم ثم التوقيع"])

    async def scenario(voice, page):
        voice._speak_reply("افتح إعدادات المستخدم ثم التوقيع.", "ar", [], turn=None)
        await idle(voice, page)
        voice.utterance({"over_bot": False, "since_bot_ms": 300}, WAV)
        await until(lambda: page.events("ignored"))

    page = run(fake, scenario)
    assert page.events("ignored")[0]["reason"] == "echo" and fake.sent == []


def test_a_refused_spoken_submit_is_said_not_only_shown():
    fake = FakeOle5(phase="confirm", heard=["تمام", "تمام"])
    fake.refuse_submit = True

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await asyncio.sleep(0.2)
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: line("cannot_submit") in page.said())

    page = run(fake, scenario)
    assert page.events("error")



def test_a_yes_in_latin_letters_does_not_switch_the_conversation_to_english():
    fake = FakeOle5(phase="confirm", heard=["Naam, er Silha?"], replies=["تم إنشاء التذكرة."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: fake.sent)
        assert voice.language == "ar"

    run(fake, scenario)
    assert fake.sent[0][1] == "ar"


def test_a_later_part_of_a_reply_cut_short_is_made_again_and_the_longer_take_is_sent():
    text = "ثم اضغط على نسيت كلمة المرور، وأدخل بريدك الإلكتروني المسجل في النظام لتصلك رسالة فيها الرابط."
    fake = FakeOle5()
    # frames per take: the first part is long enough (3 s) to cover making the later part twice, whose first take
    # came back cut short
    takes = {"first": [75], text: [2, 150]}

    async def speech(said, language):
        fake.spoken.append(said)
        for _ in range(takes[said].pop(0)):
            yield FRAME

    fake.speech = speech

    async def scenario(voice, page):
        voice.speaker.say("reply", "first", "ar", part=0)
        voice.speaker.say("reply", text, "ar", part=1)
        await until(lambda: len(page.events("tts_end")) == 2)

    page = run(fake, scenario)
    assert fake.spoken == ["first", text, text]  # the first part streams once; the cut later part is made again
    sent = [n for x, n in (x for x in page.out if isinstance(x, tuple))]
    assert sum(sent) == (75 + 150) * len(FRAME) and sent[-1] < 150 * len(FRAME)  # the longer take, in 250 ms pieces


def test_a_later_part_that_cannot_be_made_whole_in_time_streams():
    text = "ثم اضغط على نسيت كلمة المرور، وأدخل بريدك الإلكتروني المسجل في النظام لتصلك رسالة فيها الرابط."
    fake = FakeOle5()
    takes = {"first": [2], text: [2]}  # 80 ms of audio ahead: no time to make the later part whole and check it

    async def speech(said, language):
        fake.spoken.append(said)
        for _ in range(takes[said].pop(0)):
            yield FRAME

    fake.speech = speech

    async def scenario(voice, page):
        voice.speaker.say("reply", "first", "ar", part=0)
        voice.speaker.say("reply", text, "ar", part=1)
        await until(lambda: len(page.events("tts_end")) == 2)

    page = run(fake, scenario)
    assert fake.spoken == ["first", text] and [m["whole"] for m in page.events("tts_start")] == [False, False]


def acks_for(phase, heard, language="ar"):
    """What is said first for each utterance at a phase (the turn's reply is a placeholder)."""
    fake = FakeOle5(phase=phase, heard=heard, replies=["ر."] * len(heard), language=language)

    async def scenario(voice, page):
        for i in range(len(heard)):
            voice.utterance({}, WAV)
            await until(lambda: len(page.said("reply")) == i + 1)
            await idle(voice, page)

    return [t for t in run(fake, scenario).said() if t != "ر."]


def test_the_first_acknowledgement_fits_the_step():
    assert acks_for("none", ["كيف أؤرشف مراسلة في النظام؟"]) == [line("ack_ok")]
    assert acks_for("none", ["السلام عليكم، كيف حالك؟", "شكراً", "نعم"]) == []  # answered at once
    assert acks_for("offer_ticket", ["نعم"]) == [line("ack_accept")]
    assert acks_for("offer_ticket", ["لا شكراً"]) == []  # Ole5's reply acknowledges the no
    assert acks_for("collect", ["على أندرويد", "من أمس", "تمام"]) == [line("ack_noted"), line("ack_ok"),
                                                                       line("ack_noted")]
    assert acks_for("confirm", ["نعم أرسلها"]) == [line("submitting")]
    assert acks_for("confirm", ["غير الأولوية إلى عالية"]) == [line("ack_ok")]
    assert acks_for("none", ["How do I archive a correspondence?"], "en") == [line("ack_ok", "en")]


def test_the_echo_of_an_acknowledgement_after_a_quick_turn_is_not_a_new_turn():
    fake = FakeOle5(phase="collect", heard=["على أندرويد", "فهمت"], replies=["هل تظهر رسالة خطأ؟"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        await asyncio.sleep(0.8)  # a 1 s clip that began as "فهمت." played arrives after it (and the detector wait)
        voice.utterance({"since_bot_ms": 0}, WAV)  # the microphone heard "فهمت." as it ended
        await until(lambda: page.events("ignored"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["على أندرويد"] and not fake.notes
    assert page.events("ignored")[-1]["reason"] == "echo"


def test_no_progress_line_while_the_answer_is_being_written_after_the_search():
    fake = FakeOle5(heard=["كيف أؤرشف مراسلة في النظام؟"], replies=["الجواب."])
    fake.events = [{"type": "search_started"}, {"type": "search_done", "searches": 1, "found": True}]
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("filler")) == 2)  # the acknowledgement and "let me check"
        await asyncio.sleep(0.5)  # well past progress_every, within the 3 s after the search
        assert len(page.said("filler")) == 2
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, progress_every=0.05)
    assert page.said() == [line("ack_ok"), line("searching"), "الجواب."]


def test_thanks_said_over_the_sending_line_does_not_stop_it():
    fake = FakeOle5(phase="confirm", heard=["نعم أرسلها", "شكراً"], replies=["تم إنشاء التذكرة."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("filler"))
        ack = page.events("tts_start")[-1]["id"]
        voice.utterance({"over_bot": True, "over_id": ack}, WAV)  # «شكراً» while «حسناً، سأرسل التذكرة الآن.» plays
        await until(lambda: page.events("ignored"))
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("submitting"), "تم إنشاء التذكرة."] and not page.events("stop_audio")


def test_the_customer_going_on_over_the_acknowledgement_is_held_not_dropped():
    fake = FakeOle5(heard=["عندي مشكلة في البريد", "يطلع لي خطأ عند فتح المرفقات"], replies=["ر1.", "ر2."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("filler"))
        voice.utterance({"over_bot": True, "over_id": page.events("tts_start")[-1]["id"]}, WAV)
        await until(lambda: voice.held is not None)
        fake.gate.set()
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["عندي مشكلة في البريد", "يطلع لي خطأ عند فتح المرفقات"]


def test_the_customers_own_okay_well_after_the_acknowledgement_is_answered():
    fake = FakeOle5(phase="collect", heard=["على أندرويد", "حسناً"], replies=["هل هناك شيء آخر؟", "ر."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        await asyncio.sleep(1.5)  # the acknowledgement «فهمت.» ended long before this began
        voice.utterance({"since_bot_ms": 300}, WAV)
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    run(fake, scenario)
    assert [t for t, _, _ in fake.sent] == ["على أندرويد", "حسناً"]


def test_fixed_lines_come_whole_and_stream_when_the_cache_fails():
    fake = FakeOle5(heard=["كيف أؤرشف مراسلة؟"], replies=["الجواب."])

    async def broken(text, language):
        raise OSError("disk")

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.backend.cached = broken
        voice.speaker.say("line", line("there"), "ar", fixed=True)
        await until(lambda: len(page.events("tts_end")) == 3)

    page = run(fake, scenario)
    starts = page.events("tts_start")
    assert [m["whole"] for m in starts] == [True, False, False]  # cached ack; reply streams; the line streams
    assert line("there") in fake.spoken


def test_progress_silence_is_counted_from_the_end_of_what_was_heard_and_the_customer_holds_it():
    fake = FakeOle5(heard=["شكراً جزيلاً"], replies=["الجواب."])  # no acknowledgement: nothing else is said
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: voice.turn is not None)
        voice.speech(True)  # the customer talks on for longer than the silence a line waits for
        await asyncio.sleep(0.4)
        assert not page.said("filler")
        voice.speech(False)
        await until(lambda: page.said("filler"))
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario, progress_every=0.1)


def test_touch_reopens_connections_when_speech_starts_and_the_search_returns():
    fake = FakeOle5(heard=["كيف أؤرشف مراسلة؟"], replies=["الجواب."])
    fake.events = [{"type": "search_started"}, {"type": "search_done", "searches": 1, "found": True}]
    touched = []

    async def touch():
        touched.append(1)

    async def scenario(voice, page):
        voice.backend.touch = touch
        voice.speech(True)
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    run(fake, scenario)
    assert len(touched) >= 3  # speech start, the turn's start, the search's end


def test_an_unanswered_question_gets_one_silence_prompt():
    fake = FakeOle5(heard=["عندي مشكلة في البريد"], replies=["هل تظهر لك رسالة خطأ؟"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await until(lambda: line("idle_prompt") in page.said())
        await asyncio.sleep(0.4)  # once only
        await idle(voice, page)

    page = run(fake, scenario, idle_after=0.2)
    assert page.said().count(line("idle_prompt")) == 1


def test_no_silence_prompt_after_a_statement_while_muted_or_once_a_ticket_exists():
    for reply, muted, tickets in (("تم حفظ الملاحظة.", False, []), ("هل تريد شيئاً آخر؟", True, []),
                                  ("هل تريد شيئاً آخر؟", False, [{"ticket_number": "T-1"}])):
        fake = FakeOle5(heard=["سؤال عن البريد الوارد"], replies=[reply])
        fake.after = {"created_tickets": tickets}

        async def scenario(voice, page, muted=muted):
            voice.mute(muted)
            voice.utterance({}, WAV)
            await until(lambda: page.said("reply"))
            await idle(voice, page)
            await asyncio.sleep(0.5)

        assert line("idle_prompt") not in run(fake, scenario, idle_after=0.2).said(), (reply, muted, tickets)


def test_the_customer_starting_to_speak_cancels_the_silence_prompt():
    fake = FakeOle5(heard=["عندي مشكلة في البريد"], replies=["هل تظهر لك رسالة خطأ؟"])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.speech(True)
        voice.speech(False)  # a cough: nothing sent, but the customer is there
        await asyncio.sleep(0.5)

    assert line("idle_prompt") not in run(fake, scenario, idle_after=0.2).said()


def test_an_unsure_transcript_is_asked_again_not_answered():
    fake = FakeOle5()

    async def transcribe(wav, language):
        return Heard(text="", language=None, raw="Kæf og erjist", seconds=3.1, ms=5, forced="ar", confidence=-0.85,
                     unclear=True)

    fake.transcribe = transcribe

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("unclear")] and not fake.sent

