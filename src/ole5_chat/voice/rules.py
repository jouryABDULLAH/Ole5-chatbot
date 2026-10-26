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
ACK = _norm("تمام", "تامام", "تاما", "طيب", "اوكي", "اوكيه", "اوك", "حسنا", "زين", "ماشي", "اها", "اه", "ok", "okay",
            "okey", "alright", "all right", "mhm", "mhmm", "mm hmm", "uh huh", "tama", "tamam")
# Hesitations: listening sounds too, but at the confirm step "امم" / "hmm" means "I'm thinking", not "go ahead", so it
# is asked again and never counts for D15.
HESITATION = _norm("امم", "مم", "ممم", "هممم", "hmm", "hm", "umm", "um")
# The assistant's own acknowledgements (D38) that are not the customer's: their echo during a turn is a listening
# sound, but they stay out of ACK, so the confirm table and D15 are unchanged ("فهمت" after "just to confirm" means
# "I understood the instructions", not "submit").
OWN_ACK = _norm("فهمت", "يسعدني ذلك", "got it", "certainly")
# Greetings and how-are-you: answered by Ole5 at once, with no acknowledgement before it.
GREETING = _norm("السلام عليكم", "السلام", "عليكم السلام", "وعليكم السلام", "مرحبا", "مرحبتين", "هلا", "هلا والله", "اهلا",
                 "اهلين", "صباح الخير", "مساء الخير", "كيف حالك", "كيف الحال", "كيفك", "شلونك", "عساك بخير",
                 "اهلا وسهلا", "مرحبا بك", "يا هلا", "hi", "hello", "hey", "hi there", "hello there", "hey there",
                 "good morning", "good afternoon", "good evening", "how are you")
# A plain yes: an answer to a direct question. Polite words alone ("لو سمحت", "please") are not a yes.
YES = _norm("نعم", "ايه", "اي", "ايوه", "ايوا", "اكيد", "موافق", "naam", "aywa", "yes", "yeah", "yep", "sure",
            "of course")
SUBMIT = _norm("ارسل", "ارسلها", "ارسله", "ارسلي", "ارسلوها", "قدم", "قدمها", "قدمه", "افتحها", "افتح التذكره",
               "سجلها", "اعتمدها", "submit", "send", "confirm", "create", "go ahead",
               # the English take of "أرسلها", seen in the browser: "Naam, Ersilha."
               "erselha", "irsilha", "ersilha", "arsilha", "arselha", "irselha", "ersilaha", "er silha", "ar silha",
               "ir silha")
# Thanks said over the assistant is a listening sound too; said on its own it goes to Ole5 like any message.
THANKS = _norm("شكرا", "شكرا لك", "شكرا جزيلا", "مشكور", "يعطيك العافيه", "thank you", "thanks", "thank you very much",
               "thanks a lot")
# Words that may sit around a submit word at the confirm step without changing it: "yes, submit it now please".
CONFIRM_FILLER = _norm("it", "the", "ticket", "now", "go", "ahead", "do", "that", "التذكره", "الحين", "الان", "يا",
                       "اياها", "لي")
# Words that carry no content of their own next to a yes at the confirm step: "نعم أرسلها، شكراً", "Yes, go ahead and
# submit it", "يلا ارسلها الله يعافيك". A yes with any other word ("yes but make it high") is not an explicit submit.
CONTENT_FREE = _norm("and", "you", "can", "i", "want", "to", "is", "correct", "right", "that s right", "for me", "خلاص", "يلا",
                     "والله", "صحيح", "الله يعافيك", "يعطيك العافيه", "ابي", "ابغى", "و", "للدعم", "to support")
NEGATION = _norm("لا", "لاء", "مو", "ما", "بلاش", "الغ", "الغها", "الغي", "la", "no", "not", "don", "dont", "don t",
                 "never", "cancel")
# Short words that are words, not Whisper fragments.
KNOWN_SHORT = _norm("hi", "oh", "اوه", "بس", "هل", "من", "في", "و", "يا", "is", "it", "me", "my", "on", "to")

PHRASES = frozenset().union(*COMMANDS.values(), STOP_WHILE_TALKING, POLITE, ACK, YES, SUBMIT, THANKS, CONFIRM_FILLER,
                            CONTENT_FREE, NEGATION, GREETING, OWN_ACK, HESITATION)
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


def _command_word(phrase: str, enough: bool = True) -> str | None:
    """stop or wait for a command phrase. enough: "خلاص" counts as a stop (it does when said alone)."""
    if phrase in COMMANDS["stop"] or (enough and phrase in STOP_WHILE_TALKING):
        return "stop"
    return "wait" if phrase in COMMANDS["wait"] else None


def interrupting(text: str) -> tuple[str, str] | None:
    """(stop or wait, what was said after it) when an utterance said over the assistant starts with that command:
    "وقف، الأولوية غلط", "Stop, I meant the archive screen". The rest is kept as written. Only stop and wait:
    repeat's words ("read", "again") are too common."""
    words, i, found, real = text.split(), 0, None, False
    while i < len(words):
        for n in (3, 2, 1):
            if i + n > len(words):
                continue
            phrase = normalize(" ".join(words[i:i + n]))
            if n == 1 and not phrase:  # punctuation on its own
                i += 1
                break
            name = _command_word(phrase)
            if name or phrase in POLITE:
                found = found or name
                real = real or _command_word(phrase, enough=False) is not None
                i += n
                break
        else:
            break  # not a command, a polite word or punctuation: what was meant starts here
    rest = " ".join(words[i:]).strip(" ،,.!?؟")
    if found is None or (rest and not real):
        return None  # "خلاص ارسلها" is "fine, send it", not a stop
    return found, rest


def command_inside(text: str) -> tuple[str, str] | None:
    """(stop or wait, the other words) when a stop or wait word is anywhere in the utterance: "كلمة المرور وقف" is
    the assistant's own words caught by the microphone, then the customer's stop. The caller decides whether the
    other words are that echo; "التطبيق توقف فجأة" (the app stopped suddenly) is not, and is no command."""
    found = tokens(text)
    name = next((n for n in (_command_word(t, enough=False) for t in found) if n), None)
    if name is None:
        return None
    return name, " ".join(t for t in found if not _command_word(t) and t not in POLITE)


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
    return bool(found) and len(found) <= 3 and all(t in ACK or t in YES or t in THANKS or t in OWN_ACK or t in HESITATION
                                                   for t in found)


ConfirmKind = Literal["submit", "ack", "unclear", "other"]


def explicit_submit(text: str) -> bool:
    """A clear yes to submitting: at least one yes or submit word (a polite word alone is not one), no negation, and
    nothing else but words with no content of their own (thanks, polite words, "it", "now", "go ahead", "صحيح"...).
    The graph's guard (D4) and the confirm table both use it."""
    found = tokens(text)
    allowed = YES | SUBMIT | ACK | THANKS | POLITE | CONFIRM_FILLER | CONTENT_FREE
    return (any(t in YES or t in SUBMIT for t in found) and not any(t in NEGATION for t in found)
            and all(t in allowed for t in found))


def content_free(text: str) -> bool:
    """Only acknowledgements, yes words, thanks and fillers: nothing to change or ask."""
    found = tokens(text)
    allowed = YES | SUBMIT | ACK | THANKS | POLITE | CONFIRM_FILLER | CONTENT_FREE
    return bool(found) and all(t in allowed for t in found)


def confirm_kind(text: str) -> ConfirmKind:
    """The confirm table (VOICE_PLAN.md section 6) for an utterance at the confirm step, after the read-back.

    submit: explicit_submit. ack: only acknowledgements (and thanks with one). unclear: a one- or two-letter fragment such as Whisper's "E"
    for "ايه", or only polite words. other: anything with content ("نعم بس غير الأولوية", "cancel", a question), and
    thanks on its own, which can mean "no thanks": for Ole5 to route."""
    found = tokens(text)
    if not found:
        return "unclear"
    if any(t in NEGATION for t in found):
        return "other"
    if all(t in ACK or t in THANKS for t in found) and any(t in ACK for t in found):
        return "ack"  # "ok thanks" is an acknowledgement; thanks alone is not
    if explicit_submit(text):
        return "submit"
    if is_fragment(text) or all(t in POLITE or t in HESITATION for t in found):
        return "unclear"
    return "other"


def is_thanks(text: str) -> bool:
    found = tokens(text)
    return bool(found) and all(t in THANKS or t in POLITE for t in found) and any(t in THANKS for t in found)


def is_greeting(text: str) -> bool:
    """Only a greeting or how-are-you, maybe with thanks or a polite word: nothing to look up."""
    found = tokens(text)
    return bool(found) and all(t in GREETING or t in POLITE or t in THANKS or t in ACK or t in OWN_ACK
                               for t in found) \
        and any(t in GREETING for t in found)


def is_no(text: str) -> bool:
    """A bare no ("لا شكراً", "no thanks"): Ole5's own reply acknowledges it."""
    found = tokens(text)
    return bool(found) and all(t in NEGATION or t in POLITE or t in THANKS for t in found) \
        and any(t in NEGATION for t in found)


def is_fragment(text: str) -> bool:
    """One or two letters that are not a known word: what Whisper makes of a short word it missed ("E", "y")."""
    found = tokens(text)
    return (len(found) == 1 and len(found[0]) <= 2 and not found[0].isdigit() and found[0] not in PHRASES
            and found[0] not in KNOWN_SHORT)


QUESTION_WORDS = _norm("how", "what", "why", "where", "when", "which", "who", "can", "could", "would", "is", "are", "do",
                       "does", "did", "should", "will", "كيف", "ماذا", "لماذا", "ليش", "ليه", "متى", "وين", "اين", "فين", "هل",
                       "ايش", "وش", "شو", "شلون", "كم", "مين", "ممكن")
QUESTION_NOUNS = _norm("سؤال", "سوال", "استفسار", "question", "questions")


def is_question(text: str) -> bool:
    """Written as a question (Whisper ends one with "?"), begun with a question word ("وكيف" counts as "كيف"), or
    saying it is one ("وعندي سؤال ثاني")."""
    words = normalize(text).split()
    if not words:
        return False
    return (text.rstrip().endswith(("?", "؟")) or words[0] in QUESTION_WORDS
            or (words[0].startswith("و") and words[0][1:] in QUESTION_WORDS)
            or any(w in QUESTION_NOUNS for w in words))


LINES = {
    # No line may hold a command, a yes or a send word: its echo would be heard as the customer saying it (the English
    # "one moment please" was a wait; tests/test_voice_rules.py checks every line).
    "searching": {"ar": "لحظات من فضلك، أتحقق من ذلك.", "en": "Let me check that for you."},
    "still_checking": {"ar": "ما زلت أتحقق، شكراً لصبرك.", "en": "I am still checking. Thank you for your patience."},
    "still_looking": {"ar": "ما زلت أبحث في أدلة الاستخدام.", "en": "Still looking through the documentation."},
    "long_wait": {"ar": "يستغرق هذا وقتاً أطول قليلاً، وما زلت أعمل عليه.",
                  "en": "This is taking a little longer, but I'm still working on it."},
    "not_done": {"ar": "لم أنتهِ بعد، أواصل البحث.", "en": "Not done yet, I'm still searching."},
    # Said once when a question of the assistant's has gone unanswered (D17): a statement, so nothing is asked twice.
    "idle_prompt": {"ar": "أنا هنا إن احتجت إلى أي مساعدة.", "en": "I'm here whenever you're ready."},
    # The first acknowledgement, said as soon as what the customer said goes to Ole5 (D38).
    "ack_ok": {"ar": "حسناً.", "en": "Okay."},
    "ack_accept": {"ar": "يسعدني ذلك.", "en": "Certainly."},  # «بكل سرور» came back «بكل سرق» every take
    "ack_noted": {"ar": "فهمت.", "en": "Got it."},
    "submitting": {"ar": "حسناً، سأرسل التذكرة الآن.", "en": "Okay, sending the ticket now."},
    "apology": {"ar": "عذراً، حدث خطأ من جهتنا. هل تعيد ما قلته من فضلك؟",
                "en": "Sorry, something went wrong on our side. Could you say that again?"},
    "late": {"ar": "عذراً، هذا يستغرق وقتاً أطول من المعتاد. سيظهر الرد على الشاشة حين يصل.",
             "en": "Sorry, this is taking longer than usual. The answer will appear on your screen when it is ready."},
    "speech_failed": {"ar": "عذراً، تعذّر عليّ قراءة الرد بصوتي. تجده مكتوباً على الشاشة.",
                      "en": "Sorry, I couldn't read the answer out. You'll find it on your screen."},
    "unclear": {"ar": "عذراً، لم أسمع ذلك جيداً. هل تعيده من فضلك؟", "en": "Sorry, I didn't catch that. Could you say it again?"},
    # Ends on a question with no yes or send word in it: the assistant's own echo of its last words cannot answer it.
    "just_confirm": {"ar": "للتأكيد: لإرسال التذكرة قل: نعم أرسلها، أو قل لا. هل تريد إرسالها الآن؟",
                     "en": "Just to confirm: to send the ticket, say: yes, submit, or say no. Would you like it "
                           "submitted now?"},
    "held": {"ar": "سأجيب عن ذلك بعد قليل.", "en": "I'll come to that right after this."},
    # Said between the answer that was running and the one held during it, so the second does not run on from the first.
    "next_question": {"ar": "والآن، بخصوص سؤالك الآخر.", "en": "Now, about your other question."},
    "next_added": {"ar": "والآن، بخصوص ما أضفته.", "en": "Now, about what you added."},
    "wait": {"ar": "حسناً، أنا في انتظارك.", "en": "Of course, take your time."},
    "there": {"ar": "نعم، أنا معك.", "en": "Yes, I'm here."},
    "there_busy": {"ar": "نعم، ما زلت أتحقق من سؤالك.", "en": "Yes, I'm still checking your question."},
    "nothing_to_repeat": {"ar": "لم أقل شيئاً بعد. كيف أقدر أساعدك؟", "en": "I haven't said anything yet. How can I help?"},
    "cannot_submit": {"ar": "عذراً، لا يمكن إرسال التذكرة بهذا الشكل. أخبرني بما تريد تغييره، أو اسألني عن أي شيء.",
                      "en": "Sorry, I can't send the ticket like this. Tell me what you'd like to change, or ask me "
                            "anything."},
}


def line(key: str, language: str) -> str:
    texts = LINES[key]
    return texts.get(language) or texts["en"]
