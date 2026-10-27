"""The search node: on a question turn the model writes the searches, one per reply, and code runs them. Every other
turn passes through: a ticket turn is an answer about the ticket, and a question asked during a ticket is routed to
question."""

import contextvars
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.config import get_stream_writer

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.state import ChatState
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, Verdict, merge
from ole5_chat.knowledge.tool import make_search_tool

log = logging.getLogger(__name__)
# Voice turns run a search here so it can be cut off at voice_search_timeout; RAGent2 itself cannot be cancelled.
_voice_searches = ThreadPoolExecutor(max_workers=4, thread_name_prefix="voice-search")

# Turns answered as a question: the only ones that search and can offer a ticket.
QUESTIONS = ("question",)


def _emit(event: dict) -> None:
    """A progress event for a caller streaming the graph (the voice channel). A no-op under invoke or outside a graph."""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer(event)


def _search_within(tool, call: dict, sink: list[Retrieval], seconds: float):
    """Runs one search, giving up after seconds. A search still running then is one that has found nothing direct
    (measured 2026-10-26: answerable questions came back in 20-28 s, the rest ran 33-84 s), so it is recorded as not
    found and the reply offers a ticket (D28). The search runs in the turn's context: it reads the voice flag."""
    future = _voice_searches.submit(contextvars.copy_context().run, tool.invoke, call)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout:
        log.warning("search  voice search cut off after %.0f s, answered as not found", seconds)
        sink.append(Retrieval(query=call["args"].get("question", ""), verdict=Verdict.NOT_IN_KB,
                              elapsed_ms=int(seconds * 1000), warnings=[f"no passage within {seconds:.0f} s"]))
        return ToolMessage(content=json.dumps({"status": "no_result", "detail": "nothing found in time"}),
                           tool_call_id=call["id"])


def search(state: ChatState) -> dict:
    if state.get("next") not in QUESTIONS:
        return {}
    voice = state.get("channel") == "voice"
    settings = get_settings()
    _emit({"type": "search_started"})
    sink: list[Retrieval] = []
    tool = make_search_tool(state["identity"].product, sink)
    convo = [SystemMessage(content=prompt.search(state)), *state["messages"]]
    # A voice turn searches once and within a time limit: every RAGent2 search takes 17-20 s while the customer waits.
    cap = settings.voice_kb_max_searches if voice else settings.kb_max_searches
    # The model makes one tool call per reply, so it sees each result and searches again until it stops or hits the cap.
    while len(sink) < cap:
        try:
            reply = llm.invoke(convo, tool, "auto" if sink else "required")
        except llm.ModelFailed as exc:
            log.warning("search  model failed: %s", str(exc)[:300])
            if sink:
                break
            failed = Retrieval(query="", verdict=Verdict.NOT_IN_KB, error=f"search call failed: {exc}")
            _emit({"type": "search_done", "searches": 0})
            return {"last_retrieval": failed.as_json()}
        calls = [c for c in reply.tool_calls if c["name"] == tool.name][:cap - len(sink)]
        if not calls:
            break
        convo.append(reply)
        for call in calls:
            if voice:
                convo.append(_search_within(tool, call, sink, settings.voice_search_timeout))
                continue
            result = tool.invoke(call)
            if sink[-1].error:
                # RAGent2's own model call sometimes fails; one more try usually gets through.
                sink.pop()
                result = tool.invoke(call)
            convo.append(result)
    log.info("search  next=%s searches=%d", state.get("next"), len(sink))
    merged = merge(sink) if sink else None
    # found: the knowledge base had something to answer from (the voice channel says so while the answer is written).
    _emit({"type": "search_done", "searches": len(sink),
           "found": bool(merged and merged.verdict != Verdict.NOT_IN_KB and not merged.error)})
    return {"last_retrieval": merged.as_json() if merged else None}
