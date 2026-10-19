"""The agent graph with a scripted model and the stand-in knowledge base: greeting, answers, ticket offers, never-answered topics and value checks."""

import pytest

from ole5_chat import sessions
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision
from ole5_chat.agent.validate import NO_ANSWER, check_ticket
from ole5_chat.values import catalog


@pytest.fixture
def searches(monkeypatch):
    seen = []
    real = graph.kb_search

    def spy(question, product):
        seen.append(question)
        return real(question, product)

    monkeypatch.setattr(graph, "kb_search", spy)
    return seen


def chat(email="ole5@example.com"):
    sid, _ = sessions.start(email)
    return sid


def test_hi_gets_a_greeting_from_respond_and_no_search(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="greeting", language="ar"), Reply(say="أهلاً بك، كيف أساعدك؟", step="greet"))
    state = sessions.send(sid, "مرحبا")
    assert state["phase"] == "greet"
    assert state["messages"][-1].text == "أهلاً بك، كيف أساعدك؟"
    assert searches == []
    assert [c[0] for c in model.calls] == [RouterDecision, Reply]


def test_first_message_is_the_fixed_greeting_without_a_model_call(model):
    sid = chat()
    state = sessions.get(sid)
    assert state["messages"][0].text == "أهلاً، كيف يمكنني مساعدتك في Ole5؟"
    assert model.calls == []


def test_ticket_offer_written_by_respond_is_kept(model):
    sid = chat()
    offer = "ليس لدي إجابة على هذا السؤال. هل تريد أن أفتح لك تذكرة؟"
    model.add(RouterDecision(intent="question", queries=["كيف أغير الباشورد"], language="ar"),
              Reply(say=offer, step="offer_ticket"))
    state = sessions.send(sid, "كيف اعيد صياغة الباشورد في حسابي")
    assert state["messages"][-1].text == offer


def test_covered_question_is_answered_with_sources(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]),
              Reply(say="Choose Forgot password on the sign-in page.", step="answer", sources=["STUB-01 Signing in"]))
    state = sessions.send(sid, "I forgot my password")
    assert searches == ["How do I reset my password?"]
    assert state["phase"] == "answer"
    assert state["sources"] == ["STUB-01 Signing in"]
    assert "STUB-01 Signing in" in model.calls[-1][1]


def test_uncovered_answer_is_sent_back_and_respond_rewrites_it(model):
    sid = chat()
    offer = "I don't have an answer for this. Would you like me to open a ticket?"
    model.add(RouterDecision(intent="question", language="en", queries=["How do I export a report?"]),
              Reply(say="Go to Reports and press Export.", step="answer", sources=["Made up"]),
              Reply(say=offer, step="offer_ticket"))
    state = sessions.send(sid, "how do I export a report?")
    assert state["phase"] == "offer_ticket"
    assert state["messages"][-1].text == offer
    assert state["sources"] == []
    assert [c[0] for c in model.calls] == [RouterDecision, Reply, Reply]
    assert any("sent back to respond" in c for c in state["corrections"])


def test_retry_messages_are_never_saved(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["export"]),
              Reply(say="Press Export.", step="answer"),
              Reply(say="I don't have an answer for this. Would you like me to open a ticket?", step="offer_ticket"))
    state = sessions.send(sid, "how do I export?")
    texts = [m.text for m in state["messages"]]
    assert texts[1:] == ["how do I export?", "I don't have an answer for this. Would you like me to open a ticket?"]
    assert "Press Export." not in texts
    assert not any("Note from the system" in t for t in texts)


def test_second_broken_reply_falls_back_to_the_standard_text(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="ar", queries=["export"]),
              Reply(say="Press Export.", step="answer"), Reply(say="Really, press Export.", step="answer"))
    state = sessions.send(sid, "كيف أصدر تقريراً؟")
    assert state["phase"] == "offer_ticket"
    assert state["messages"][-1].text == NO_ANSWER["ar"]
    assert any("still broken after the retry" in c for c in state["corrections"])


def test_at_most_four_searches(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=[f"q{i}" for i in range(6)]),
              Reply(say="I don't have an answer for this. Would you like me to open a ticket?", step="offer_ticket"))
    sessions.send(sid, "six questions")
    assert searches == ["q0", "q1", "q2", "q3"]


def test_router_language_is_required():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        RouterDecision(intent="other")


def test_inferential_only_is_not_an_answer(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I use the archive?"]),
              Reply(say="Open the Archive screen.", step="answer", sources=["STUB-02 Archive"]),
              Reply(say="I don't have an answer for this. Would you like me to open a ticket?", step="offer_ticket"))
    assert sessions.send(sid, "archive?")["phase"] == "offer_ticket"


def test_always_ticket_is_never_searched_or_answered(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["password migration"], always_ticket=True),
              Reply(say="Here is how.", step="answer"),
              Reply(say="I don't have an answer for this. Would you like me to open a ticket?", step="offer_ticket"))
    state = sessions.send(sid, "migrate our data")
    assert searches == []
    assert state["phase"] == "offer_ticket"


def test_ticket_flow_refuses_a_value_then_accepts_the_retry(model):
    sid = chat()
    model.add(RouterDecision(intent="ticket_reply", language="en"),
              Reply(say="Which screen?", step="collect", collected={"problem": "app crashes"}, missing=["screen"]))
    state = sessions.send(sid, "yes open a ticket")
    assert state["phase"] == "collect"
    assert state["ticket_draft"].product == "Ole5"

    model.add(RouterDecision(intent="ticket_reply", language="en"),
              Reply(say="Your ticket summary is ready. Press Submit to send it.", step="confirm",
                    collected={"screen": "Inbox"}),
              Classification(queue="Ole5 Helpdesk", type="Mobile App Issue", subtype="Application crash",
                             reason="Issue - app crashes on Inbox", priority="Medium", title="App crash",
                             summary="The app crashes on the Inbox screen.", confidence="high", queue_reason="tech"),
              Classification(queue="Ole5 Technical", type="Mobile App Issue", subtype="Application crash",
                             reason="Issue - app crashes on Inbox", priority="Medium", title="App crash",
                             summary="The app crashes on the Inbox screen.", confidence="high", queue_reason="tech"))
    state = sessions.send(sid, "the Inbox screen")
    draft = state["ticket_draft"]
    assert state["phase"] == "confirm"
    assert (draft.queue, draft.type, draft.subtype) == ("Ole5 Technical", "Mobile App Issue", "Application crash")
    assert draft.collected == {"problem": "app crashes", "screen": "Inbox"}
    assert draft.missing == []


def test_value_still_wrong_after_retry_is_left_empty(model):
    sid = chat()
    bad = Classification(queue="Nope", type="Bug", subtype="Software malfunction", reason="Bug - x",
                         priority="Medium", title="t", summary="s", confidence="low", queue_reason="r")
    model.add(RouterDecision(intent="ticket_reply", language="en"), Reply(say="Ready.", step="confirm"), bad, bad)
    state = sessions.send(sid, "yes")
    assert state["ticket_draft"].queue is None
    assert any("queue: 'Nope'" in c for c in state["corrections"])


def test_subtype_must_sit_under_its_type():
    c = Classification(queue="Ole5 Support", type="Bug", subtype="Data migration", reason="r", priority="Low")
    fields, problems = check_ticket(c, catalog("OLE5"))
    assert fields["subtype"] is None
    assert problems == ["subtype: 'Data migration' is not on the list"]


def test_rich_reason_comes_from_the_catalog_not_the_model():
    c = Classification(queue="RiCH Technical", type="Inquiry", subtype="dlr - not delivered- stc",
                       reason="anything", priority="high")
    fields, problems = check_ticket(c, catalog("RICH"))
    assert problems == []
    assert fields["subtype"] == "DLR - Not Delivered- STC"
    assert fields["priority"] == "High"
    assert fields["reason"] == "Use when a customer reports that messages are not being delivered"


def test_model_failure_gives_a_safe_reply(model):
    sid = chat()
    state = sessions.send(sid, "hello?")
    assert state["phase"] == "ask"
    assert state["messages"][-1].text.startswith("عذراً")
    assert any("router failed" in c for c in state["corrections"])


def test_products_differ_in_the_router_prompt(model):
    model.add(RouterDecision(intent="other", language="en"), Reply(say="ok", step="ask"))
    sessions.send(chat("rich@example.com"), "x")
    model.add(RouterDecision(intent="other", language="en"), Reply(say="ok", step="ask"))
    sessions.send(chat("ole5@example.com"), "x")
    rich_prompt, ole5_prompt = model.calls[0][1], model.calls[2][1]
    assert "whitelisting an IP or a user" in rich_prompt and "data migration requests" in ole5_prompt
