"""Speech in and out without the network: the transcript cleaning and the short-clip strategy, the streamed WAV parser
and the 40 ms framing, and the spoken form of a reply (markdown, menu paths, the ticket number, segments)."""

import asyncio
import io
import struct
import wave

import pytest

from ole5_chat.voice import speakable, stt, tts

HINT = stt.HINTS["ar"]


def wav(seconds: float, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return out.getvalue()


@pytest.mark.parametrize("raw, clean", [
    ("نعم، اشتركوا في القناة", "نعم"),
    ("نعم أرسلها. لا تنسوا الاشتراك في القناة", "نعم أرسلها."),
    ("ترجمة نانسي قنقر", ""),
    ("How do I reset my password? Thanks for watching!", "How do I reset my password?"),
    ("تَمَامُ", "تمام"),
    (f"{HINT} كيف أفتح المراسلات؟", "كيف أفتح المراسلات؟"),
    ("...", ""),
])
def test_known_invented_lines_and_the_hint_are_removed(raw, clean):
    assert stt.clean(raw, HINT) == clean


def fake_whisper(monkeypatch, answers):
    """answers: language -> (raw text, confidence). Records (language, hint) per call."""
    calls = []

    async def whisper(data, language, hint):
        calls.append((language, hint))
        raw, confidence = answers[language]
        return stt._Take(language, raw, stt.clean(raw, hint or ""), confidence)

    monkeypatch.setattr(stt, "_whisper", whisper)
    return calls


def test_one_failed_call_keeps_the_other_take(monkeypatch):
    async def whisper(data, language, hint):
        if language == "en":
            raise RuntimeError("Groq 503")
        return stt._Take(language, "نعم", "نعم", -0.1)

    monkeypatch.setattr(stt, "_whisper", whisper)
    assert asyncio.run(stt.transcribe(wav(1.4), "ar")).text == "نعم"


def test_both_languages_are_tried_and_a_short_clip_goes_without_the_hint(monkeypatch):
    calls = fake_whisper(monkeypatch, {"ar": ("نعم", -0.11), "en": ("Naam", -0.25)})
    heard = asyncio.run(stt.transcribe(wav(1.4), "ar"))
    assert sorted(calls) == [("ar", None), ("en", None)]
    assert (heard.text, heard.language, heard.forced, heard.seconds) == ("نعم", None, "ar", 1.4)  # too short to tell


def test_a_long_clip_goes_with_each_languages_hint(monkeypatch):
    calls = fake_whisper(monkeypatch, {"ar": ("عندي سؤال عن Ole5", -0.2), "en": ("عن Ole5", -0.3)})
    asyncio.run(stt.transcribe(wav(2.5), "ar"))
    assert sorted(calls) == [("ar", stt.HINTS["ar"]), ("en", stt.HINTS["en"])]


def test_when_both_takes_share_a_script_the_call_for_that_script_is_kept(monkeypatch):
    fake_whisper(monkeypatch, {"ar": ("The mobile app closes by itself", -0.14),
                               "en": ("The mobile app closes by itself.", -0.13)})
    heard = asyncio.run(stt.transcribe(wav(3.8), "ar"))
    assert heard.forced == "en" and heard.language == "en" and heard.text == "The mobile app closes by itself."


def test_when_the_scripts_differ_the_more_confident_take_wins_with_a_lead_for_the_session_language(monkeypatch):
    # The user's "Yes, submit": the Arabic call invented a closing line, the English one was right.
    fake_whisper(monkeypatch, {"ar": ("نعم, سبب", -0.75), "en": ("Yes, submit", -0.41)})
    assert asyncio.run(stt.transcribe(wav(2.2), "ar")).text == "Yes, submit"
    # The user's "وغفت" (stop): close confidences, the session language decides.
    fake_whisper(monkeypatch, {"ar": ("وغفت", -0.87), "en": ("What gift?", -0.82)})
    assert asyncio.run(stt.transcribe(wav(1.5), "ar")).text == "وغفت"
    assert asyncio.run(stt.transcribe(wav(1.5), "en")).text == "What gift?"


def test_in_a_short_clip_a_known_phrase_beats_an_unknown_one(monkeypatch):
    # The user's "تمام" said over the assistant: the English call heard "Time out." with more confidence.
    fake_whisper(monkeypatch, {"ar": ("تمام", -0.65), "en": ("Time out.", -0.2)})
    assert asyncio.run(stt.transcribe(wav(1.1), "ar")).text == "تمام"
    # A longer clip goes by confidence as before.
    fake_whisper(monkeypatch, {"ar": ("تمام كذا يعني", -0.9), "en": ("Time out of the system", -0.2)})
    assert asyncio.run(stt.transcribe(wav(2.5), "ar")).text == "Time out of the system"


def test_a_known_yes_beats_its_transliteration_in_a_longer_clip(monkeypatch):
    # The user's "نعم أرسلها" played into the browser: the English call wrote "Naam, Ersilha." and was more confident.
    fake_whisper(monkeypatch, {"ar": ("نعم أرسلها", -0.6), "en": ("Naam, Ersilha.", -0.1)})
    assert asyncio.run(stt.transcribe(wav(2.0), "ar")).text == "نعم أرسلها"


def test_the_transliteration_is_a_submit_too():
    from ole5_chat.voice import rules
    assert rules.explicit_submit("Naam, Ersilha.") and rules.confirm_kind("Naam, Ersilha.") == "submit"
    assert rules.explicit_submit("Naam, er Silha?")


def test_a_take_made_only_of_hint_words_is_dropped(monkeypatch):
    fake_whisper(monkeypatch, {"ar": ("نظام Ole5", -0.61), "en": ("Yes.", -0.91)})
    assert asyncio.run(stt.transcribe(wav(2.1), "ar")).text == "Yes."
    assert stt.clean("ودعم RiCH", "") == "" and stt.clean("Ole5 لا يفتح", "") == "Ole5 لا يفتح"


def groq_wav(pcm: bytes) -> bytes:
    """A WAV the way Groq streams it: RIFF, fmt, a LIST chunk (78-byte header in all), then the samples."""
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
    info = b"INFOISFT\x0e\x00\x00\x00Lavf60.16.100\x00"
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"LIST" + struct.pack("<I", len(info)) + info
    body += b"data" + struct.pack("<I", 0xFFFFFFFF) + pcm
    return b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + body


def test_the_header_is_skipped_however_the_stream_is_cut():
    pcm = bytes(range(256)) * 40
    data = groq_wav(pcm)
    assert len(data) - len(pcm) == 78
    for size in (1, 3, 7, 77, 78, 79, 1000):
        parser = tts.WavStream()
        out = b"".join(parser.feed(data[i:i + size]) for i in range(0, len(data), size))
        assert out == pcm, size


def test_audio_in_another_format_is_refused():
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    data = b"RIFF\x00\x00\x00\x00WAVEfmt " + struct.pack("<I", 16) + fmt + b"data\x00\x00\x00\x00"
    with pytest.raises(tts.BadAudio):
        tts.WavStream().feed(data)


def test_frames_are_40_ms_and_the_last_one_keeps_whole_samples():
    framer = tts.Framer()
    frames = framer.push(b"\x01" * 1000) + framer.push(b"\x02" * 3000)
    assert [len(f) for f in frames] == [1920, 1920]
    assert len(framer.flush()) == 160  # 4000 - 3840, already even
    framer.push(b"\x03" * 7)
    assert framer.flush() == b"\x03" * 6
    assert tts.FRAME_BYTES == 1920


def tone(seconds: float, level: int = 8000) -> bytes:
    return b"".join(struct.pack("<h", level if i % 2 else -level) for i in range(int(seconds * 24000)))


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * 24000)


def trimmed(pcm: bytes) -> float:
    trimmer, out = tts.Trimmer(), []
    for frame in tts.frames_of(pcm):
        out += trimmer.push(frame)
    out += trimmer.flush()
    return len(b"".join(out)) / 2 / 24000


def test_silence_around_speech_is_cut_and_long_pauses_are_shortened():
    assert round(trimmed(silence(0.6) + tone(1.0) + silence(0.5)), 2) == 0.04 + 1.0 + 0.08
    assert round(trimmed(tone(0.4) + silence(2.0) + tone(0.4)), 2) == 1.2  # the 2 s pause becomes 0.4 s
    assert round(trimmed(tone(0.4) + silence(0.2) + tone(0.4)), 2) == 1.0  # a short pause stays
    assert trimmed(silence(1.0)) == 0


def test_the_voice_follows_the_language_of_the_reply_not_the_letters_of_one_sentence():
    # D35: an English reply's sentence that is mostly an Arabic screen name keeps the English voice
    assert tts.voice_for('Then choose "إنشاء مراسلة جديدة".', "en")[1] == "troy"
    assert tts.voice_for('اضغط على "Create New Correspondence" ثم "Save".', "ar")[1] == "abdullah"
    assert tts.voice_for("أهلاً بك في Ole5", None)[1] == "abdullah"  # no language given: by its letters
    assert tts.voice_for("OK", None)[1] == "troy"


def test_markdown_lists_links_and_menu_paths_read_as_sentences():
    text = ("لإضافة توقيع:\n1. افتح **إعدادات المستخدم** ← التوقيع.\n2. اكتب التوقيع ثم اضغط \"حفظ\"\n"
            "- راجع [الدليل](https://example.com/guide)")
    assert speakable.speakable(text, "ar") == ("لإضافة توقيع: افتح إعدادات المستخدم، ثم التوقيع. اكتب التوقيع ثم اضغط "
                                               "حفظ. راجع الدليل.")
    assert speakable.speakable("## Steps\n- Open *Settings* → Users\n- Press `Save`", "en") == \
        "Steps. Open Settings, then Users. Press Save."


NUMBER = "T-MOCK-20261026153000-0001"


@pytest.mark.parametrize("text, spoken", [
    (f"تم إنشاء تذكرتك برقم **{NUMBER}**، وسيتواصل معك فريق الدعم عبر البريد الإلكتروني.",
     "تم إنشاء تذكرتك، وسيتواصل معك فريق الدعم عبر البريد الإلكتروني. "
     "رقم التذكرة ظاهر على شاشتك، وينتهي بـ صفر صفر صفر واحد."),
    (f"رقم تذكرتك هو {NUMBER}. سيرد عليك فريق الدعم عبر البريد.",
     "رقم التذكرة ظاهر على شاشتك، وينتهي بـ صفر صفر صفر واحد. سيرد عليك فريق الدعم عبر البريد."),
])
def test_the_ticket_number_is_replaced_by_where_to_find_it_in_arabic(text, spoken):
    assert speakable.speakable(text, "ar", [NUMBER]) == spoken


@pytest.mark.parametrize("text, spoken", [
    (f"Your ticket number is {NUMBER}. The support team will reply by email.",
     "Your ticket number is on your screen, ending in zero zero zero one. The support team will reply by email."),
    (f"Your ticket has been created with number {NUMBER}. We will email you.",
     "Your ticket has been created. Your ticket number is on your screen, ending in zero zero zero one. We will "
     "email you."),
])
def test_the_ticket_number_is_replaced_by_where_to_find_it_in_english(text, spoken):
    assert speakable.speakable(text, "en", [NUMBER]) == spoken


ENDING = "Your ticket number is on your screen, ending in zero zero zero one."


@pytest.mark.parametrize("text, spoken", [
    (f"Your ticket {NUMBER} has been created, and the support team will reply by email.",
     "Your ticket has been created, and the support team will reply by email."),
    (f"Ticket {NUMBER} was created successfully.", "the ticket was created successfully."),
    (f"I've created ticket {NUMBER} for your login issue.", "I've created the ticket for your login issue."),
    (f"Done! Ticket #{NUMBER} is open.", "Done! the ticket is open."),
    (f"Your ticket number: {NUMBER}", ""),
])
def test_the_ticket_noun_stays_when_the_number_leaves(text, spoken):
    assert speakable.speakable(text, "en", [NUMBER]) == f"{spoken} {ENDING}".strip()


def test_ole5_is_said_the_way_the_user_picked_in_each_language():
    assert speakable.speakable("أنا مساعد دعم Ole5 ونظام OLE5.", "ar") == "أنا مساعد دعم أُولِي فايف ونظام أُولِي فايف."
    assert speakable.speakable("I am the Ole5 assistant, Ole 5 support.", "en") == \
        "I am the Oh-lee five assistant, Oh-lee five support."


def test_a_reply_without_the_number_is_unchanged():
    assert speakable.speakable("Press Save.", "en", [NUMBER]) == "Press Save."


EARLIER = "2026102010000012"


@pytest.mark.parametrize("language, text, spoken", [
    ("en", f"You have two tickets. Ticket number {EARLIER}, Archive button, from 20 October, is open.\n"
           f"Ticket {NUMBER}, Inbox crash, from today, is new.",
     "You have two tickets. Ticket ending in zero zero one two, Archive button, from 20 October, is open. Inbox "
     "crash, from today, is new. Your ticket number is on your screen, ending in zero zero zero one."),
    ("ar", f"تذكرتك رقم {EARLIER} عن زر الأرشفة، من ٢٠ أكتوبر، وحالتها مفتوحة.",
     "تذكرتك رقمها ينتهي بـ صفر صفر واحد اثنان عن زر الأرشفة، من ٢٠ أكتوبر، وحالتها مفتوحة."),
    ("en", f"{EARLIER}, Archive button, 20 October, open.", "Ticket ending in zero zero one two, Archive button, 20 "
                                                            "October, open."),
    ("ar", f"{EARLIER}، زر الأرشفة، ٢٠ أكتوبر، مفتوحة.", "التذكرة التي ينتهي رقمها بـ صفر صفر واحد اثنان، زر الأرشفة، "
                                                         "٢٠ أكتوبر، مفتوحة."),
])
def test_an_earlier_tickets_number_is_said_in_place_as_how_it_ends(language, text, spoken):
    # A status reply (D12): earlier tickets keep their place in the list; this session's ticket still points to the screen.
    assert speakable.speakable(text, language, [NUMBER], [EARLIER, NUMBER]) == spoken


def test_segments_start_with_one_sentence_then_group_up_to_the_limit():
    long = "This sentence is long enough to stand on its own as a segment. "
    parts = speakable.segments("Hello. " + long * 8)
    assert parts[0] == "Hello. This sentence is long enough to stand on its own as a segment."
    assert all(len(p) <= speakable.SEGMENT_CHARS for p in parts)
    assert " ".join(parts) == ("Hello. " + long * 8).strip()


def test_a_sentence_longer_than_the_limit_is_cut_at_commas():
    sentence = ", ".join(["open the screen and then choose the option you need"] * 8) + "."
    parts = speakable.segments(sentence)
    assert len(parts) > 1 and all(len(p) <= speakable.SEGMENT_CHARS for p in parts)
    assert " ".join(parts) == sentence


def test_the_number_sentence_stays_before_the_next_issues_question():
    reply = (f"The ticket was created. Your ticket number is {NUMBER}. The support team will reply by email. You also "
             "mentioned the archive button. Would you like a ticket for that?")
    spoken = speakable.speakable(reply, "en", [NUMBER])
    assert spoken.endswith("Would you like a ticket for that?")
    assert spoken.index("on your screen") < spoken.index("archive button")
    arabic = speakable.speakable(f"تم إنشاء التذكرة. رقم تذكرتك هو {NUMBER}. هل تريد أن أفتح تذكرة للمشكلة الأخرى؟", "ar",
                                 [NUMBER])
    assert arabic.endswith("هل تريد أن أفتح تذكرة للمشكلة الأخرى؟") and "ظاهر على شاشتك" in arabic


def test_a_take_far_faster_than_the_voice_speaks_was_cut_short():
    english = ("Install the app. Sign in with your credentials. Do you see any error message when the app closes, "
               "and does it happen on every phone or only on one?")
    assert tts.cut_short(english, "en", 3.7)  # seen on 2026-10-26: the question at the end was lost
    assert not tts.cut_short(english, "en", 7.5)
    arabic = "ثم اضغط على نسيت كلمة المرور، وأدخل بريدك الإلكتروني المسجل في النظام لتصلك رسالة فيها الرابط."
    assert tts.cut_short(arabic, "ar", 3.0) and not tts.cut_short(arabic, "ar", 6.5)
    assert not tts.cut_short("Got it.", "en", 0.3)  # too short to judge


def test_a_long_clip_heard_too_unsurely_is_not_acted_on(monkeypatch):
    # the browser's replayed Arabic question, seen on 2026-10-27
    fake_whisper(monkeypatch, {"ar": ("Kæf og erjist möraslaði fannig nígama?", -0.85),
                               "en": ("Kæf og erjist möraslaði fannig nígama?", -0.9)})
    heard = asyncio.run(stt.transcribe(wav(3.1), "ar"))
    assert heard.unclear and heard.text == ""
    fake_whisper(monkeypatch, {"ar": ("لغير", -1.07), "en": ("Lagheer", -1.2)})  # a one-word «وقف» is still heard
    assert not asyncio.run(stt.transcribe(wav(1.5), "ar")).unclear

