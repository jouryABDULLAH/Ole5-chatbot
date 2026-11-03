"""Component-specific context assembly for the graph.

This module only selects and labels inputs. It does not interpret messages,
retrieve evidence or mutate workflow state.
"""

from collections.abc import Iterable

from langchain_core.messages import BaseMessage

from ole5_chat.agent.state import AgentState


def _text(message: BaseMessage) -> str:
    return str(message.content)


def latest_customer_message(messages: Iterable[BaseMessage]) -> str:
    for message in reversed(list(messages)):
        if message.type == "human":
            return _text(message)
    return ""


def previous_assistant_message(messages: Iterable[BaseMessage]) -> str:
    messages = list(messages)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].type == "human":
            for previous in reversed(messages[:index]):
                if previous.type == "ai":
                    return _text(previous)
            break
    return ""


def _router_search_summary(state: AgentState) -> dict | None:
    result = state.get("search_result") or {}
    references = result.get("references") or state.get("current_references", [])
    outcome = result.get("outcome") or state.get("search_outcome")
    scope = result.get("scope") or state.get("search_scope")
    if not outcome and not references and not scope:
        return None
    return {
        "outcome": outcome or "none",
        "reference_count": len(references),
        "scope": scope or "none",
    }


def _router_ticket_operator_summary(state: AgentState) -> dict | None:
    result = state.get("ticket_operator_result")
    if not result:
        return None
    ticket_result = result.get("ticket_result") or {}
    pending = state.get("pending_customer_response")
    return {
        "action": result.get("action") or "none",
        "ticket_created": bool(ticket_result.get("submission_succeeded")),
        "pending_kind": pending.kind if pending is not None else "none",
    }


def _router_ticket_enquiry_summary(state: AgentState) -> dict | None:
    result = state.get("ticket_enquiry_result")
    if not result:
        return None
    tickets = result.get("tickets") or []
    return {
        "action": result.get("action") or "none",
        "lookup_succeeded": result.get("lookup_succeeded"),
        "ticket_count": len(tickets),
        "error": bool(result.get("error")),
    }


def router_context(state: AgentState) -> dict:
    """Inputs Router needs to classify and dispatch the current turn."""

    return {
        "conversation": list(state.get("messages", [])),
        "latest_customer_message": latest_customer_message(state.get("messages", [])),
        "previous_assistant_message": previous_assistant_message(state.get("messages", [])),
        "active_issue": state.get("active_issue"),
        "pending_customer_response": state.get("pending_customer_response"),
        "last_worker": state.get("last_worker"),
        "search_summary": _router_search_summary(state),
        "ticket_operator_summary": _router_ticket_operator_summary(state),
        "ticket_enquiry_summary": _router_ticket_enquiry_summary(state),
        "identity": state.get("identity"),
    }


def search_context(state: AgentState) -> dict:
    """Inputs Search needs; explicit ticket intent is carried in state, not searched."""

    return {
        "conversation": list(state.get("messages", [])),
        "request": state.get("current_request") or latest_customer_message(state.get("messages", [])),
        "identity": state.get("identity"),
    }


def respond_context(state: AgentState) -> dict:
    """Inputs Respond needs after the preceding workflow has produced its result."""

    return {
        "conversation": list(state.get("messages", [])),
        "latest_customer_message": latest_customer_message(state.get("messages", [])),
        "language": state.get("language"),
        "active_issue": state.get("active_issue"),
        "pending_customer_response": state.get("pending_customer_response"),
        "current_references": state.get("current_references", []),
        "last_answer_evidence": state.get("last_answer_evidence"),
        "search_outcome": state.get("search_outcome"),
        "ticket_operator_result": state.get("ticket_operator_result"),
        "ticket_enquiry_result": state.get("ticket_enquiry_result"),
        "manager_action": state.get("manager_action"),
        "created_tickets": state.get("created_tickets", []),
        "manager_reason": state.get("manager_reason"),
        "ticket_result": state.get("ticket_result"),
        "intent": state.get("intent"),
        "response_packet": state.get("response_packet"),
        "route_reason": state.get("route_reason"),
        "explicit_ticket_request": state.get("explicit_ticket_request", False),
    }



