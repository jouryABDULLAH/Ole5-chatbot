"""Search the knowledge base and classify the retrieval outcome."""

import logging

from langchain_core.messages import SystemMessage

from ole5_chat import llm
from ole5_chat.agent.prompts.search import search_prompt
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, Verdict, merge
from ole5_chat.knowledge.tool import make_search_tool
from ole5_chat.knowledge.search import search as knowledge_search
from ole5_chat.agent.state import AgentState

log = logging.getLogger(__name__)

def _search_node(state: AgentState) -> dict:
    # What reaches Search is decided by the Router alone: account-data requests and support-process questions are
    # routed elsewhere before this node runs.
    request = state.get("current_request") or next(
        (str(message.content) for message in reversed(state.get("messages", [])) if message.type == "human"), ""
    )
    if get_settings().kb_retrieval_mode == "direct":
        retrieval = knowledge_search(request, state["identity"].product)
        references = [
            {
                "text": evidence.text,
                "document": evidence.document,
                "section": evidence.section,
                "source_file": evidence.source_file,
                "relation": evidence.relation,
                "score": evidence.score,
                "reason": evidence.reason,
                "chunk_index": evidence.chunk_index,
            }
            for evidence in retrieval.evidence
        ]
        outcome = "failed" if retrieval.error else (
            "answered" if retrieval.direct else "partial" if references else "no_answer"
        )
        log.info("search mode=direct outcome=%s references=%d", outcome, len(references))
        return {
            "current_request": request,
            "current_references": references,
            "search_queries": [request],
            "search_outcome": outcome,
            "search_scope": "product",
            "unresolved_information": None if outcome == "answered" else {
                "request": request, "retrieval": retrieval.as_json()
            },
        }
    sink: list[Retrieval] = []
    tool = make_search_tool(state["identity"].product, sink)
    conversation = [SystemMessage(content=search_prompt({**state, "current_request": request})), *state.get("messages", [])]
    cap = get_settings().kb_max_searches
    log.info("search  request=%r cap=%d", request, cap)
    while len(sink) < cap:
        try:
            reply = llm.invoke(conversation, tool, "auto" if sink else "required")
        except llm.ModelFailed as exc:
            log.error("ERROR search model call failed: %s", exc)
            retrieval = Retrieval(query=request, verdict=Verdict.NOT_IN_KB, error=f"search call failed: {exc}")
            return {
                "current_request": request,
                "current_references": [],
                "search_outcome": "failed",
                "search_scope": "product",
                "unresolved_information": {"request": request, "retrieval": retrieval.as_json()},
            }
        calls = [call for call in reply.tool_calls if call["name"] == tool.name][:cap - len(sink)]
        log.info("search  attempt=%d retrieved=%d tool_calls=%d", len(sink) + 1, len(sink), len(calls))
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
            "chunk_index": evidence.chunk_index,
        }
        for evidence in retrieval.evidence
    ]
    log.info("search  outcome=%s references=%d searches=%d", outcome, len(references), len(sink))
    return {
        "current_request": request,
        "current_references": references,
        # The Search agent's own queries first, then every rephrasing find() tried, so a miss can be replayed.
        "search_queries": list(dict.fromkeys([*(r.query for r in sink), *retrieval.queries_tried])),
        "search_outcome": outcome,
        "search_scope": "product",
        "unresolved_information": None if outcome == "answered" else {"request": request, "retrieval": retrieval.as_json()},
    }


def search_node(state: AgentState) -> dict:
    """Run Search and expose its complete structured result to the orchestrator."""

    result = _search_node(state)
    result["last_worker"] = "search"
    result["search_result"] = {
        "request": result.get("current_request"),
        "outcome": result.get("search_outcome"),
        "references": result.get("current_references", []),
        "scope": result.get("search_scope"),
        "unresolved_information": result.get("unresolved_information"),
    }
    return result




