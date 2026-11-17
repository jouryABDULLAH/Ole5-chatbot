"""Look up the customer's tickets.

A tool agent calls the ticket tools, the interpreter picks the tickets the customer asked about, and code builds the
result from the tool results recorded during the run. A lookup that could not be completed is lookup_failed, never
an empty or missing ticket."""

import json
import logging
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from typing import Annotated, Literal

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import Field

from ole5_chat import llm, products, sheets, values
from ole5_chat.agent.contracts import TicketEnquiryCompletion
from ole5_chat.agent.prompts.ticket_enquiry import (
    ticket_enquiry_structured_prompt,
    ticket_enquiry_tool_prompt,
)
from ole5_chat.agent.state import AgentState
from ole5_chat.tickets.client import TicketApiError, ticket_client

log = logging.getLogger(__name__)

LIST_TOOLS = {"list_tickets", "find_tickets_by_title", "find_tickets_by_date"}
# Model and tool steps of the tool agent: about seven rounds of tool calls (a list, then one get_ticket per ticket),
# then its final message.
TOOL_AGENT_STEPS = 16


def _definitions(state: AgentState) -> list[dict]:
    """Each status with open or closed (values.xlsx) and, when chat.yaml gives one, what it means for the customer."""
    meanings = products.load(state["identity"].product).status_meanings
    return [
        {"value": option.value, "description": option.description,
         **({"meaning": meanings[option.value]} if option.value in meanings else {})}
        for option in values.catalog(state["identity"].product).statuses
    ]


def _rows(state: AgentState) -> list[dict]:
    """The current customer's tickets, and no one else's. The Ticket Operator's check_submitted_tickets reads them too."""
    email = state["identity"].email.strip().casefold()
    return [row for row in sheets.tickets() if str(row.get("customer_email") or "").strip().casefold() == email]


def _summary(row: dict) -> dict:
    created = _created(row)
    return {**{key: str(row.get(key) or "") for key in ("ticket_number", "title", "product")},
            "created_at": created.date().isoformat() if created else str(row.get("created_at") or "")}


def _key(number) -> str:
    """Ticket numbers compare without surrounding spaces and case."""
    return str(number or "").strip().casefold()


def _created(row: dict) -> datetime | None:
    """The creation time as a date; None when the sheet has none or it cannot be read as a date."""
    value = row.get("created_at")
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value or "").strip())
        except ValueError:
            return None
    # Older rows have no time zone; the chatbot writes UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _matches(found: list[dict]) -> str:
    tickets = [_summary(row) for row in found]
    return json.dumps({"outcome": "success" if tickets else "empty", "tickets": tickets}, ensure_ascii=False)


def _shape_problem(name: str, result) -> str | None:
    """What is wrong with a tool result's shape, or None. A malformed result is a failure, never "no tickets"."""
    if not isinstance(result, dict):
        return "the result is not an object"
    outcome = result.get("outcome")
    if name in LIST_TOOLS:
        tickets = result.get("tickets")
        if outcome not in {"success", "empty"} or not isinstance(tickets, list):
            return f"unexpected outcome {outcome!r} or tickets"
        if not all(isinstance(t, dict) and t.get("ticket_number") for t in tickets):
            return "a ticket without a ticket number"
        if (outcome == "empty") != (not tickets):
            return f"outcome {outcome!r} does not match {len(tickets)} tickets"
    elif name == "get_ticket":
        if outcome in {"found", "status_unavailable"}:
            if not (isinstance(result.get("ticket"), dict) and result["ticket"].get("ticket_number")):
                return f"outcome {outcome!r} without a ticket"
        elif outcome != "not_found" or not result.get("ticket_number"):
            return f"unexpected outcome {outcome!r}"
    return None


def _recorded(sink: list):
    """Record each tool call's result, or its error, in sink, so the result is built from what the tools returned
    even when the tool agent stops partway. A tool error or a malformed result still ends the tool agent."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            call = {"name": function.__name__, "args": dict(bound.arguments)}
            try:
                payload = function(*args, **kwargs)
                result = json.loads(payload)
                if problem := _shape_problem(function.__name__, result):
                    raise ValueError(f"malformed result: {problem}")
            except Exception as exc:
                log.error("ERROR ticket_enquiry tool_error name=%s: %s", function.__name__, exc)
                sink.append({**call, "error": f"{function.__name__} failed: {exc}"})
                raise
            log.info("ticket_enquiry tool_result name=%s args=%s outcome=%s", function.__name__, call["args"],
                     result.get("outcome"))
            sink.append({**call, "result": result})
            return payload
        return wrapped
    return decorate


def build_ticket_tools(state: AgentState, sink: list):
    """Build state-bound tools that can only access the current customer's tickets; sink collects their results."""

    api = ticket_client()

    @tool("list_tickets")
    @_recorded(sink)
    def list_tickets(order: Literal["as_stored", "oldest_first", "newest_first"] = "as_stored",
                     limit: Annotated[int, Field(ge=1)] | None = None) -> str:
        """List the customer's tickets with ticket number, title, product and creation time. order is optional: as
        stored, oldest first or newest first by creation time; tickets without a date come last. limit is optional:
        how many tickets to return.

        Call when the customer asks for their tickets, or for their first, oldest, latest or last ticket (limit=1)."""

        found = _rows(state)
        if order != "as_stored":
            dated = sorted((row for row in found if _created(row)), key=_created, reverse=order == "newest_first")
            found = [*dated, *(row for row in found if not _created(row))]
        return _matches(found[:limit] if limit else found)

    @tool("find_tickets_by_title")
    @_recorded(sink)
    def find_tickets_by_title(text: str) -> str:
        """The customer's tickets whose title contains the given words, any order, ignoring case. Titles are in
        English; search with English words for the issue.
        
        Call when the customer refers to a ticket by its issue, without its number."""

        words = text.casefold().split()
        return _matches([row for row in _rows(state) if all(w in str(row.get("title") or "").casefold() for w in words)])

    @tool("find_tickets_by_date")
    @_recorded(sink)
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
    @_recorded(sink)
    def get_ticket(ticket_number: str) -> str:
        """Retrieve one of the customer's tickets by its ticket number (T-...), with its current status.

        Call when the customer provides a ticket number, or after a list or find tool returned the ticket asked about."""

        number = _key(ticket_number)
        return with_status(next((row for row in _rows(state) if _key(row.get("ticket_number")) == number), None),
                           "ticket_number", ticket_number)

    return [list_tickets, find_tickets_by_title, find_tickets_by_date, get_ticket]


def run_ticket_tool_agent(state: AgentState, sink: list) -> dict:
    """Run the tool-calling phase; its tool results go to sink."""

    agent = create_agent(
        model=llm.tool_client(0),
        tools=build_ticket_tools(state, sink),
        system_prompt=ticket_enquiry_tool_prompt(state),
        name="ticket_enquiry_tools",
    )
    return agent.invoke({"messages": list(state.get("messages", []))}, config={"recursion_limit": TOOL_AGENT_STEPS})


def _numbers(sink: list) -> set[str]:
    """Every ticket number a tool returned or was asked about this turn; the only numbers the interpreter may select."""
    numbers = set()
    for call in sink:
        result = call.get("result") or {}
        if call["name"] in LIST_TOOLS:
            numbers |= {_key(t.get("ticket_number")) for t in result.get("tickets") or []}
        elif call["name"] == "get_ticket":
            numbers.add(_key((result.get("ticket") or {}).get("ticket_number") or call["args"].get("ticket_number")))
    return numbers - {""}


def _selection_check(sink: list):
    """The selection must come from this turn's tool results, and a lookup that returned tickets must select some.
    A clarify after get_ticket found a ticket is sent back once; kept a second time, it stands."""
    returned = _numbers(sink)
    found = [call["result"]["ticket"]["ticket_number"] for call in sink
             if call["name"] == "get_ticket" and (call.get("result") or {}).get("ticket")]
    sent_back = []

    def check(completion: TicketEnquiryCompletion) -> str | None:
        # Only a lookup reports tickets from this turn's tools. explain_status refers to a ticket shown earlier, and
        # clarify reports none, so their selection is not checked.
        invented = [n for n in completion.selected_ticket_numbers if _key(n) not in returned]
        if completion.action == "lookup_status" and invented:
            return (f"selected_ticket_numbers {invented} are not in the tool results. Select only numbers the tool "
                    "results give.")
        if completion.action == "lookup_status" and not completion.selected_ticket_numbers and returned:
            return ("selected_ticket_numbers is empty, but the tools returned tickets. Select the tickets the customer "
                    "asked about; for all their tickets, every listed number.")
        if completion.action == "clarify" and found and not sent_back:
            sent_back.append(True)
            return (f"get_ticket found {found}. If that is the ticket the customer asked about, return lookup_status "
                    "and select it. Keep clarify only if their message still leaves several possible tickets.")
        return None

    return check


def _failed(error: str, failed_lookups: list | None = None) -> dict:
    return {"action": "lookup_failed", "outcome": None, "tickets": [], "failed_lookups": failed_lookups or [],
            "error": error, "clarification_question": None}


# Not used: when the interpreter fails, the selection could be rebuilt from the tool results (the get_ticket results,
# or else the listed tickets). Kept off because it can show more tickets than the customer asked for; an interpreter
# failure is lookup_failed instead.
#
# def _selection_from_tools(sink: list) -> TicketEnquiryCompletion:
#     fetched = [call["result"]["ticket"]["ticket_number"] for call in sink
#                if call["name"] == "get_ticket" and (call.get("result") or {}).get("ticket")]
#     listed = [ticket["ticket_number"] for call in sink if call["name"] in LIST_TOOLS
#               for ticket in (call.get("result") or {}).get("tickets") or []]
#     return TicketEnquiryCompletion(action="lookup_status", reason="Selection taken from the tool results.",
#                                    selected_ticket_numbers=list(dict.fromkeys(fetched or listed)))


def _result(state: AgentState, completion: TicketEnquiryCompletion, sink: list, error: str | None) -> dict:
    """The lookup result, from the recorded tool results and the interpreter's selection."""
    listed, fetched, failed = {}, {}, {}
    all_listed = searched = False  # list_tickets succeeded; a find tool succeeded
    for call in sink:
        if "error" in call:
            if call["name"] == "get_ticket":
                number = call["args"].get("ticket_number")
                failed[_key(number)] = {"ticket_number": number, "reason": "failed"}
            continue
        result = call["result"]
        if call["name"] in LIST_TOOLS:
            all_listed |= call["name"] == "list_tickets"
            searched |= call["name"] != "list_tickets"
            listed.update({_key(t.get("ticket_number")): t for t in result.get("tickets") or []})
        elif result.get("ticket"):
            # Found, with its status or without one (status_available false). It replaces an earlier failure.
            fetched[_key(result["ticket"].get("ticket_number"))] = {
                **result["ticket"], "status_available": bool(result.get("status_available"))}
        elif result.get("outcome") == "not_found":
            number = result.get("ticket_number") or call["args"].get("ticket_number")
            failed[_key(number)] = {"ticket_number": number, "reason": "not_found"}

    # Every lookup that did not find its ticket is reported, whatever the interpreter selected.
    failed_lookups = [entry for number, entry in failed.items() if number not in fetched]
    if completion.action == "clarify":
        if error:
            # The lookup stopped partway: that is a failure, not an unclear request.
            return _failed(error, failed_lookups)
        return {"action": "clarify", "outcome": None, "tickets": [], "failed_lookups": failed_lookups,
                "error": None, "clarification_question": completion.clarification_question}

    # A ticket created in this chat also has its title in the customer's language.
    titles = {_key(t.get("ticket_number")): t.get("title_customer") for t in state.get("created_tickets") or []}
    selected = list(dict.fromkeys(_key(n) for n in completion.selected_ticket_numbers))
    tickets = [{**(fetched.get(n) or listed[n]), **({"title_customer": titles[n]} if titles.get(n) else {})}
               for n in selected if n in fetched or n in listed]

    if tickets:
        partial = error or failed_lookups or any(t.get("status_available") is False for t in tickets)
        outcome = "partial" if partial else "found"
    elif failed_lookups and not error and all(f["reason"] == "not_found" for f in failed_lookups):
        outcome = "not_found"
    elif completion.action == "lookup_status" and all_listed and not listed and not error:
        outcome = "empty"  # only list_tickets returning nothing: the customer has no tickets
    elif completion.action == "lookup_status" and searched and not listed and not error:
        outcome = "not_found"  # a title or date search matched nothing; the customer may still have tickets
    elif completion.action == "explain_status":
        # The meaning of a status needs no ticket, so it is still given; a lookup that stopped stays visible.
        outcome = "partial" if error else None
    else:
        return _failed(error or "no lookup returned the requested tickets", failed_lookups)

    result = {"action": completion.action, "outcome": outcome, "tickets": tickets, "failed_lookups": failed_lookups,
              "error": error, "clarification_question": None}
    if completion.action == "lookup_status":
        # Preserve the actual selection operations, not an inferred label based on the customer's words.
        # Keep each operation's full returned set: sorted lists are not necessarily single-ticket lookups.
        result["lookup_basis"] = [
            {"tool": call["name"], "arguments": dict(call["args"]),
             "returned_ticket_numbers": [t["ticket_number"] for t in call["result"]["tickets"]]}
            for call in sink
            if call["name"] in LIST_TOOLS and "error" not in call
            and any(_key(t["ticket_number"]) in selected for t in call["result"]["tickets"])
        ]
    # A lookup gets them too, so the reply explains each status instead of naming it. Was (2026-11-17): only
    # explain_status.
    result["definitions"] = _definitions(state)
    return result


def _ticket_enquiry_node(state: AgentState) -> dict:
    log.info("ticket_enquiry  customer=%s", state["identity"].email)
    sink: list[dict] = []
    error = None
    try:
        run_ticket_tool_agent(state, sink)
    except Exception as exc:
        log.error("ERROR ticket_enquiry tool agent failed: %s", exc)
        error = next((call["error"] for call in reversed(sink) if "error" in call), f"tool agent failed: {exc}")
    if error and not any("result" in call for call in sink):
        # Nothing was retrieved, so there is nothing to interpret.
        return {"ticket_enquiry_result": _failed(error)}

    try:
        completion = llm.structured(
            ticket_enquiry_structured_prompt(state, sink, error),
            [],
            TicketEnquiryCompletion,
            node="ticket_enquiry",
            check=_selection_check(sink),
        )
    except llm.ModelFailed as exc:
        # Includes a lookup whose selection still failed its check: it is not trusted. See _selection_from_tools.
        log.error("ERROR ticket_enquiry result interpretation failed: %s", exc)
        return {"ticket_enquiry_result": _failed(f"result interpretation failed: {exc}")}

    result = _result(state, completion, sink, error)
    log.info("ticket_enquiry completion action=%s outcome=%s tickets=%d failed_lookups=%d reason=%r",
             result["action"], result["outcome"], len(result["tickets"]), len(result["failed_lookups"]),
             completion.reason)
    return {"ticket_enquiry_result": result}


def ticket_enquiry_node(state: AgentState) -> dict:
    """Run Ticket Enquiry and expose its structured result to the orchestrator."""

    result = _ticket_enquiry_node(state)
    result["last_worker"] = "ticket_enquiry"
    return result
