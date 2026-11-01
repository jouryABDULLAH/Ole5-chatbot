"""Search the knowledge base and classify the retrieval outcome."""

import logging

from langchain_core.messages import SystemMessage

from ole5_chat import llm
from ole5_chat.agent_v2.prompts.search import search_prompt
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, Verdict, merge
from ole5_chat.knowledge.tool import make_search_tool
from ole5_chat.agent_v2.state import AgentState

log = logging.getLogger(__name__)


def search_node(state: AgentState) -> dict:
    request = state.get("current_request") or next(
        (str(message.content) for message in reversed(state.get("messages", [])) if message.type == "human"), ""
    )
    sink: list[Retrieval] = []
    tool = make_search_tool(state["identity"].product, sink)
    conversation = [SystemMessage(content=search_prompt({**state, "current_request": request})), *state.get("messages", [])]
    cap = get_settings().kb_max_searches
    log.info("search_v2  request=%r cap=%d", request, cap)
    while len(sink) < cap:
        try:
            reply = llm.invoke(conversation, tool, "auto" if sink else "required")
        except llm.ModelFailed as exc:
            log.warning("search_v2 model failed: %s", exc)
            retrieval = Retrieval(query=request, verdict=Verdict.NOT_IN_KB, error=f"search call failed: {exc}")
            return {
                "current_request": request,
                "current_references": [],
                "search_outcome": "failed",
                "unresolved_information": {"request": request, "retrieval": retrieval.as_json()},
            }
        calls = [call for call in reply.tool_calls if call["name"] == tool.name][:cap - len(sink)]
        log.info("search_v2  attempt=%d retrieved=%d tool_calls=%d", len(sink) + 1, len(sink), len(calls))
        if not calls:
            break
        conversation.append(reply)
        for call in calls:
            result = tool.invoke(call)
            conversation.append(result)
    retrieval = merge(sink) if sink else Retrieval(query=request, verdict=Verdict.NOT_IN_KB)
    if retrieval.error:
        outcome = "failed"
    elif retrieval.direct:
        outcome = "answered"
    elif retrieval.verdict == Verdict.PARTIAL:
        outcome = "partial"
    else:
        outcome = "no_answer"
    references = [
        {
            "text": evidence.text,
            "document": evidence.document,
            "section": evidence.section,
            "source_file": evidence.source_file,
            "relation": evidence.relation,
            "score": evidence.score,
            "reason": evidence.reason,
        }
        for evidence in retrieval.evidence
    ]
    log.info("search_v2  outcome=%s references=%d searches=%d", outcome, len(references), len(sink))
    return {
        "current_request": request,
        "current_references": references,
        "search_outcome": outcome,
        "unresolved_information": None if outcome == "answered" else {"request": request, "retrieval": retrieval.as_json()},
    }
