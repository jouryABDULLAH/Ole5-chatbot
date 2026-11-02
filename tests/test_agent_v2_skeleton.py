from langchain_core.messages import AIMessage, HumanMessage
from unittest.mock import patch

from ole5_chat.agent.contracts import manager_schema, router_schema
from ole5_chat.agent.context import previous_assistant_message, router_context
from ole5_chat.agent.graph import after_router, after_search, build
from ole5_chat.agent.nodes.router import router_node
from ole5_chat.agent.nodes.finalize import finalize_node
from ole5_chat.agent.nodes.ticket_operator import ticket_operator_node
from ole5_chat.agent.nodes.ticket_enquiry import ticket_enquiry_node
from ole5_chat.agent.nodes.validate import validate_node
from ole5_chat.agent.prompts.router import router_prompt
from ole5_chat.agent.state import AgentState
from ole5_chat.agent.contracts import PendingCustomerResponse
from ole5_chat.identity import Identity


def test_new_graph_compiles():
    build()


def test_router_routes_have_single_owner_boundary():
    assert after_router({"router_decision": "question"}) == "search"
    assert after_router({"router_decision": "pending_response"}) == "ticket_operator"
    assert after_router({"router_decision": "escalation"}) == "ticket_operator"
    assert after_router({"router_decision": "ticket_enquiry"}) == "ticket_enquiry"
    assert after_router({"router_decision": "other"}) == "respond"


def test_search_outcomes_have_explicit_edges():
    assert after_search({"search_outcome": "answered"}) == "respond"
    assert after_search({"search_outcome": "failed"}) == "respond"
    assert after_search({"search_outcome": "partial"}) == "ticket_operator"
    assert after_search({"search_outcome": "no_answer"}) == "ticket_operator"


def test_context_labels_latest_customer_and_previous_assistant():
    messages = [AIMessage(content="What browser do you use?"), HumanMessage(content="Chrome")]
    assert previous_assistant_message(messages) == "What browser do you use?"
    context = router_context(AgentState(messages=messages))
    assert context["latest_customer_message"] == "Chrome"
    assert context["previous_assistant_message"] == "What browser do you use?"


def test_contracts_scope_manager_actions_by_pending_context():
    assert "accept" in manager_schema("offer").model_fields["action"].annotation.__args__
    assert "submit" in manager_schema("summary").model_fields["action"].annotation.__args__
    assert "submit" not in manager_schema("offer").model_fields["action"].annotation.__args__
    assert "pending_response" in router_schema(True).model_fields["decision"].annotation.__args__
    assert "pending_response" not in router_schema(False).model_fields["decision"].annotation.__args__


def _state(messages, pending=None):
    state = AgentState(
        messages=messages,
        identity=Identity(customer_id="test", email="test@example.com", name="Test",
                          organization="Test", product="OLE5", language="en"),
        pending_customer_response=pending,
    )
    return state


def test_router_prompt_labels_latest_message_and_pending_context():
    prompt = router_prompt(_state([
        AIMessage(content="What browser do you use?"),
        HumanMessage(content="Chrome"),
    ]))
    assert "Latest customer message: Chrome" in prompt
    assert "Previous assistant message: What browser do you use?" in prompt
    assert "Pending customer response:" in prompt
    assert "Do not choose a ticket operation" in prompt


def test_router_node_writes_decision_fields():
    state = _state([HumanMessage(content="How do I export a report?")])
    decision = type("Decision", (), {
        "decision": "question", "explicit_ticket_request": False,
        "reason": "Product question needs knowledge search.",
    })()
    with patch("ole5_chat.agent.nodes.router.llm.structured", return_value=decision):
        result = router_node(state)
    assert result == {
        "router_decision": "question",
        "intent": "question",
        "next_target": "search",
        "route_reason": "Product question needs knowledge search.",
        "explicit_ticket_request": False,
    }


def test_router_updates_current_intent_when_selecting_next_target():
    state = _state([HumanMessage(content="How do I export a report?")])
    state["intent"] = "other"
    decision = type("Decision", (), {
        "decision": "question", "explicit_ticket_request": False,
        "reason": "Follow-up needs knowledge search.",
    })()
    with patch("ole5_chat.agent.nodes.router.llm.structured", return_value=decision):
        result = router_node(state)
    assert result["next_target"] == "search"
    assert result["intent"] == "question"


def test_router_model_failure_uses_safe_other_route():
    state = _state([HumanMessage(content="???")])
    with patch("ole5_chat.agent.nodes.router.llm.structured", side_effect=__import__("ole5_chat.llm", fromlist=["ModelFailed"]).ModelFailed("offline")):
        result = router_node(state)
    assert result["router_decision"] == "other"
    assert result["explicit_ticket_request"] is False


def test_validate_falls_back_when_answer_has_no_evidence():
    state = _state([HumanMessage(content="How do I export a report?")])
    state.update(reply="The report is exported from Reports.", search_outcome="answered", current_references=[])
    result = validate_node(state)
    assert result["validation_outcome"] == "fallback"
    assert "evidence" in result["validation_reason"]


def test_finalize_appends_reply_and_retains_answer_evidence():
    state = _state([HumanMessage(content="How do I export a report?")])
    state.update(
        reply="Open Reports.",
        current_references=[{"document": "guide.md", "text": "Open Reports.", "relation": "DIRECT"}],
        search_outcome="answered",
        router_decision="question",
    )
    result = finalize_node(state)
    assert result["messages"][0].content == "Open Reports."
    assert result["last_answer_evidence"]["evidence"][0]["document"] == "guide.md"
    assert result["current_references"] == []
    assert result["router_decision"] is None


def test_manager_registers_issue_and_opens_offer_pending_state():
    state = _state([HumanMessage(content="The archive button does not work")])
    state.update(router_decision="escalation", current_request="The archive button does not work")
    decision = type("Decision", (), {"action": "register_issue", "reason": "New issue."})()
    with patch("ole5_chat.agent.nodes.ticket_operator.llm.structured", return_value=decision):
        result = ticket_operator_node(state)
    assert result["active_issue"]["description"] == "The archive button does not work"
    assert result["pending_customer_response"].kind == "offer"
    assert result["manager_action"] == "register_issue"


def test_manager_unanswered_search_explains_missing_kb_answer_before_offer():
    state = _state([HumanMessage(content="Can the system translate correspondence automatically?")])
    state.update(router_decision="question", current_request="Can the system translate correspondence automatically?",
                 search_outcome="partial", current_references=[{"relation": "INFERENTIAL"}])
    decision = type("Decision", (), {"action": "register_issue", "reason": "No supported answer."})()
    with patch("ole5_chat.agent.nodes.ticket_operator.llm.structured", return_value=decision):
        result = ticket_operator_node(state)
    instruction = result["ticket_result"]["response_instruction"]
    assert "did not provide a supported answer" in instruction
    assert result["pending_customer_response"].kind == "offer"


def test_manager_decline_clears_active_offer():
    state = _state([AIMessage(content="Would you like a ticket?"), HumanMessage(content="No")])
    issue = {"id": "issue-1", "description": "Archive button fails", "source": "no_answer", "urgent": False}
    state.update(active_issue=issue, pending_customer_response=PendingCustomerResponse(kind="offer", issue_id="issue-1"))
    decision = type("Decision", (), {"action": "decline", "reason": "Customer declined."})()
    with patch("ole5_chat.agent.nodes.ticket_operator.llm.structured", return_value=decision):
        result = ticket_operator_node(state)
    assert result["active_issue"] is None
    assert result["pending_customer_response"] is None


def test_manager_submit_failure_preserves_summary():
    state = _state([AIMessage(content="Submit the summary?"), HumanMessage(content="Yes")])
    issue = {"id": "issue-1", "description": "Archive button fails", "source": "no_answer", "urgent": False}
    state.update(
        active_issue=issue,
        ticket_draft={"product": "Ole5", "required": {}, "missing": []},
        pending_customer_response=PendingCustomerResponse(kind="summary", issue_id="issue-1"),
    )
    decision = type("Decision", (), {"action": "submit", "reason": "Customer approved the summary."})()
    with patch("ole5_chat.agent.nodes.ticket_operator.llm.structured", return_value=decision):
        result = ticket_operator_node(state)
    assert result["ticket_result"]["submission_requested"] is True
    assert result.get("created_tickets") is None
    assert result["ticket_result"]["submission_succeeded"] is False
    assert result["pending_customer_response"].kind == "summary"


def test_manager_submit_success_records_ticket_and_clears_active_issue():
    state = _state([AIMessage(content="Submit the summary?"), HumanMessage(content="Yes")])
    issue = {"id": "issue-1", "description": "Archive button fails", "source": "no_answer", "urgent": False}
    state.update(
        active_issue=issue,
        ticket_draft={
            "product": "Ole5", "queue": "General Support", "type": "Bug", "subtype": "Other",
            "reason": "Archive button fails", "priority": "P3", "title": "Archive button fails",
            "summary": "The archive button fails.", "classified": True, "required": {}, "missing": [],
        },
        pending_customer_response=PendingCustomerResponse(kind="summary", issue_id="issue-1"),
    )
    decision = type("Decision", (), {"action": "submit", "reason": "Customer approved the summary."})()
    with patch("ole5_chat.agent.nodes.ticket_operator.llm.structured", return_value=decision), \
         patch("ole5_chat.agent.nodes.ticket_operator.tickets.submit", return_value=({"ticket_number": "T1", "ticket_id": "1", "created_at": "now"}, [])):
        result = ticket_operator_node(state)
    assert result["ticket_result"]["submission_succeeded"] is True
    assert result["created_tickets"][0]["ticket_number"] == "T1"
    assert result["active_issue"] is None


def test_ticket_status_lookup_returns_authorized_data():
    state = _state([HumanMessage(content="What is the status of my ticket?")])
    decision = type("Decision", (), {"action": "lookup_status", "reason": "Customer asks for current status."})()
    tickets = [{"ticket_number": "T1", "title": "Archive button fails", "created_at": "2026-11-01", "state": "open"}]
    with patch("ole5_chat.agent.nodes.ticket_enquiry.llm.structured", return_value=decision), \
         patch("ole5_chat.agent.nodes.ticket_enquiry.ticket_api_status.my_tickets", return_value=tickets):
        result = ticket_enquiry_node(state)
    assert result["ticket_enquiry_result"]["lookup_succeeded"] is True
    assert result["ticket_enquiry_result"]["tickets"] == tickets



