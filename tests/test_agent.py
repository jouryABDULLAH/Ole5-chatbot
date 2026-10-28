"""The agent with scripted models and searches: the greeting, classify and its value checks, respond_agent's gates, the router's options and prompt, the search node, and the graph's handlers, post_check and pending issues."""

import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm, sessions
from ole5_chat.agent.contracts import Classification
from ole5_chat.agent.tasks import TASKS
from ole5_chat.agent.validate import check_ticket
from ole5_chat.knowledge.contracts import Evidence, Retrieval, Verdict
from ole5_chat.tickets import client as ticket_api
from ole5_chat.values import catalog

# The real llm.structured, taken before the autouse model fixture replaces it with the scripted one.
REAL_STRUCTURED = llm.structured
GOOD = Classification(queue="Ole5 Technical", type="Mobile App Issue", subtype="Application crash",
                      reason="Issue - app crashes on Inbox", priority="Medium", title="App crash",
                      summary="The app crashes on the Inbox screen.", title_customer="تعطل التطبيق",
                      summary_customer="يتعطل التطبيق عند فتح شاشة الوارد.", confidence="high", queue_reason="tech",
                      collected={"screen": "Inbox"}, unknown=["app version"],
                      collected_customer={"الشاشة": "الوارد"}, unknown_customer=["نسخة التطبيق"],
                      required={"page": "Inbox", "error text or screenshot": None})
# The bug kind, every item given.
COMPLETE = GOOD.model_copy(update={"required": {**GOOD.required, "error text or screenshot": "Error 500"},
                                   "unknown": []})


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
    found = GOOD.model_copy(update={"collected": {"screen": "Inbox"}, "unknown": ["device"]})
    model.add(GOOD.model_copy(update={"queue": "Ole5 Helpdesk"}), found)
    draft = classify(drafting())["ticket_draft"]
    assert draft.classified and draft.queue == "Ole5 Technical"
    # missing is derived from the required items not given yet, not written by the model.
    assert (draft.collected, draft.missing, draft.unknown) == ({"screen": "Inbox"}, ["error text or screenshot"],
                                                               ["device"])


def test_classify_refuses_required_keys_that_are_not_a_kind(model):
    from ole5_chat.agent.classify import classify
    model.add(GOOD.model_copy(update={"required": {"colour": None}}), GOOD)
    draft = classify(drafting())["ticket_draft"]
    assert draft.required == GOOD.required and len(model.calls) == 2


def test_classify_with_no_kinds_drops_the_required_keys(model):
    from unittest.mock import patch
    from ole5_chat import products
    from ole5_chat.agent.classify import classify
    config = products.load("OLE5").model_copy(update={"required": {}})
    model.add(GOOD)
    with patch.object(products, "load", lambda code: config):
        draft = classify(drafting())["ticket_draft"]
    assert (draft.required, draft.missing) == ({}, []) and len(model.calls) == 1


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
    """Stands in for llm.tool_client: returns the scripted AIMessages in order and records each call's temperature, messages, the tools bound to it and the tool choice."""

    def __init__(self, *replies):
        self.replies, self.calls, self.bound, self.choices = list(replies), [], [], []

    def __call__(self, temperature):
        self.temperature, self.tools, self.choice = temperature, None, None
        return self

    def bind_tools(self, tools, tool_choice=None):
        self.tools, self.choice = tools, tool_choice
        return self

    def invoke(self, convo):
        self.calls.append((self.temperature, list(convo)))
        self.bound.append(self.tools)
        self.choices.append(self.choice)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def passage(document, relation="DIRECT"):
    return Evidence(text=f"{document} text", document=document, section=None, source_file=None, relation=relation,
                    score=3, reason="")


@pytest.fixture
def respond_node(monkeypatch):
    """Runs the search node then respond_agent on one customer message, with scripted tool-model replies and scripted search results."""
    from ole5_chat import llm
    from ole5_chat.agent import handlers, respond, search
    from ole5_chat.knowledge import tool

    def run(message, replies, retrievals=(), next_="question"):
        found = list(retrievals)
        monkeypatch.setattr(tool, "search", lambda question, product: found.pop(0))
        fake = ToolModel(*replies)
        monkeypatch.setattr(llm, "tool_client", fake)
        state = sessions.get(chat())
        state = {**state, "messages": [*state["messages"], HumanMessage(content=message)], "next": next_}
        state["task"] = handlers.chat(state)["task"] if next_ in ("question", "talk") else None
        searched = search.search(state)
        out = respond.respond_agent({**state, **searched})
        return {**searched, **out}, fake

    return run


# What the search model says once it has its results and searches no more.
DONE = AIMessage(content="")


def searching(*questions):
    return AIMessage(content="", tool_calls=[{"name": "search_knowledge_base", "args": {"question": q}, "id": f"c{i}"}
                                             for i, q in enumerate(questions)])


def answer(status, reply, sources=(), unsupported=()):
    """What respond writes from DIRECT passages, with the sources it names, and the grounding call's verdict on it."""
    from ole5_chat.agent.respond import Answer, Grounding
    return [Answer(status=status, reply=reply, sources=list(sources)), Grounding(unsupported=list(unsupported))]


def hit(*documents, verdict=Verdict.ANSWERABLE):
    return Retrieval(query="q", verdict=verdict, evidence=[passage(d) for d in documents])


def miss():
    return Retrieval(query="q", verdict=Verdict.NOT_IN_KB, evidence=[passage("Other", relation="INFERENTIAL")])


def grounding(model):
    return [c for c in model.calls if c[0].__name__ == "Grounding"]


def sent(fake, n=-1):
    """The messages of the nth tool-model call."""
    return fake.calls[n][1]


def rejected(code=None, generation=""):
    """Groq's 400. With a code it is a rejected generation, the model's output in failed_generation."""
    from groq import BadRequestError
    import httpx
    body = {"error": {"code": code, "message": f"Groq rejected: {code}", "failed_generation": generation}} if code else None
    return BadRequestError("bad tool call", response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                           body=body)


def test_a_question_forces_the_search_and_respond_has_no_tool(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide"]))
    out, fake = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [hit("Guide")])
    assert fake.choices == ["required", "auto"] and fake.bound[0] and fake.bound[1] and len(fake.calls) == 2
    assert "You read a support chat" in sent(fake, 0)[0].content and sent(fake, 1)[-1].type == "tool"
    assert out["reply"].say == "Press Save as draft." and out["last_retrieval"] is not None


def test_respond_agent_keeps_a_partial_answer_that_offers_a_ticket(model, respond_node):
    model.add(*answer("partial", "Save it as a draft from the message screen. Should I open a ticket for how long it is "
                      "kept?", ["Guide"]))
    out, _ = respond_node("How do I save a draft, and how long is it kept?", [searching("How to save a draft?"), DONE],
                          [hit("Guide", verdict=Verdict.PARTIAL)])
    assert out["reply"].say.startswith("Save it as a draft") and out["reply"].sources == ["Guide"]
    assert out["offered"] is True and out["corrections"] == []


def test_respond_agent_cites_the_document_the_reply_names(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide"]))
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [hit("Guide", "Manual")])
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide"]
    assert out["offered"] is False and out["corrections"] == []


def test_a_question_without_a_direct_passage_gets_the_no_answer(model, respond_node):
    out, fake = respond_node("Does it run on Linux?", [searching("Does Ole5 run on Linux?"), DONE,
                                                       AIMessage(content="I have no answer for that. Open a ticket?")],
                             [miss()])
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == []
    assert out["offered"] is True and out["corrections"] == ["no DIRECT passage"] and grounding(model) == []
    assert "You have no answer for this" in sent(fake)[-1].content
    assert "The search for this message returned:" in sent(fake)[-2].content and "(inferential)" in sent(fake)[-2].content
    assert fake.bound[2] is None


def test_respond_agent_sends_the_apology_when_the_model_fails(respond_node):
    out, fake = respond_node("شكراً", [rejected(), rejected()], next_="talk")
    assert out["reply"].say.startswith("عذراً") and out["reply"].sources == [] and out["offered"] is False
    assert any(c.startswith("respond failed:") for c in out["corrections"])
    assert [t for t, _ in fake.calls] == [0, 0.3]


def test_a_failed_search_call_sends_the_apology_not_an_offer(respond_node):
    out, fake = respond_node("Does it run on Linux?", [rejected(), rejected()])
    assert out["reply"].say.startswith("Sorry") and out["offered"] is False and out["corrections"] == ["search failed"]
    assert len(fake.calls) == 2


def sheet_tickets(monkeypatch, rows, states=None):
    """tickets.xlsx holds these rows; the Ticket API gives these states by ticket id, or fails when states is None."""
    from ole5_chat import sheets
    from ole5_chat.tickets import client
    from ole5_chat.tickets.client import TicketApiError

    class Api:
        def get(self, ticket_id):
            if states is None:
                raise TicketApiError("TicketGet: HTTP 500")
            return {"state": states.get(ticket_id)}

    monkeypatch.setattr(sheets, "tickets", lambda: tuple(rows))
    monkeypatch.setattr(client, "ticket_client", lambda: Api())


def ticket_row(email, number="T-1", ticket_id="7"):
    return {"customer_email": email, "ticket_id": ticket_id, "ticket_number": number, "title": "Archive button",
            "product": "OLE5", "created_at": "2026-10-01"}


def test_status_with_no_tickets_says_so_without_a_search_or_an_offer(new_path, monkeypatch):
    from ole5_chat import llm
    sheet_tickets(monkeypatch, [ticket_row("someone@else.com")], {"7": "open"})
    sid = chat()
    state, prompt_text = new_path(sid, "what's the status of my tickets?", "status",
                                  [AIMessage(content="There are no tickets on your account from this chat.")])
    assert "There are no tickets on their account" in prompt_text and llm.tool_client.bound == [None]
    assert "Say how support works: " in prompt_text
    assert state["phase"] == "none" and state["pending"] == [] and state["sources"] == []


def test_status_in_collect_lists_the_tickets_and_goes_back_to_the_draft(new_path, monkeypatch):
    sid = chat()
    before = collecting(new_path, sid)[0]
    email = before["identity"].email
    sheet_tickets(monkeypatch, [ticket_row(email.upper()), ticket_row("someone@else.com", "T-2", "8")], {"7": "open"})
    state, prompt_text = new_path(sid, "وش صار على تذكرتي؟", "status", [AIMessage(content="T-1 مفتوحة.")])
    assert "- T-1, Archive button, 2026-10-01, open" in prompt_text and "T-2" not in prompt_text
    assert "one per line as number, title, date, state" in prompt_text and "never the word unknown" in prompt_text
    assert "Then continue the current ticket: ask again, in one sentence, for the one piece of information" in prompt_text
    assert state["phase"] == "collect" and state["pending"] == before["pending"]
    assert state["ticket_draft"] == before["ticket_draft"]


def test_status_gives_every_ticket_newest_first_with_dates_only(new_path, monkeypatch):
    sid = chat()
    email = sessions.get(sid)["identity"].email
    rows = [{**ticket_row(email, f"T-{n}", str(n)), "created_at": f"2026-10-0{n}T09:00:00+00:00"} for n in range(1, 7)]
    sheet_tickets(monkeypatch, rows, {str(n): "open" for n in range(1, 7)})
    _, prompt_text = new_path(sid, "what's the status of my tickets?", "status", [AIMessage(content="Your tickets.")])
    listed = [line.split(",")[0][2:] for line in prompt_text.splitlines() if line.startswith("- T-")]
    assert listed == ["T-6", "T-5", "T-4", "T-3", "T-2", "T-1"] and "- T-6, Archive button, 2026-10-06, open" in prompt_text
    assert "Today: " in prompt_text and "give the 5 most recent, one line each, and say how many more" in prompt_text
    # What each state means comes from the code, and the hours from chat.yaml, never from the model.
    assert "- New: open, received, not yet picked up by the team" in prompt_text
    assert "- closed successful: closed, resolved and closed" in prompt_text
    assert "give the support hours: من الأحد إلى الخميس" in prompt_text  # in the customer's language


def test_a_ticket_whose_state_cannot_be_read_shows_unknown(new_path, monkeypatch):
    sid = chat()
    sheet_tickets(monkeypatch, [ticket_row(sessions.get(sid)["identity"].email)])
    _, prompt_text = new_path(sid, "what's the status of my tickets?", "status", [AIMessage(content="T-1, unknown.")])
    assert "- T-1, Archive button, 2026-10-01, unknown" in prompt_text


def test_status_when_the_sheet_cannot_be_read_says_try_again(new_path, monkeypatch):
    from ole5_chat import sheets

    def locked():
        raise PermissionError("tickets.xlsx is open in another program")

    monkeypatch.setattr(sheets, "tickets", locked)
    sid = chat()
    state, prompt_text = new_path(sid, "what's the status of my tickets?", "status",
                                  [AIMessage(content="I can't read your tickets right now.")])
    assert "could not be read right now" in prompt_text and state["phase"] == "none"
    assert state["corrections"][-1].startswith("status lookup failed:")


def test_a_talk_turn_has_no_search_and_never_offers(model, respond_node):
    out, fake = respond_node("شكراً", [AIMessage(content="العفو، أنا هنا إن احتجت شيئاً.")], next_="talk")
    assert len(fake.calls) == 1 and fake.bound == [None] and grounding(model) == []
    assert out["reply"].say == "العفو، أنا هنا إن احتجت شيئاً." and out["reply"].sources == []
    assert out["offered"] is False and out["corrections"] == [] and "last_retrieval" not in out


def test_a_question_about_the_assistant_needs_no_search(model, respond_node):
    intro = "أنا مساعد دعم Ole5، أجيب عن أسئلتك حول النظام وأفتح لك تذكرة دعم عند الحاجة."
    out, fake = respond_node("من أنت", [AIMessage(content=intro)], next_="talk")
    assert len(fake.calls) == 1 and out["reply"].say == intro and out["offered"] is False
    assert "This message needs no search" in sent(fake)[0].content


def test_a_declined_direct_passage_is_the_no_answer_without_a_retry(model, respond_node):
    model.add(answer("no_answer", "I have no answer for that. Open a ticket?")[0])
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [hit("Guide")])
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == []
    assert out["offered"] is True and out["corrections"] == [] and grounding(model) == []


def test_respond_agent_keeps_its_own_sources_when_grounding_fails(model, respond_node):
    model.add(answer("answered", "Press Save as draft.", ["Guide"])[0])
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [hit("Guide", "Manual")])
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide"]
    assert out["corrections"] == ["grounding call failed"] and out["offered"] is False


def test_respond_agent_rewrites_an_answer_in_the_wrong_language(model, respond_node):
    model.add(answer("answered", "Press Save as draft.")[0], *answer("answered", "اضغط حفظ كمسودة.", ["Guide"]))
    out, _ = respond_node("كيف أحفظ مسودة؟", [searching("How to save a draft?"), DONE], [hit("Guide")])
    assert out["reply"].say == "اضغط حفظ كمسودة." and any("Arabic" in p for p in model.problems)


def test_respond_agent_keeps_an_answer_still_in_the_wrong_language(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide"]))
    out, _ = respond_node("كيف أحفظ مسودة؟", [searching("How to save a draft?"), DONE], [hit("Guide")])
    assert out["reply"].say == "Press Save as draft." and out["corrections"][0].startswith("language:")


def test_respond_agent_rewrites_a_reply_in_the_wrong_language(respond_node):
    out, fake = respond_node("كيف أحفظ مسودة؟", [searching("drafts"), DONE, AIMessage(content="I have no answer."),
                                               AIMessage(content="ليس لدي إجابة.")], [miss()])
    assert "The customer wrote in Arabic" in sent(fake)[-1].content
    assert out["reply"].say == "ليس لدي إجابة." and out["corrections"] == ["no DIRECT passage", "language retried"]


def test_respond_agent_sends_the_last_reply_after_two_language_retries(respond_node):
    out, fake = respond_node("كيف أحفظ مسودة؟", [searching("drafts"), DONE, *[AIMessage(content="I have no answer.")] * 3],
                             [miss()])
    assert len(fake.calls) == 5 and out["reply"].say == "I have no answer."
    assert out["corrections"][1:3] == ["language retried", "language retried"]
    assert out["corrections"][3].startswith("language:")


def test_respond_agent_rewrites_a_reply_with_chinese_characters(respond_node):
    out, fake = respond_node("شكراً", [AIMessage(content="العفو 保存"), AIMessage(content="العفو")], next_="talk")
    assert "Chinese characters" in sent(fake)[-1].content
    assert out["reply"].say == "العفو" and out["corrections"] == ["language retried"]


def test_a_failed_search_sends_the_apology_not_an_offer(respond_node):
    failed = Retrieval(query="q", verdict=Verdict.NOT_IN_KB, error="ReadTimeout")
    out, fake = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [failed, failed])
    assert out["reply"].say.startswith("Sorry") and out["offered"] is False and out["corrections"] == ["search failed"]
    assert len(fake.calls) == 2


def test_the_search_model_searches_again_after_each_result(model, respond_node):
    model.add(*answer("answered", "Press Save as draft. Drafts are kept 30 days.", ["Guide", "Manual"]))
    out, fake = respond_node("How do I save a draft, and how long is it kept?",
                             [searching("How to save a draft?"), searching("How long are drafts kept?"), DONE],
                             [hit("Guide"), hit("Manual")])
    assert fake.choices == ["required", "auto", "auto"] and sent(fake, 2)[-1].type == "tool"
    assert out["reply"].sources == ["Guide", "Manual"]


def test_a_failed_search_is_tried_once_more(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide"]))
    failed = Retrieval(query="q", verdict=Verdict.NOT_IN_KB, error="tool_use_failed")
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [failed, hit("Guide")])
    assert out["reply"].say == "Press Save as draft." and out["reply"].sources == ["Guide"]


def test_the_search_call_is_retried_warmer(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide"]))
    out, fake = respond_node("How do I save a draft?", [rejected(), searching("How to save a draft?"), DONE], [hit("Guide")])
    assert [t for t, _ in fake.calls] == [0, 0.3, 0]
    assert out["reply"].say == "Press Save as draft." and out["corrections"] == []
    assert len(sent(fake, 1)) == len(sent(fake, 0))  # a 400 without a generation: nothing to send back


def test_a_rejected_generation_is_sent_back_to_the_model(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide"]))
    refusal = rejected("tool_use_failed", "I can't share that.")
    out, fake = respond_node("How do I save a draft?", [refusal, searching("How to save a draft?"), DONE], [hit("Guide")])
    retry = sent(fake, 1)
    assert retry[-2].type == "ai" and retry[-2].content == "I can't share that."
    assert retry[-1].type == "human" and "Groq rejected: tool_use_failed" in retry[-1].content
    assert out["reply"].say == "Press Save as draft." and out["corrections"] == []


def test_structured_sends_a_rejected_generation_back_to_the_model(monkeypatch):
    from ole5_chat.agent.contracts import Reply

    class Client:
        calls = []

        def __init__(self, raises):
            self.raises = raises

        def invoke(self, convo, config=None):
            Client.calls.append(list(convo))
            if self.raises:
                raise rejected("output_parse_failed", "{say: broken")
            return AIMessage(content='{"say": "ok", "sources": []}')

    monkeypatch.setattr(llm, "_client", lambda temperature: Client(raises=temperature == 0))
    result = REAL_STRUCTURED("system", [HumanMessage(content="hi")], Reply)
    assert result.say == "ok" and len(Client.calls) == 2
    assert Client.calls[1][-2].content == "{say: broken" and "output_parse_failed" in Client.calls[1][-1].content


def test_the_search_node_runs_at_most_the_cap(respond_node, monkeypatch):
    from ole5_chat.config import get_settings
    monkeypatch.setenv("KB_MAX_SEARCHES", "1")
    get_settings.cache_clear()
    out, _ = respond_node("How do I save a draft and export it?", [searching("a", "b"), AIMessage(content="No answer.")],
                          [miss()])
    assert out["reply"].say == "No answer." and out["offered"] is True


def test_respond_agent_finds_the_title_inside_the_sources_the_reply_names(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["[Guide] Saving drafts"]))
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [hit("Guide", "Manual")])
    assert out["reply"].sources == ["Guide"] and out["corrections"] == []


def test_a_source_that_is_not_a_direct_title_is_dropped(model, respond_node):
    model.add(*answer("answered", "Press Save as draft.", ["Guide", "Admin handbook"]))
    out, _ = respond_node("How do I save a draft?", [searching("How to save a draft?"), DONE], [hit("Guide")])
    assert out["reply"].sources == ["Guide"] and out["corrections"] == []


def test_an_answer_without_a_source_is_kept_and_logged(model, respond_node):
    model.add(*answer("answered", "Which report do you mean?"))
    out, fake = respond_node("The report doesn't come out", [searching("Report shows no results"), DONE], [hit("Guide")])
    assert out["reply"].say == "Which report do you mean?" and out["reply"].sources == [] and out["offered"] is False
    assert out["corrections"] == ["answered without a source"] and len(grounding(model)) == 1 and len(fake.calls) == 2


def test_respond_agent_removes_unsupported_facts_on_retry(model, respond_node):
    model.add(*answer("answered", "Fill the form with your Tenant ID.", ["Guide"], ["Tenant ID"]),
              *answer("answered", "Fill the requirements form and send it to support.", ["Guide"]))
    out, _ = respond_node("How do I set up SSO?", [searching("How to set up SSO?"), DONE], [hit("Guide")])
    assert out["reply"].say == "Fill the requirements form and send it to support."
    assert out["reply"].sources == ["Guide"] and out["corrections"] == ["retried: unsupported"]


def test_respond_agent_unsupported_facts_twice_give_the_no_answer(model, respond_node):
    model.add(*answer("answered", "Fill the form with your Tenant ID.", ["Guide"], ["Tenant ID"]),
              *answer("answered", "Enter your Tenant ID in the form.", ["Guide"], ["Tenant ID"]))
    out, fake = respond_node("How do I set up SSO?", [searching("How to set up SSO?"), DONE,
                                                      AIMessage(content="I have no answer for that. Open a ticket?")],
                             [hit("Guide")])
    assert "You have no answer for this" in sent(fake)[-1].content
    assert out["reply"].say.startswith("I have no answer") and out["reply"].sources == [] and out["offered"] is True
    assert out["corrections"] == ["retried: unsupported", "reply not grounded"]


def test_a_ticket_turn_never_searches(respond_node):
    out, fake = respond_node("version 17, and how do I save a draft?", [AIMessage(content="When did it start?")],
                             next_="details")
    assert len(fake.calls) == 1 and fake.bound == [None] and out.get("last_retrieval") is None
    assert out["reply"].say == "When did it start?" and out["offered"] is False


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
    for option in ("question", "talk", "ticket", "urgent", "status", "accept", "decline", "details", "done", "cancel", "yes",
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


def test_router_failure_gives_try_again_and_a_correction(model):
    from ole5_chat.agent.router import router
    out = router(router_state())
    from ole5_chat.agent.validate import SORRY
    assert out["next"] is None and out["reply"].say == SORRY["ar"]
    assert out["corrections"][-1].startswith("router failed: nothing scripted")


def test_a_failed_route_sends_try_again_and_changes_nothing(model):
    sid = chat()
    before = sessions.get(sid)
    from ole5_chat.agent.validate import SORRY
    state = sessions.send(sid, "hi")
    assert state["messages"][-1].text == SORRY["ar"] and state["phase"] == before["phase"]
    assert state["pending"] == before["pending"] and state["created_tickets"] == []


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
    """The graph with a scripted router, tool model, searches and structured calls. send() runs one turn and returns
    the state and the system prompt respond_agent was given."""
    from ole5_chat import llm
    from ole5_chat.agent import prompt
    from ole5_chat.agent.contracts import route_schema
    from ole5_chat.knowledge import tool
    found, fake, prompts = [], ToolModel(), []
    monkeypatch.setattr(tool, "search", lambda question, product: found.pop(0))
    monkeypatch.setattr(llm, "tool_client", fake)
    real = prompt.respond
    monkeypatch.setattr(prompt, "respond", lambda state, language: prompts.append(real(state, language)) or prompts[-1])

    def send(sid, message, next_, replies, retrievals=(), groundings=()):
        model.add(route_schema(sessions.get(sid)["phase"])(next=next_), *groundings)
        fake.replies.extend(replies)
        found.extend(retrievals)
        state = sessions.send(sid, message)
        return state, prompts[-1]

    return send


def offered_seen(sid):
    return any(s.values.get("offered") for s in sessions._graph.get_state_history(sessions._config(sid)))


def unanswered(new_path, sid, question, next_="question"):
    return new_path(sid, question, next_, [searching(question), DONE, AIMessage(content="I have no answer. Open a ticket?")],
                    [miss()])


def test_new_path_answered_question_stays_in_none(new_path):
    sid = chat()
    state, _ = new_path(sid, "How do I save a draft?", "question", [searching("How to save a draft?"), DONE],
                        [hit("Guide")], answer("answered", "Press Save as draft.", ["Guide"]))
    assert (state["phase"], state["pending"], state["sources"]) == ("none", [], ["Guide"])
    assert state["messages"][-1].additional_kwargs["sources"] == ["Guide"]


def test_new_path_unanswered_question_is_queued_and_offered(new_path):
    sid = chat()
    state, prompt_text = unanswered(new_path, sid, "Does it run on Linux?")
    assert state["messages"][-1].text == "I have no answer. Open a ticket?" and offered_seen(sid)
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"] and state["pending"][0].source == "no_answer"
    assert state["phase"] == "offer_ticket" and state["offered"] is False and state["task"] is None


def test_new_path_partial_answer_is_cited_and_queued(new_path):
    sid = chat()
    state, _ = new_path(sid, "How do I save a draft, and how long is it kept?", "question", [searching("drafts"), DONE],
                        [hit("Guide")], answer("partial", "Press Save as draft. Open a ticket for the rest?", ["Guide"]))
    assert state["sources"] == ["Guide"] and state["phase"] == "offer_ticket"
    assert [p.text for p in state["pending"]] == ["How do I save a draft, and how long is it kept?"]


def test_new_path_greeting_queues_nothing(new_path):
    sid = chat()
    state, _ = new_path(sid, "مرحبا", "talk", [AIMessage(content="أهلاً بك")])
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
    state, prompt_text = new_path(sid, "ايه", "accept", [AIMessage(content="أي إصدار تستخدم؟")], groundings=[GOOD])
    assert state["ticket_draft"] is not None and state["phase"] == "collect"
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"]
    assert "The customer agreed to a ticket for: Does it run on Linux?" in prompt_text
    # Two required items, one not given yet: the first ask names it after the one-line lead.
    assert "ask for this one thing: error text or screenshot." in prompt_text
    assert state["ticket_draft"].missing == ["error text or screenshot"]


def test_new_path_decline_with_one_issue_empties_pending(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "لا شكراً", "decline", [AIMessage(content="تمام، تقدر تتواصل معنا في أي وقت.")])
    assert (state["phase"], state["pending"]) == ("none", [])
    assert "none was opened. Acknowledge it in one line without saying a ticket was cancelled" in prompt_text
    assert "Say how support works: " in prompt_text and "at any time" not in prompt_text


def test_new_path_decline_with_two_issues_offers_the_next(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = unanswered(new_path, sid, "Can I export to Excel?")
    assert len(state["pending"]) == 2 and state["phase"] == "offer_ticket"
    assert "A ticket offer for this issue is still open: Does it run on Linux?" in prompt_text
    state, prompt_text = new_path(sid, "لا", "decline", [AIMessage(content="تمام. تبغى تذكرة للتصدير؟")])
    assert [p.text for p in state["pending"]] == ["Can I export to Excel?"] and state["phase"] == "offer_ticket"
    assert "This needs the support team: Can I export to Excel?" in prompt_text


def test_new_path_side_question_keeps_the_offer_open(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "How do I save a draft?", "question", [searching("drafts"), DONE], [hit("Guide")],
                                  answer("answered", "Press Save as draft. Still want the ticket?", ["Guide"]))
    assert [p.text for p in state["pending"]] == ["Does it run on Linux?"] and state["phase"] == "offer_ticket"
    assert state["sources"] == ["Guide"]
    assert "A ticket offer for this issue is still open: Does it run on Linux?" in prompt_text


def test_new_path_same_unanswered_question_is_queued_once(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, _ = unanswered(new_path, sid, "Does it run on Linux?")
    assert len(state["pending"]) == 1 and state["phase"] == "offer_ticket"


def test_new_path_an_acknowledgement_is_never_queued(new_path):
    sid = chat()
    new_path(sid, "How do I save a draft?", "question", [searching("drafts"), DONE], [hit("Guide")],
             answer("answered", "Press Save as draft.", ["Guide"]))
    state, _ = new_path(sid, "ايه", "talk", [AIMessage(content="تمام، أي شيء ثاني؟")])
    assert (state["phase"], state["pending"]) == ("none", [])


def collecting(new_path, sid, question="Does it run on Linux?", found=GOOD):
    """An unanswered question, then accept with classify scripted: a draft in collect, the error text still missing."""
    unanswered(new_path, sid, question)
    return new_path(sid, "yes", "accept", [AIMessage(content="Which version do you use?")], groundings=[found])


def confirming(new_path, sid, **found):
    """collecting, then done with classify scripted: the summary shown in confirm."""
    collecting(new_path, sid)
    return new_path(sid, "nothing else", "done", [AIMessage(content="The summary is ready. Submit it?")],
                    groundings=[GOOD.model_copy(update=found)])


def test_accept_with_everything_given_shows_the_summary_at_once(new_path):
    sid = chat()
    state, prompt_text = collecting(new_path, sid, found=COMPLETE)
    assert state["phase"] == "confirm" and state["ticket_draft"].missing == []
    assert "The ticket summary is ready" in prompt_text


def test_accept_with_no_kind_fitting_shows_the_summary_at_once(new_path):
    """A how-to or a request matches no kind: required is empty, nothing is asked."""
    sid = chat()
    state, prompt_text = collecting(new_path, sid, found=GOOD.model_copy(update={"required": {}}))
    assert state["phase"] == "confirm" and state["ticket_draft"].classified
    assert "The ticket summary is ready" in prompt_text


def test_accept_with_a_one_item_kind_asks_directly(new_path):
    sid = chat()
    from unittest.mock import patch
    from ole5_chat import products
    one = GOOD.model_copy(update={"required": {"username": None}})
    config = products.load("OLE5").model_copy(update={"required": {"password": ["username"]}})
    with patch.object(products, "load", lambda code: config):
        state, prompt_text = collecting(new_path, sid, found=one)
    assert state["phase"] == "collect" and "you need this one thing, and ask for it: username." in prompt_text


def test_details_ask_the_next_missing_item_and_settle_unknown_and_declined(new_path):
    sid = chat()
    two_missing = GOOD.model_copy(update={"required": {**GOOD.required, "page": None}})
    collecting(new_path, sid, found=two_missing)
    # "I don't know" settles the page; the next question is the error text.
    state, prompt_text = new_path(sid, "ما أعرف", "details", [AIMessage(content="وش نص الخطأ؟")],
                                  groundings=[GOOD.model_copy(update={"required": {**two_missing.required, "page": "unknown"}})])
    assert state["ticket_draft"].rounds == 1 and state["phase"] == "collect"
    assert prompt_text.count("Ask for this one thing: error text or screenshot.") == 1
    assert "agreed to a ticket" not in prompt_text and state["ticket_draft"].required["page"] == "unknown"
    # A refusal settles the error text; nothing is missing, so the summary is shown.
    state, prompt_text = new_path(sid, "ما أبي أعطيك", "details", [AIMessage(content="الملخص جاهز.")],
                                  groundings=[GOOD.model_copy(update={"required": {"page": "unknown", "error text or screenshot": "declined"}})])
    assert state["phase"] == "confirm" and state["ticket_draft"].required["error text or screenshot"] == "declined"
    assert state["ticket_draft"].missing == []


def test_details_answering_every_item_at_once_shows_the_summary(new_path):
    sid = chat()
    none_given = GOOD.model_copy(update={"required": dict.fromkeys(GOOD.required)})
    collecting(new_path, sid, found=none_given)
    both = GOOD.model_copy(update={"required": {"page": "Inbox", "error text or screenshot": "Error 500"}})
    state, prompt_text = new_path(sid, "Inbox, Error 500", "details", [AIMessage(content="Summary ready.")],
                                  groundings=[both])
    assert state["phase"] == "confirm" and "The ticket summary is ready" in prompt_text


def test_three_rounds_with_items_missing_show_the_summary(new_path):
    sid = chat()
    collecting(new_path, sid)
    for message in ("hmm", "not sure"):
        state, _ = new_path(sid, message, "details", [AIMessage(content="Which version?")], groundings=[GOOD])
        assert state["phase"] == "collect"
    state, prompt_text = new_path(sid, "still no", "details", [AIMessage(content="Summary ready.")], groundings=[GOOD])
    assert state["ticket_draft"].rounds == 3 and state["phase"] == "confirm"
    assert state["ticket_draft"].missing == ["error text or screenshot"] and "The ticket summary is ready" in prompt_text


def test_accept_with_classify_failing_asks_to_send_again(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, prompt_text = new_path(sid, "yes", "accept", [AIMessage(content="Please send that again.")])
    assert state["phase"] == "collect" and state["ticket_draft"] is not None and not state["ticket_draft"].classified
    assert TASKS["retry_summary"] in prompt_text
    # The task says it; classify's note is not sent as well.
    assert "could not be built" not in prompt_text and "anything else they'd like to add" not in prompt_text


def test_done_with_classify_failing_on_a_draft_never_classified_asks_to_send_again(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    new_path(sid, "yes", "accept", [AIMessage(content="Please send that again.")])
    state, prompt_text = new_path(sid, "nothing else", "done", [AIMessage(content="Please send that again.")])
    assert state["phase"] == "collect" and not state["ticket_draft"].classified
    assert TASKS["retry_summary"] in prompt_text and "anything else they'd like to add" not in prompt_text


def test_done_classifies_once_and_shows_the_summary(new_path, model):
    sid = chat()
    state, prompt_text = confirming(new_path, sid, collected={"version": "17"}, unknown=["since when"])
    draft = state["ticket_draft"]
    assert state["phase"] == "confirm" and draft.classified and draft.queue == "Ole5 Technical"
    # done: the required item still not given becomes unknown, in both lists.
    assert (draft.collected, draft.missing, draft.unknown) == ({"version": "17"}, [],
                                                               ["since when", "error text or screenshot"])
    assert draft.required["error text or screenshot"] == "unknown"
    assert "The ticket summary is ready" in prompt_text
    # One classify at accept, one at done.
    assert [c[0] for c in model.calls].count(Classification) == 2


def test_done_with_classify_failing_shows_the_earlier_draft(new_path):
    sid = chat()
    collecting(new_path, sid)
    state, prompt_text = new_path(sid, "nothing else", "done", [AIMessage(content="Please send that again.")])
    # The draft accept classified is shown; what is still missing becomes unknown.
    draft = state["ticket_draft"]
    assert state["phase"] == "confirm" and draft.missing == [] and "error text or screenshot" in draft.unknown
    assert "The ticket summary is ready" in prompt_text and any("classify failed" in c for c in state["corrections"])


def test_change_classifies_again(new_path, model):
    sid = chat()
    confirming(new_path, sid)
    state, prompt_text = new_path(sid, "make the priority high", "change",
                                  [AIMessage(content="Priority is now High. Submit it?")],
                                  groundings=[GOOD.model_copy(update={"priority": "High"})])
    assert state["phase"] == "confirm" and state["ticket_draft"].priority == "High"
    assert "The ticket summary was updated" in prompt_text
    assert [c[0] for c in model.calls].count(Classification) == 3


def test_yes_creates_the_ticket(new_path):
    sid = chat()
    confirming(new_path, sid)
    state, prompt_text = new_path(sid, "submit it", "yes", [AIMessage(content="Created.")])
    created = state["created_tickets"][-1]
    number = created["ticket_number"]
    assert number.startswith("T-MOCK-") and state["ticket_draft"] is None
    assert (state["phase"], state["pending"]) == ("none", [])
    # The card keeps the customer's language; the ticket sent to the API is in English.
    assert (created["title"], created["title_customer"]) == ("App crash", "تعطل التطبيق")
    assert (created["collected_customer"], created["unknown_customer"]) == ({"الشاشة": "الوارد"}, ["نسخة التطبيق"])
    (_, create), (_, update) = ticket_api.ticket_client().calls[-2:]
    assert (create["Ticket"]["Title"], create["Ticket"]["Queue"]) == ("App crash", "Ole5 Technical")
    assert update["Article"]["Body"].startswith(
        "Required: page: Inbox; error text or screenshot: unknown\nCollected: screen: Inbox\n")
    # The creation time in Riyadh time as DD/MM/YYYY HH:MM, and the support hours from chat.yaml.
    assert re.search(rf"The ticket {number} was created on \d\d/\d\d/\d{{4}} \d\d:\d\d \(Asia/Riyadh\)\.", prompt_text)
    assert "reply by email during the support hours: Sunday to Thursday, 09:00 to 18:00 Riyadh time." in prompt_text
    assert "This needs the support team" not in prompt_text


def test_yes_with_a_second_issue_offers_it(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    unanswered(new_path, sid, "Can I export to Excel?")
    new_path(sid, "yes", "accept", [AIMessage(content="Which version do you use?")])
    new_path(sid, "nothing else", "done", [AIMessage(content="Submit it?")], groundings=[GOOD])
    state, prompt_text = new_path(sid, "submit it", "yes", [AIMessage(content="Created. A ticket for the export?")])
    assert state["phase"] == "offer_ticket" and [p.text for p in state["pending"]] == ["Can I export to Excel?"]
    assert "This needs the support team: Can I export to Excel?" in prompt_text


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
    assert "Say how support works: Technical support is through the ticket system only" in prompt_text


def test_submit_button_skips_the_router(new_path, model):
    from ole5_chat import llm
    sid = chat()
    confirming(new_path, sid)
    routed = [c for c in model.calls if c[0].__name__ == "RouteDecision"]
    llm.tool_client.replies.append(AIMessage(content="Created."))
    state = sessions.submit(sid)
    assert [c for c in model.calls if c[0].__name__ == "RouteDecision"] == routed
    assert state["created_tickets"] and state["phase"] == "none" and state["ticket_draft"] is None
    sent_last = llm.tool_client.calls[-1][1]
    assert sent_last[-2].content.endswith("the customer pressed Submit.") and "was created" in sent_last[-1].content


def test_a_stale_next_does_not_skip_the_router(new_path, model):
    sid = chat()
    collecting(new_path, sid)
    sessions._graph.update_state(sessions._config(sid), {"next": "yes"}, as_node="validate")
    state, _ = new_path(sid, "version 17", "details", [AIMessage(content="Anything else?")], groundings=[GOOD])
    assert state["created_tickets"] == [] and state["phase"] == "collect" and state["ticket_draft"].rounds == 1


def test_search_runs_only_on_question_and_respond_never_binds_a_tool(new_path):
    from ole5_chat import llm
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    new_path(sid, "yes", "accept", [AIMessage(content="Which version do you use?")], groundings=[GOOD])
    new_path(sid, "version 17", "details", [AIMessage(content="Anything else?")], groundings=[GOOD])
    new_path(sid, "nothing else", "done", [AIMessage(content="Submit it?")], groundings=[GOOD])
    fake = llm.tool_client
    assert fake.choices == ["required", "auto", None, None, None, None]
    assert [bool(b) for b in fake.bound] == [True, True, False, False, False, False]


def test_a_json_reply_is_retried_as_a_chat_message(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    state, _ = new_path(sid, "yes", "accept", [AIMessage(content='{"queue": "Ole5 Support", "action": "CreateTicket"}'),
                                               AIMessage(content="Which version do you use?")])
    assert state["messages"][-1].text == "Which version do you use?" and "reply was JSON" in state["corrections"]


def test_done_moves_what_is_missing_to_unknown(new_path):
    sid = chat()
    state, _ = confirming(new_path, sid)
    assert state["ticket_draft"].missing == [] and "error text or screenshot" in state["ticket_draft"].unknown


def task_sent(new_path, *args, **kwargs):
    """Runs one turn and returns the last message of respond_agent's first call (the search node's call is skipped)."""
    from ole5_chat import llm
    first = len(llm.tool_client.calls)
    new_path(*args, **kwargs)
    calls = [c for _, c in llm.tool_client.calls[first:] if not c[0].content.startswith("You read a support chat")]
    return calls[0][-1].content


def test_the_task_is_the_last_message_off_the_plain_chat_turn(new_path):
    from ole5_chat.agent.respond import NOTE
    sid = chat()
    plain = task_sent(new_path, sid, "thanks", "talk", [AIMessage(content="You're welcome.")])
    assert plain == "thanks"
    unanswered(new_path, sid, "Does it run on Linux?")
    turns = [
        (("We need to migrate our data", "ticket", [AIMessage(content="After this one.")]), {}, "The customer raised a new issue: We need to migrate our data."),
        (("thanks", "talk", [AIMessage(content="Sure. Still want it?")]), {}, "Answer the customer's message as usual. A ticket offer"),
        (("yes", "accept", [AIMessage(content="Which version?")]), {"groundings": [GOOD]}, "The customer agreed to a ticket"),
        (("version 17", "details", [AIMessage(content="Anything else?")]), {"groundings": [GOOD]}, "Ask for this one thing"),
        (("ok", "talk", [AIMessage(content="Anything else?")]), {},
         "Answer the customer's message as usual. Then continue the current ticket: ask again, in one sentence, for "
         "the one piece of information you last asked for"),
        (("nothing else", "done", [AIMessage(content="Submit it?")]), {"groundings": [GOOD]}, "The ticket summary is ready"),
        (("high priority", "change", [AIMessage(content="Changed. Submit?")]), {"groundings": [GOOD]}, "The ticket summary was updated"),
        (("submit", "yes", [AIMessage(content="Created.")]), {}, "The ticket T-MOCK-"),
    ]
    for args, kwargs, task in turns:
        assert task_sent(new_path, sid, *args, **kwargs).startswith(NOTE + task), task


def test_the_task_follows_the_passages_and_the_no_answer_note_follows_both(new_path):
    from ole5_chat import llm
    from ole5_chat.agent.respond import NOTE
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    unanswered(new_path, sid, "Can I export to Excel?")
    last = llm.tool_client.calls[-1][1]
    assert last[-3].content.startswith(NOTE + "The search for this message returned:")
    assert last[-2].content.startswith(NOTE + "Answer the customer's message as usual. A ticket offer")
    assert last[-1].content.startswith(NOTE + "You have no answer for this")


def test_the_cancel_task_is_the_last_message(new_path):
    from ole5_chat.agent.respond import NOTE
    sid = chat()
    collecting(new_path, sid)
    assert task_sent(new_path, sid, "cancel it", "cancel", [AIMessage(content="Cancelled.")]).startswith(
        NOTE + "The customer cancelled the ticket.")


def test_a_details_turn_without_a_question_does_not_search(new_path):
    from ole5_chat import llm
    sid = chat()
    collecting(new_path, sid)
    first = len(llm.tool_client.calls)
    state, _ = new_path(sid, "version 17", "details", [AIMessage(content="When did it start?")], groundings=[GOOD])
    assert len(llm.tool_client.calls) == first + 1 and state["sources"] == []


URGENT_ISSUE = "nobody in our organization can sign in"


def test_urgent_goes_straight_to_the_summary(new_path, model):
    from ole5_chat.agent.respond import NOTE
    sid = chat()
    last = task_sent(new_path, sid, URGENT_ISSUE, "urgent", [AIMessage(content="It is urgent; the summary is ready.")],
                     groundings=[GOOD])
    state = sessions.get(sid)
    draft = state["ticket_draft"]
    assert state["phase"] == "confirm" and draft.classified and draft.urgent and draft.urgent_reason == URGENT_ISSUE
    assert [p.text for p in state["pending"]] == [URGENT_ISSUE] and state["pending"][0].urgent
    assert last.startswith(NOTE + "This is urgent, so the ticket was prepared right away")
    assert [c[0] for c in model.calls].count(Classification) == 1


def test_urgent_with_classify_failing_is_offered(new_path):
    sid = chat()
    state, prompt_text = new_path(sid, URGENT_ISSUE, "urgent", [AIMessage(content="Urgent. Open a ticket?")])
    assert state["phase"] == "offer_ticket" and state["ticket_draft"] is None
    assert f"This sounds urgent. This needs the support team: {URGENT_ISSUE}." in prompt_text
    assert "could not be built" in prompt_text


def test_urgent_waits_behind_the_current_issue_until_it_is_cancelled(new_path):
    sid = chat()
    collecting(new_path, sid)
    state, prompt_text = new_path(sid, URGENT_ISSUE, "urgent", [AIMessage(content="After this one.")])
    assert state["phase"] == "collect" and [p.text for p in state["pending"]] == ["Does it run on Linux?", URGENT_ISSUE]
    assert "cancel the current one to start it now" in prompt_text
    state, prompt_text = new_path(sid, "cancel the first one", "cancel", [AIMessage(content="Cancelled. Urgent one ready.")],
                                  groundings=[GOOD])
    assert state["phase"] == "confirm" and state["ticket_draft"].urgent and [p.text for p in state["pending"]] == [URGENT_ISSUE]
    assert "The customer cancelled the ticket." in prompt_text and "This is urgent" in prompt_text


def test_submit_starts_the_urgent_issue_waiting(new_path):
    sid = chat()
    collecting(new_path, sid)
    new_path(sid, URGENT_ISSUE, "urgent", [AIMessage(content="After this one.")])
    new_path(sid, "nothing else", "done", [AIMessage(content="Submit it?")], groundings=[GOOD])
    state, prompt_text = new_path(sid, "submit it", "yes", [AIMessage(content="Created. The urgent one is ready.")],
                                  groundings=[GOOD])
    assert len(state["created_tickets"]) == 1 and state["phase"] == "confirm" and state["ticket_draft"].urgent
    assert "was created on " in prompt_text and "This is urgent" in prompt_text


def test_decline_starts_the_urgent_issue_waiting(new_path):
    sid = chat()
    unanswered(new_path, sid, "Does it run on Linux?")
    new_path(sid, URGENT_ISSUE, "urgent", [AIMessage(content="After this one.")])
    state, prompt_text = new_path(sid, "no thanks", "decline", [AIMessage(content="Okay. The urgent one is ready.")],
                                  groundings=[GOOD])
    assert state["phase"] == "confirm" and state["ticket_draft"].urgent and [p.text for p in state["pending"]] == [URGENT_ISSUE]
    assert "This is urgent" in prompt_text


def test_change_keeps_an_urgent_draft_urgent(new_path):
    sid = chat()
    new_path(sid, URGENT_ISSUE, "urgent", [AIMessage(content="Ready.")], groundings=[GOOD])
    state, _ = new_path(sid, "make the priority high", "change", [AIMessage(content="Changed.")],
                        groundings=[GOOD.model_copy(update={"priority": "High", "urgent": False})])
    draft = state["ticket_draft"]
    # An urgent ticket keeps what is missing: it skipped the questions.
    assert draft.urgent and draft.urgent_reason == URGENT_ISSUE and draft.missing == ["error text or screenshot"] and draft.priority == "High"


def test_post_check_never_classifies(model):
    from ole5_chat.agent import handlers
    from ole5_chat.agent.contracts import Reply
    state = {**sessions.get(chat()), "messages": [HumanMessage(content="Does it run on Linux?")], "next": "question",
             "offered": True, "phase": "none", "pending": [], "reply": Reply(say="No answer. Open a ticket?"),
             "last_retrieval": miss().as_json()}
    out = handlers.post_check(state)
    assert out["phase"] == "offer_ticket" and not out["pending"][0].urgent
    assert Classification not in [c[0] for c in model.calls]


@pytest.mark.parametrize("phase", ["offer_ticket", "collect", "confirm"])
def test_a_queued_issue_goes_back_to_the_current_ticket(new_path, phase):
    from ole5_chat.agent.respond import NOTE
    sid = chat()
    if phase == "offer_ticket":
        unanswered(new_path, sid, "Does it run on Linux?")
    elif phase == "collect":
        collecting(new_path, sid)
    else:
        confirming(new_path, sid)
    task = task_sent(new_path, sid, "We need to migrate our data", "ticket", [AIMessage(content="After this one.")])
    ending = {"offer_ticket": "ask whether they would like a ticket for: Does it run on Linux?.",
              "collect": "ask again, in one sentence, for the one piece of information you last asked for about: Does "
                         "it run on Linux?.",
              "confirm": "the summary for Does it run on Linux? is still shown; ask whether to submit it."}[phase]
    assert task.startswith(NOTE + "The customer raised a new issue: We need to migrate our data.") and task.endswith(ending)
    assert "don't offer a ticket for it now" in task and "This needs the support team" not in task
    assert sessions.get(sid)["phase"] == phase


def test_a_chat_turn_in_confirm_goes_back_to_the_submit_question(new_path):
    sid = chat()
    confirming(new_path, sid)
    task = task_sent(new_path, sid, "thanks", "talk", [AIMessage(content="Sure. Submit the summary?")])
    assert task.endswith("the summary for Does it run on Linux? is still shown; ask whether to submit it.")
    assert "what the support team will need" not in task and sessions.get(sid)["phase"] == "confirm"


def test_an_unanswered_question_in_confirm_is_queued_behind_the_summary(new_path):
    sid = chat()
    confirming(new_path, sid)
    state, _ = unanswered(new_path, sid, "how long do replies take?")
    assert state["phase"] == "confirm" and [p.text for p in state["pending"]] == ["Does it run on Linux?",
                                                                                 "how long do replies take?"]
