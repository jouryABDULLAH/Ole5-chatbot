"""Gather and structure authorized ticket-enquiry information."""

import json
import logging
from typing import Literal

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


def _rows(state: AgentState) -> list[dict]:
    """The current customer's tickets, and no one else's. The Ticket Operator's check_submitted_tickets reads them too."""
    email = state["identity"].email.casefold()
    return [row for row in sheets.tickets() if str(row.get("customer_email") or "").casefold() == email]


def _summary(row: dict) -> dict:
    return {key: str(row.get(key) or "") for key in ("ticket_number", "title", "product", "created_at")}


def _matches(found: list[dict]) -> str:
    tickets = [_summary(row) for row in found]
    return json.dumps({"outcome": "success" if tickets else "empty", "tickets": tickets}, ensure_ascii=False)


def build_ticket_tools(state: AgentState):
    """Build state-bound tools that can only access the current customer's tickets."""

    api = ticket_client()

    @tool("list_tickets")
    def list_tickets(order: Literal["as_stored", "oldest_first", "newest_first"] = "as_stored") -> str:
        """List all the customer's tickets with ticket number, title, product and creation time. order is optional:
        as stored, oldest first or newest first by creation time.

        Call when the customer asks for their tickets, or for their first, oldest, latest or last ticket."""

        found = _rows(state)
        if order != "as_stored":
            found = sorted(found, key=lambda row: str(row.get("created_at") or ""), reverse=order == "newest_first")
        return _matches(found)

    @tool("find_tickets_by_title")
    def find_tickets_by_title(text: str) -> str:
        """The customer's tickets whose title contains the given words, any order, ignoring case. Titles are in
        English; search with English words for the issue.
        
        Call when the customer refers to a ticket by its issue, without its number."""

        words = text.casefold().split()
        return _matches([row for row in _rows(state) if all(w in str(row.get("title") or "").casefold() for w in words)])

    @tool("find_tickets_by_date")
    def find_tickets_by_date(date_from: str, date_to: str | None = None) -> str:
        """The customer's tickets created on a day (YYYY-MM-DD), or from date_from to date_to inclusive.
        
        Call when the customer refers to a ticket by when it was created, without its number."""

        last = date_to or date_from
        return _matches([row for row in _rows(state) if date_from <= str(row.get("created_at") or "")[:10] <= last])

    def with_status(row: dict | None, key: str, value: str) -> str:
        if row is None:
            return json.dumps({"outcome": "not_found", key: value}, ensure_ascii=False)
        ticket = _summary(row)
        try:
            status = api.get(str(row["ticket_id"]))
        except (TicketApiError, OSError, KeyError) as exc:
            log.warning("ticket_enquiry  status unavailable ticket=%s: %s", ticket["ticket_number"], exc)
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

    @tool("get_ticket")
    def get_ticket(ticket_number: str) -> str:
        """Retrieve one of the customer's tickets by its exact ticket number (T-...), with its current status.
        
        Call when the customer provides a ticket number, or after a list or find tool returned the ticket asked about."""

        number = ticket_number.strip()
        return with_status(next((row for row in _rows(state) if str(row.get("ticket_number") or "") == number), None),
                           "ticket_number", ticket_number)

    return [list_tickets, find_tickets_by_title, find_tickets_by_date, get_ticket]


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
        tool_calls = getattr(message, "tool_calls", None)
        if message.content or tool_calls:
            report.append(
                {
                    "type": message.type,
                    "name": getattr(message, "name", None),
                    "content": message.content,
                    "tool_calls": tool_calls,
                }
            )
    return json.dumps(report, ensure_ascii=False, default=str)


def _log_tool_activity(report: str) -> None:
    for entry in json.loads(report):
        for call in entry.get("tool_calls") or []:
            log.info(
                "ticket_enquiry tool_call name=%s args=%s",
                call.get("name"),
                call.get("args") or {},
            )
        if entry.get("name") in {"list_tickets", "find_tickets_by_title", "find_tickets_by_date", "get_ticket"}:
            try:
                result = json.loads(entry.get("content") or "{}")
            except (TypeError, json.JSONDecodeError):
                log.error("ERROR ticket_enquiry invalid tool result name=%s", entry.get("name"))
                continue
            log.info(
                "ticket_enquiry tool_result name=%s outcome=%s ticket=%s",
                entry.get("name"),
                result.get("outcome"),
                (result.get("ticket") or {}).get("ticket_number"),
            )


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
    entries = json.loads(report)
    listed = [entry for entry in entries
              if entry.get("name") in {"list_tickets", "find_tickets_by_title", "find_tickets_by_date"}]
    list_results = []
    for entry in listed:
        try:
            list_results.append(json.loads(entry.get("content") or "{}"))
        except (TypeError, json.JSONDecodeError):
            log.error("ERROR ticket_enquiry invalid list_tickets result")
    result["list_succeeded"] = any(
        item.get("outcome") in {"success", "empty"} for item in list_results
    )

    if completion.action == "lookup_status":
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
        if not lookups:
            # A list or find request with no get_ticket: the listed tickets are the result, without a status.
            result["tickets"] = [ticket for item in list_results for ticket in item.get("tickets") or []]
            result["lookup_succeeded"] = bool(result["tickets"])
        result["lookup_outcomes"] = [item.get("outcome") for item in parsed]
        log.info(
            "ticket_enquiry completion action=%s list_succeeded=%s lookup_succeeded=%s tickets=%d",
            completion.action,
            result["list_succeeded"],
            result["lookup_succeeded"],
            len(result["tickets"]),
        )
    return result


def _ticket_enquiry_node(state: AgentState) -> dict:
    log.info("ticket_enquiry  customer=%s", state["identity"].email)
    try:
        tool_state = run_ticket_tool_agent(state)
        report = _tool_report(tool_state.get("messages", []))
        _log_tool_activity(report)
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
