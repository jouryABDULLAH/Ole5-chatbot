"""Exercise response guards through the compiled graph with scripted model output."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import Grounding, GroundingIssue
from ole5_chat.support_core.state import TicketDraft
from ole5_chat.agent.contracts import Language
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import (OperatorCompletion, PendingCustomerResponse, RouterDecision,
                                      RouterDecisionWithoutPending, manager_schema)
from ole5_chat.agent.nodes import respond
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity


def conversation(text, language="ar"):
    state = initial_state(Identity(customer_id="test", email="ole5@example.com", name="Test",
                                   organization="Test", product="OLE5", language=language))
    state.update(language=language, input_event="message",
                 messages=[AIMessage(content="ما اسم المستخدم؟"), HumanMessage(content=text)])
    return state


@pytest.mark.parametrize("next_field", [None, "browser name"])
def test_username_remains_arabic_when_operator_advances(model, operator_model, next_field):
    state = conversation("ahmad123")
    state.update(active_issue={"id": "issue-1", "description": "Cannot sign in"},
                 pending_customer_response=PendingCustomerResponse(kind="detail", issue_id="issue-1", item="username"),
                 ticket_draft=TicketDraft(product="OLE5", required={"username": None}).model_dump())
    required = {"username": "ahmad123"}
    if next_field:
        required[next_field] = None
    # A summary needs every classification field, as save_ticket_draft checks before it reports summary_ready.
    draft = TicketDraft(product="OLE5", required=required, classified=True, queue="Ole5 Support", type="Bug",
                        subtype="Other", reason="Sign-in fails", priority="Medium", title="Cannot sign in",
                        summary="The customer cannot sign in.")
    operator_model.call("save_ticket_draft", fields={"username": "ahmad123"}, issue_id="issue-1")
    completion = (OperatorCompletion(action="request_detail", reason="Username saved", requested_detail=next_field)
                  if next_field else OperatorCompletion(action="show_summary", reason="Username saved"))
    # A bare value names no language; the reply keeps the conversation's.
    model.add(RouterDecision(intent="pending_response", next_target="ticket_operator", explicit_ticket_request=False, reason="Requested username"),
              completion,
              RouterDecision(intent="pending_response", next_target="respond", explicit_ticket_request=False, reason="Ticket detail was processed."),
              Language(language="none"))
    with patch("ole5_chat.support_core.classify.classify", return_value={"ticket_draft": draft}),          patch.object(llm, "invoke", return_value=AIMessage(content="تم تسجيل اسم المستخدم.")) as writer:
        result = graph.build().invoke(state)
    assert result["language"] == "ar"
    assert result["pending_customer_response"].kind == ("detail" if next_field else "summary")
    assert "in Arabic" in writer.call_args_list[0].args[0][-1].content  # the first reply, before any rewrite
    assert sum(schema is Language for schema, _ in model.calls) == 2  # the customer's value, then the reply


@pytest.mark.parametrize("text,route,action", [
    ("I do not know why this happens", "pending_response", "provide"),
    ("Please reply in English", "pending_response", "provide"),
    ("How do I reset my password", "question", "provide"),
    ("Hello", "other", "provide"),
    ("E403", "other", "provide"),
    ("E403", "pending_response", "not_an_answer"),
])
def test_conversational_messages_use_language_detector(text, route, action):
    state = conversation(text)
    state.update(intent=route, manager_action=action,
                 answered_detail=PendingCustomerResponse(kind="detail", issue_id="issue-1", item="error text"))
    with patch.object(respond, "language_of", return_value="en") as detector:
        assert respond._expected_language(state) == "en"
    detector.assert_called_once_with(text)


@pytest.mark.parametrize("item,text", [("browser name", "Google Chrome"), ("error text", '"Access denied"'),
                                      ("error code", "E403"), ("filename", "screen.png")])
def test_literal_values_preserve_language(item, text):
    state = conversation(text)
    state.update(intent="pending_response", manager_action="provide",
                 answered_detail=PendingCustomerResponse(kind="detail", issue_id="issue-1", item=item))
    with patch.object(respond, "language_of") as detector:
        assert respond._expected_language(state) == "ar"
    detector.assert_not_called()


@pytest.mark.parametrize("failure", ["rewritten", "check_error", "rewrite_error"])
def test_grounding_rewrites_only_the_unsupported_sentence_once(model, failure, caplog):
    state = conversation("How do I archive a record?", "en")
    model.add(RouterDecisionWithoutPending(intent="question", next_target="search", explicit_ticket_request=False, reason="Product question"))
    model.add(RouterDecisionWithoutPending(intent="question", next_target="respond", explicit_ticket_request=False, reason="Search returned the product result."))
    first = "Select Archive. There is an invented limit of 5 files."
    if failure != "check_error":  # with nothing scripted, the check raises ModelFailed
        model.add(Grounding(unsupported=[GroundingIssue(sentence="There is an invented limit of 5 files.",
                                                        problem="No passage gives a limit.", passage="none")]))
    rewrite = llm.ModelFailed("rewrite failed") if failure == "rewrite_error" else AIMessage(content="Select Archive.")
    retrieval = {"search_outcome": "answered", "current_references": [
        {"document": "Archive guide", "text": "Select Archive.", "relation": "DIRECT"}
    ]}
    with patch.object(respond, "GROUNDING_ENFORCE", True), \
         patch.object(graph, "search_node", return_value=retrieval), \
         patch.object(llm, "invoke", side_effect=[AIMessage(content=first), rewrite]) as writer:
        result = graph.build().invoke(state)
    message = result["messages"][-1]
    # The rewrite is published without a second check; when the check or the rewrite fails, the reply passes as it is.
    assert message.content == ("Select Archive." if failure == "rewritten" else first)
    assert message.additional_kwargs["sources"] == ["Archive guide"]
    assert sum(schema is Grounding for schema, _ in model.calls) == 1
    if failure != "check_error":
        note = writer.call_args_list[1].args[0][-1].content
        assert "«There is an invented limit of 5 files.»: No passage gives a limit." in note
        assert "Keep every other sentence as it was" in note
    else:
        assert "grounding check failed" in caplog.text





