"""Interpret a ticket-enquiry request and perform the authorized lookup."""

import logging

from ole5_chat import llm, sheets, values
from ole5_chat.agent.contracts import TicketStatusDecision
from ole5_chat.agent.prompts.ticket_enquiry import ticket_enquiry_prompt
from ole5_chat.agent.state import AgentState
from ole5_chat.tickets import status as ticket_api_status
from ole5_chat.tickets.client import TicketApiError, ticket_client

log = logging.getLogger(__name__)


def _definitions(state: AgentState) -> list[dict]:
    return [
        {"value": option.value, "description": option.description}
        for option in values.catalog(state["identity"].product).statuses
    ]


def _ticket_enquiry_node(state: AgentState) -> dict:
    log.info("ticket_enquiry  customer=%s", state["identity"].email)
    try:
        decision = llm.structured(
            ticket_enquiry_prompt(state), list(state.get("messages", [])),
            TicketStatusDecision, node="ticket_enquiry"
        )
    except llm.ModelFailed as exc:
        log.error("ERROR ticket_enquiry model call failed: %s", exc)
        return {"ticket_enquiry_result": {"action": "clarify", "reason": "Status interpretation failed; ask for a ticket reference."}}

    if decision.action == "explain_status":
        log.info("ticket_enquiry  action=explain_status")
        return {
            "ticket_enquiry_result": {
                "action": decision.action,
                "reason": decision.reason,
                "definitions": _definitions(state),
            }
        }
    if decision.action == "clarify":
        log.info("ticket_enquiry  action=clarify reason=%r", decision.reason)
        return {"ticket_enquiry_result": {"action": decision.action, "reason": decision.reason}}
    try:
        found = ticket_api_status.my_tickets(state["identity"].email, ticket_client())
    except (TicketApiError, OSError, KeyError) as exc:
        log.error("ERROR ticket_enquiry lookup failed: %s", exc)
        return {"ticket_enquiry_result": {"action": "lookup_status", "lookup_succeeded": False, "error": str(exc)}}
    log.info("ticket_enquiry  action=lookup_status tickets=%d", len(found))
    return {
        "ticket_enquiry_result": {
            "action": decision.action,
            "reason": decision.reason,
            "lookup_succeeded": True,
            "tickets": sorted(found, key=lambda ticket: ticket.get("created_at", ""), reverse=True),
            "definitions": _definitions(state),
        }
    }


def ticket_enquiry_node(state: AgentState) -> dict:
    """Run Ticket Enquiry and expose its structured result to the orchestrator."""

    result = _ticket_enquiry_node(state)
    result["ticket_enquiry_result"] = result.get("ticket_enquiry_result")
    return result



