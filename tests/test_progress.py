"""Verify progress is visible during node execution, not after it."""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from ole5_chat.agent import session
from ole5_chat.agent.state import AgentState


def test_progress_is_set_before_search_and_uses_state_language(monkeypatch):
    seen = []

    def router(state):
        return {"language": "ar"}

    def search(state):
        seen.append(session.progress("progress-test"))
        return {}

    builder = StateGraph(AgentState)
    builder.add_node("router", router)
    builder.add_node("search", search)
    builder.add_edge(START, "router")
    builder.add_edge("router", "search")
    builder.add_edge("search", END)
    monkeypatch.setattr(session, "_graph", builder.compile(checkpointer=MemorySaver()))
    session._run("progress-test", {"language": "en"}, "en")
    assert seen[0]["key"] == "searching"
    assert seen[0]["language"] == "ar"
    assert seen[0]["ar"] == "أفكر…"
    assert session.progress("progress-test")["key"] == "done"


def test_progress_stream_emits_sse_events_and_ignores_previous_terminal_value(monkeypatch):
    monkeypatch.setattr(session, "_state", lambda session_id: {})
    session._set_progress("sse-test", "done", "en")
    stream = session.progress_stream("sse-test")
    session._set_progress("sse-test", "searching", "en")
    first = next(stream)
    assert first.startswith("data: ")
    assert '"key": "searching"' in first
    session._set_progress("sse-test", "done", "en")
    final = next(stream)
    assert '"key": "done"' in final
    stream.close()
