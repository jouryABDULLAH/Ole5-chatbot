"""Ole5's voice mode on the real graph with scripted models (CP2): the confirm step read back from the draft in the
reply's language and ending on the fixed closing words, the graph-side yes guard (D4) on spoken turns only, a change
said back as what really changed, the failure paths, the created task's number sentence, and the voice sections of
the respond and router prompts."""

import pytest
from langchain_core.messages import AIMessage

from ole5_chat import llm, sessions
from ole5_chat.agent import prompt, tasks
from ole5_chat.agent.contracts import route_schema
from ole5_chat.knowledge import tool
from ole5_chat.tickets import client as ticket_api
from ole5_chat.voice import rules
from test_agent import DONE, GOOD, ToolModel, chat, miss, searching, sheet_tickets, ticket_row

URGENT_ISSUE = "ولا موظف عندنا يقدر يسجل دخول"


@pytest.fixture
def graph(monkeypatch, model):
    """turn(sid, message, next_, replies, retrievals=(), groundings=(), **send options) -> (state, task). The task is
    the one respond_agent was given this turn."""
    found, fake, prompts, routers = [], ToolModel(), [], []
    monkeypatch.setattr(tool, "search", lambda question, product: found.pop(0))
    monkeypatch.setattr(llm, "tool_client", fake)
    real_respond, real_router = prompt.respond, prompt.router
    monkeypatch.setattr(prompt, "respond", lambda state, language: prompts.append((state, real_respond(state, language)))
                        or prompts[-1][1])
    monkeypatch.setattr(prompt, "router", lambda state: routers.append(real_router(state)) or routers[-1])

    def turn(sid, message, next_, replies, retrievals=(), groundings=(), **options):
        model.add(route_schema(sessions.get(sid)["phase"])(next=next_), *groundings)
        fake.replies.extend(replies)
        found.extend(retrievals)
        state = sessions.send(sid, message, **options)
        return state, prompts[-1][0].get("task") if prompts else None

    turn.fake, turn.prompts, turn.routers = fake, prompts, routers
    return turn


def spoken(language="en"):
    return {"channel": "voice", "heard_language": language}


def to_confirm(turn, sid, language="en", reply=None, **found):
    """An unanswered question, accept, then done with classify scripted: the summary read back in confirm."""
    say = (lambda en, ar: ar if language == "ar" else en)
    turn(sid, say("Does it run on Linux?", "هل يعمل على لينكس؟"), "question",
         [searching("Linux"), AIMessage(content=say("I have no answer. Open a ticket?", "لا أملك إجابة. هل أفتح تذكرة؟"))],
         [miss()], **spoken(language))  # a voice turn searches once, so no DONE reply is asked for
    turn(sid, say("yes", "نعم"), "accept", [AIMessage(content=say("Which version do you use?", "ما الإصدار الذي تستخدمه؟"))],
         **spoken(language))
    return turn(sid, say("nothing else", "لا شيء آخر"), "done",
                [AIMessage(content=reply or say("The summary is ready. Submit it?", "الملخص جاهز. هل أرسله؟"))],
                groundings=[GOOD.model_copy(update=found)], **spoken(language))


# ---- the read-back ----

def test_the_voice_confirm_step_reads_back_the_draft_and_ends_on_the_closing_words(graph):
    sid = chat()
    state, task = to_confirm(graph, sid)
    assert state["phase"] == "confirm"
    assert "(App crash. The app crashes on the Inbox screen.)" in task and "Medium" in task
    assert "without labels" in task
    assert task.endswith(f'end with exactly these words: "{tasks.VOICE_CLOSE["en"]}" Don\'t say the ticket is '
                         "created, opened or being opened.")
    assert "نعم أرسلها" not in task  # only the reply's language


def test_the_arabic_read_back_has_the_arabic_priority_and_closing_words_only(graph):
    sid = chat()
    _, task = to_confirm(graph, sid, language="ar", priority="High")
    assert "High (in Arabic: عالية)" in task and tasks.VOICE_CLOSE["ar"] in task
    assert "(The app crashes on the Inbox screen.)" in task and "App crash" not in task  # no English title in Arabic
    assert "yes, submit" not in task and "formal Arabic" in task


def test_the_closing_question_cannot_be_answered_by_its_own_echo():
    for language, close in tasks.VOICE_CLOSE.items():
        question = close.split(". ")[-1] if language == "en" else close.split(". ")[-1]
        assert question.rstrip().endswith(("?", "؟")) and not rules.explicit_submit(question), language
        assert not rules.explicit_submit(rules.line("just_confirm", language).split(". ")[-1])


def test_text_chat_keeps_the_confirm_task_unchanged(graph):
    from test_agent import confirming

    def chat_turn(sid, message, next_, replies, retrievals=(), groundings=()):
        return graph(sid, message, next_, replies, retrievals, groundings)
    sid = chat()
    _, task = confirming(chat_turn, sid)
    assert task == tasks.TASKS["confirm"].format(issue="Does it run on Linux?")


def test_an_urgent_issue_on_voice_is_read_back_at_once(graph):
    sid = chat()
    state, task = graph(sid, URGENT_ISSUE, "urgent", [AIMessage(content="هذا عاجل. الملخص جاهز. هل تريد إرسالها الآن؟")],
                        groundings=[GOOD.model_copy(update={"priority": "Critical"})], **spoken("ar"))
    assert state["phase"] == "confirm" and state["ticket_draft"].urgent
    assert task.startswith("This is urgent") and "Critical (in Arabic: حرجة)" in task and tasks.VOICE_CLOSE["ar"] in task
    assert "marked urgent" not in task  # said once, by the opening sentence


# ---- the guard ----

@pytest.mark.parametrize("said", ["نعم أرسلها، شكراً", "Yes, submit. Thank you.", "Yes, go ahead and submit it",
                                  "اي والله ارسلها"])
def test_a_spoken_explicit_yes_creates_the_ticket(graph, said):
    sid = chat()
    to_confirm(graph, sid)
    state, task = graph(sid, said, "yes", [AIMessage(content="Created.")], **spoken())
    assert state["created_tickets"] and "was created" in task


def test_a_spoken_yes_the_router_guessed_is_asked_again_and_nothing_is_created(graph):
    sid = chat()
    to_confirm(graph, sid)
    state, task = graph(sid, "yes but make it high", "yes", [AIMessage(content="It has not been changed yet.")],
                        **spoken())
    assert state["created_tickets"] == [] and state["phase"] == "confirm" and not state["reconfirmed"]
    assert "nothing on the summary was changed" in task and tasks.VOICE_CLOSE["en"] in task
    assert "voice yes not explicit: yes but make it high" in state["corrections"]


def test_an_acknowledgement_the_router_took_for_yes_gets_the_fixed_line(graph):
    sid = chat()
    to_confirm(graph, sid)
    line = rules.line("just_confirm", "en")
    state, task = graph(sid, "okay, alright, thanks", "yes", [AIMessage(content=line)], **spoken())
    assert state["created_tickets"] == [] and state["reconfirmed"] is True and line in task


def test_text_chat_and_typed_voice_messages_are_not_guarded(graph):
    sid = chat()
    to_confirm(graph, sid)
    created = AIMessage(content="تم إنشاء التذكرة.")
    state, _ = graph(sid, "تمام كذا", "yes", [created], channel="voice", heard_language=None)
    assert state["created_tickets"]
    sid = chat()
    to_confirm(graph, sid)
    state, _ = graph(sid, "تمام كذا", "yes", [created])  # the text chat
    assert state["created_tickets"]


def test_a_second_acknowledgement_after_just_to_confirm_submits_without_the_guard(graph):
    sid = chat()
    to_confirm(graph, sid)
    graph.fake.replies.append(AIMessage(content="Created."))
    state = sessions.submit(sid, channel="voice", said="okay", heard_language="en")
    assert state["created_tickets"] and state["confirmed"] is True
    graph(sid, "thanks", "talk", [AIMessage(content="You're welcome.")], **spoken())
    assert sessions.get(sid)["confirmed"] is False  # every send resets it


# ---- created, failures, changes ----

def test_the_voice_created_task_gives_the_number_its_own_sentence(graph):
    sid = chat()
    to_confirm(graph, sid)
    state, task = graph(sid, "yes, submit", "yes", [AIMessage(content="Created.")], **spoken())
    number = state["created_tickets"][0]["ticket_number"]
    assert f'written exactly "Your ticket number is {number}."' in task and task.endswith("Nothing more.")


def test_a_voice_submit_failure_is_short_and_stops_inviting_after_the_second(graph, monkeypatch):
    sid = chat()
    to_confirm(graph, sid)

    def down(*args, **kwargs):
        raise ticket_api.TicketApiError("the ticket system is down")
    from ole5_chat.tickets.mock import MockTicketClient
    monkeypatch.setattr(MockTicketClient, "create", down)
    _, first = graph(sid, "yes, submit", "yes", [AIMessage(content="It could not be created.")], **spoken())
    _, second = graph(sid, "yes, submit", "yes", [AIMessage(content="It can't be created now.")], **spoken())
    assert "to try again" in first and "Don't ask them to submit" in second and tasks.VOICE_CLOSE["en"] not in first
    assert sessions.get(sid)["ticket_draft"].submit_failures == 2


def test_a_new_tickets_first_failure_is_not_called_a_second_one(graph, monkeypatch):
    from ole5_chat.tickets.mock import MockTicketClient
    real = MockTicketClient.create
    calls = {"n": 0}

    def flaky(self, ticket, article):
        calls["n"] += 1
        if calls["n"] in (1, 3):
            raise ticket_api.TicketApiError("the ticket system is down")
        return real(self, ticket, article)
    monkeypatch.setattr(MockTicketClient, "create", flaky)
    sid = chat()
    to_confirm(graph, sid)
    graph(sid, "yes, submit", "yes", [AIMessage(content="It could not be created.")], **spoken())
    state, _ = graph(sid, "yes, submit", "yes", [AIMessage(content="Created.")], **spoken())
    assert state["created_tickets"]
    to_confirm(graph, sid)
    _, task = graph(sid, "yes, submit", "yes", [AIMessage(content="It could not be created.")], **spoken())
    assert "to try again" in task and "again." not in task.split(".")[0]


def test_on_voice_the_urgent_sentence_comes_before_the_closing_words(graph):
    sid = chat()
    to_confirm(graph, sid)
    _, task = graph(sid, URGENT_ISSUE, "urgent", [AIMessage(content="سأتعامل معها بعد هذه.")], **spoken("ar"))
    assert task.index("cancel the current one") < task.index(tasks.VOICE_CLOSE["ar"]) and task.endswith(
        f'"{tasks.VOICE_CLOSE["ar"]}"')


def test_on_voice_a_status_question_in_confirm_goes_back_on_the_closing_words(graph, monkeypatch):
    sid = chat()
    to_confirm(graph, sid)
    sheet_tickets(monkeypatch, [ticket_row(sessions.get(sid)["identity"].email)], {"7": "open"})
    state, task = graph(sid, "what happened to my old ticket?", "status", [AIMessage(content="T-1 is open.")],
                        **spoken())
    assert "- T-1, Archive button, 2026-10-01, open" in task and state["phase"] == "confirm"
    assert task.endswith(f'"{tasks.VOICE_CLOSE["en"]}"')


def test_a_typed_message_in_voice_mode_gets_tasks_in_its_own_language(graph):
    sid = chat()
    to_confirm(graph, sid, language="ar")
    state, task = graph(sid, "make the priority high please", "change", [AIMessage(content="The priority is now High.")],
                        groundings=[GOOD.model_copy(update={"priority": "High"})], channel="voice", heard_language=None)
    assert tasks.VOICE_CLOSE["en"] in task and tasks.VOICE_CLOSE["ar"] not in task and "formal Arabic" not in task


def test_a_voice_change_says_what_really_changed(graph):
    sid = chat()
    to_confirm(graph, sid)
    state, task = graph(sid, "make the priority high", "change", [AIMessage(content="Priority is now High.")],
                        groundings=[GOOD.model_copy(update={"priority": "High"})], **spoken())
    assert state["ticket_draft"].priority == "High" and "priority Medium -> High" in task
    assert "as the customer asked" not in task and "say that part was not changed" in task
    _, task = graph(sid, "make it critical", "change", [AIMessage(content="That could not be changed.")],
                    groundings=[GOOD.model_copy(update={"priority": "High"})], **spoken())
    assert "nothing on it changed" in task and "already reads that way" in task and "priority High" in task


def test_a_reworded_description_is_reported_as_rewritten_not_as_done(graph):
    sid = chat()
    to_confirm(graph, sid)
    _, task = graph(sid, "make it critical", "change", [AIMessage(content="The priority was not changed.")],
                    groundings=[GOOD.model_copy(update={"title": "App crashes when opening Inbox"})], **spoken())
    assert "the description was rewritten" in task and "priority" not in task.split("only in:")[1].split(".")[0]
    assert "say that part was not changed" in task


def test_a_voice_change_that_fails_to_classify_does_not_invite_a_submit(graph):
    sid = chat()
    to_confirm(graph, sid)
    state, task = graph(sid, "make the priority high", "change", [AIMessage(content="The change could not be made.")],
                        **spoken())  # nothing scripted for classify: it fails
    assert state["phase"] == "confirm" and "could not be made" in task and "Don't ask them to submit" in task


# ---- prompts and questions ----

def test_the_voice_prompts_get_their_sections_only_on_voice(graph):
    sid = chat()
    graph(sid, "hello", "talk", [AIMessage(content="Hello.")])
    assert prompt.VOICE_STYLE not in graph.prompts[-1][1] and prompt.VOICE_ROUTER not in graph.routers[-1]
    graph(sid, "hello again", "talk", [AIMessage(content="Hello.")], **spoken())
    assert prompt.VOICE_STYLE in graph.prompts[-1][1] and prompt.VOICE_ROUTER in graph.routers[-1]
    graph(sid, "typed in voice mode", "talk", [AIMessage(content="Hello.")], channel="voice", heard_language=None)
    assert prompt.VOICE_STYLE in graph.prompts[-1][1] and prompt.VOICE_ROUTER not in graph.routers[-1]


def test_voice_accept_and_details_ask_one_or_two_things(graph):
    sid = chat()
    graph(sid, "Does it run on Linux?", "question",
          [searching("Linux"), AIMessage(content="I have no answer. Open a ticket?")], [miss()], **spoken())
    _, task = graph(sid, "yes", "accept", [AIMessage(content="Which version?")], **spoken())
    assert task.endswith(tasks.VOICE_FEW_QUESTIONS)
    _, task = graph(sid, "version 17", "details", [AIMessage(content="When did it start?")], **spoken())
    assert task.endswith(tasks.VOICE_FEW_QUESTIONS)
    _, task = graph(sid, "yesterday", "details", [AIMessage(content="Anything else?")], **spoken())
    assert tasks.VOICE_FEW_QUESTIONS not in task  # the open question stays one question
