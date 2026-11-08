from unittest.mock import patch
import re

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import api
from ole5_chat.agent import session
from ole5_chat.agent.contracts import OperatorCompletion, PendingCustomerResponse, RouterDecision, RouterDecisionWithoutPending
from ole5_chat.agent.nodes.router import router_node
from ole5_chat.support_core.contracts import Classification
from ole5_chat.identity import Identity


def _identity():
    return Identity(
        customer_id="test@example.com",
        email="test@example.com",
        name="Test",
        organization="Test Org",
        product="OLE5",
        language="en",
    )


def test_start_persists_identity_and_configured_greeting():
    with patch.object(session, "lookup", return_value=_identity()), \
         patch.object(session.products, "load") as load:
        load.return_value.greet.return_value = "Hello from Ole5"
        session_id, state = session.start("test@example.com")

    assert state["identity"] == _identity()
    assert state["messages"][-1].content == "Hello from Ole5"
    assert session.get(session_id)["identity"] == _identity()


def test_send_sets_message_event_and_passes_customer_message(monkeypatch):
    identity = _identity()
    with patch.object(session, "lookup", return_value=identity), \
         patch.object(session.products, "load") as load:
        load.return_value.greet.return_value = "Hello"
        session_id, _ = session.start(identity.email)

    captured = {}

    def stream(values, config, stream_mode="updates"):
        captured.update(values)
        yield {"router": {}}

    monkeypatch.setattr(session._graph, "stream", stream)
    session.send(session_id, "Chrome")
    assert captured["input_event"] == "message"
    assert captured["messages"][0].content == "Chrome"


def test_submit_sets_submit_event_without_calling_router(monkeypatch):
    identity = _identity()
    with patch.object(session, "lookup", return_value=identity), \
         patch.object(session.products, "load") as load:
        load.return_value.greet.return_value = "Hello"
        session_id, _ = session.start(identity.email)

    captured = {}

    def stream(values, config, stream_mode="updates"):
        captured.update(values)
        yield {"ticket_operator": {}}

    monkeypatch.setattr(session._graph, "stream", stream)
    session.submit(session_id)
    assert captured == {"input_event": "submit"}


def test_api_switch_and_v2_state_view(monkeypatch):
    from ole5_chat.config import get_settings

    get_settings.cache_clear()


def _writer(messages):
    """Respond: names the ticket number when the turn note gives one."""
    match = re.search(r"Ticket created, number: (\S+)", messages[-1].content)
    return AIMessage(content=f"Your ticket number is {match.group(1)}." if match else "Here is your ticket summary.")


def test_v2_ticket_submission_returns_valid_customer_ticket_card(model, operator_model):
    from ole5_chat import llm
    from ole5_chat.config import get_settings

    get_settings.cache_clear()
    client = TestClient(api.app)

    start = client.post("/chat/sessions", json={"email": "ole5@example.com"})
    assert start.status_code == 200
    session_id = start.json()["session_id"]

    model.add(
        RouterDecisionWithoutPending(intent="needs_ticket", next_target="ticket_operator", explicit_ticket_request=True,
                                     reason="Customer requested a ticket."),
        Classification(
            queue="Ole5 Technical", type="Bug", subtype="Software malfunction",
            reason="Issue - calendar synchronization", priority="Medium", title="Calendar synchronization issue",
            summary="The customer reports a calendar synchronization issue.",
            title_customer="Calendar synchronization issue",
            summary_customer="The customer reports a calendar synchronization issue.",
            confidence="high", queue_reason="The issue concerns technical behavior",
            collected={"page": "Calendar"},
            required={"page": "Calendar", "error text or screenshot": "provided"},
        ),
        OperatorCompletion(action="show_summary", reason="Summary ready."),
        RouterDecision(intent="needs_ticket", next_target="respond", explicit_ticket_request=True,
                       reason="Ticket summary prepared."),
    )
    operator_model.call("manage_issues", operation="register", description="Calendar synchronization issue")
    operator_model.call("save_ticket_draft", fields={"summary": "Calendar synchronization issue"})
    with patch.object(llm, "invoke", side_effect=_writer):
        ready = client.post(f"/chat/sessions/{session_id}/messages", json={
            "text": "Please create a ticket for a calendar synchronization issue."
        })
    assert ready.status_code == 200
    assert ready.json()["phase"] == "confirm"

    prepared = session.get(session_id)
    operator_model.call("submit_ticket", issue_id=prepared["active_issue"]["id"],
                        draft_version=prepared["summary_delivered_version"])
    model.add(OperatorCompletion(action="submitted", reason="Created."),
              RouterDecisionWithoutPending(intent="needs_ticket", next_target="respond",
                                           explicit_ticket_request=True, reason="Ticket submitted."))
    with patch.object(llm, "invoke", side_effect=_writer):
        submitted = client.post(f"/chat/sessions/{session_id}/submit")
    assert submitted.status_code == 200, submitted.text
    view = submitted.json()
    assert view["phase"] == "none"
    assert view["ticket_number"] in view["messages"][-1]["text"]
    card = view["created_tickets"][-1]
    assert card["ticket_number"] == view["ticket_number"]
    assert card["title"] == "Calendar synchronization issue"
    assert card["message_index"] == len(view["messages"]) - 1
    assert "created_at" not in card and "issue_id" not in card

    listed = client.get("/chat/tickets", params={"session_id": session_id})
    assert listed.status_code == 200
    assert any(ticket["ticket_number"] == view["ticket_number"] for ticket in listed.json())


def test_v2_user_specific_data_bypasses_knowledge_router(model):
    state = {
        "identity": _identity(),
        "messages": [HumanMessage(content="What permissions does my account have?")],
        "pending_customer_response": None,
    }
    model.add(RouterDecisionWithoutPending(
        intent="needs_ticket", next_target="ticket_operator", explicit_ticket_request=False,
        reason="Customer-specific account or user data is outside the chat scope.",
    ))
    result = router_node(state)
    assert result["intent"] == "needs_ticket"
    assert "outside the chat scope" in result["route_reason"]
    assert model.calls


def test_v2_user_specific_question_goes_to_ticket_offer_without_search(model, operator_model):
    from ole5_chat import llm
    from ole5_chat.config import get_settings

    get_settings.cache_clear()
    client = TestClient(api.app)
    session_id = client.post("/chat/sessions", json={"email": "ole5@example.com"}).json()["session_id"]
    model.add(RouterDecisionWithoutPending(
        intent="needs_ticket", next_target="ticket_operator", explicit_ticket_request=False,
        reason="Customer-specific account or user data is outside the chat scope.",
    ), OperatorCompletion(action="offer", reason="Account data needs support."), RouterDecision(
        intent="needs_ticket", next_target="respond", explicit_ticket_request=False,
        reason="Ticket Operator prepared the authorized response.",
    ))
    operator_model.call("manage_issues", operation="register", description="Account permissions question")

    with patch.object(llm, "invoke", return_value=AIMessage(
            content="I cannot access account details here. Would you like a support ticket?")):
        response = client.post(f"/chat/sessions/{session_id}/messages", json={
            "text": "What permissions does my account have?"
        })
    assert response.status_code == 200
    view = response.json()
    assert view["phase"] == "offer_ticket"
    assert "ticket" in view["messages"][-1]["text"].lower()
    # Router classifies both the incoming turn and the completed Ticket Operator result; Search never runs.
    from ole5_chat.agent.contracts import Language, TicketActionCheck
    assert all(schema in (RouterDecision, RouterDecisionWithoutPending, OperatorCompletion, Language, TicketActionCheck)
               for schema, _ in model.calls)
    from ole5_chat.agent.tools.ticket_operator import build_ticket_operator_tools
    operator_tools = {tool.name for tool in build_ticket_operator_tools({})}
    assert operator_model.bound and set(operator_model.bound) <= operator_tools
    assert api._session_backend() is session

    state = {
        "identity": _identity(),
        "messages": [],
        "pending_customer_response": PendingCustomerResponse(kind="offer", issue_id="issue-1"),
        "ticket_draft": None,
        "created_tickets": [],
        "attachments": [],
    }
    view = api._view("v2-session", state)
    assert view.phase == "offer_ticket"
    get_settings.cache_clear()





