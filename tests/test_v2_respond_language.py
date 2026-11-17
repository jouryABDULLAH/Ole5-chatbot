"""Simulate response generation and correction without calling an external model."""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat.agent.contracts import Grounding, Language
from ole5_chat.agent.nodes import respond, validate
from ole5_chat.identity import Identity
from ole5_chat.agent.contracts import PendingCustomerResponse


def state(text):
    return {
        "identity": Identity(customer_id="test@example.com", email="test@example.com",
                             name="Test", organization="Test", product="OLE5", language="ar"),
        "messages": [HumanMessage(content=text)], "intent": "other",
    }


def test_english_sentence_with_arabic_name_corrects_mixed_reply(model):
    # Requirement changed 2026-11-17: Respond no longer rewrites a reply in the wrong language itself; Validate's
    # language check rejects it, in the same note as the other checks, and Respond rewrites it once from that note.
    values = state("Explain the screen named الأرشفة")
    wrong = "هذه صفحة الأرشفة Archive."
    corrected = "This is the archive screen."
    model.add(Language(language="en"), Language(language="ar"), Language(language="en"), Language(language="en"))
    with patch.object(respond.llm, "invoke", side_effect=[
        AIMessage(content=wrong), AIMessage(content=corrected)
    ]) as generate:
        values.update(respond.respond_node(values))
        assert values == {**values, "reply": wrong, "language": "en"}
        checked = validate.validate_node(values)
        assert checked["validation_outcome"] == "repair"
        assert f"- «{wrong}»: The reply is in Arabic." in checked["validation_note"]
        values.update(checked)
        values.update(respond.respond_node(values))
        assert validate.validate_node(values)["reply"] == corrected
    correction_messages = generate.call_args_list[1].args[0]
    assert wrong in correction_messages[-1].content and "in English" in correction_messages[-1].content
    assert "Judge by the language of the sentences" in next(system for schema, system in model.calls
                                                             if schema is Language)
    assert validate._expected_language(values) == "en"


def test_arabic_with_english_product_terms_needs_no_rewrite(model):
    # Since 2026-11-17 Validate checks the language: the reply passes there, so Respond writes once.
    reply = "اختر Open في الشاشة."
    model.add(Language(language="ar"), Language(language="ar"))
    values = state("كيف أستخدم Open؟")
    with patch.object(respond.llm, "invoke", return_value=AIMessage(content=reply)) as generate:
        values.update(respond.respond_node(values))
        result = validate.validate_node(values)
    assert result["reply"] == reply and result["validation_outcome"] == "pass"
    assert generate.call_count == 1


def test_language_alone_still_wrong_after_the_rewrite_is_sent(model):
    # Was the old Respond behaviour too: after its rewrites, a reply still read as the wrong language was sent.
    values = state("Explain the screen named الأرشفة")
    values.update(language="en", reply="هذه صفحة الأرشفة Archive.", validation_retries=validate.REWRITES,
                  validation_note="- earlier")
    model.add(Language(language="ar"))
    result = validate.validate_node(values)
    assert result["validation_outcome"] == "pass" and result["reply"] == values["reply"]


def test_every_check_that_rejects_a_sentence_goes_into_one_note(model):
    from ole5_chat.agent.contracts import SentenceVerdict, TicketActionCheck, TicketViolation
    values = state("هل فتحت التذكرة؟")
    values.update(language="ar", reply="الحد الأقصى خمس ملفات. تم إنشاء التذكرة.")
    model.add(Language(language="ar"),
              Grounding(verdicts=[SentenceVerdict(number=1, verdict="unsupported", problem="No passage gives a limit."),
                                  SentenceVerdict(number=2, verdict="not_product_fact")]),
              TicketActionCheck(violations=[TicketViolation(sentence="تم إنشاء التذكرة.", problem="No ticket was created.",
                                                            fact="ticket_result is empty.")]))
    result = validate.validate_node(values)
    assert result["validation_outcome"] == "repair"
    assert result["validation_note"] == ("- «الحد الأقصى خمس ملفات.»: No passage gives a limit. Fact: no passage "
                                         "supports it.\n- «تم إنشاء التذكرة.»: No ticket was created. Fact: "
                                         "ticket_result is empty.")


def test_conversation_language_kept_when_semantic_detection_unavailable():
    # No regex fallback: when the detector gives no language, the conversation's language (here the profile's) stays.
    values = state("Hello")
    # Respond no longer checks the reply's language (Validate does, since 2026-11-17), so only the detector is patched.
    with patch.object(respond, "language_of", return_value=None), \
         patch.object(respond.llm, "invoke", return_value=AIMessage(content="مرحبا")) as generate:
        result = respond.respond_node(values)
    assert result == {"reply": "مرحبا", "language": "ar"}
    assert generate.call_count == 1


def test_username_detail_keeps_established_arabic_language():
    values = state("ahmad123")
    values.update({
        "language": "ar",
        "intent": "pending_response",
        "manager_action": "provide",
        "answered_detail": PendingCustomerResponse(
            kind="detail", issue_id="issue-1", item="username"
        ),
    })
    # The detector reads the bare username and finds no language; the reply is then checked in Arabic.
    detections = [Language(language="none"), Language(language="ar"), Grounding(verdicts=[])]
    with patch.object(respond.llm, "structured", side_effect=detections) as detector, \
         patch.object(respond.llm, "invoke", return_value=AIMessage(content="تم تسجيل اسم المستخدم.")):
        result = respond.respond_node(values)
    assert result["language"] == "ar"
    assert detector.call_args_list[0].args[1][-1].content == "ahmad123"


def test_new_english_question_still_switches_language_during_ticket_prep():
    values = state("How do I reset my password?")
    values.update({
        "language": "ar",
        "intent": "question",
        "pending_customer_response": PendingCustomerResponse(
            kind="detail", issue_id="issue-1", item="username"
        ),
    })
    with patch.object(respond.llm, "structured", side_effect=[
        Language(language="en"), Language(language="en"), Grounding(verdicts=[])
    ]), patch.object(respond.llm, "invoke", return_value=AIMessage(content="Follow these steps.")):
        result = respond.respond_node(values)
    assert result["language"] == "en"






