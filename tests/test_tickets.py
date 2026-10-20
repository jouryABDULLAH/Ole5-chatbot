"""Submit against the mock Ticket API: the payloads as the requirements show them, every refusal, one ticket per session, a failed TicketUpdate, the tickets.xlsx row and GET /chat/tickets."""

import pytest
from fastapi.testclient import TestClient

from ole5_chat import sessions, sheets
from ole5_chat.agent.contracts import RouterDecision
from ole5_chat.agent.state import TicketDraft
from ole5_chat.api import app
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets.client import TicketApiError

DRAFT = dict(product="Ole5", queue="Ole5 Technical", type="Mobile App Issue", subtype="Application crash",
             reason="Issue - the app crashes on the Inbox screen", priority="Medium", title="App crashes on Inbox",
             summary="The mobile app crashes when the customer opens the Inbox screen.",
             collected={"screen": "Inbox"}, missing=[], classified=True, confidence="high",
             queue_reason="An app defect")


def confirmed(email="ole5@example.com", **changes):
    sid, _ = sessions.start(email)
    sessions._graph.update_state(sessions._config(sid), {
        "phase": "confirm", "ticket_draft": TicketDraft(**{**DRAFT, **changes}),
        "decision": RouterDecision(intent="ticket_reply", language="en"),
    }, as_node="validate")
    return sid


@pytest.fixture
def api():
    return ticket_api.ticket_client()


def test_submit_creates_the_ticket_as_the_spec_shows(api):
    client = TestClient(app)
    sid = confirmed()
    view = client.post(f"/chat/sessions/{sid}/submit").json()
    assert view["phase"] == "created"
    number = view["ticket_number"]
    assert number.startswith("T-MOCK-")
    assert view["messages"][-1] == {"author": "bot", "text": f"Your ticket {number} has been created. "
                                                             "The support team will reply to you by email."}

    (op1, create), (op2, update) = api.calls
    assert (op1, op2) == ("TicketCreate", "TicketUpdate")
    assert create["Ticket"] == {"Title": "App crashes on Inbox", "Service": "Ole5", "Queue": "Ole5 Technical",
                                "State": "new", "Priority": "Medium", "Type": "Mobile App Issue",
                                "CustomerUser": "ole5@example.com"}
    assert create["Article"]["Body"].startswith(DRAFT["summary"] + "\n\nAssistant: ")
    assert create["Article"]["SenderType"] == "customer" and create["Article"]["IsVisibleForCustomer"] == 1
    assert update["DynamicField"] == [{"Name": "SubType", "Value": "Application crash"},
                                      {"Name": "Conclusions", "Value": DRAFT["reason"]}]
    assert update["Article"]["IsVisibleForCustomer"] == 0
    assert update["Article"]["Body"].splitlines()[:6] == [
        "Collected: screen: Inbox", "Missing: nothing", "Customer does not know: nothing", "Confidence: high",
        "Why this queue: An app defect", "Urgent: no"]
    assert view["ticket_draft"] is None and not view["draft_ready"] and view["ticket_numbers"] == [number]

    row = sheets.tickets()[-1]
    assert (row["ticket_number"], row["customer_email"], row["product"]) == (number, "ole5@example.com", "OLE5")


def test_second_submit_is_refused(api):
    client = TestClient(app)
    sid = confirmed()
    client.post(f"/chat/sessions/{sid}/submit")
    again = client.post(f"/chat/sessions/{sid}/submit")
    assert again.status_code == 409
    assert again.json()["detail"] == "there is no ticket summary to submit"
    assert [c[0] for c in api.calls].count("TicketCreate") == 1


def test_a_second_ticket_in_the_same_session(api):
    client = TestClient(app)
    sid = confirmed()
    first = client.post(f"/chat/sessions/{sid}/submit").json()["ticket_number"]
    sessions._graph.update_state(sessions._config(sid), {"ticket_draft": TicketDraft(**DRAFT)}, as_node="validate")
    view = client.post(f"/chat/sessions/{sid}/submit").json()
    assert view["ticket_numbers"] == [first, view["ticket_number"]] and view["ticket_number"] != first


def test_unclassified_draft_is_refused(api):
    response = TestClient(app).post(f"/chat/sessions/{confirmed(classified=False)}/submit")
    assert response.status_code == 409
    assert api.calls == []


def test_submit_without_a_draft_is_refused(api):
    sid, _ = sessions.start("ole5@example.com")
    response = TestClient(app).post(f"/chat/sessions/{sid}/submit")
    assert response.status_code == 409
    assert api.calls == []


def test_missing_field_is_refused(api):
    response = TestClient(app).post(f"/chat/sessions/{confirmed(queue=None)}/submit")
    assert response.status_code == 409
    assert response.json()["detail"] == "the ticket is missing: queue"
    assert api.calls == []


def test_value_not_on_the_lists_is_refused_before_ticket_create(api):
    response = TestClient(app).post(f"/chat/sessions/{confirmed(subtype='Data migration')}/submit")
    assert response.status_code == 409
    assert "subtype" in response.json()["detail"]
    assert api.calls == []


def test_failed_update_still_gives_the_number(api, monkeypatch):
    def broken(*args, **kwargs):
        raise TicketApiError("TicketUpdate: HTTP 500")

    monkeypatch.setattr(api, "update", broken)
    sid = confirmed()
    view = TestClient(app).post(f"/chat/sessions/{sid}/submit").json()
    assert view["phase"] == "created" and view["ticket_number"]
    assert any("TicketUpdate failed" in c for c in sessions.get(sid)["corrections"])


def test_my_tickets_lists_only_this_customer(api):
    client = TestClient(app)
    mine = confirmed()
    client.post(f"/chat/sessions/{mine}/submit")
    client.post(f"/chat/sessions/{confirmed('rich@example.com', product='RiCH', queue='RiCH Technical', type='Inquiry', subtype='DLR - Not Delivered- STC', reason='x')}/submit")
    tickets = client.get("/chat/tickets", params={"session_id": mine}).json()
    assert len(tickets) == 1
    assert tickets[0]["state"] == "new" and tickets[0]["product"] == "OLE5"
