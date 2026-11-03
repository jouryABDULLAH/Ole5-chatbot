"""Gather and structure authorized ticket-enquiry information."""

import json
import logging

from langchain.agents import create_agent
from langchain_core.tools import tool

from ole5_chat import llm, sheets, values
from ole5_chat.agent.contracts import TicketEnquiryCompletion
from ole5_chat.agent.prompts.ticket_enquiry import (
    ticket_enquiry_structured_prompt,
    ticket_enquiry_tool_prompt,
)
from ole5_chat.agent.state import AgentState
from ole5_chat.tickets.client import TicketApiError, ticket_client

log = logging.getLogger(__name__)


def _definitions(state: AgentState) -> list[dict]:
    return [
        {"value": option.value, "description": option.description}
        for option in values.catalog(state["identity"].product).statuses
    ]


def build_ticket_tools(state: AgentState):
    """Build state-bound tools that can only access the current customer's tickets."""

    api = ticket_client()
    email = state["identity"].email.casefold()

    @tool("list_tickets")
    def list_tickets() -> str:
        """List the customer's tickets with identifiers and basic metadata."""

        rows = [
            row for row in sheets.tickets()
            if str(row.get("customer_email") or "").casefold() == email
        ]
        tickets = [
            {
                "ticket_number": str(row.get("ticket_number") or ""),
                "title": str(row.get("title") or ""),
                "product": str(row.get("product") or ""),
                "created_at": str(row.get("created_at") or ""),
            }
            for row in rows
        ]
        return json.dumps(
            {"outcome": "success" if tickets else "empty", "tickets": tickets},
            ensure_ascii=False,
        )

    @tool("get_ticket")
    def get_ticket(ticket_number: str) -> str:
        """Retrieve one of the customer's tickets by exact ticket number."""

        rows = [
            row for row in sheets.tickets()
            if str(row.get("customer_email") or "").casefold() == email
        ]
        row = next(
            (
                row for row in rows
                if str(row.get("ticket_number") or "") == ticket_number.strip()
            ),
            None,
        )
        if row is None:
            return json.dumps(
                {"outcome": "not_found", "ticket_number": ticket_number},
                ensure_ascii=False,
            )

        ticket = {
            "ticket_number": str(row.get("ticket_number") or ""),
            "title": str(row.get("title") or ""),
            "product": str(row.get("product") or ""),
            "created_at": str(row.get("created_at") or ""),
        }
        try:
            status = api.get(str(row["ticket_id"]))
        except (TicketApiError, OSError, KeyError) as exc:
            log.warning("ticket_enquiry  status unavailable ticket=%s: %s", ticket_number, exc)
            return json.dumps(
                {
                    "outcome": "status_unavailable",
                    "ticket": ticket,
                    "status_available": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )

        ticket.update(status)
        return json.dumps(
            {
                "outcome": "found",
                "ticket": ticket,
                "status_available": status.get("state") is not None,
            },
            ensure_ascii=False,
        )

    return [list_tickets, get_ticket]


def run_ticket_tool_agent(state: AgentState) -> dict:
    """Run the tool-calling phase and return its complete message transcript."""

    agent = create_agent(
        model=llm.tool_client(0),
        tools=build_ticket_tools(state),
        system_prompt=ticket_enquiry_tool_prompt(state),
        name="ticket_enquiry_tools",
    )
    return agent.invoke({"messages": list(state.get("messages", []))})


def _tool_report(messages: list) -> str:
    report = []
    for message in messages:
        if message.content:
            report.append(
                {
                    "type": message.type,
                    "name": getattr(message, "name", None),
                    "content": message.content,
                    "tool_calls": getattr(message, "tool_calls", None),
                }
            )
    return json.dumps(report, ensure_ascii=False, default=str)


def _compatible_result(
    state: AgentState,
    completion: TicketEnquiryCompletion,
    report: str,
) -> dict:
    """Preserve the result fields consumed by Router, Respond, and Finalize."""

    result = {
        "action": completion.action,
        "reason": completion.reason,
        "selected_ticket_numbers": completion.selected_ticket_numbers,
        "clarification_question": completion.clarification_question,
        "definitions": _definitions(state),
        "tool_report": report,
    }
    if completion.action == "lookup_status":
        entries = json.loads(report)
        lookups = [entry for entry in entries if entry.get("name") == "get_ticket"]
        parsed = []
        for entry in lookups:
            try:
                parsed.append(json.loads(entry.get("content") or "{}"))
            except (TypeError, json.JSONDecodeError):
                log.error("ERROR ticket_enquiry invalid get_ticket result: %r", entry.get("content"))
        result["lookup_succeeded"] = any(
            item.get("outcome") == "found" for item in parsed
        )
        result["tickets"] = [
            item["ticket"] for item in parsed if item.get("ticket") is not None
        ]
    return result


def _ticket_enquiry_node(state: AgentState) -> dict:
    log.info("ticket_enquiry  customer=%s", state["identity"].email)
    try:
        tool_state = run_ticket_tool_agent(state)
        report = _tool_report(tool_state.get("messages", []))
    except Exception as exc:
        log.error("ERROR ticket_enquiry tool agent failed: %s", exc)
        report = json.dumps({"tool_agent_error": str(exc)}, ensure_ascii=False)

    try:
        completion = llm.structured(
            ticket_enquiry_structured_prompt(state, report),
            [],
            TicketEnquiryCompletion,
            node="ticket_enquiry",
        )
    except llm.ModelFailed as exc:
        log.error("ERROR ticket_enquiry result extraction failed: %s", exc)
        return {
            "ticket_enquiry_result": {
                "action": "clarify",
                "reason": "Ticket information could not be interpreted safely.",
                "clarification_question": "Which ticket would you like me to check?",
                "lookup_succeeded": False,
                "definitions": _definitions(state),
            }
        }

    return {"ticket_enquiry_result": _compatible_result(state, completion, report)}


def ticket_enquiry_node(state: AgentState) -> dict:
    """Run Ticket Enquiry and expose its structured result to the orchestrator."""

    result = _ticket_enquiry_node(state)
    result["last_worker"] = "ticket_enquiry"
    return result
