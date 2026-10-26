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
