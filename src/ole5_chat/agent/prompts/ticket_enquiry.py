"""Prompts for the Ticket Enquiry tool agent and result interpretation phase."""

import json

from ole5_chat import products, values
from ole5_chat.agent.context import router_context
from ole5_chat.agent.state import AgentState


def _context(state: AgentState) -> tuple[object, dict, str]:
    config = products.load(state["identity"].product)
    context = router_context(state)
    statuses = "; ".join(
        f"{option.value}: {option.description or 'no approved explanation'}"
        for option in values.catalog(state["identity"].product).statuses
    ) or "none"
    return config, context, statuses


def ticket_enquiry_tool_prompt(state: AgentState) -> str:
    config, context, statuses = _context(state)
    return f"""You are the Ticket Enquiry component for {config.name}. Interpret the latest customer message and choose one status action. Do not answer the customer and do not change ticket preparation.

Gather the information needed to answer the customer's latest ticket question. Tools:
- get_ticket: one ticket by its exact ticket number, with its status;
- list_tickets: all the customer's tickets; order is optional (as stored, oldest first, newest first);
- find_tickets_by_title: tickets whose title contains given words (titles are in English);
- find_tickets_by_date: tickets created on a day or in a date range.
When the customer refers to a ticket without its number (the first or latest one, one about an issue, one from a
date), find it with list_tickets or a find tool, then call get_ticket with its exact ticket number.
"First", "oldest", "latest" or "last" ticket («اول تذكرة», «اقدم تذكرة», «آخر تذكرة») means among all the
customer's tickets: use list_tickets with that order and limit=1. For example «هلا وش اقدم تذكرة عندي» or "hi, what
is my oldest ticket?" → oldest_first, limit=1. Only when the customer limits it to this chat
(«في هذه المحادثة», "in this chat"), take it from the tickets created in this chat below, then call get_ticket for it.
When the customer asks for their available tickets or what tickets they have, call list_tickets.
Use no tool when the customer asks only for the meaning of an already identified status.
Ask for clarification only when the requested ticket cannot be identified safely.
If the previous assistant already reported that a ticket number was not found and the
customer only confirms that same number, do not request the same identifier again.
Return a final clarification for the customer to provide another number or describe the issue here.

Historical ticket lookup does not automatically create or offer a new ticket.
An unavailable status must remain unavailable.

Approved status definitions: {statuses}
Latest customer message: {context['latest_customer_message'] or 'none'}
Previous assistant message: {context['previous_assistant_message'] or 'none'}
Tickets created in this chat: {state.get('created_tickets') or 'none'}
Use them only to identify a ticket from this chat that the customer refers to, or when they limit the question to
this chat. Otherwise, for their tickets or for the first, oldest, latest or last one, use the tools.

Call only the provided ticket tools. Do not produce a customer-facing answer."""


def ticket_enquiry_structured_prompt(state: AgentState, tool_results: list[dict], error: str | None) -> str:
    config, context, statuses = _context(state)
    stopped = (f"\nThe lookup stopped with an error after these results ({error}). Select only from the results "
               "above; code reports the rest as not retrieved.\n" if error else "")
    return f"""You are the Ticket Enquiry result interpreter for {config.name}.

Read the customer's latest message and the tool results, and say which tickets the customer asked about.
Do not answer the customer and do not create or offer a new ticket.

Choose action:
- lookup_status: ticket data was requested or retrieved;
- explain_status: the customer asks what an identified status means;
- clarify: the requested ticket cannot be identified safely.

A ticket named by position (first, oldest, latest, last), by its issue or by its date is identified once the tools
return it: the customer does not need to give its number. Use clarify only when the results still leave several
possible tickets.

selected_ticket_numbers: every ticket number the customer asked about, exactly as the tool results give it,
including a number get_ticket did not find. For all their tickets, every listed number. For the first, oldest,
latest or last one, only that one. Only numbers in the tool results can be selected.
A ticket that get_ticket found but whose status is unavailable (status_available false) is still identified:
return lookup_status with its ticket number, not clarify.
Include a clarification question only when action is clarify.
If the previous assistant already reported a missing ticket and the customer only confirms
the same identifier, use a final clarification instead of repeating the same request.

Approved status definitions: {statuses}
Latest customer message: {context['latest_customer_message'] or 'none'}
Previous assistant message: {context['previous_assistant_message'] or 'none'}

Tool results, in the order they ran:
{json.dumps(tool_results, ensure_ascii=False, default=str)}
{stopped}
Return JSON only with action, reason, selected_ticket_numbers, and clarification_question."""


# Compatibility for modules that still import the previous prompt name.
ticket_enquiry_prompt = ticket_enquiry_tool_prompt
