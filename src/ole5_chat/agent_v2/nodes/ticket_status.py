"""Interpret a ticket-status request and perform the authorized lookup."""

import logging

from ole5_chat import llm, sheets, values
from ole5_chat.agent_v2.contracts import TicketStatusDecision
from ole5_chat.agent_v2.prompts.ticket_status import ticket_status_prompt
from ole5_chat.agent_v2.state import AgentState
from ole5_chat.tickets import status as ticket_status
from ole5_chat.tickets.client import TicketApiError, ticket_client

log = logging.getLogger(__name__)


def _definitions(state: AgentState) -> list[dict]:
    return [
        {"value": option.value, "description": option.description}
        for option in values.catalog(state["identity"].product).statuses
    ]


def ticket_status_node(state: AgentState) -> dict:
    log.info("ticket_status_v2  customer=%s", state["identity"].email)
    try:
        decision = llm.structured(
            ticket_status_prompt(state), list(state.get("messages", [])),
            TicketStatusDecision, node="ticket_status_v2"
        )
    except llm.ModelFailed as exc:
        log.error("ERROR ticket_status_v2 model call failed: %s", exc)
        return {"ticket_status_result": {"action": "clarify", "reason": "Status interpretation failed; ask for a ticket reference."}}

    if decision.action == "explain_status":
        log.info("ticket_status_v2  action=explain_status")
        return {
            "ticket_status_result": {
                "action": decision.action,
                "reason": decision.reason,
                "definitions": _definitions(state),
            }
        }
    if decision.action == "clarify":
        log.info("ticket_status_v2  action=clarify reason=%r", decision.reason)
        return {"ticket_status_result": {"action": decision.action, "reason": decision.reason}}
    try:
        found = ticket_status.my_tickets(state["identity"].email, ticket_client())
    except (TicketApiError, OSError, KeyError) as exc:
        log.error("ERROR ticket_status_v2 lookup failed: %s", exc)
        return {"ticket_status_result": {"action": "lookup_status", "lookup_succeeded": False, "error": str(exc)}}
    log.info("ticket_status_v2  action=lookup_status tickets=%d", len(found))
    return {
        "ticket_status_result": {
            "action": decision.action,
            "reason": decision.reason,
            "lookup_succeeded": True,
            "tickets": sorted(found, key=lambda ticket: ticket.get("created_at", ""), reverse=True),
            "definitions": _definitions(state),
        }
    }
