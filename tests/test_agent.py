"""The agent with scripted models and searches: the greeting, classify and its value checks, respond_agent's gates, the router's options and prompt, and the graph's handlers, post_check and pending issues."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import sessions
from ole5_chat.agent.contracts import Classification
from ole5_chat.agent.validate import check_ticket
from ole5_chat.knowledge.contracts import Evidence, Retrieval, Verdict
from ole5_chat.values import catalog

GOOD = Classification(queue="Ole5 Technical", type="Mobile App Issue", subtype="Application crash",
                      reason="Issue - app crashes on Inbox", priority="Medium", title="App crash",
                      summary="The app crashes on the Inbox screen.", confidence="high", queue_reason="tech")


def chat(email="ole5@example.com"):
    sid, _ = sessions.start(email)
    return sid


def test_first_message_is_the_fixed_greeting_without_a_model_call(model):
    state = sessions.get(chat())
    assert state["messages"][0].text.startswith("أهلاً بك، أنا مساعد دعم Ole5")
    assert model.calls == []


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


def drafting():
    """A conversation with a ticket draft that has the screen collected, ready for classify."""
    from ole5_chat.agent.handlers import _new_draft
    state = sessions.get(chat())
    draft = _new_draft(state).model_copy(update={"collected": {"screen": "Inbox"}})
    return {**state, "messages": [*state["messages"], HumanMessage(content="the Inbox screen")], "ticket_draft": draft}


def test_classify_retries_a_refused_value_then_classifies(model):
    from ole5_chat.agent.classify import classify
    found = GOOD.model_copy(update={"collected": {"screen": "Inbox"}, "missing": ["app version"], "unknown": ["device"]})
    model.add(GOOD.model_copy(update={"queue": "Ole5 Helpdesk"}), found)
    draft = classify(drafting())["ticket_draft"]
    assert draft.classified and draft.queue == "Ole5 Technical"
    assert (draft.collected, draft.missing, draft.unknown) == ({"screen": "Inbox"}, ["app version"], ["device"])


def test_classify_failure_keeps_the_draft_unclassified(model):
    from ole5_chat.agent.classify import classify
    out = classify(drafting())
    assert "ticket_draft" not in out and "could not be built" in out["note"]
    assert any("classify failed" in c for c in out["corrections"])


def test_values_refused_twice_keep_the_draft_unclassified(model):
    from ole5_chat.agent.classify import classify
    bad = GOOD.model_copy(update={"queue": "Ole5 Helpdesk"})
    model.add(bad, bad)
    out = classify(drafting())
    assert "ticket_draft" not in out and "could not be built" in out["note"]
    assert any("classify refused" in c for c in out["corrections"])


class ToolModel:
    """Stands in for llm.tool_client: returns the scripted AIMessages in order and records each call's temperature, messages and the tools bound to it."""

    def __init__(self, *replies):
        self.replies, self.calls, self.bound = list(replies), [], []

    def __call__(self, temperature):
        self.temperature, self.tools = temperature, None
        return self

    def bind_tools(self, tools, tool_choice=None):
        self.tools = tools
        return self

    def invoke(self, convo):
        self.calls.append((self.temperature, list(convo)))
        self.bound.append(self.tools)
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


def test_respond_agent_keeps_a_thanks_without_a_nudge_or_a_search(model, respond_node):
    from ole5_chat.agent.respond_agent import Grounding
    model.add(Grounding(used=[], answered=False, offered=False, unsupported=[]))
    out, fake = respond_node("شكراً", [AIMessage(content="العفو، أنا هنا إن احتجت شيئاً.")])
    assert len(fake.calls) == 1 and len(grounding(model)) == 1
    assert out["reply"].say == "العفو، أنا هنا إن احتجت شيئاً." and out["reply"].sources == []
    assert out["corrections"] == ["no search needed"] and out["last_retrieval"] is None


def test_respond_agent_searches_when_nudged(model, respond_node):
    from ole5_chat.agent.respond_agent import NUDGE, Grounding
    model.add(Grounding(used=[], answered=True, offered=False, unsupported=[]),
              Grounding(used=["Guide"], answered=True, offered=False, unsupported=[]))
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
    state["phase"] = phase
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


def test_router_writes_only_next(model):
    from ole5_chat.agent.contracts import route_schema
    from ole5_chat.agent.router import router
    model.add(route_schema("offer_ticket")(next="accept"))
    assert router(router_state(phase="offer_ticket", messages=["yes please"])) == {"next": "accept"}


@pytest.mark.parametrize("phase", ["none", "offer_ticket", "collect", "confirm"])
def test_router_prompt_defines_only_the_options_of_its_phase(phase):
    from ole5_chat.agent import prompt
    from ole5_chat.agent.contracts import OPTIONS
    text = prompt.router(router_state(phase=phase))
    for option in ("chat", "ticket", "urgent", "status", "accept", "decline", "details", "done", "cancel", "yes",
                   "change"):
        assert (f"\n{option}: " in text) == (option in OPTIONS[phase])
    assert phase != "none" or "accept" not in text
    assert phase != "confirm" or "\ndetails" not in text


def test_router_prompt_carries_each_products_lists():
    from ole5_chat.agent import prompt
    ole5, rich = prompt.router(router_state()), prompt.router(router_state("rich@example.com"))
    assert "data migration requests" in ole5 and "no user in the organization can sign in" in ole5
    assert "whitelisting an IP or a user" in rich and "the portal or the API is down" in rich
    assert "data migration requests" not in rich and "whitelisting an IP or a user" not in ole5


def test_router_failure_gives_chat_and_a_correction(model):
    from ole5_chat.agent.router import router
    out = router(router_state())
    assert out["next"] == "chat" and out["corrections"][-1].startswith("router failed: nothing scripted")


def test_router_sends_the_whole_transcript(monkeypatch):
    from ole5_chat import llm
    from ole5_chat.agent.router import router
    seen = []

    def fake(system, messages, schema, node=None, check=None):
        seen.append(messages)
        return schema(next="details")

    monkeypatch.setattr(llm, "structured", fake)
    state = router_state(phase="collect", messages=["it fails", "on the Inbox"])
    assert router(state) == {"next": "details"}
    assert [m.text for m in seen[0]] == [m.text for m in state["messages"]] and len(seen[0]) == 3


def test_initial_state_has_the_router_fields():
    state = sessions.get(chat())
    assert (state["phase"], state["pending"], state["next"], state["task"], state["language"]) == \
        ("none", [], None, None, None)


@pytest.fixture
def new_path(monkeypatch, model):
    """The graph with a scripted router, tool model, searches and grounding calls. send() runs one turn and returns
    the state and the system prompt respond_agent was given."""
    from ole5_chat import llm
    from ole5_chat.agent.contracts import route_schema
    from ole5_chat.knowledge import tool
    found, fake = [], ToolModel()
    monkeypatch.setattr(tool, "search", lambda question, product: found.pop(0))
    monkeypatch.setattr(llm, "tool_client", fake)

    def send(sid, message, next_, replies, retrievals=(), groundings=()):
        model.add(route_schema(sessions.get(sid)["phase"])(next=next_), *groundings)
        fake.replies.extend(replies)
        found.extend(retrievals)
        first = len(fake.calls)
        state = sessions.send(sid, message)
        return state, fake.calls[first][1][0].content

    return send


def ground(answered, offered, used=()):
    from ole5_chat.agent.respond_agent import Grounding
    return Grounding(used=list(used), answered=answered, offered=offered, unsupported=[])


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
    assert (state["phase"], state["pending"], state["sources"]) == ("none", [], ["Guide"])


def test_new_path_unanswered_question_is_queued_and_offered(new_path):
    sid = chat()
    state, prompt_text = unanswered(new_path, sid, "Does it run on Linux?")
    assert state["messages"][-1].text == "I have no answer. Open a ticket?" and offered_seen(sid)
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"] and state["pending"][0].source == "no_answer"
    assert state["phase"] == "offer_ticket" and state["offered"] is False and state["task"] is None


def test_new_path_partial_answer_is_cited_and_queued(new_path):
    sid = chat()
    state, _ = new_path(sid, "How do I save a draft, and how long is it kept?", "chat",
                        [searching("drafts"), AIMessage(content="Press Save as draft. Open a ticket for the rest?")],
                        [hit("Guide")], [ground(True, True, ["Guide"])])
    assert state["sources"] == ["Guide"] and state["phase"] == "offer_ticket"
    assert [p.text for p in state["pending"]] == ["How do I save a draft, and how long is it kept?"]


def test_new_path_answer_without_a_direct_passage_is_replaced(new_path):
    sid = chat()
    state, _ = new_path(sid, "Does it run on Linux?", "chat",
                        [searching("Linux"), AIMessage(content="Yes, Ole5 runs on Linux."),
                         AIMessage(content="I have no answer. Open a ticket?")],
                        [miss()], [ground(True, False)])
    assert state["messages"][-1].text == "I have no answer. Open a ticket?" and offered_seen(sid)
    assert "answer without a DIRECT passage" in state["corrections"]
    assert len(state["pending"]) == 1 and state["phase"] == "offer_ticket"


def test_new_path_greeting_queues_nothing(new_path):
    sid = chat()
    state, _ = new_path(sid, "مرحبا", "chat", [AIMessage(content="أهلاً بك")], groundings=[ground(False, False)])
    assert (state["phase"], state["pending"]) == ("none", []) and not offered_seen(sid)


@pytest.mark.parametrize("next_", ["ticket", "urgent"])
def test_new_path_router_ticket_or_urgent_is_queued_and_offered(new_path, next_):
    sid = chat()
    state, prompt_text = new_path(sid, "We need to migrate our data", next_, [AIMessage(content="Shall I open a ticket?")])
    issue = state["pending"][0]
    assert (issue.text, issue.source, issue.urgent) == ("We need to migrate our data", "always_ticket", next_ == "urgent")
    assert state["phase"] == "offer_ticket" and len(state["pending"]) == 1
    offer = "This needs the support team: We need to migrate our data."
    assert ("This sounds urgent. " + offer if next_ == "urgent" else offer) in prompt_text


def test_new_path_accept_creates_the_draft(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "ايه", "accept", [AIMessage(content="أي إصدار تستخدم؟")])
    assert state["ticket_draft"] is not None and state["phase"] == "collect"
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"]
    assert "The customer agreed to a ticket for: Does it run on Linux?" in prompt_text


def test_new_path_decline_with_one_issue_empties_pending(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "لا شكراً", "decline", [AIMessage(content="تمام، تقدر تتواصل معنا في أي وقت.")])
    assert (state["phase"], state["pending"]) == ("none", [])
    assert "none was opened. Acknowledge it in one line without saying a ticket was cancelled" in prompt_text


def test_new_path_decline_with_two_issues_offers_the_next(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = unanswered(new_path, sid, "Can I export to Excel?")
    assert len(state["pending"]) == 2 and state["phase"] == "offer_ticket"
    assert "A ticket offer for this issue is still open: Does it run on Linux?" in prompt_text
    state, prompt_text = new_path(sid, "لا", "decline", [AIMessage(content="تمام. تبغى تذكرة للتصدير؟")])
    assert [p.text for p in state["pending"]] == ["Can I export to Excel?"] and state["phase"] == "offer_ticket"
    assert "offer a ticket for their other issue: Can I export to Excel?" in prompt_text


def test_new_path_side_question_keeps_the_offer_open(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "How do I save a draft?", "chat",
                                  [searching("drafts"), AIMessage(content="Press Save as draft. Still want the ticket?")],
                                  [hit("Guide")], [ground(True, True, ["Guide"])])
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"] and state["phase"] == "offer_ticket"
    assert state["sources"] == ["Guide"]
    assert "A ticket offer for this issue is still open: Does it run on Linux?" in prompt_text


def test_new_path_same_unanswered_question_is_queued_once(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, _ = unanswered(new_path, sid, "Does it run on Linux?")
    assert len(state["pending"]) == 1 and state["phase"] == "offer_ticket"


def test_new_path_accept_is_not_nudged_to_search(new_path, model):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    before = len(grounding(model))
    state, _ = new_path(sid, "ايه", "accept", [AIMessage(content="أي إصدار تستخدم؟")])
    assert state["messages"][-1].text == "أي إصدار تستخدم؟" and "nudged" not in state["corrections"]
    assert len(grounding(model)) == before


def collecting(new_path, sid, question="Does it run on Linux?"):
    """An unanswered question, then accept: a draft in collect."""
    unanswered(new_path, sid, question)
    return new_path(sid, "yes", "accept", [AIMessage(content="Which version do you use?")])


def confirming(new_path, sid, **found):
    """collecting, then done with classify scripted: the summary shown in confirm."""
    collecting(new_path, sid)
    return new_path(sid, "nothing else", "done", [AIMessage(content="The summary is ready. Submit it?")],
                    groundings=[GOOD.model_copy(update=found)])


def test_details_count_rounds_then_ask_the_open_question(new_path):
    sid = chat()
    collecting(new_path, sid)
    state, prompt_text = new_path(sid, "version 17", "details", [AIMessage(content="When did it start?")])
    assert state["ticket_draft"].rounds == 1 and "Ask what else the support team needs" in prompt_text
    state, prompt_text = new_path(sid, "yesterday", "details", [AIMessage(content="Anything else to add?")])
    assert state["ticket_draft"].rounds == 2 and state["phase"] == "collect"
    assert "Ask whether there is anything else they'd like to add about: Does it run on Linux?" in prompt_text


def test_done_classifies_once_and_shows_the_summary(new_path, model):
    sid = chat()
    state, prompt_text = confirming(new_path, sid, collected={"version": "17"}, missing=["device"],
                                    unknown=["since when"])
    draft = state["ticket_draft"]
    assert state["phase"] == "confirm" and draft.classified and draft.queue == "Ole5 Technical"
    assert (draft.collected, draft.missing, draft.unknown) == ({"version": "17"}, ["device"], ["since when"])
    assert "The ticket summary is ready" in prompt_text
    assert [c[0] for c in model.calls].count(Classification) == 1


def test_done_with_classify_failing_stays_in_collect(new_path):
    sid = chat()
    collecting(new_path, sid)
    state, prompt_text = new_path(sid, "nothing else", "done", [AIMessage(content="Please send that again.")])
    assert state["phase"] == "collect" and not state["ticket_draft"].classified
    assert "could not be built" in prompt_text and any("classify failed" in c for c in state["corrections"])


def test_change_classifies_again(new_path, model):
    sid = chat()
    confirming(new_path, sid)
    state, prompt_text = new_path(sid, "make the priority high", "change",
                                  [AIMessage(content="Priority is now High. Submit it?")],
                                  groundings=[GOOD.model_copy(update={"priority": "High"})])
    assert state["phase"] == "confirm" and state["ticket_draft"].priority == "High"
    assert "The ticket summary was updated" in prompt_text
    assert [c[0] for c in model.calls].count(Classification) == 2


def test_yes_creates_the_ticket(new_path):
    sid = chat()
    confirming(new_path, sid)
    state, prompt_text = new_path(sid, "submit it", "yes", [AIMessage(content="Created.")])
    number = state["created_tickets"][-1]["ticket_number"]
    assert number.startswith("T-MOCK-") and state["ticket_draft"] is None
    assert (state["phase"], state["pending"]) == ("none", [])
    assert f"The ticket {number} was created." in prompt_text and "Then offer" not in prompt_text


def test_yes_with_a_second_issue_offers_it(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    unanswered(new_path, sid, "Can I export to Excel?")
    new_path(sid, "yes", "accept", [AIMessage(content="Which version do you use?")])
    new_path(sid, "nothing else", "done", [AIMessage(content="Submit it?")], groundings=[GOOD])
    state, prompt_text = new_path(sid, "submit it", "yes", [AIMessage(content="Created. A ticket for the export?")])
    assert state["phase"] == "offer_ticket" and [p.text for p in state["pending"]] == ["Can I export to Excel?"]
    assert "Then offer a ticket for their other issue: Can I export to Excel?" in prompt_text


def test_yes_with_the_ticket_api_failing_stays_in_confirm(new_path, monkeypatch):
    from ole5_chat.tickets import client as ticket_api
    from ole5_chat.tickets.client import TicketApiError
    sid = chat()
    confirming(new_path, sid)

    def broken(*args, **kwargs):
        raise TicketApiError("TicketCreate: HTTP 500")

    monkeypatch.setattr(ticket_api.ticket_client(), "create", broken)
    state, prompt_text = new_path(sid, "submit it", "yes", [AIMessage(content="It could not be created.")])
    assert state["phase"] == "confirm" and state["created_tickets"] == [] and state["ticket_draft"].classified
    assert "The ticket could not be created" in prompt_text


@pytest.mark.parametrize("where", ["collect", "confirm"])
def test_cancel_drops_the_draft_and_the_issue(new_path, where):
    sid = chat()
    if where == "collect":
        collecting(new_path, sid)
    else:
        confirming(new_path, sid)
    state, prompt_text = new_path(sid, "cancel it", "cancel", [AIMessage(content="Cancelled.")])
    assert (state["ticket_draft"], state["pending"], state["phase"]) == (None, [], "none")
    assert "The customer cancelled the ticket." in prompt_text


def test_submit_button_skips_the_router(new_path, model):
    from ole5_chat import llm
    sid = chat()
    confirming(new_path, sid)
    routed = [c for c in model.calls if c[0].__name__ == "RouteDecision"]
    llm.tool_client.replies.append(AIMessage(content="Created."))
    state = sessions.submit(sid)
    assert [c for c in model.calls if c[0].__name__ == "RouteDecision"] == routed
    assert state["created_tickets"] and state["phase"] == "none" and state["ticket_draft"] is None
    assert llm.tool_client.calls[-1][1][-1].content.endswith("the customer pressed Submit.")


def test_a_stale_next_does_not_skip_the_router(new_path, model):
    sid = chat()
    collecting(new_path, sid)
    sessions._graph.update_state(sessions._config(sid), {"next": "yes"}, as_node="validate")
    state, _ = new_path(sid, "version 17", "details", [AIMessage(content="Anything else?")])
    assert state["created_tickets"] == [] and state["phase"] == "collect" and state["ticket_draft"].rounds == 1


@pytest.mark.parametrize("next_", ["details", "done", "change", "cancel", "yes"])
def test_post_check_leaves_ticket_turns_alone(next_):
    from ole5_chat.agent import handlers
    from ole5_chat.agent.contracts import PendingIssue, Reply
    state = {**sessions.get(chat()), "messages": [HumanMessage(content="hmm")], "next": next_, "offered": True,
             "phase": "collect", "pending": [PendingIssue(text="Linux", source="no_answer")],
             "reply": Reply(say="Open a ticket?")}
    assert [p.text for p in handlers.post_check(state)["pending"]] == ["Linux"]


def test_ticket_turns_bind_no_tools_and_send_no_nudge(new_path):
    from ole5_chat import llm
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    start = len(llm.tool_client.bound)
    new_path(sid, "yes", "accept", [AIMessage(content="Which version do you use?")])
    new_path(sid, "version 17", "details", [AIMessage(content="Anything else?")])
    state, _ = new_path(sid, "nothing else", "done", [AIMessage(content="Submit it?")], groundings=[GOOD])
    assert llm.tool_client.bound[start:] == [None, None, None]
    assert "nudged" not in state["corrections"]


def test_a_json_reply_is_retried_as_a_chat_message(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, _ = new_path(sid, "yes", "accept", [AIMessage(content='{"queue": "Ole5 Support", "action": "CreateTicket"}'),
                                               AIMessage(content="Which version do you use?")])
    assert state["messages"][-1].text == "Which version do you use?" and "reply was JSON" in state["corrections"]

