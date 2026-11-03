"""Search the knowledge base and classify the retrieval outcome."""

import logging
import re

from langchain_core.messages import SystemMessage

from ole5_chat import llm
from ole5_chat.agent.scope import is_user_specific_request
from ole5_chat.agent.prompts.search import search_prompt
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, Verdict, merge
from ole5_chat.knowledge.tool import make_search_tool
from ole5_chat.knowledge.search import search as knowledge_search
from ole5_chat.agent.state import AgentState

log = logging.getLogger(__name__)

_OUTSIDE_PRODUCT_SCOPE = re.compile(
    r"\b(?:what can you do|what do you do|your capabilities|ticketing process|support ticket work|"
    r"support hours|how do i contact support|where will the reply|ticket status|my account|my permissions)\b",
    re.IGNORECASE)


def _search_node(state: AgentState) -> dict:
    request = state.get("current_request") or next(
        (str(message.content) for message in reversed(state.get("messages", [])) if message.type == "human"), ""
    )
    if is_user_specific_request(request):
        log.info("search  skipped: customer-specific data is outside chat scope") # Change
        return {
            "current_request": request,
            "current_references": [],
            "search_outcome": "no_answer",
            "unresolved_information": {"request": request, "outside_scope": True},
            "route_reason": "Customer-specific account or user data is outside the chat scope.",
            "search_scope": "outside_scope",
        }
    if _OUTSIDE_PRODUCT_SCOPE.search(request):
        log.info("search scope=outside_product no_search=true request=%r", request)
        return {
            "current_request": request,
            "current_references": [],
            "search_outcome": "no_answer",
            "search_scope": "outside_scope",
            "unresolved_information": {"request": request, "outside_scope": True},
            "route_reason": "The request is outside the product knowledge-base scope.",
        }
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




