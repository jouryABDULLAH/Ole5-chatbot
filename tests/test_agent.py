"""The agent graph with a scripted model and the stand-in knowledge base: the router picks the step in code from what its model call reports, for greetings, answers, ticket offers, the ticket flow, detours, cancels and failures."""

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import sessions
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision
from ole5_chat.agent.validate import Language, check_ticket
from ole5_chat.knowledge.contracts import Evidence, Retrieval, Verdict
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


def test_values_refused_twice_keep_the_draft_unclassified(model):
    sid = chat()
    collecting(model, sid)
    bad = GOOD.model_copy(update={"queue": "Ole5 Helpdesk"})
    model.add(ticket_reply(collected={"screen": "Inbox"}), bad, bad, Reply(say="I couldn't build the summary; send that again."))
    state = sessions.send(sid, "the Inbox screen")
    draft = state["ticket_draft"]
    assert state["phase"] == "ask" and not draft.classified and draft.collected["screen"] == "Inbox"
    assert "could not be built" in respond_prompt(model)
    assert any("classify refused" in c for c in state["corrections"])


def test_greeting_detection():
    from ole5_chat.agent.prompt import greeting_in
    assert greeting_in("السلام عليكم متى تردون علي") == "السلام عليكم"
    assert greeting_in("Hello, how do I reset my password?") == "Hello"
    assert greeting_in("good morning!") == "good morning"
    assert greeting_in("كيف أستعيد كلمة المرور؟") is None
    assert greeting_in("hithere") is None


def test_outline_carries_the_greeting(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="ar", queries=["How do I reset my password?"]),
              Reply(say="وعليكم السلام. اختر Forgot password.", sources=["STUB-01 Signing in"]))
    sessions.send(sid, "السلام عليكم، كيف أستعيد كلمة المرور؟")
    assert '1. Reply to the customer\'s greeting ("السلام عليكم")' in respond_prompt(model)


def test_outline_for_an_urgent_offer(model):
    sid = chat()
    model.add(RouterDecision(intent="question", language="ar", queries=["system down"], urgent=True),
              Reply(say="المشكلة عاجلة، هل أفتح تذكرة عاجلة الآن؟"))
    sessions.send(sid, "النظام كله ما يفتح")
    assert "The problem is urgent" in respond_prompt(model) and "Return the customer" not in respond_prompt(model)


class ToolModel:
    """Stands in for llm.tool_client: returns the scripted AIMessages in order and records each call's temperature and messages."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, temperature):
        self.temperature = temperature
        return self

    def bind_tools(self, tools, tool_choice=None):
        return self

    def invoke(self, convo):
        self.calls.append((self.temperature, list(convo)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def passage(document, relation="DIRECT"):
    return Evidence(text=f"{document} text", document=document, section=None, source_file=None, relation=relation,
                    score=3, reason="")


@pytest.fixture
def respond_node(monkeypatch):
    """Runs respond_agent on one customer message with scripted tool-model replies and scripted search results."""
    from ole5_chat import llm
    from ole5_chat.agent import respond_agent
    from ole5_chat.knowledge import tool

    def run(message, replies, retrievals=()):
        found = list(retrievals)
        monkeypatch.setattr(tool, "search", lambda question, product: found.pop(0))
        fake = ToolModel(*replies)
        monkeypatch.setattr(llm, "tool_client", fake)
        state = sessions.get(chat())
        state = {**state, "messages": [*state["messages"], HumanMessage(content=message)]}
        return respond_agent.respond_agent(state), fake

    return run


def searching(question, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": "search_knowledge_base", "args": {"question": question},
                                              "id": call_id}])


def test_respond_agent_keeps_a_partial_answer_that_offers_a_ticket(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=["Guide"], answered=True, offered=True, unsupported=[]))
    hit = Retrieval(query="drafts", verdict=Verdict.PARTIAL, evidence=[passage("Guide")])
    out, _ = respond_node("How do I save a draft, and how long is it kept?",
                          [searching("How to save a draft?"),
                           AIMessage(content="Save it as a draft from the message screen. Should I open a ticket for how long it is kept?")],
                          [hit])
    assert out["reply"].say.startswith("Save it as a draft")
    assert out["reply"].sources == ["Guide"]
    assert "reply offered a ticket" in out["corrections"]


def test_respond_agent_sends_the_apology_when_the_model_fails(respond_node):
    out, fake = respond_node("كيف أحفظ مسودة؟", [rejected(), rejected()])
    assert out["reply"].say.startswith("عذراً") and out["reply"].sources == []
    assert out["last_retrieval"] is None
    assert any(c.startswith("respond failed:") for c in out["corrections"])
    assert [t for t, _ in fake.calls] == [0, 0.3]


def hit(*documents):
    return Retrieval(query="q", verdict=Verdict.ANSWERABLE, evidence=[passage(d) for d in documents])


def miss():
    return Retrieval(query="q", verdict=Verdict.NOT_IN_KB, evidence=[passage("Other", relation="INFERENTIAL")])


def grounding(model):
    return [c for c in model.calls if c[0].__name__ == "Grounding"]


def sent(fake, n=-1):
    """The messages of the nth tool-model call."""
    return fake.calls[n][1]


def rejected():
    from groq import BadRequestError
    import httpx
    return BadRequestError("bad tool call", response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                           body=None)


def test_respond_agent_cites_the_document_the_grounding_names(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=["Guide"], answered=True, offered=False, unsupported=[]))
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"),
                                                     AIMessage(content="Press Save as draft.")], [hit("Guide", "Manual")])
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide"]
    assert out["last_retrieval"] is not None and out["corrections"] == []


def test_respond_agent_without_a_direct_passage_is_grounded_with_no_passages(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=[], answered=False, offered=True, unsupported=[]))
    out, _ = respond_node("Does it run on Linux?", [searching("Does Ole5 run on Linux?"),
                                                    AIMessage(content="I have no answer for that. Open a ticket?")],
                          [miss()])
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == []
    assert out["offered"] is True and out["corrections"] == []
    assert len(grounding(model)) == 1


def test_respond_agent_keeps_a_greeting_written_without_a_search(model, respond_node):
    from ole5_chat.agent.respond_agent import NUDGE, Grounding
    model.add(Grounding(used=[], answered=False, offered=False, unsupported=[]))
    out, fake = respond_node("مرحبا", [AIMessage(content="أهلاً بك، كيف أقدر أساعدك؟"),
                                       AIMessage(content="أهلاً بك، كيف أقدر أساعدك؟")])
    assert sent(fake)[-1].content == NUDGE
    assert out["reply"].say == "أهلاً بك، كيف أقدر أساعدك؟" and out["reply"].sources == []
    assert out["corrections"] == ["nudged", "replied without searching"] and out["last_retrieval"] is None


def test_respond_agent_searches_when_nudged(model, respond_node):
    from ole5_chat.agent.respond_agent import NUDGE, Grounding
    model.add(Grounding(used=["Guide"], answered=True, offered=False, unsupported=[]))
    out, fake = respond_node("How do I save a draft?", [AIMessage(content="Press Save."),
                                                        searching("How to save a draft?"),
                                                        AIMessage(content="Press Save as draft.")], [hit("Guide")])
    assert sent(fake, 1)[-1].content == NUDGE
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide"]
    assert out["corrections"] == ["nudged"]


def test_respond_agent_replaces_an_answer_written_without_a_search(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=[], answered=True, offered=False, unsupported=[]))
    out, fake = respond_node("How long are drafts kept?", [AIMessage(content="Drafts are kept 30 days."),
                                                           AIMessage(content="Drafts are kept 30 days."),
                                                           AIMessage(content="I have no answer for that. Open a ticket?")])
    assert "You have no answer for this" in sent(fake)[-1].content
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == []
    assert out["corrections"] == ["nudged", "answer without searching"]


def test_respond_agent_declining_a_direct_passage_twice_gives_the_no_answer(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=[], answered=False, offered=True, unsupported=[]), Grounding(used=[], answered=False, offered=True, unsupported=[]))
    out, fake = respond_node("How do I save a draft?", [searching("How to save a draft?"),
                                                        AIMessage(content="Open a ticket?"),
                                                        AIMessage(content="Open a ticket?"),
                                                        AIMessage(content="I have no answer for that. Open a ticket?")],
                             [hit("Guide")])
    assert "Write what it says, even if it is one sentence, and nothing more." in sent(fake, 2)[-1].content
    assert len(grounding(model)) == 2
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == []
    assert out["corrections"] == ["gate 2 retried: declined", "respond declined a DIRECT passage"]


def test_respond_agent_cites_every_direct_document_when_grounding_fails(model, respond_node):
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"),
                                                     AIMessage(content="Press Save as draft.")], [hit("Guide", "Manual")])
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide", "Manual"]
    assert "grounding call failed" in out["corrections"]


def test_respond_agent_rewrites_a_reply_in_the_wrong_language(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(*[Grounding(used=["Guide"], answered=True, offered=False, unsupported=[])] * 2)
    out, fake = respond_node("كيف أحفظ مسودة؟", [searching("How to save a draft?"),
                                               AIMessage(content="Press Save as draft."),
                                               AIMessage(content="اضغط حفظ كمسودة.")], [hit("Guide")])
    assert "The customer wrote in Arabic" in sent(fake)[-1].content
    assert out["reply"].say == "اضغط حفظ كمسودة." and out["corrections"] == ["language retried"]


def test_respond_agent_sends_the_last_reply_after_two_language_retries(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(*[Grounding(used=["Guide"], answered=True, offered=False, unsupported=[])] * 3)
    out, fake = respond_node("كيف أحفظ مسودة؟", [searching("How to save a draft?"),
                                               *[AIMessage(content="Press Save as draft.")] * 3], [hit("Guide")])
    assert len(fake.calls) == 4
    assert out["reply"].say == "Press Save as draft."
    assert out["corrections"][:2] == ["language retried", "language retried"]
    assert out["corrections"][2].startswith("language:")


def test_respond_agent_rewrites_a_reply_with_chinese_characters(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(*[Grounding(used=["Guide"], answered=True, offered=False, unsupported=[])] * 2)
    out, fake = respond_node("كيف أحفظ مسودة؟", [searching("How to save a draft?"),
                                               AIMessage(content="اضغط 保存 كمسودة."),
                                               AIMessage(content="اضغط حفظ كمسودة.")], [hit("Guide")])
    assert "Chinese characters" in sent(fake)[-1].content
    assert out["reply"].say == "اضغط حفظ كمسودة." and out["corrections"] == ["language retried"]


def test_respond_agent_shows_a_failed_search_to_the_model(model, respond_node):
    from langchain_core.messages import ToolMessage
    failed = Retrieval(query="q", verdict=Verdict.NOT_IN_KB, error="ReadTimeout")
    out, fake = respond_node("How do I save a draft?", [searching("How to save a draft?"),
                                                        AIMessage(content="I could not check that now. Open a ticket?")],
                             [failed])
    tool_reply = [m for m in sent(fake) if isinstance(m, ToolMessage)][0]
    assert '"status": "search_failed"' in tool_reply.content and "does NOT" in tool_reply.content
    assert out["reply"].sources == []


def test_respond_agent_retries_a_rejected_call_warmer(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=["Guide"], answered=True, offered=False, unsupported=[]))
    out, fake = respond_node("How do I save a draft?", [rejected(), searching("How to save a draft?"),
                                                        AIMessage(content="Press Save as draft.")], [hit("Guide")])
    assert [t for t, _ in fake.calls] == [0, 0.3, 0]
    assert out["reply"].say == "Press Save as draft." and out["corrections"] == []


def test_respond_agent_stops_searching_at_the_cap(model, respond_node, monkeypatch):
    from ole5_chat.config import get_settings
    from ole5_chat.agent.respond_agent import Grounding
    monkeypatch.setenv("KB_MAX_SEARCHES", "1")
    get_settings.cache_clear()
    model.add(Grounding(used=[], answered=False, offered=True, unsupported=[]))
    out, fake = respond_node("How do I save a draft?", [searching("a", "c1"), searching("b", "c2"),
                                                        AIMessage(content="I have no answer for that. Open a ticket?")],
                             [miss(), miss()])
    assert len(fake.calls) == 3
    assert "no more searches" in sent(fake)[-1].content
    assert out["reply"].say.startswith("I have no answer") and out["corrections"] == ["search cap reached"]


def test_respond_agent_finds_the_title_inside_what_the_grounding_returns(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=["[Guide] Saving drafts"], answered=True, offered=False, unsupported=[]))
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"),
                                                     AIMessage(content="Press Save as draft.")], [hit("Guide", "Manual")])
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide"]
    assert out["corrections"] == []


def test_respond_agent_sees_direct_passages_whole_and_inferential_cut(model, respond_node):
    import json
    from langchain_core.messages import ToolMessage
    from ole5_chat.agent.respond_agent import Grounding
    from ole5_chat.knowledge.tool import PASSAGE_CHARS
    direct, inferential = passage("Guide"), passage("Manual", relation="INFERENTIAL")
    direct.text, inferential.text = "d" * 2000, "i" * 2000
    model.add(Grounding(used=["Guide"], answered=True, offered=False, unsupported=[]))
    _, fake = respond_node("How do I save a draft?", [searching("How to save a draft?"),
                                                      AIMessage(content="Press Save as draft.")],
                           [Retrieval(query="q", verdict=Verdict.ANSWERABLE, evidence=[direct, inferential])])
    tool_reply = json.loads([m for m in sent(fake) if isinstance(m, ToolMessage)][0].content)
    texts = {e["document"]: e["text"] for e in tool_reply["evidence"]}
    assert texts["Guide"] == "d" * 2000
    assert texts["Manual"] == "i" * PASSAGE_CHARS


def test_respond_agent_removes_unsupported_facts_on_retry(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=["Guide"], answered=True, offered=False, unsupported=["Tenant ID"]),
              Grounding(used=["Guide"], answered=True, offered=False, unsupported=[]))
    out, fake = respond_node("How do I set up SSO?", [searching("How to set up SSO?"),
                                                      AIMessage(content="Fill the form with your Tenant ID."),
                                                      AIMessage(content="Fill the requirements form and send it to support.")],
                             [hit("Guide")])
    assert "Tenant ID" in sent(fake)[-1].content
    assert out["reply"].say == "Fill the requirements form and send it to support."
    assert out["reply"].sources == ["Guide"] and out["corrections"] == ["gate 2 retried: unsupported"]


def test_respond_agent_unsupported_facts_twice_give_the_no_answer(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(*[Grounding(used=["Guide"], answered=True, offered=False, unsupported=["Tenant ID"])] * 2)
    out, fake = respond_node("How do I set up SSO?", [searching("How to set up SSO?"),
                                                      AIMessage(content="Fill the form with your Tenant ID."),
                                                      AIMessage(content="Enter your Tenant ID in the form."),
                                                      AIMessage(content="I have no answer for that. Open a ticket?")],
                             [hit("Guide")])
    assert "You have no answer for this" in sent(fake)[-1].content
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == []
    assert out["corrections"] == ["gate 2 retried: unsupported", "reply kept unsupported facts"]


def router_state(email="ole5@example.com", phase="none", messages=("hi",)):
    from ole5_chat.agent.contracts import PendingIssue
    state = sessions.get(chat(email))
    state["chat_phase"] = phase
    state["pending"] = [] if phase == "none" else [PendingIssue(text="Access denied", source="no_answer")]
    state["messages"] = state["messages"] + [HumanMessage(content=m) for m in messages]
    return state


@pytest.mark.parametrize("phase", ["none", "offer_ticket", "collect", "confirm"])
def test_route_schema_takes_only_the_options_of_its_phase(phase):
    from pydantic import ValidationError
    from ole5_chat.agent.contracts import OPTIONS, route_schema
    schema = route_schema(phase)
    for option in OPTIONS[phase]:
        assert schema(next=option).next == option
    other = {"none": "yes", "offer_ticket": "details", "collect": "accept", "confirm": "decline"}[phase]
    with pytest.raises(ValidationError):
        schema(next=other)


def test_router_v2_writes_only_next(model):
    from ole5_chat.agent.contracts import route_schema
    from ole5_chat.agent.router_v2 import router_v2
    model.add(route_schema("offer_ticket")(next="accept"))
    assert router_v2(router_state(phase="offer_ticket", messages=["yes please"])) == {"next": "accept"}


@pytest.mark.parametrize("phase", ["none", "offer_ticket", "collect", "confirm"])
def test_router_v2_prompt_defines_only_the_options_of_its_phase(phase):
    from ole5_chat.agent import prompt
    from ole5_chat.agent.contracts import OPTIONS
    text = prompt.router_v2(router_state(phase=phase))
    for option in ("chat", "ticket", "urgent", "status", "accept", "decline", "details", "done", "cancel", "yes",
                   "change"):
        assert (f"\n{option}: " in text) == (option in OPTIONS[phase])
    assert phase != "none" or "accept" not in text
    assert phase != "confirm" or "\ndetails" not in text


def test_router_v2_prompt_carries_each_products_lists():
    from ole5_chat.agent import prompt
    ole5, rich = prompt.router_v2(router_state()), prompt.router_v2(router_state("rich@example.com"))
    assert "data migration requests" in ole5 and "no user in the organization can sign in" in ole5
    assert "whitelisting an IP or a user" in rich and "the portal or the API is down" in rich
    assert "data migration requests" not in rich and "whitelisting an IP or a user" not in ole5


def test_router_v2_failure_gives_chat_and_a_correction(model):
    from ole5_chat.agent.router_v2 import router_v2
    out = router_v2(router_state())
    assert out["next"] == "chat" and out["corrections"][-1].startswith("router failed: nothing scripted")


def test_router_v2_sends_the_whole_transcript(monkeypatch):
    from ole5_chat import llm
    from ole5_chat.agent.router_v2 import router_v2
    seen = []

    def fake(system, messages, schema, node=None, check=None):
        seen.append(messages)
        return schema(next="details")

    monkeypatch.setattr(llm, "structured", fake)
    state = router_state(phase="collect", messages=["it fails", "on the Inbox"])
    assert router_v2(state) == {"next": "details"}
    assert [m.text for m in seen[0]] == [m.text for m in state["messages"]] and len(seen[0]) == 3


def test_initial_state_has_the_router_v2_fields():
    state = sessions.get(chat())
    assert (state["chat_phase"], state["pending"], state["next"], state["task"], state["language"]) == \
        ("none", [], None, None, None)


@pytest.fixture
def new_path(monkeypatch, model):
    """The RESPOND_AGENT graph with a scripted router, tool model, searches and grounding calls. send() runs one turn
    and returns the state and the system prompt respond_agent was given."""
    from langgraph.checkpoint.memory import MemorySaver
    from ole5_chat import llm
    from ole5_chat.agent.contracts import route_schema
    from ole5_chat.config import get_settings
    from ole5_chat.knowledge import tool
    monkeypatch.setenv("RESPOND_AGENT", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(sessions, "_graph", graph.build(MemorySaver(serde=sessions._serde)))
    found, fake = [], ToolModel()
    monkeypatch.setattr(tool, "search", lambda question, product: found.pop(0))
    monkeypatch.setattr(llm, "tool_client", fake)

    def send(sid, message, next_, replies, retrievals=(), groundings=()):
        model.add(route_schema(sessions.get(sid)["chat_phase"])(next=next_), *groundings)
        fake.replies.extend(replies)
        found.extend(retrievals)
        first = len(fake.calls)
        state = sessions.send(sid, message)
        return state, fake.calls[first][1][0].content

    return send


def ground(answered, offered, used=()):
    from ole5_chat.agent.respond_agent import Grounding
    return Grounding(used=list(used), answered=answered, offered=offered, unsupported=[])


def plain(text):
    """A reply written without a search, and the same reply again after the nudge."""
    return [AIMessage(content=text), AIMessage(content=text)]


def offered_seen(sid):
    return any(s.values.get("offered") for s in sessions._graph.get_state_history(sessions._config(sid)))


def unanswered(new_path, sid, question, next_="chat"):
    return new_path(sid, question, next_, [searching(question), AIMessage(content="I have no answer. Open a ticket?")],
                    [miss()], [ground(False, True)])


def test_new_path_answered_question_stays_in_none(new_path):
    sid = chat()
    state, _ = new_path(sid, "How do I save a draft?", "chat",
                        [searching("How to save a draft?"), AIMessage(content="Press Save as draft.")],
                        [hit("Guide")], [ground(True, False, ["Guide"])])
    assert (state["chat_phase"], state["pending"], state["sources"]) == ("none", [], ["Guide"])


def test_new_path_unanswered_question_is_queued_and_offered(new_path):
    sid = chat()
    state, prompt_text = unanswered(new_path, sid, "Does it run on Linux?")
    assert state["messages"][-1].text == "I have no answer. Open a ticket?" and offered_seen(sid)
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"] and state["pending"][0].source == "no_answer"
    assert state["chat_phase"] == "offer_ticket" and state["offered"] is False and state["task"] is None


def test_new_path_partial_answer_is_cited_and_queued(new_path):
    sid = chat()
    state, _ = new_path(sid, "How do I save a draft, and how long is it kept?", "chat",
                        [searching("drafts"), AIMessage(content="Press Save as draft. Open a ticket for the rest?")],
                        [hit("Guide")], [ground(True, True, ["Guide"])])
    assert state["sources"] == ["Guide"] and state["chat_phase"] == "offer_ticket"
    assert [p.text for p in state["pending"]] == ["How do I save a draft, and how long is it kept?"]


def test_new_path_answer_without_a_direct_passage_is_replaced(new_path):
    sid = chat()
    state, _ = new_path(sid, "Does it run on Linux?", "chat",
                        [searching("Linux"), AIMessage(content="Yes, Ole5 runs on Linux."),
                         AIMessage(content="I have no answer. Open a ticket?")],
                        [miss()], [ground(True, False)])
    assert state["messages"][-1].text == "I have no answer. Open a ticket?" and offered_seen(sid)
    assert "answer without a DIRECT passage" in state["corrections"]
    assert len(state["pending"]) == 1 and state["chat_phase"] == "offer_ticket"


def test_new_path_greeting_queues_nothing(new_path, searches):
    sid = chat()
    state, _ = new_path(sid, "مرحبا", "chat", plain("أهلاً بك"), groundings=[ground(False, False)])
    assert (state["chat_phase"], state["pending"]) == ("none", []) and not offered_seen(sid)


@pytest.mark.parametrize("next_", ["ticket", "urgent"])
def test_new_path_router_ticket_or_urgent_is_queued_and_offered(new_path, next_):
    sid = chat()
    state, prompt_text = new_path(sid, "We need to migrate our data", next_, plain("Shall I open a ticket?"),
                                  groundings=[ground(False, True)])
    issue = state["pending"][0]
    assert (issue.text, issue.source, issue.urgent) == ("We need to migrate our data", "always_ticket", next_ == "urgent")
    assert state["chat_phase"] == "offer_ticket" and len(state["pending"]) == 1
    offer = "This needs the support team: We need to migrate our data."
    assert ("This sounds urgent. " + offer if next_ == "urgent" else offer) in prompt_text


def test_new_path_accept_creates_the_draft(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "ايه", "accept", plain("أي إصدار تستخدم؟"), groundings=[ground(False, False)])
    assert state["ticket_draft"] is not None and state["chat_phase"] == "collect"
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"]
    assert "The customer agreed to a ticket for: Does it run on Linux?" in prompt_text


def test_new_path_decline_with_one_issue_empties_pending(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "لا شكراً", "decline", plain("تمام، تقدر تتواصل معنا في أي وقت."), groundings=[ground(False, False)])
    assert (state["chat_phase"], state["pending"]) == ("none", [])
    assert "The customer declined the ticket. Acknowledge it in one line and say they can contact" in prompt_text


def test_new_path_decline_with_two_issues_offers_the_next(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = unanswered(new_path, sid, "Can I export to Excel?")
    assert len(state["pending"]) == 2 and state["chat_phase"] == "offer_ticket"
    assert "A ticket offer for this issue is still open: Does it run on Linux?" in prompt_text
    state, prompt_text = new_path(sid, "لا", "decline", plain("تمام. تبغى تذكرة للتصدير؟"),
                                  groundings=[ground(False, True)])
    assert [p.text for p in state["pending"]] == ["Can I export to Excel?"] and state["chat_phase"] == "offer_ticket"
    assert "offer a ticket for their other issue: Can I export to Excel?" in prompt_text


def test_new_path_side_question_keeps_the_offer_open(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "How do I save a draft?", "chat",
                                  [searching("drafts"), AIMessage(content="Press Save as draft. Still want the ticket?")],
                                  [hit("Guide")], [ground(True, True, ["Guide"])])
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"] and state["chat_phase"] == "offer_ticket"
    assert state["sources"] == ["Guide"]
    assert "A ticket offer for this issue is still open: Does it run on Linux?" in prompt_text


def test_new_path_same_unanswered_question_is_queued_once(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, _ = unanswered(new_path, sid, "Does it run on Linux?")
    assert len(state["pending"]) == 1 and state["chat_phase"] == "offer_ticket"
