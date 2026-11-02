"""Append the validated reply and clean current-turn state."""

import logging

from langchain_core.messages import AIMessage

from ole5_chat.agent.state import AgentState

log = logging.getLogger(__name__)


def finalize_node(state: AgentState) -> dict:
    log.info("finalize  route=%s validation=%s refs=%d created=%d", state.get("router_decision"),
             state.get("validation_outcome"), len(state.get("current_references", [])),
             len(state.get("created_tickets", [])))
    result = {
        "messages": [AIMessage(content=state.get("reply") or "", additional_kwargs={
            "sources": state.get("sources", []), "source_details": state.get("source_details", [])})],
        "router_decision": None,
        "input_event": None,
        "route_reason": None,
        "explicit_ticket_request": False,
        "current_request": None,
        "current_references": [],
        "search_outcome": None,
        "search_scope": None,
        "unresolved_information": None,
        "ticket_result": None,
        "ticket_enquiry_result": None,
        "manager_action": None,
        "manager_reason": None,
        "answered_detail": None,
        "grounding_failed": False,
        "reply": None,
        "validation_outcome": None,
        "validation_reason": None,
        "validation_note": None,
        "validation_retries": 0,
        "sources": [],
        "source_details": [],
    }
    if state.get("current_references") and not state.get("grounding_failed") and state.get("validation_outcome") != "fallback":
        result["last_answer_evidence"] = {"evidence": state["current_references"]}
    return result



