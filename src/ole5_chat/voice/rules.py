"""Text rules for the voice channel, with no model call: normalization, the short commands answered without Ole5,
backchannels, the confirm-step policy, and the fixed lines the assistant speaks.

An utterance is read as phrases, longest first, so "all right", "uh huh" and "لو سمحت" count as one word each. The
word lists include the spellings Whisper produced for the user's own recordings (VOICE_LOG.md, CP0 and CP1), such as
"وغف" or "One gift" for "وقف" and "Reid" or "ريد" for "عيد"."""

import re
from typing import Literal

TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
NOT_WORD = re.compile(r"[^\w\s]")
FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"})


def normalize(text: str) -> str:
    """Lowercase, no diacritics, tatweel or punctuation, hamza and alef forms unified: what every rule matches on."""
    text = TASHKEEL.sub("", text).translate(FOLD).lower().replace("_", " ")
    return " ".join(NOT_WORD.sub(" ", text).split())


def _norm(*phrases: str) -> frozenset[str]:
    return frozenset(normalize(p) for p in phrases)


# Commands. One fires when the utterance is only that command's phrases and polite words ("وقف وقف", "Okay, stop",
# "لحظة لو سمحت"), so "النظام توقف" or "I've been waiting an hour" still go to Ole5.
COMMANDS = {
    "stop": _norm("وقف", "وغف", "وغفو", "وغفت", "لغير", "وقفي", "اوقف", "توقف", "قف", "wagif", "waagif", "waggir",
                  "one gift", "what gift", "stop", "stopp", "stop it"),
    "wait": _norm("لحظة", "لحظه", "ثانية", "ثانيه", "انتظر", "lahza", "lahzah", "lhzah", "wait", "one moment",
                  "hold on", "just a moment", "a moment"),
    "repeat": _norm("عيد", "اعد", "اعيد", "عيدها", "اعدها", "كرر", "كررها", "ريت", "ريد", "reid", "rit", "heit", "read",
                    "repeat", "repeat that", "say that again", "again"),
    "there": _norm("معي", "معين", "انت معي", "هل انت معي", "معاي", "انت هنا", "are you there", "you there",
                   "are you still there", "still there"),
}
# "خلاص" (enough) stops the assistant while it talks; said in silence it means "that's all" and goes to Ole5.
STOP_WHILE_TALKING = _norm("خلاص", "خلاص خلاص", "enough", "that's enough")
POLITE = _norm("لو سمحت", "من فضلك", "please", "ok", "okay", "اوكي", "تمام", "طيب", "يا", "now", "الحين")
# Acknowledgements: listening sounds, never a decision on their own.
ACK = _norm("تمام", "تامام", "تاما", "طيب", "اوكي", "اوكيه", "اوك", "حسنا", "زين", "ماشي", "اها", "اه", "امم", "مم", "ممم", "ok", "okay",
            "okey", "alright", "all right", "hmm", "mhm", "mhmm", "mm hmm", "uh huh", "tama", "tamam")
# A plain yes: an answer to a direct question.
YES = _norm("نعم", "ايه", "اي", "ايوه", "ايوا", "اكيد", "naam", "aywa", "yes", "yeah", "yep", "sure", "of course",
            "please", "من فضلك", "لو سمحت")
SUBMIT = _norm("ارسل", "ارسلها", "ارسله", "ارسلي", "ارسلوها", "قدم", "قدمها", "قدمه", "submit", "send", "erselha",
               "irsilha")
# Thanks said over the assistant is a listening sound too; said on its own it goes to Ole5 like any message.
THANKS = _norm("شكرا", "شكرا لك", "شكرا جزيلا", "مشكور", "يعطيك العافيه", "thank you", "thanks", "thank you very much",
               "thanks a lot")
# Words that may sit around a submit word at the confirm step without changing it: "yes, submit it now please".
CONFIRM_FILLER = _norm("it", "the", "ticket", "now", "go", "ahead", "do", "that", "التذكره", "الحين", "الان", "يا",
                       "اياها", "لي")
NEGATION = _norm("لا", "لاء", "مو", "ما", "بلاش", "الغ", "الغها", "الغي", "la", "no", "not", "don", "dont", "don t",
                 "never", "cancel")
# Short words that are words, not Whisper fragments.
KNOWN_SHORT = _norm("hi", "oh", "اوه", "بس", "هل", "من", "في", "و", "يا", "is", "it", "me", "my", "on", "to")

PHRASES = frozenset().union(*COMMANDS.values(), STOP_WHILE_TALKING, POLITE, ACK, YES, SUBMIT, THANKS, CONFIRM_FILLER,
                            NEGATION)
LONGEST = max(len(p.split()) for p in PHRASES)


def tokens(text: str) -> list[str]:
    """The utterance as known phrases where they match, longest first, and single words elsewhere."""
    words, out, i = normalize(text).split(), [], 0
    while i < len(words):
        for n in range(min(LONGEST, len(words) - i), 0, -1):
            candidate = " ".join(words[i:i + n])
            if n == 1 or candidate in PHRASES:
                out.append(candidate)
                i += n
                break
    return out


def command(text: str, talking: bool = False) -> str | None:
    """stop, wait, repeat or there when the utterance is only that command (said once or more) and polite words;
    otherwise None. talking: the assistant is speaking, so "خلاص" is a stop too."""
    found = tokens(text)
    if not found or len(found) > 5:
        return None
    for name, phrases in COMMANDS.items():  # in priority order: stop > wait > repeat > there
        extra = STOP_WHILE_TALKING if name == "stop" and talking else frozenset()
        if any(t in phrases or t in extra for t in found) and all(t in phrases or t in extra or t in POLITE
                                                                  for t in found):
            return name
    return None


def interrupting(text: str) -> str | None:
    """stop or wait when an utterance said over the assistant contains that command anywhere: the microphone may have
    caught some of the assistant's own words with it ("... كلمة المرور وقف"). Only stop and wait: repeat's words
    ("read", "again") are too common to be heard in passing."""
    found = tokens(text)
    for name in ("stop", "wait"):
        extra = STOP_WHILE_TALKING if name == "stop" else frozenset()
        if any(t in COMMANDS[name] or t in extra for t in found):
            return name
    return None


SHORT_SOUND_S = 1.6  # a one- or two-word segment, the detector's padding included


def is_short_sound(text: str, seconds: float | None) -> bool:
    """A very short utterance over the assistant that is not a command and not a no: a listening sound Whisper wrote
    some other way ("تاما", "Time out." for "تمام"). A real interruption with content is longer; "stop", "wait" and
    "لا" still interrupt."""
    found = tokens(text)
    return (seconds is not None and seconds < SHORT_SOUND_S and 0 < len(found) <= 2
            and not any(t in NEGATION for t in found) and command(text, talking=True) is None)


def is_backchannel(text: str) -> bool:
    """True for "تمام", "ايه", "ok thanks", "all right" and the like: what a listener says while the assistant talks."""
    found = tokens(text)
    return bool(found) and len(found) <= 3 and all(t in ACK or t in YES or t in THANKS for t in found)


ConfirmKind = Literal["submit", "ack", "unclear", "other"]


def confirm_kind(text: str) -> ConfirmKind:
    """The confirm table (VOICE_PLAN.md section 6) for an utterance at the confirm step, after the read-back.

    submit: only yes, submit, acknowledgement and filler words ("it", "now"), at least one of them a yes or a submit
    word, and no negation. ack: only acknowledgements and thanks. unclear: a one- or two-letter fragment such as
    Whisper's "E" for "ايه". other: anything with content ("نعم بس غير الأولوية", "cancel", a question), for Ole5."""
    found = tokens(text)
    if not found:
        return "unclear"
    if any(t in NEGATION for t in found):
        return "other"
    if all(t in ACK or t in THANKS for t in found):
        return "ack"
    if all(t in YES or t in SUBMIT or t in ACK or t in CONFIRM_FILLER for t in found) and \
            any(t in YES or t in SUBMIT for t in found):
        return "submit"
    if is_fragment(text):
        return "unclear"
    return "other"


def is_thanks(text: str) -> bool:
    found = tokens(text)
    return bool(found) and all(t in THANKS or t in POLITE for t in found) and any(t in THANKS for t in found)


def is_fragment(text: str) -> bool:
    """One or two letters that are not a known word: what Whisper makes of a short word it missed ("E", "y")."""
    found = tokens(text)
    return (len(found) == 1 and len(found[0]) <= 2 and not found[0].isdigit() and found[0] not in PHRASES
            and found[0] not in KNOWN_SHORT)


LINES = {
    "searching": {"ar": "لحظات من فضلك، أتحقق من ذلك.", "en": "One moment please, let me check that."},
    "still_checking": {"ar": "ما زلت أتحقق، شكراً لصبرك.", "en": "I am still checking. Thank you for your patience."},
    "still_looking": {"ar": "ما زلت أبحث في الأدلة، لحظات من فضلك.",
                      "en": "Still looking through the documentation, one moment please."},
    "apology": {"ar": "عذراً، حدث خطأ من جهتنا. هل تعيد ما قلته من فضلك؟",
                "en": "Sorry, something went wrong on our side. Could you say that again?"},
    "late": {"ar": "عذراً، هذا يستغرق وقتاً أطول من المعتاد. سيظهر الرد على الشاشة حين يصل.",
             "en": "Sorry, this is taking longer than usual. The answer will appear on your screen when it is ready."},
    "speech_failed": {"ar": "عذراً، تعذّر عليّ قراءة الرد بصوتي. تجده مكتوباً على الشاشة.",
                      "en": "Sorry, I couldn't read the answer out. You'll find it on your screen."},
    "unclear": {"ar": "عذراً، لم أسمع ذلك جيداً. هل تعيده من فضلك؟", "en": "Sorry, I didn't catch that. Could you say it again?"},
    "just_confirm": {"ar": "للتأكيد: هل أرسل التذكرة الآن؟ قل: نعم أرسلها، أو لا.",
                     "en": "Just to confirm: shall I submit the ticket now? Say: yes, submit, or no."},
    "held": {"ar": "سأجيب عن ذلك بعد قليل.", "en": "I'll come to that right after this."},
    "wait": {"ar": "حسناً، أنا في انتظارك.", "en": "Of course, take your time."},
    "there": {"ar": "نعم، أنا معك.", "en": "Yes, I'm here."},
    "there_busy": {"ar": "نعم، ما زلت أتحقق من سؤالك.", "en": "Yes, I'm still checking your question."},
    "nothing_to_repeat": {"ar": "لم أقل شيئاً بعد. كيف أقدر أساعدك؟", "en": "I haven't said anything yet. How can I help?"},
}


def line(key: str, language: str) -> str:
    texts = LINES[key]
    return texts.get(language) or texts["en"]
