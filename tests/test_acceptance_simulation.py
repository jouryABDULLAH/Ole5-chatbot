"""Customer-facing workflow simulations through the HTTP API and LangGraph."""

from langchain_core.messages import AIMessage
from fastapi.testclient import TestClient
import re

from ole5_chat import llm, sessions, sheets
from ole5_chat.agent.contracts import Classification, route_schema
from ole5_chat.agent.respond import Answer, Grounding
from ole5_chat.agent.tasks import CHANNEL
from ole5_chat.api import app


class ConversationModel:
    """Script customer-visible model replies while leaving the app graph and HTTP API real."""

    def __init__(self):
        self.replies = []
        self.calls = []

    def __call__(self, temperature):
        self.tools = None
        self.tool_choice = None
        return self

    def bind_tools(self, tools, tool_choice=None):
        self.tools = tools
        self.tool_choice = tool_choice
        return self

    def invoke(self, messages):
        self.calls.append((self.tools if hasattr(self, "tools") else None, list(messages)))
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _route(model, session_id, next_):
    phase = sessions.get(session_id)["phase"]
    model.add(route_schema(phase)(next=next_))


def _tool_search(question):
    return AIMessage(content="", tool_calls=[{
        "name": "search_knowledge_base", "args": {"question": question}, "id": "search-1", "type": "tool_call"
    }])


def test_customer_simulation_greeting_answer_no_answer_and_ticket_submission(model, monkeypatch):
    client = TestClient(app)
    bot_model = ConversationModel()
    monkeypatch.setattr(llm, "tool_client", bot_model)

    started = client.post("/chat/sessions", json={"email": "ole5@example.com"})
    assert started.status_code == 200
    view = started.json()
    sid = view["session_id"]
    assert view["product"] == "OLE5" and view["language"] == "ar"
    assert len(view["messages"]) == 1
    assert model.calls == []  # Greeting is configured, not generated or searched.

    _route(model, sid, "talk")
    bot_model.replies.append(AIMessage(content="Hello, how can I help you today?"))
    greeting = client.post(f"/chat/sessions/{sid}/messages", json={"text": "hi"}).json()
    assert "how can i help" in greeting["messages"][-1]["text"].lower()
    assert len(bot_model.calls) == 1 and bot_model.calls[-1][0] is None

    _route(model, sid, "question")
    bot_model.replies.extend([_tool_search("how do I reset my password"), AIMessage(content="")])
    model.add(Answer(status="answered",
                     reply="Choose Forgot password on the sign-in page and enter your username.",
                     sources=["STUB-01 Signing in"]), Grounding(unsupported=[]))
    answered = client.post(f"/chat/sessions/{sid}/messages", json={"text": "How do I reset my password?"}).json()
    assert "Forgot password" in answered["messages"][-1]["text"]
    assert answered["messages"][-1]["sources"] == ["STUB-01 Signing in"]
    assert answered["phase"] == "none"

    _route(model, sid, "question")
    bot_model.replies.extend([_tool_search("can Ole5 connect to my calendar"), AIMessage(content=""),
                              AIMessage(content="I couldn't find an answer for that. Would you like a support ticket?")])
    unanswered = client.post(f"/chat/sessions/{sid}/messages", json={
        "text": "Can Ole5 connect to my calendar?"
    }).json()
    assert unanswered["phase"] == "offer_ticket"
    assert "ticket" in unanswered["messages"][-1]["text"].lower()

    _route(model, sid, "accept")
    model.add(Classification(
        queue="Ole5 Technical", type="Bug", subtype="Software malfunction",
        reason="Issue - calendar connection fails", priority="Medium", title="Calendar connection fails",
        summary="The customer cannot connect Ole5 to a calendar.", title_customer="Calendar connection fails",
        summary_customer="Ole5 cannot connect to a calendar.", confidence="medium",
        queue_reason="The issue concerns an integration", collected={"page": "Calendar"},
        required={"page": "Calendar", "error text or screenshot": None},
    ))
    bot_model.replies.append(AIMessage(content="To prepare the ticket, what error do you see?"))
    collecting = client.post(f"/chat/sessions/{sid}/messages", json={"text": "Yes, please."}).json()
    assert collecting["phase"] == "collect" and not collecting["draft_ready"]

    _route(model, sid, "details")
    model.add(Classification(
        queue="Ole5 Technical", type="Bug", subtype="Software malfunction",
        reason="Issue - calendar connection fails", priority="Medium", title="Calendar connection fails",
        summary="The customer cannot connect Ole5 to a calendar.", title_customer="Calendar connection fails",
        summary_customer="Ole5 cannot connect to a calendar.", confidence="medium",
        queue_reason="The issue concerns an integration", collected={"page": "Calendar", "error": "Access denied"},
        required={"page": "Calendar", "error text or screenshot": "Access denied"},
    ))
    bot_model.replies.append(AIMessage(content="The summary is ready. Shall I submit it?"))
    ready = client.post(f"/chat/sessions/{sid}/messages", json={"text": "Access denied"}).json()
    assert ready["phase"] == "confirm" and ready["draft_ready"]
    assert ready["ticket_draft"]["title"] == "Calendar connection fails"
    assert [call for call in bot_model.calls if call[0] is not None]  # Knowledge search ran on product questions.

    def announce_ticket(messages):
        task = next(message.content for message in reversed(messages)
                    if isinstance(message.content, str) and "The ticket T-MOCK-" in message.content)
        number = re.search(r"The ticket (T-MOCK-[^ ]+) was created", task).group(1)
        return AIMessage(content=f"Your ticket number is {number}.")

    bot_model.replies.append(announce_ticket)
    created = client.post(f"/chat/sessions/{sid}/submit")
    assert created.status_code == 200
    created_view = created.json()
    assert created_view["phase"] == "none"
    assert created_view["ticket_number"].startswith("T-MOCK-")
    assert created_view["ticket_number"] in created_view["messages"][-1]["text"]
    tickets = client.get("/chat/tickets", params={"session_id": sid}).json()
    assert any(ticket["ticket_number"] == created_view["ticket_number"] for ticket in tickets)


def test_customer_direct_ticket_and_decline_gets_support_contact_without_search(model, monkeypatch):
    client = TestClient(app)
    bot_model = ConversationModel()
    monkeypatch.setattr(llm, "tool_client", bot_model)
    sid = client.post("/chat/sessions", json={"email": "ole5@example.com"}).json()["session_id"]
    prior_tickets = {row["ticket_number"] for row in sheets.tickets()}

    _route(model, sid, "ticket")
    bot_model.replies.append(AIMessage(content="Would you like me to prepare a ticket for data migration?"))
    offer = client.post(f"/chat/sessions/{sid}/messages", json={
        "text": "We need to migrate data from the old system."
    }).json()
    assert offer["phase"] == "offer_ticket"
    assert not any(tools for tools, _ in bot_model.calls)

    _route(model, sid, "decline")
    bot_model.replies.append(AIMessage(content="Understood. Technical support is available through the ticket system."))
    declined = client.post(f"/chat/sessions/{sid}/messages", json={"text": "No, thank you."}).json()
    assert declined["phase"] == "none"
    assert "ticket system" in declined["messages"][-1]["text"].lower()
    assert "Technical support is through the ticket system only" in bot_model.calls[-1][1][0].content
    assert {row["ticket_number"] for row in sheets.tickets()} == prior_tickets

    _route(model, sid, "talk")
    bot_model.replies.append(AIMessage(content="You're welcome. I can still help with Ole5 questions."))
    continued = client.post(f"/chat/sessions/{sid}/messages", json={"text": "Thanks."}).json()
    assert continued["phase"] == "none"
    assert "welcome" in continued["messages"][-1]["text"].lower()


def test_user_specific_account_questions_are_never_searched_or_assumed(model, monkeypatch):
    from ole5_chat.agent.router import is_user_specific_request

    assert is_user_specific_request("What is the email address on my account?")
    assert is_user_specific_request("ما هي صلاحياتي؟")
    assert not is_user_specific_request("What is the status of my ticket?")

    client = TestClient(app)
    bot_model = ConversationModel()
    monkeypatch.setattr(llm, "tool_client", bot_model)
    session_id = client.post("/chat/sessions", json={"email": "rich@example.com"}).json()["session_id"]
    bot_model.replies.append(AIMessage(content="I can't access account details here. Would you like a support ticket?"))

    response = client.post(f"/chat/sessions/{session_id}/messages", json={
        "text": "What is the email address on my account?"
    })
    assert response.status_code == 200
    view = response.json()
    assert view["phase"] == "offer_ticket"
    assert "ticket" in view["messages"][-1]["text"].lower()
    assert "your email is" not in view["messages"][-1]["text"].lower()
    assert not any(tools for tools, _ in bot_model.calls)
    assert "Never answer or infer" in bot_model.calls[-1][1][0].content

    arabic_session = client.post("/chat/sessions", json={"email": "ole5@example.com"}).json()["session_id"]
    bot_model.replies.append(AIMessage(content="لا أستطيع الاطلاع على بيانات الحساب. هل ترغب في فتح تذكرة للدعم؟"))
    arabic = client.post(f"/chat/sessions/{arabic_session}/messages", json={"text": "ما هي صلاحياتي؟"})
    assert arabic.status_code == 200
    assert arabic.json()["phase"] == "offer_ticket"
    assert "تذكرة" in arabic.json()["messages"][-1]["text"]
    assert not any(tools for tools, _ in bot_model.calls)


def test_customer_can_decline_the_final_ticket_summary(model, monkeypatch):
    client = TestClient(app)
    bot_model = ConversationModel()
    monkeypatch.setattr(llm, "tool_client", bot_model)
    sid = client.post("/chat/sessions", json={"email": "ole5@example.com"}).json()["session_id"]

    _route(model, sid, "ticket")
    bot_model.replies.append(AIMessage(content="Would you like a support ticket for data migration?"))
    client.post(f"/chat/sessions/{sid}/messages", json={"text": "We need data migration."})

    _route(model, sid, "accept")
    model.add(Classification(
        queue="Ole5 Technical", type="Bug", subtype="Software malfunction",
        reason="Issue - migration request", priority="Medium", title="Data migration request",
        summary="The customer requests data migration.", title_customer="Data migration request",
        summary_customer="The customer requests data migration.", confidence="high",
        queue_reason="This is a technical data request", collected={"page": "Data migration"},
        required={"page": "Data migration", "error text or screenshot": "not applicable"},
    ))
    bot_model.replies.append(AIMessage(content="The summary is ready. Shall I submit it?"))
    ready = client.post(f"/chat/sessions/{sid}/messages", json={"text": "Yes"}).json()
    assert ready["phase"] == "confirm"

    _route(model, sid, "cancel")
    bot_model.replies.append(AIMessage(content="Understood. For technical support, use the ticket system."))
    declined = client.post(f"/chat/sessions/{sid}/messages", json={"text": "No, do not submit it."}).json()
    assert declined["phase"] == "none"
    assert "ticket system" in declined["messages"][-1]["text"].lower()
    assert "Technical support is through the ticket system only" in bot_model.calls[-1][1][0].content
    assert not declined["ticket_numbers"]
