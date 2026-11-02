"""Simulate response generation and correction without calling an external model."""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat.agent.contracts import Language
from ole5_chat.agent.nodes import respond, validate
from ole5_chat.identity import Identity
from ole5_chat.agent.contracts import PendingCustomerResponse


def state(text):
    return {
        "identity": Identity(customer_id="test@example.com", email="test@example.com",
                             name="Test", organization="Test", product="OLE5", language="ar"),
        "messages": [HumanMessage(content=text)], "intent": "other",
    }


def test_english_sentence_with_arabic_name_corrects_mixed_reply():
    values = state("Explain the screen named الأرشفة")
    wrong = "هذه صفحة الأرشفة Archive."
    corrected = "This is the archive screen."
    with patch.object(respond.llm, "structured", side_effect=[
        Language(language="en"), Language(language="ar"), Language(language="en")
    ]) as detector, patch.object(respond.llm, "invoke", side_effect=[
        AIMessage(content=wrong), AIMessage(content=corrected)
    ]) as generate:
        result = respond.respond_node(values)
    assert result == {"reply": corrected, "language": "en"}
    correction_messages = generate.call_args_list[1].args[0]
    assert correction_messages[-2].content == wrong
    assert "The customer wrote in en" in correction_messages[0].content
    assert "Judge by the language of the sentences" in detector.call_args_list[0].args[0]
    assert validate._expected_language({**values, **result}) == "en"


def test_arabic_with_english_product_terms_needs_no_rewrite():
    reply = "اختر Open في الشاشة."
    with patch.object(respond.llm, "structured", side_effect=[
        Language(language="ar"), Language(language="ar")
    ]), patch.object(respond.llm, "invoke", return_value=AIMessage(content=reply)) as generate:
        result = respond.respond_node(state("كيف أستخدم Open؟"))
    assert result["reply"] == reply
    assert generate.call_count == 1


def test_character_guard_still_corrects_when_semantic_detection_unavailable():
    values = state("Hello")
    with patch.object(respond, "language_of", return_value=None), \
         patch.object(respond, "language_problem", return_value=None), \
         patch.object(respond.llm, "invoke", side_effect=[
             AIMessage(content="مرحبا"), AIMessage(content="Hello")
         ]) as generate:
        result = respond.respond_node(values)
    assert result == {"reply": "Hello", "language": "en"}
    assert generate.call_count == 2


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
    with patch.object(respond.llm, "structured", return_value=Language(language="ar")) as detector, \
         patch.object(respond.llm, "invoke", return_value=AIMessage(content="تم تسجيل اسم المستخدم.")):
        result = respond.respond_node(values)
    assert result["language"] == "ar"
    # The latest username was not sent to the previous implementation language detector.
    assert all("ahmad123" not in str(call.args[1][-1].content) for call in detector.call_args_list)


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
        Language(language="en"), Language(language="en")
    ]), patch.object(respond.llm, "invoke", return_value=AIMessage(content="Follow these steps.")):
        result = respond.respond_node(values)
    assert result["language"] == "en"






