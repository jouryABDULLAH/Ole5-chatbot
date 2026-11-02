"""Conversation simulations for the bounded Validate -> Respond correction."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import Grounding, RouterDecisionWithoutPending, TicketActionCheck
from ole5_chat.agent.nodes import respond
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.ticket_action_guard import fallback
from ole5_chat.agent import ticket_action_guard
from ole5_chat.identity import Identity


@pytest.mark.parametrize("rejected_twice", [False, True])
@pytest.mark.parametrize("route", ["question", "other"])
def test_ticket_lie_gets_only_one_response_retry(model, rejected_twice, route):
    state = initial_state(Identity(customer_id="test", email="ole5@example.com", name="Test",
                                   organization="Test", product="OLE5", language="ar"))
    state["messages"] = [AIMessage(content="تم فتح تذكرة."), HumanMessage(content=(
        "افتح لي تذكرة عن عدم إمكانية حذف المراسلات" if route == "question" else "هل أنت متأكد؟"))]
    model.add(RouterDecisionWithoutPending(decision=route, explicit_ticket_request=route == "question", reason="Test"))
    model.add(TicketActionCheck(violations=["No actual ticket was created. Answer from evidence."]),
              TicketActionCheck(violations=["Still no actual creation result."] if rejected_twice else []))
    model.add(Grounding(unsupported=[]), Grounding(unsupported=[]))
    bad = "تم إنشاء التذكرة بنجاح."
    good = "حذف المراسلات يتطلب الصلاحية الموثقة. إذا استمرت المشكلة، أخبرني وسأساعدك ضمن نطاقي."
    evidence = {"search_outcome": "answered", "current_references": [
        {"document": "Guide", "text": "حذف المراسلات يتطلب الصلاحية الموثقة.", "relation": "DIRECT"}]}
    with patch.object(respond, "GROUNDING_ENFORCE", False), \
         patch.object(graph, "search_node", return_value=evidence) as search, \
         patch.object(llm, "invoke", side_effect=[AIMessage(content=bad), AIMessage(content=bad if rejected_twice else good)]) as writer, \
         patch("ole5_chat.agent.nodes.ticket_operator.tickets.submit") as submit:
        result = graph.build().invoke(state)
    assert writer.call_count == 2
    assert search.call_count == (1 if route == "question" else 0)
    submit.assert_not_called()
    assert "No actual ticket was created" in writer.call_args.args[0][0].content
    assert result["created_tickets"] == []
    assert result["validation_retries"] == 0
    assert result["validation_note"] is None
    assert len(result["messages"]) == 3  # rejected candidate never entered history
    final = result["messages"][-1]
    assert final.content == (fallback({**state, "language": "ar"}) if rejected_twice else good)
    if rejected_twice:
        assert final.additional_kwargs["sources"] == []
        assert result["last_answer_evidence"] is None


def test_fallback_preserves_actual_creation():
    state = {"language": "en", "ticket_result": {
        "submission_succeeded": True, "ticket": {"ticket_number": "T-123"}}}
    assert fallback(state) == "Ticket T-123 was created."


def test_submit_intent_is_not_creation():
    state = {"language": "en", "manager_action": "submit", "ticket_result": {
        "submission_succeeded": False}}
    assert "unsuccessful" in fallback(state)


def test_checker_receives_authoritative_records_and_failure_is_not_a_pass(model):
    identity = Identity(customer_id="test", email="ole5@example.com", name="Test",
                        organization="Test", product="OLE5", language="en")
    state = initial_state(identity)
    state.update(manager_action="submit", ticket_result={"submission_succeeded": False},
                 created_tickets=[{"ticket_number": "OLD-1", "title": "Unrelated issue"}])
    with patch.object(llm, "structured", return_value=TicketActionCheck(violations=["Submission failed"])) as checker:
        assert ticket_action_guard.violations(state, "Ticket created") == ["Submission failed"]
    payload = checker.call_args.args[1][0].content
    assert '"submission_succeeded": false' in payload
    assert '"OLD-1"' in payload
    with patch.object(llm, "structured", side_effect=llm.ModelFailed("offline")):
        assert ticket_action_guard.violations(state, "Ticket created")
