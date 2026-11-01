from unittest.mock import patch

from langchain_core.messages import HumanMessage

from ole5_chat import api
from ole5_chat.agent_v2 import session
from ole5_chat.agent_v2.contracts import PendingCustomerResponse
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

    def invoke(values, config):
        captured.update(values)
        return {**session.get(session_id), "messages": [HumanMessage(content="Chrome")]}

    monkeypatch.setattr(session._graph, "invoke", invoke)
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

    def invoke(values, config):
        captured.update(values)
        return session.get(session_id)

    monkeypatch.setattr(session._graph, "invoke", invoke)
    session.submit(session_id)
    assert captured == {"input_event": "submit"}


def test_api_switch_and_v2_state_view(monkeypatch):
    monkeypatch.setenv("AGENT_GRAPH", "v2")
    from ole5_chat.config import get_settings

    get_settings.cache_clear()
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
