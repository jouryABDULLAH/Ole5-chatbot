"""Exercise queue handoffs through Manager, Respond, Validate and Finalize."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.support_core.state import TicketDraft
from ole5_chat.agent.graph import build
from ole5_chat.agent.contracts import PendingCustomerResponse, RouterDecision, RouterDecisionWithoutPending, manager_schema
from ole5_chat.agent.nodes import ticket_operator as manager
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity


@pytest.mark.parametrize("kind,action", [("offer", "decline"), ("detail", "cancel"),
                                       ("summary", "cancel"), ("summary", "submit")])
@pytest.mark.parametrize("next_kind", ["ordinary", "urgent", "failed_urgent", "empty"])
def test_queue_handoff(model, kind, action, next_kind):
    state = initial_state(Identity(customer_id="test", email="ole5@example.com", name="Test",
                                  organization="Test", product="OLE5", language="en"))
    next_issue = {"id": "issue-2", "description": "Next issue: everyone cannot sign in",
                  "urgent": next_kind in {"urgent", "failed_urgent"}, "source": "urgent"}
    tail = {"id": "issue-3", "description": "Later issue", "urgent": False}
    state.update(
        input_event="submit" if action == "submit" else "message",
        language="en", messages=[AIMessage(content="Previous question"), HumanMessage(content=action)],
        active_issue={"id": "issue-1", "description": "Old archive issue"},
        pending_issues=[] if next_kind == "empty" else [next_issue, tail],
        pending_customer_response=PendingCustomerResponse(kind=kind, issue_id="issue-1", item="browser"),
        ticket_draft=TicketDraft(product="OLE5", title="Old archive issue", classified=True).model_dump(),
        summary_shown=kind == "summary", submission_requested=kind == "summary",
    )
    if action != "submit":
        model.add(RouterDecision(intent="pending_response", next_target="ticket_operator", explicit_ticket_request=False, reason=action),
                  manager_schema(kind)(action=action, reason=action))
    return_schema = RouterDecisionWithoutPending if next_kind == "empty" else RouterDecision
    model.add(return_schema(intent="escalation" if next_kind == "empty" else "pending_response",
                            next_target="respond", explicit_ticket_request=False,
                            reason="Ticket Operator completed this turn."))
    draft = TicketDraft(product="OLE5", title=next_issue["description"], classified=True,
                        required={"browser": None})
    captured = []

    def writer(messages):
        prompt = messages[0].content
        captured.append(prompt)
        if next_kind == "empty":
            return AIMessage(content="The previous action is complete.")
        assert next_issue["description"] in prompt
        assert "Then, in a separate short paragraph" in prompt
        assert "Ask only the next issue's question" in prompt
        if next_kind == "urgent":
            assert "summary is ready because it is urgent" in prompt
            text = "The urgent sign-in issue summary is ready. Shall I submit it?"
        elif next_kind == "failed_urgent":
            assert "summary could not be prepared" in prompt
            text = "The sign-in issue summary could not be prepared. Shall I retry?"
        else:
            assert "Ask whether the customer wants a support ticket prepared for that issue" in prompt
            text = "Would you like a support ticket for the sign-in issue?"
        return AIMessage(content="The previous action is complete.\n\n" + text)

    with patch.object(manager, "_classify", return_value=None if next_kind == "failed_urgent" else draft) as classify, \
         patch.object(manager.tickets, "submit", return_value=({"ticket_number": "T-1", "ticket_id": "1"}, [])) as submit, \
         patch.object(llm, "invoke", side_effect=writer):
        result = build().invoke(state)
    assert submit.call_count == (1 if action == "submit" else 0)
    assert sum(schema in (RouterDecision, RouterDecisionWithoutPending)
               for schema, _ in model.calls) == (1 if action == "submit" else 2)
    assert len(result["created_tickets"]) == (1 if action == "submit" else 0)
    if action == "submit":
        assert "Confirm that ticket T-1 was created for Old archive issue" in captured[0]
    if next_kind == "empty":
        assert result["active_issue"] is None
        assert result["pending_customer_response"] is None
        assert "Then, in a separate short paragraph" not in captured[0]
    else:
        assert result["active_issue"] == next_issue
        assert result["pending_issues"] == [tail]
        assert result["pending_customer_response"].issue_id == "issue-2"
        assert result["pending_customer_response"].kind == ("summary" if next_kind == "urgent" else "offer")
        assert result["summary_shown"] == (next_kind == "urgent")
        assert result["submission_requested"] == (next_kind == "urgent")
        assert "sign-in issue" in result["messages"][-1].content
    assert classify.call_count == (1 if next_kind in {"urgent", "failed_urgent"} else 0)
    if classify.called:
        classifier_state, fresh_draft = classify.call_args.args
        assert classifier_state["active_issue"]["id"] == "issue-2"
        assert fresh_draft.title is None  # never reuse the previous ticket's draft
    if next_kind == "urgent":
        assert result["ticket_draft"]["urgent"] is True




