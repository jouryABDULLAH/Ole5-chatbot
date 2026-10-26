"""The hooks inside Ole5 for voice turns, on the real graph with scripted models: the language taken from speech with
no language model call, the progress events, one search and a time limit on it, transport errors answered with the
apology, the prompt nonce and short limits, and the history note and spoken submit used by the confirm step."""

import time

import httpx
import pytest
from groq import APIConnectionError
from langchain_core.messages import AIMessage

from ole5_chat import llm, sessions
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import route_schema
from ole5_chat.agent.validate import Language
from ole5_chat.config import get_settings
from ole5_chat.knowledge import tool
from test_agent import DONE, ToolModel, answer, chat, confirming, hit, miss, searching


@pytest.fixture
def graph(monkeypatch, model):
    """One scripted turn through the graph; same call shape as test_agent's new_path, plus send's keywords."""
    found, fake, languages = [], ToolModel(), []

    def search(question, product):
        result = found.pop(0)
        return result() if callable(result) else result

    monkeypatch.setattr(tool, "search", search)
    monkeypatch.setattr(llm, "tool_client", fake)
    real = prompt.respond
    monkeypatch.setattr(prompt, "respond", lambda state, language: languages.append(language) or real(state, language))

    def turn(sid, message, next_, replies, retrievals=(), groundings=(), **options):
        model.add(route_schema(sessions.get(sid)["phase"])(next=next_), *groundings)
        fake.replies.extend(replies)
        found.extend(retrievals)
        return sessions.send(sid, message, **options), None

    turn.fake, turn.languages, turn.found = fake, languages, found
    return turn


def language_calls(model):
    return [c for c in model.calls if c[0] is Language]


def test_a_voice_turn_takes_the_language_from_speech_and_streams_its_progress(graph, model):
    sid, events = chat(), []
    state, _ = graph(sid, "How do I save a draft?", "question", [searching("save a draft"), DONE], [hit("Guide")],
                     answer("answered", "Press Save as draft.", ["Guide"]), channel="voice", heard_language="en",
                     on_event=events.append)
    assert state["messages"][-1].text == "Press Save as draft." and state["channel"] == "voice"
    assert graph.languages == ["en"] and language_calls(model) == []
    kinds = [e.get("node") or e["type"] for e in events]
    assert kinds.index("router") < kinds.index("search_started") < kinds.index("search_done") < \
        kinds.index("respond_agent") < kinds.index("validate")
    assert {"type": "search_done", "searches": 1, "found": True} in events


def test_a_chat_turn_still_judges_the_language_with_the_model(graph, model):
    sid = chat()
    graph(sid, "How do I save a draft?", "question", [searching("save a draft"), DONE], [hit("Guide")],
          answer("answered", "Press Save as draft.", ["Guide"]))
    assert graph.languages == ["en"] and language_calls(model)
    assert sessions.get(sid)["channel"] == "chat"


def test_the_channel_is_reset_on_every_turn(graph):
    sid = chat()
    graph(sid, "hello", "talk", [AIMessage(content="Hello.")], channel="voice", heard_language="en")
    graph(sid, "thanks", "talk", [AIMessage(content="You're welcome.")])
    assert sessions.get(sid)["channel"] == "chat" and sessions.get(sid)["heard_language"] is None


def test_a_voice_turn_searches_once(graph, model):
    sid, asked = chat(), []
    graph.found.extend([])
    state, _ = graph(sid, "How do I save and export a draft?", "question",
                     [searching("save a draft", "export a draft"), DONE],
                     [lambda: asked.append(1) or hit("Guide"), lambda: asked.append(2) or hit("Export")],
                     answer("answered", "Press Save as draft.", ["Guide"]), channel="voice", heard_language="en")
    assert asked == [1] and state["messages"][-1].text == "Press Save as draft."


def test_a_voice_search_past_the_limit_is_answered_as_not_found_and_offers_a_ticket(graph, monkeypatch):
    monkeypatch.setenv("VOICE_SEARCH_TIMEOUT", "0.3")
    get_settings.cache_clear()
    sid = chat()

    def slow():
        time.sleep(2)
        return hit("Guide")

    started = time.perf_counter()
    state, _ = graph(sid, "How do I save a draft?", "question",
                     [searching("save a draft"), AIMessage(content="I could not find that. Shall I open a ticket?")],
                     [slow], channel="voice", heard_language="en")
    assert time.perf_counter() - started < 1.8
    assert state["messages"][-1].text == "I could not find that. Shall I open a ticket?"
    assert state["phase"] == "offer_ticket" and "search failed" not in state["corrections"]


def test_a_voice_search_runs_in_the_turns_context(graph):
    sid, seen = chat(), []
    graph(sid, "How do I save a draft?", "question", [searching("save a draft"), DONE],
          [lambda: seen.append(llm.voice_active()) or hit("Guide")], answer("answered", "Press Save as draft.", ["Guide"]),
          channel="voice", heard_language="en")
    assert seen == [True]


def test_voice_searches_use_the_lighter_profile_and_chat_the_usual_one(monkeypatch):
    from ole5_chat.knowledge import retriever, search
    calls = []
    monkeypatch.setenv("KB_BACKEND", "ragent2")
    get_settings.cache_clear()
    monkeypatch.setattr(retriever, "retrieve", lambda question, tenant, voice=False: calls.append(voice) or miss())
    search.search("q", "OLE5")
    with llm.voice_turn():
        search.search("q", "OLE5")
    assert calls == [False, True]


def test_a_groq_timeout_is_answered_with_the_apology_without_a_retry(graph):
    sid = chat()
    down = APIConnectionError(request=httpx.Request("POST", "https://api.groq.com"))
    state, _ = graph(sid, "How do I save a draft?", "question", [down], channel="voice", heard_language="en")
    assert state["messages"][-1].text.startswith("Sorry") and len(graph.fake.calls) == 1


def test_voice_model_calls_get_a_fresh_first_line_a_short_timeout_and_no_retries(monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    convo = [SystemMessage(content="You are Ole5."), HumanMessage(content="hi")]
    assert llm.prepare(convo) == convo
    chat_client = llm.tool_client(0)
    with llm.voice_turn():
        first, second = llm.prepare(convo)[0].content, llm.prepare(convo)[0].content
        voice_client = llm.tool_client(0)
    assert first != second and first.startswith("request ") and first.endswith("\n\nYou are Ole5.")
    assert (voice_client.request_timeout, voice_client.max_retries) == (15.0, 0)
    assert chat_client.max_retries != 0 and not llm.voice_active()


def test_a_note_adds_the_exchange_to_the_history(graph):
    sid = chat()
    state = sessions.note(sid, "تمام", "للتأكيد: هل أرسل التذكرة الآن؟")
    assert [(m.type, m.text) for m in state["messages"][-2:]] == [("human", "تمام"), ("ai", "للتأكيد: هل أرسل التذكرة الآن؟")]


def test_a_spoken_submit_keeps_the_words_and_skips_the_router(graph, model):
    sid = chat()
    confirming(graph, sid)
    routed = len([c for c in model.calls if c[0].__name__ == "RouteDecision"])
    before = len(language_calls(model))
    graph.fake.replies.append(AIMessage(content="تم إنشاء التذكرة."))
    state = sessions.submit(sid, channel="voice", said="تمام", heard_language="ar")
    assert state["created_tickets"] and state["phase"] == "none"
    assert len([c for c in model.calls if c[0].__name__ == "RouteDecision"]) == routed
    assert [(m.type, m.text) for m in state["messages"][-2:]] == [("human", "تمام"), ("ai", "تم إنشاء التذكرة.")]
    assert graph.languages[-1] == "ar" and len(language_calls(model)) == before
