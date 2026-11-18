"""Exercise queue handoffs through Ticket Operator, Respond, Validate and Finalize."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.support_core.state import TicketDraft
from ole5_chat.agent.graph import build
from ole5_chat.agent.contracts import (OperatorCompletion, PendingCustomerResponse, ReturnDecision, RouterDecision,
                                      RouterDecisionWithoutPending)
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity


@pytest.mark.parametrize("kind,action", [("offer", "decline"), ("detail", "cancel"),
                                       ("summary", "cancel"), ("summary", "submit")])
@pytest.mark.parametrize("next_kind", ["ordinary", "urgent", "empty"])
def test_queue_moves_on_after_operator(model, operator_model, kind, action, next_kind):
    state = initial_state(Identity(customer_id="test", email="ole5@example.com", name="Test",
                                  organization="Test", product="OLE5", language="en"))
    next_issue = {"id": "issue-2", "description": "Next issue: everyone cannot sign in", "urgent": next_kind == "urgent",
                  "source": "no_answer"}
    tail = {"id": "issue-3", "description": "Later issue", "urgent": False}
    state.update(
        input_event="submit" if action == "submit" else "message",
        language="en", messages=[AIMessage(content="Previous question"), HumanMessage(content=action)],
        active_issue={"id": "issue-1", "description": "Old archive issue"},
        pending_issues=[] if next_kind == "empty" else [next_issue, tail],
        pending_customer_response=PendingCustomerResponse(kind=kind, issue_id="issue-1", item="browser"),
        ticket_draft=TicketDraft(product="OLE5", title="Old archive issue", classified=True).model_dump(),
        summary_shown=kind == "summary", submission_requested=kind == "summary",
        draft_version=1, summary_delivered_version=1 if kind == "summary" else None,
    )
    if action == "submit":
        operator_model.call("submit_ticket", issue_id="issue-1", draft_version=1)
        model.add(OperatorCompletion(action="submitted", reason="Created."))
    else:
        model.add(RouterDecision(intent="pending_response", next_target="ticket_operator",
                                 explicit_ticket_request=False, reason=action))
        operator_model.call("manage_issues", operation="cancel_preparation", issue_id="issue-1")
        stopped = "declined" if action == "decline" else "cancelled"
        model.add(OperatorCompletion(action=stopped if next_kind == "empty" else "offer", reason=action,
                                     issue_id=None if next_kind == "empty" else "issue-2"))
    model.add(ReturnDecision(next_target="respond", reason="Ticket Operator completed this turn."))
    notes = []

    def writer(messages, temperature=0):
        notes.append(messages[-1].content)
        return AIMessage(content="The previous step is done.")

    with patch("ole5_chat.support_core.classify.classify") as classify,          patch("ole5_chat.tickets.submit.submit", return_value=({"ticket_number": "T-1", "ticket_id": "1"}, [])) as submit,          patch("ole5_chat.tickets.client.ticket_client", return_value=object()),          patch.object(llm, "invoke", side_effect=writer):
        result = build().invoke(state)
    note = notes[0]
    assert submit.call_count == (1 if action == "submit" else 0)
    # The first pass classifies (none on a Submit press); the return pass only routes the Operator's result.
    assert sum(schema in (RouterDecision, RouterDecisionWithoutPending)
               for schema, _ in model.calls) == (0 if action == "submit" else 1)
    assert sum(schema is ReturnDecision for schema, _ in model.calls) == 1
    assert len(result["created_tickets"]) == (1 if action == "submit" else 0)
    # Urgency does not bypass consent for the next queued request. Preparation starts only after acceptance.
    assert classify.call_count == 0
    if action == "submit":
        assert "Stage: submitted" in note
        assert "Ticket created, number: T-1" in note
    if next_kind == "empty":
        assert result["active_issue"] is None
        assert result["pending_customer_response"] is None
        assert "Next request" not in note and "Offered now" not in note
        assert f"Stage: {'submitted' if action == 'submit' else 'preparation_stopped'}" in note
    else:
        assert result["active_issue"] == next_issue
        assert result["pending_issues"] == [tail]
        assert result["pending_customer_response"].issue_id == "issue-2"
        assert result["pending_customer_response"].kind == "offer"
        assert result["summary_shown"] is False
        if action == "submit":
            assert f"Next request, now offered: «{next_issue['description']}»" in note
        else:
            assert "Stage: offer" in note
            assert "Stopped this turn: «Old archive issue»." in note
            assert f"Offered now: «{next_issue['description']}»." in note
    assert result["messages"][-1].content == "The previous step is done."
