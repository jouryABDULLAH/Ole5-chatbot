"""The voice channel's text rules: normalization, the commands answered without Ole5 (with the spellings Whisper
produced for the user's recordings), backchannels, the confirm table one row at a time, and the script check."""

import pytest

from ole5_chat.agent.validate import script_language, script_problem
from ole5_chat.voice import rules


def test_normalize_drops_diacritics_tatweel_and_punctuation_and_unifies_letters():
    assert rules.normalize("تَمَامُ!") == "تمام"
    assert rules.normalize("أرسلـــها، الآن.") == "ارسلها الان"
    assert rules.normalize("التذكرة") == rules.normalize("التذكره")
    assert rules.normalize("  Yes,   SUBMIT. ") == "yes submit"


@pytest.mark.parametrize("text, name", [
    ("وقف", "stop"), ("وغف", "stop"), ("وغفت", "stop"), ("لغير", "stop"), ("Stop.", "stop"), ("توقف", "stop"),
    ("لحظة", "wait"), ("لحظة من فضلك", "wait"), ("wait", "wait"), ("hold on", "wait"),
    ("عيد", "repeat"), ("Reid", "repeat"), ("ريد", "repeat"), ("Rit", "repeat"), ("أعد من فضلك", "repeat"), ("repeat that", "repeat"),
    ("معي؟", "there"), ("معين", "there"), ("هل أنت معي؟", "there"), ("Are you there?", "there"),
    ("Stop, stop.", "stop"), ("وقف وقف", "stop"), ("وقف لو سمحت", "stop"), ("Okay, stop.", "stop"),
    ("Wait, wait.", "wait"), ("لحظة من فضلك", "wait"), ("عيد لو سمحت", "repeat"), ("One gift.", "stop"),
])
def test_a_whole_utterance_command_is_recognized_in_its_heard_spellings(text, name):
    assert rules.command(text) == name


@pytest.mark.parametrize("text", ["النظام توقف عن العمل", "I have been waiting an hour", "wait, my screen froze again",
                                  "عيد الميلاد", "stop the emails from coming"])
def test_a_command_word_inside_a_sentence_goes_to_ole5(text):
    assert rules.command(text) is None


def test_khalas_is_a_stop_only_while_the_assistant_talks():
    assert rules.command("خلاص", talking=True) == "stop" and rules.command("خلاص") is None


@pytest.mark.parametrize("text", ["تمام", "اوكي", "حسناً", "Okay.", "mhm", "ايه", "نعم", "طيب تمام", "All right.",
                                  "Uh-huh.", "Mm-hmm.", "ok thanks", "اوكي شكرا"])
def test_listening_sounds_are_backchannels(text):
    assert rules.is_backchannel(text)


@pytest.mark.parametrize("text", ["تمام بس عندي سؤال", "no", "ok but the app still crashes", "كيف أغير كلمة المرور"])
def test_words_with_content_are_not_backchannels(text):
    assert not rules.is_backchannel(text)


# The confirm table (VOICE_PLAN.md section 6), one row each.
@pytest.mark.parametrize("text", ["أرسلها", "ارسل", "قدمها", "submit", "send it", "Send it now please"])
def test_confirm_a_submit_word_submits(text):
    assert rules.confirm_kind(text) == "submit"


@pytest.mark.parametrize("text", ["نعم", "ايه", "ايوه", "أكيد", "yes", "yes please", "sure", "نعم أرسلها", "Yes, submit.",
                                  "تمام ارسلها", "ok yes", "نعم من فضلك", "نعم لو سمحت"])
def test_confirm_a_plain_yes_submits(text):
    assert rules.confirm_kind(text) == "submit"


@pytest.mark.parametrize("text", ["تمام", "طيب", "اوكي", "ok", "Okay.", "alright", "تمام تمام", "All right.", "Uh-huh.",
                                  "ok thanks", "اوكي شكرا"])
def test_confirm_only_an_acknowledgement_is_asked_again(text):
    assert rules.confirm_kind(text) == "ack"


@pytest.mark.parametrize("text", ["نعم بس غير الأولوية", "لا", "cancel", "don't send it", "لا ترسلها",
                                  "send me a copy by email", "what happens after I submit?", "تمام كذا"])
def test_confirm_anything_with_content_goes_to_ole5(text):
    assert rules.confirm_kind(text) == "other"


@pytest.mark.parametrize("text", ["E", "y", "..."])
def test_confirm_a_fragment_is_unclear(text):
    assert rules.confirm_kind(text) == "unclear"


def test_fragments_are_short_unknown_tokens_only():
    assert rules.is_fragment("E") and rules.is_fragment("y")
    assert not rules.is_fragment("ok") and not rules.is_fragment("لا") and not rules.is_fragment("5")
    assert not rules.is_fragment("Ole5")
    assert not any(rules.is_fragment(w) for w in ("Hi.", "بس", "اه"))


def test_every_fixed_line_exists_in_both_languages():
    for key, texts in rules.LINES.items():
        assert set(texts) == {"ar", "en"}, key
        assert script_language(texts["ar"]) == "ar" and script_language(texts["en"]) == "en", key


def test_the_script_check_allows_names_in_the_other_script():
    assert script_problem("افتح شاشة Settings ثم اختر Users من القائمة.", "ar") is None
    assert script_problem("Open the شاشة المراسلات screen and press Save.", "en") is None
    assert script_problem("Open the Settings screen and press Save.", "ar").startswith("The reply is in English.")
    assert script_problem("افتح الإعدادات ثم اضغط حفظ.", "en").startswith("The reply is in Arabic.")
    assert script_language("ok") is None


@pytest.mark.parametrize("text, found", [
    ("وقف، الأولوية غلط", ("stop", "الأولوية غلط")), ("وقف وقف", ("stop", "")), ("Okay, stop.", ("stop", "")),
    ("لحظة لحظة عندي سؤال", ("wait", "عندي سؤال")),
    ("Stop. The version is 3.2, not 3.1", ("stop", "The version is 3.2, not 3.1")),  # the rest kept as written
    ("خلاص", ("stop", "")), ("خلاص خلاص", ("stop", ""))])
def test_a_stop_or_wait_at_the_start_interrupts_and_keeps_what_came_after(text, found):
    assert rules.interrupting(text) == found


@pytest.mark.parametrize("text", ["التطبيق توقف فجأة", "it stopped working", "خلاص ارسلها", "نعم خلاص أرسلها"])
def test_a_stop_word_later_in_a_sentence_or_khalas_with_more_is_not_an_interruption(text):
    assert rules.interrupting(text) is None


def test_a_stop_word_after_the_assistants_words_is_found_inside():
    assert rules.command_inside("كلمة المرور وقف") == ("stop", "كلمه المرور")
    assert rules.command_inside("then press save wait") == ("wait", "then press save")
    assert rules.command_inside("نعم خلاص أرسلها") is None  # "خلاص" inside a sentence is "fine"


@pytest.mark.parametrize("text", ["I already read that", "say it again", "تمام", "كيف أغير كلمة المرور"])
def test_other_words_over_the_assistant_are_not_an_interruption_command(text):
    assert rules.interrupting(text) is None


@pytest.mark.parametrize("text", ["تاما", "Time out.", "hmm yes", "ممم"])
def test_a_very_short_sound_over_the_assistant_is_a_listening_sound(text):
    assert rules.is_short_sound(text, 1.1)


@pytest.mark.parametrize("text, seconds", [("لا", 1.0), ("Stop.", 1.0), ("وقف", 1.2), ("no the mobile app", 1.4),
                                           ("Time out.", 2.0), ("تاما", None)])
def test_commands_a_no_and_longer_speech_over_the_assistant_are_not_listening_sounds(text, seconds):
    assert not rules.is_short_sound(text, seconds)



@pytest.mark.parametrize("text", ["نعم أرسلها، شكراً", "Yes, submit. Thank you.", "ارسلها يعطيك العافية", "نعم صحيح",
                                  "yes, that is correct", "نعم أرسلها للدعم", "اي والله ارسلها", "Yes, go ahead and submit it",
                                  "Yes, you can submit it", "Go ahead", "Confirm", "Create it", "خلاص ارسلها", "يلا ارسلها",
                                  "ايه ارسلها الله يعافيك", "موافق", "افتحها", "yes please", "نعم من فضلك"])
def test_an_explicit_submit_may_carry_words_with_no_content(text):
    assert rules.explicit_submit(text) and rules.confirm_kind(text) == "submit"


@pytest.mark.parametrize("text", ["yes but make it high", "نعم بس خل الأولوية عالية", "تمام كذا", "لو سمحت", "please",
                                  "لا لا ارسلها", "Know.", "ok", "تمام تمام"])
def test_anything_else_is_not_an_explicit_submit(text):
    assert not rules.explicit_submit(text)


def test_thanks_alone_at_confirm_goes_to_ole5_and_polite_words_alone_are_unclear():
    assert rules.confirm_kind("شكراً") == "other" and rules.confirm_kind("thank you") == "other"
    assert rules.confirm_kind("ok thanks") == "ack"
    assert rules.confirm_kind("لو سمحت") == "unclear" and rules.confirm_kind("please") == "unclear"


