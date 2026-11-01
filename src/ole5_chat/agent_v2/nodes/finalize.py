"""Append the validated reply and clean current-turn state."""

import logging

from langchain_core.messages import AIMessage

from ole5_chat.agent_v2.state import AgentState

log = logging.getLogger(__name__)


def finalize_node(state: AgentState) -> dict:
    log.info("finalize_v2  route=%s validation=%s refs=%d created=%d", state.get("router_decision"),
             state.get("validation_outcome"), len(state.get("current_references", [])),
             len(state.get("created_tickets", [])))
    result = {
        "messages": [AIMessage(content=state.get("reply") or "")],
        "router_decision": None,
        "input_event": None,
        "route_reason": None,
        "explicit_ticket_request": False,
        "current_request": None,
        "current_references": [],
        "search_outcome": None,
        "unresolved_information": None,
        "ticket_result": None,
        "ticket_status_result": None,
        "manager_action": None,
        "manager_reason": None,
        "reply": None,
        "validation_outcome": None,
        "validation_reason": None,
    }
    if state.get("current_references"):
        result["last_answer_evidence"] = {"evidence": state["current_references"]}
    return result
