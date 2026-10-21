"""The agent graph with a scripted model and the stand-in knowledge base: the router picks the step in code from what its model call reports, for greetings, answers, ticket offers, the ticket flow, detours, cancels and failures."""

import logging

import pytest

from ole5_chat import sessions
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision
from ole5_chat.agent.validate import Language, check_ticket
from ole5_chat.knowledge.contracts import Retrieval, Verdict
from ole5_chat.values import catalog

GOOD = Classification(queue="Ole5 Technical", type="Mobile App Issue", subtype="Application crash",
                      reason="Issue - app crashes on Inbox", priority="Medium", title="App crash",
                      summary="The app crashes on the Inbox screen.", confidence="high", queue_reason="tech")


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


def ticket_reply(**fields):
    return RouterDecision(intent="ticket_reply", language="en", **fields)


def respond_prompt(model):
    return [system for schema, system in model.calls if schema is Reply][-1]


def collecting(model, sid):
    model.add(ticket_reply(collected={"problem": "app crashes"}, missing=["screen"]), Reply(say="Which screen?"))
    return sessions.send(sid, "yes open a ticket")


def confirming(model, sid):
    collecting(model, sid)
    model.add(ticket_reply(collected={"screen": "Inbox"}), GOOD, Reply(say="Your summary is ready."))
    return sessions.send(sid, "the Inbox screen")


def test_first_message_is_the_fixed_greeting_without_a_model_call(model):
    state = sessions.get(chat())
    assert state["messages"][0].text.startswith("أهلاً بك، أنا مساعد دعم Ole5")
    assert model.calls == []


def test_hi_is_greeted_without_a_search(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="greeting", language="ar"), Reply(say="أهلاً بك، كيف أساعدك؟"))
    state = sessions.send(sid, "مرحبا")
    assert state["phase"] == "greet"
    assert state["messages"][-1].text == "أهلاً بك، كيف أساعدك؟"
    assert searches == []
    assert [c[0] for c in model.calls if c[0] is not Language] == [RouterDecision, Reply]
    assert "Step: greet" in respond_prompt(model)


def test_covered_question_is_answered_with_sources(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]),
              Reply(say="Choose Forgot password on the sign-in page.", sources=["STUB-01 Signing in", "Made up"]))
    state = sessions.send(sid, "hi, I forgot my password")
    assert searches == ["How do I reset my password?"]
    assert state["phase"] == "answer"
    assert state["sources"] == ["STUB-01 Signing in"]
    assert "Step: answer" in respond_prompt(model) and "STUB-01 Signing in" in respond_prompt(model)
    assert state["last_retrieval"] is None and state["step"] is None


def test_uncovered_question_gets_a_ticket_offer_chosen_in_code(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I export a report?"]),
              Reply(say="I don't have an answer for this. Would you like a ticket?", sources=["Made up"]))
    state = sessions.send(sid, "how do I export a report?")
    assert state["phase"] == "offer_ticket"
    assert state["sources"] == []
    assert "Step: offer_ticket" in respond_prompt(model)


def test_inferential_only_is_not_an_answer(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I use the archive?"]),
              Reply(say="I don't have an answer for this."))
    assert sessions.send(sid, "archive?")["phase"] == "offer_ticket"


def test_failed_search_reads_as_no_answer_and_is_logged(model, monkeypatch, caplog):
    sid = chat()
    monkeypatch.setattr(graph, "kb_search", lambda q, p: Retrieval(query=q, verdict=Verdict.NOT_IN_KB,
                                                                   error="retrieval incomplete: timeout"))
    model.add(RouterDecision(intent="question", language="en", queries=["q"]), Reply(say="No answer."))
    with caplog.at_level(logging.WARNING):
        state = sessions.send(sid, "q?")
    assert state["phase"] == "offer_ticket"
    assert any("search failed" in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_at_most_four_searches(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=[f"q{i}" for i in range(6)]),
              Reply(say="No answer."))
    sessions.send(sid, "six questions")
    assert searches == ["q0", "q1", "q2", "q3"]


def test_router_language_is_required():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        RouterDecision(intent="other")


def test_always_ticket_is_never_searched(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["password migration"], always_ticket=True),
              Reply(say="No answer."))
    state = sessions.send(sid, "migrate our data")
    assert searches == []
    assert state["phase"] == "offer_ticket"


def test_always_ticket_is_offered_before_a_draft_even_when_read_as_a_ticket_reply(model):
    sid = chat()
    model.add(ticket_reply(always_ticket=True), Reply(say="Shall I open a ticket?"))
    state = sessions.send(sid, "I need help migrating our old data")
    assert state["phase"] == "offer_ticket" and state["ticket_draft"] is None
    model.add(ticket_reply(always_ticket=True, collected={"request": "data migration"}, missing=["volume"]),
              Reply(say="How much data?"))
    assert sessions.send(sid, "yes")["phase"] == "collect"


def test_urgent_question_offers_a_ticket_without_searching(model, searches):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["password"], urgent=True),
              Reply(say="Shall I open an urgent ticket?"))
    assert sessions.send(sid, "nobody can sign in, password reset fails")["phase"] == "offer_ticket"
    assert searches == []


def test_vague_message_is_asked_to_say_more(model):
    sid = chat()
    model.add(RouterDecision(intent="other", language="ar"), Reply(say="تفضل، ما سؤالك؟"))
    assert sessions.send(sid, "عندي سؤال")["phase"] == "ask"


def test_ticket_flow_collects_then_confirms_after_a_refused_value(model):
    sid = chat()
    state = collecting(model, sid)
    assert state["phase"] == "collect"
    assert state["ticket_draft"].product == "Ole5" and state["ticket_draft"].missing == ["screen"]

    model.add(ticket_reply(collected={"screen": "Inbox"}),
              GOOD.model_copy(update={"queue": "Ole5 Helpdesk"}), GOOD, Reply(say="Your summary is ready."))
    state = sessions.send(sid, "the Inbox screen")
    draft = state["ticket_draft"]
    assert state["phase"] == "confirm"
    assert (draft.queue, draft.type, draft.subtype) == ("Ole5 Technical", "Mobile App Issue", "Application crash")
    assert draft.collected == {"problem": "app crashes", "screen": "Inbox"}
    assert draft.missing == [] and draft.classified


def test_details_already_given_go_straight_to_confirm(model):
    sid = chat()
    model.add(ticket_reply(collected={"problem": "app crashes", "screen": "Inbox"}), GOOD, Reply(say="Ready."))
    assert sessions.send(sid, "yes")["phase"] == "confirm"


def test_detail_the_customer_does_not_know_is_not_asked_again(model):
    sid = chat()
    collecting(model, sid)
    model.add(ticket_reply(missing=["screen"], unknown=["screen"]), GOOD, Reply(say="Ready."))
    state = sessions.send(sid, "I don't know which screen")
    assert state["phase"] == "confirm"
    assert state["ticket_draft"].missing == [] and state["ticket_draft"].unknown == ["screen"]


def test_cancel_drops_the_ticket(model):
    sid = chat()
    confirming(model, sid)
    model.add(RouterDecision(intent="other", language="en", cancel_ticket=True), Reply(say="Okay."))
    state = sessions.send(sid, "cancel it")
    assert state["phase"] == "ask" and state["ticket_draft"] is None


def test_answered_question_while_collecting_keeps_the_ticket(model):
    sid = chat()
    collecting(model, sid)
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]),
              Reply(say="Choose Forgot password. Which screen crashes?", sources=["STUB-01 Signing in"]))
    state = sessions.send(sid, "by the way, how do I reset my password?")
    assert state["phase"] == "answer"
    assert state["ticket_draft"].missing == ["screen"]
    assert 'Still missing: ["screen"]' in respond_prompt(model)


def test_unanswered_question_while_collecting_is_deferred(model):
    sid = chat()
    before = collecting(model, sid)["ticket_draft"]
    model.add(RouterDecision(intent="question", language="en", queries=["export"]),
              Reply(say="I don't have an answer for that. Which screen?"))
    state = sessions.send(sid, "how do I export?")
    assert state["phase"] == "collect" and state["ticket_draft"] == before


def test_question_at_confirm_keeps_the_summary_ready(model):
    sid = chat()
    confirming(model, sid)
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]),
              Reply(say="Choose Forgot password.", sources=["STUB-01 Signing in"]))
    state = sessions.send(sid, "how do I reset my password?")
    assert state["phase"] == "answer" and state["ticket_draft"].classified
    assert "Summary ready: yes" in respond_prompt(model)


def test_value_still_wrong_after_retry_is_left_empty(model):
    sid = chat()
    bad = Classification(queue="Nope", type="Bug", subtype="Software malfunction", reason="Bug - x",
                         priority="Medium", title="t", summary="s", confidence="low", queue_reason="r")
    model.add(ticket_reply(collected={"problem": "x"}), bad, bad, Reply(say="Ready."))
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
    state = sessions.send(chat(), "هلا؟")
    assert state["phase"] == "ask"
    assert state["messages"][-1].text.startswith("عذراً")
    assert any("router failed" in c for c in state["corrections"])


def test_respond_failure_never_leaves_an_empty_answer(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]))
    state = sessions.send(sid, "password?")
    assert state["phase"] == "ask" and state["sources"] == []


def test_products_differ_in_the_router_prompt(model):
    model.add(RouterDecision(intent="other", language="en"), Reply(say="ok"))
    sessions.send(chat("rich@example.com"), "x")
    model.add(RouterDecision(intent="other", language="en"), Reply(say="ok"))
    sessions.send(chat("ole5@example.com"), "x")
    rich_prompt, ole5_prompt = [c[1] for c in model.calls if c[0] is RouterDecision]
    assert "whitelisting an IP or a user" in rich_prompt and "data migration requests" in ole5_prompt


def test_offer_ticket_gets_no_passages(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I use the archive?"]),
              Reply(say="I don't have an answer for this."))
    sessions.send(sid, "archive?")
    assert "STUB-02 Archive" not in respond_prompt(model) and "No search was run" in respond_prompt(model)


def test_router_language_is_corrected_then_overridden(model):
    sid = chat()
    model.add(RouterDecision(intent="other", language="en"), RouterDecision(intent="other", language="ar"),
              Reply(say="تفضل."))
    state = sessions.send(sid, "عندي سؤال")
    assert state["decision"].language == "ar" and len(model.problems) == 1
    assert "Respond in Arabic" in respond_prompt(model)

    model.add(RouterDecision(intent="other", language="en"), RouterDecision(intent="other", language="en"),
              RouterDecision(intent="other", language="en"), Reply(say="تفضل."))
    state = sessions.send(sid, "سؤال آخر")
    assert state["decision"].language == "ar"
    assert any("overridden" in c for c in state["corrections"])


def test_reply_in_the_wrong_language_is_rewritten(model):
    sid = chat()
    model.add(RouterDecision(intent="other", language="ar"), Reply(say="Go ahead, what is your question?"),
              Reply(say="تفضل، ما سؤالك؟"))
    state = sessions.send(sid, "عندي سؤال")
    assert state["messages"][-1].text == "تفضل، ما سؤالك؟"
    assert "Write the same reply in Arabic" in model.problems[-1]
    assert not any("Note from the system" in m.text for m in state["messages"])


def test_reply_still_wrong_after_two_retries_is_sent_with_a_correction(model):
    sid = chat()
    model.add(RouterDecision(intent="other", language="ar"), *(Reply(say="Go ahead.") for _ in range(3)))
    state = sessions.send(sid, "عندي سؤال")
    assert state["messages"][-1].text == "Go ahead."
    assert any("wrong language" in c for c in state["corrections"])


def test_arabic_reply_with_english_screen_names_passes(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="ar", queries=["How do I reset my password?"]),
              Reply(say="اختر Forgot password في صفحة تسجيل الدخول.", sources=["STUB-01 Signing in"]))
    state = sessions.send(sid, "نسيت كلمة المرور")
    assert state["phase"] == "answer" and model.problems == []


def test_change_request_at_confirm_is_refused_not_offered(model):
    sid = chat()
    confirming(model, sid)
    model.add(RouterDecision(intent="question", language="en", queries=["change priority"]),
              Reply(say="The summary cannot be changed here. Press Submit, or cancel."))
    state = sessions.send(sid, "change the priority to High")
    assert state["phase"] == "confirm" and state["ticket_draft"].classified
    assert "Step: confirm" in respond_prompt(model)


def test_respond_is_told_what_the_message_was(model):
    sid = chat()
    collecting(model, sid)
    model.add(ticket_reply(missing=["screen"], unknown=["screen"]), GOOD, Reply(say="Ready."))
    sessions.send(sid, "I don't know")
    assert "The customer's message: a reply about the ticket" in respond_prompt(model)
    model.add(RouterDecision(intent="question", language="en", queries=["export"]), Reply(say="No answer; still ready."))
    sessions.send(sid, "how do I export?")
    assert "The customer's message: a question the knowledge base could not answer" in respond_prompt(model)


def test_answer_without_a_source_becomes_a_ticket_offer(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]),
              Reply(say="I don't have an answer for this. Would you like a ticket?"))
    state = sessions.send(sid, "password?")
    assert state["phase"] == "offer_ticket" and state["sources"] == []
    assert any("answer without a source" in c for c in state["corrections"])


def test_answer_without_a_source_during_a_ticket_stays_in_collect(model):
    sid = chat()
    collecting(model, sid)
    model.add(RouterDecision(intent="question", language="en", queries=["How do I reset my password?"]),
              Reply(say="I don't have an answer for that; let's finish the ticket. Which screen?"))
    state = sessions.send(sid, "password?")
    assert state["phase"] == "collect" and state["ticket_draft"].missing == ["screen"]


def test_respond_is_told_the_ticket_is_urgent(model):
    sid = chat()
    model.add(ticket_reply(urgent=True, collected={"problem": "nobody can sign in"}, missing=["since when"]), GOOD,
              Reply(say="This is urgent, so the ticket is ready now. Press Submit."))
    state = sessions.send(sid, "yes")
    assert state["phase"] == "confirm"
    assert "Urgent: yes" in respond_prompt(model) and "Step: confirm" in respond_prompt(model)


def test_urgent_offer_tells_respond_it_is_urgent(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="ar", queries=["system down"], urgent=True),
              Reply(say="هذه مشكلة عاجلة. هل تريد فتح تذكرة عاجلة الآن؟"))
    state = sessions.send(sid, "النظام كله ما يفتح")
    assert state["phase"] == "offer_ticket"
    assert "Urgent: yes" in respond_prompt(model) and "Step: offer_ticket" in respond_prompt(model)


def test_router_failure_sends_the_apology_and_changes_nothing(model):
    sid = chat()
    before = collecting(model, sid)["ticket_draft"]
    state = sessions.send(sid, "هلا؟")
    assert state["messages"][-1].text.startswith("عذراً")
    assert state["ticket_draft"] == before and state["phase"] == "ask"
    assert not any(c[0] is Reply for c in model.calls[-1:])


def test_classify_failure_keeps_the_draft_unclassified(model):
    sid = chat()
    collecting(model, sid)
    model.add(ticket_reply(collected={"screen": "Inbox"}), Reply(say="I couldn't build the summary; send that again."))
    state = sessions.send(sid, "the Inbox screen")
    draft = state["ticket_draft"]
    assert state["phase"] == "ask" and not draft.classified and draft.collected["screen"] == "Inbox"
    assert "could not be built" in respond_prompt(model)
    assert any("classify failed" in c for c in state["corrections"])


def test_greeting_detection():
    from ole5_chat.agent.prompt import greeting_in
    assert greeting_in("السلام عليكم متى تردون علي") == "السلام عليكم"
    assert greeting_in("Hello, how do I reset my password?") == "Hello"
    assert greeting_in("good morning!") == "good morning"
    assert greeting_in("كيف أستعيد كلمة المرور؟") is None
    assert greeting_in("hithere") is None


def test_outline_carries_the_greeting_and_the_reminder(model):
    sid = chat()
    collecting(model, sid)
    model.add(RouterDecision(intent="question", language="ar", queries=["How do I reset my password?"]),
              Reply(say="وعليكم السلام. اختر Forgot password. ما الشاشة؟", sources=["STUB-01 Signing in"]))
    sessions.send(sid, "السلام عليكم، كيف أستعيد كلمة المرور؟")
    p = respond_prompt(model)
    assert '1. Return the customer\'s greeting ("السلام عليكم")' in p
    assert "asking again for: screen" in p


def test_outline_for_an_urgent_offer(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="ar", queries=["system down"], urgent=True),
              Reply(say="المشكلة عاجلة، هل أفتح تذكرة عاجلة الآن؟"))
    sessions.send(sid, "النظام كله ما يفتح")
    assert "The problem is urgent" in respond_prompt(model) and "Return the customer" not in respond_prompt(model)
