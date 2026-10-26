"""The search node: on a question turn the model writes the searches, one per reply, and code runs them. Every other
turn passes through: a ticket turn is an answer about the ticket, and a question asked during a ticket is routed to
question."""

import logging

from langchain_core.messages import SystemMessage

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.state import ChatState
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, Verdict, merge
from ole5_chat.knowledge.tool import make_search_tool

log = logging.getLogger(__name__)

# Turns answered as a question: the only ones that search and can offer a ticket.
QUESTIONS = ("question",)


def search(state: ChatState) -> dict:
    if state.get("next") not in QUESTIONS:
        return {}
    sink: list[Retrieval] = []
    tool = make_search_tool(state["identity"].product, sink)
    convo = [SystemMessage(content=prompt.search(state)), *state["messages"]]
    cap = get_settings().kb_max_searches
    # The model makes one tool call per reply, so it sees each result and searches again until it stops or hits the cap.
    while len(sink) < cap:
        try:
            reply = llm.invoke(convo, tool, "auto" if sink else "required")
        except llm.ModelFailed as exc:
            log.warning("search  model failed: %s", str(exc)[:300])
            if sink:
                break
            failed = Retrieval(query="", verdict=Verdict.NOT_IN_KB, error=f"search call failed: {exc}")
            return {"last_retrieval": failed.as_json()}
        calls = [c for c in reply.tool_calls if c["name"] == tool.name][:cap - len(sink)]
        if not calls:
            break
        convo.append(reply)
        for call in calls:
            result = tool.invoke(call)
            if sink[-1].error:
                # RAGent2's own model call sometimes fails; one more try usually gets through.
                sink.pop()
                result = tool.invoke(call)
            convo.append(result)
    log.info("search  next=%s searches=%d", state.get("next"), len(sink))
    return {"last_retrieval": merge(sink).as_json() if sink else None}
