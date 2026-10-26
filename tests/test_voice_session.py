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
        self.state = {**self.state, "messages": [*self.state["messages"], *added]}
        return self.state

    def send(self, sid, text, *, channel, heard_language, on_event):
        self.sent.append((text, heard_language, channel))
        return self._turn(text, on_event)

    def submit(self, sid, *, channel, said, heard_language, on_event):
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
    assert kinds.index("heard") < kinds.index("turn") < kinds.index("view") < kinds.index("tts_start")
    assert page.said("reply") == ["افتح صفحة الدخول. ثم اضغط نسيت كلمة المرور."]
    assert page.events("turn")[0]["text"] == "كيف أستعيد كلمة المرور؟"
    assert page.events("status")[-1]["stage"] == "listening"
    assert kinds.count("audio") == 2 and page.events("tts_end")[-1]["cancelled"] is False


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
    assert page.said() == [line("searching"), "اضغط نسيت كلمة المرور."]
    assert {"type": "status", "stage": "searching"} in page.out


def test_a_long_turn_gets_progress_lines():
    fake = FakeOle5(heard=["سؤال طويل عن النظام"], replies=["الجواب."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("filler")) == 3)
        await asyncio.sleep(0.2)  # no fourth line
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, progress_every=0.05)
    assert page.said() == [line("still_checking"), line("still_looking"), line("still_checking"), "الجواب."]


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
    fake = FakeOle5(heard=["كيف أضيف توقيعاً؟", "عيد"], replies=["افتح إعدادات المستخدم ثم التوقيع."])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said("reply"))
        await idle(voice, page)
        voice.utterance({}, WAV)
        await until(lambda: len(page.said("reply")) == 2)
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said("reply") == ["افتح إعدادات المستخدم ثم التوقيع."] * 2
    assert fake.spoken == ["افتح إعدادات المستخدم ثم التوقيع."]  # said again from the kept audio
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
    assert page.said() == [line("held"), "اضغط نسيت كلمة المرور.", "افتح الملف الشخصي."]


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

    assert run(fake, scenario).said() == [line("there_busy"), "الجواب."]


def test_a_turn_past_the_deadline_says_so_and_its_late_reply_is_shown_not_spoken():
    fake = FakeOle5(heard=["سؤال بطيء", "سؤال آخر عن البريد"], replies=["رد متأخر.", "رد سريع."])
    fake.gate = threading.Event()

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.said())
        voice._graph_event(voice.turn_no, {"type": "search_started"})  # a search starting after the deadline
        voice.utterance({}, WAV)  # asked again while the late turn still runs
        await until(lambda: voice.held is not None)
        fake.gate.set()
        await until(lambda: page.said("reply"))
        await idle(voice, page)

    page = run(fake, scenario, deadline=0.1)
    assert page.said() == [line("late"), line("held"), "رد سريع."]
    assert page.events("view")[0]["session"]["messages"][-1] == "رد متأخر."


def test_a_failed_turn_gets_the_apology_and_the_page_stops_waiting():
    fake = FakeOle5(heard=["سؤال"], replies=[RuntimeError("graph broke")])

    async def scenario(voice, page):
        voice.utterance({}, WAV)
        await until(lambda: page.events("view"))
        await idle(voice, page)

    page = run(fake, scenario)
    assert page.said() == [line("apology")] and page.events("view")


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
        voice.utterance({"over_bot": True}, WAV)
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
