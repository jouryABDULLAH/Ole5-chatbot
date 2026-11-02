"""Interpret and advance the ticket-preparation workflow.

This step owns ticket state transitions, but not ticket API calls. Classification
reuses the existing validated catalog/classification helper until that helper is
ported into the graph.
"""

import logging

from ole5_chat import llm, values
from ole5_chat.support_core import classify as classifier
from ole5_chat.support_core.contracts import PendingIssue
from ole5_chat.support_core.state import ChatState, TicketDraft
from ole5_chat.agent.contracts import MANAGER_ACTIONS, ManagerContext, manager_schema
from ole5_chat.agent.prompts.ticket_operator import manager_prompt
from ole5_chat.agent.state import AgentState
from ole5_chat.identity import Identity
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets import submit as tickets
from ole5_chat.tickets.client import TicketApiError

log = logging.getLogger(__name__)


def _latest_customer(state: AgentState) -> str:
    return next((str(message.content) for message in reversed(state.get("messages", [])) if message.type == "human"), "")


def _context(state: AgentState) -> ManagerContext:
    pending = state.get("pending_customer_response")
    return pending.kind if pending else "none"


def _issue_id(state: AgentState) -> str:
    used = {str(issue.get("id")) for issue in state.get("pending_issues", [])}
    if state.get("active_issue"):
        used.add(str(state["active_issue"].get("id")))
    number = 1
    while f"issue-{number}" in used:
        number += 1
    return f"issue-{number}"


def _issue(state: AgentState, text: str, source: str, urgent: bool = False) -> dict:
    return {"id": _issue_id(state), "description": text, "source": source, "urgent": urgent}


def _draft(state: AgentState) -> TicketDraft:
    value = state.get("ticket_draft")
    if value:
        return TicketDraft.model_validate(value)
    return TicketDraft(product=values.catalog(state["identity"].product).service)


def _legacy_state(state: AgentState, draft: TicketDraft) -> ChatState:
    issue = state.get("active_issue") or {"description": _latest_customer(state), "source": "no_answer"}
    pending = [PendingIssue(
        text=issue.get("description", ""),
        urgent=bool(issue.get("urgent")),
        source="always_ticket" if issue.get("source") in {"always_ticket", "urgent"} else "no_answer",
    )]
    identity = state.get("identity")
    return ChatState(
        messages=list(state.get("messages", [])), identity=identity,
        ticket_draft=draft, corrections=[], attachments=state.get("attachments", []),
        phase="collect", pending=pending,
    )


def _classify(state: AgentState, draft: TicketDraft) -> TicketDraft | None:
    log.info("ticket_operator  classify issue=%r", (state.get("active_issue") or {}).get("description"))
    result = classifier.classify(_legacy_state(state, draft))
    classified = result.get("ticket_draft")
    log.info("ticket_operator  classify result=%s", "ok" if isinstance(classified, TicketDraft) else "failed")
    return classified if isinstance(classified, TicketDraft) else None


def _response(instruction: str, **extra) -> dict:
    return {"ticket_result": {"response_instruction": instruction}, **extra}


def _pending(kind: str, issue: dict, item: str | None = None):
    from ole5_chat.agent.contracts import PendingCustomerResponse
    return PendingCustomerResponse(kind=kind, issue_id=issue["id"], item=item)


def _summary(state: AgentState, issue: dict, draft: TicketDraft, instruction: str) -> dict:
    return _response(
        instruction,
        active_issue=issue,
        ticket_draft=draft.model_dump(),
        pending_customer_response=_pending("summary", issue),
        summary_shown=True,
        submission_requested=True,
    )


def _prepare(state: AgentState, issue: dict, *, skip_collection: bool = False, urgent: bool = False) -> dict:
    draft = _classify(state, _draft(state))
    if draft is None:
        return _response(
            "The ticket summary could not be prepared. Ask the customer to send the last message again.",
            active_issue=issue,
            pending_customer_response=_pending("offer", issue),
        )
    if urgent:
        draft = draft.model_copy(update={"urgent": True, "urgent_reason": draft.urgent_reason or issue["description"]})
    missing = [key for key, value in draft.required.items() if value is None]
    if skip_collection or urgent or not missing:
        instruction = (
            "Because this is an urgent situation, prepare the ticket directly without the usual "
            "ticket offer. Show the customer the summary and ask for confirmation before submission."
            if urgent else
            f"The ticket summary is ready. Ask whether to submit it for: {issue['description']}."
        )
        return _summary(
            state, issue, draft, instruction
        )
    item = missing[0]
    return _response(
        f"Ask the customer for this one required detail: {item}.",
        active_issue=issue,
        ticket_draft=draft.model_dump(),
        pending_customer_response=_pending("detail", issue, item),
    )


def _activate_next(state: AgentState, queue: list[dict]) -> dict:
    """Activate the next issue and supply the exact next interaction to Respond."""
    if not queue:
        return {
            "active_issue": None,
            "pending_issues": [],
            "ticket_draft": None,
            "pending_customer_response": None,
            "summary_shown": False,
            "submission_requested": False,
        }
    issue = queue[0]
    update = {
        "active_issue": issue,
        "pending_issues": queue[1:],
        "ticket_draft": None,
        "pending_customer_response": _pending("offer", issue),
        "summary_shown": False,
        "submission_requested": False,
    }
    instruction = f"Next, explicitly name the next queued issue: {issue['description']}. Ask whether the customer wants a support ticket prepared for that issue."
    if issue.get("urgent"):
        prepared = _prepare({**state, **update}, issue, urgent=True)
        if prepared.get("summary_shown"):
            instruction = (
                f"Next, explicitly name the next queued urgent issue: {issue['description']}. "
                "Explain that its summary is ready because it is urgent, and ask whether to submit it. "
                "No ticket has been created for this next issue yet."
            )
        else:
            instruction = (
                f"Next, explicitly name the next queued urgent issue: {issue['description']}. "
                "Explain that its summary could not be prepared and ask whether to retry preparing its support ticket."
            )
        update.update(prepared)
    return {**update, **_response(instruction)}


def _continuation(instruction: str, update: dict) -> str:
    next_instruction = (update.get("ticket_result") or {}).get("response_instruction")
    if not next_instruction:
        return instruction
    return f"{instruction} Then, in a separate short paragraph: {next_instruction} Ask only the next issue's question."


def _close_active(state: AgentState, instruction: str) -> dict:
    update = _activate_next(state, list(state.get("pending_issues", [])))
    return {**update, **_response(_continuation(instruction, update))}


def _queue_new(state: AgentState, text: str, source: str = "no_answer", urgent: bool = False) -> dict:
    issue = _issue(state, text, source, urgent)
    return {"pending_issues": [*state.get("pending_issues", []), issue]}


def _apply_none(state: AgentState, action: str) -> dict:
    text = state.get("current_request") or _latest_customer(state)
    if action == "clarify":
        return _response("Ask which issue the customer wants help with.")
    source = {"urgent_issue": "urgent", "failed_solution": "failed_solution"}.get(action, "no_answer")
    urgent = action == "urgent_issue"
    active = state.get("active_issue")
    if active and active.get("description") != text:
        return _response(
            f"The new issue will be handled after the current issue: {active.get('description', '')}.",
            **_queue_new(state, text, source, urgent),
        )
    issue = active or _issue(state, text, source, urgent)
    base = {"active_issue": issue}
    if active is None:
        base["pending_issues"] = list(state.get("pending_issues", []))
    if state.get("explicit_ticket_request") or action == "urgent_issue":
        return {**base, **_prepare({**state, **base}, issue, skip_collection=bool(state.get("explicit_ticket_request")), urgent=urgent)}
    if (
        action == "register_issue"
        and state.get("search_outcome") in {"partial", "no_answer"}
        and not any(item.get("relation") == "DIRECT" for item in state.get("current_references", []))
    ):
        return {**base, **_response(
            "Tell the customer that the available knowledge base did not provide a supported answer, "
            f"then offer to prepare a support ticket for: {issue['description']}.",
            pending_customer_response=_pending("offer", issue),
        )}
    return {**base, **_response(
        f"Offer to prepare a support ticket for: {issue['description']}.",
        pending_customer_response=_pending("offer", issue),
    )}


def _apply_offer(state: AgentState, action: str) -> dict:
    issue = state["active_issue"]
    if action == "accept":
        return _prepare(state, issue, urgent=bool(issue.get("urgent")))
    if action == "decline":
        return _close_active(state, "Acknowledge the declined ticket offer briefly. Say the customer can contact support whenever needed or that they can describe their problem here and you can help within your scope. Refer to ‘support,’ not ‘the ticketing system.’ Preserve any separately authorized next-issue instruction.")
    if action == "separate_issue":
        return {**_queue_new(state, _latest_customer(state)), **_response("Tell the customer the separate issue is queued after the current offer, then remind them about the current offer.")}
    if action == "update_impact":
        updated = {**issue, "impact_update": _latest_customer(state)}
        return _response("Acknowledge the impact update and continue the current ticket offer.", active_issue=updated, pending_customer_response=_pending("offer", updated))
    return _response("Ask whether the customer wants to accept or decline the current ticket offer.", pending_customer_response=state.get("pending_customer_response"))


def _apply_detail(state: AgentState, action: str) -> dict:
    issue = state["active_issue"]
    draft = _draft(state)
    item = state["pending_customer_response"].item
    if action == "cancel":
        return _close_active(state, "Acknowledge the cancelled ticket preparation and explain how to reach support if help is still needed.")
    if action == "not_an_answer":
        return _response("Explain why the requested detail is needed if the customer asked, then ask them to provide it if they have it.", pending_customer_response=state.get("pending_customer_response"))
    if action == "skip_detail" and item:
        required = {**draft.required, item: "unknown"}
        unknown = list(dict.fromkeys([*draft.unknown, item]))
        draft = draft.model_copy(update={"required": required, "unknown": unknown, "missing": [key for key, value in required.items() if value is None]})
        state = {**state, "ticket_draft": draft.model_dump()}
        return _prepare(state, issue)
    if action == "provide":
        classified = _classify(state, draft)
        if classified is None:
            return _response("Ask the customer to send the last detail again.", pending_customer_response=state.get("pending_customer_response"))
        return _prepare({**state, "ticket_draft": classified.model_dump()}, issue)
    if action == "separate_issue":
        return {**_queue_new(state, _latest_customer(state)), **_response("Tell the customer the separate issue is queued after the current ticket, then ask for the current required detail again.", pending_customer_response=state.get("pending_customer_response"))}
    if action == "update_impact":
        return _response("Acknowledge the impact update and ask for the current required detail again.", pending_customer_response=state.get("pending_customer_response"))
    return _response("Ask for the current required detail again.", pending_customer_response=state.get("pending_customer_response"))


def _apply_summary(state: AgentState, action: str) -> dict:
    issue = state["active_issue"]
    if action == "submit":
        return _submit(state, issue)
    if action == "edit_summary":
        classified = _classify(state, _draft(state))
        if classified is None:
            return _response("The summary could not be updated. Ask the customer to send the requested change again.", pending_customer_response=state.get("pending_customer_response"))
        return _summary({**state, "ticket_draft": classified.model_dump()}, issue, classified, f"Tell the customer the summary was updated and ask whether to submit it for: {issue['description']}.")
    if action == "cancel":
        return _close_active(state, "Acknowledge the cancelled ticket preparation and explain how to reach support if help is still needed.")
    if action == "separate_issue":
        return {**_queue_new(state, _latest_customer(state)), **_response("Tell the customer the separate issue is queued while this summary remains available for submission.", pending_customer_response=state.get("pending_customer_response"))}
    return _response("Ask whether the customer wants to submit, edit, or cancel the displayed summary.", pending_customer_response=state.get("pending_customer_response"))


def _submit(state: AgentState, issue: dict) -> dict:
    draft = _draft(state)
    legacy = {**_legacy_state(state, draft), "phase": "confirm"}
    try:
        log.info("ticket_operator  submit issue=%s", issue.get("id"))
        created, corrections = tickets.submit(legacy, ticket_api.ticket_client())
    except (tickets.SubmitRefused, TicketApiError) as exc:
        log.error("ERROR ticket_operator submission failed: %s", exc)
        return _response(
            "The ticket could not be created. Tell the customer that submission failed and ask them to try again.",
            ticket_result={"submission_requested": True, "submission_succeeded": False, "error": str(exc)},
            active_issue=issue,
            ticket_draft=draft.model_dump(),
            pending_customer_response=_pending("summary", issue),
        )
    queued = list(state.get("pending_issues", []))
    next_state = _activate_next(state, queued)
    log.info("ticket_operator  submitted ticket=%s queued=%d", created.get("ticket_number"), len(queued))
    card = {
        **created,
        "title": draft.title,
        "summary": draft.summary,
        "title_customer": draft.title_customer,
        "summary_customer": draft.summary_customer,
        "queue": draft.queue,
        "type": draft.type,
        "subtype": draft.subtype,
        "priority": draft.priority,
        "required": draft.required,
        "collected": draft.collected,
        "unknown": draft.unknown,
        "collected_customer": draft.collected_customer,
        "unknown_customer": draft.unknown_customer,
        "attachments": [f["file_name"] for f in state.get("attachments", [])],
        # The next assistant message announces this ticket and is appended at this index.
        "message_index": len(state.get("messages", [])),
    }
    return {
        **next_state,
        "created_tickets": [*state.get("created_tickets", []), card],
        "attachments": [],
        "ticket_result": {
            "submission_requested": True,
            "submission_succeeded": True,
            "ticket": created,
            "corrections": corrections,
            "response_instruction": _continuation(
                f"Confirm that ticket {created.get('ticket_number', '')} was created for {issue['description']} and provide its number.", next_state),
        },
    }


def _ticket_operator_node(state: AgentState) -> dict:
    context = _context(state)
    log.info("ticket_operator  context=%s event=%s active=%s pending=%d", context,
             state.get("input_event"), bool(state.get("active_issue")), len(state.get("pending_issues", [])))
    if state.get("input_event") == "submit":
        action, reason = "submit", "The customer pressed the Submit button."
        if context != "summary" or not state.get("summary_shown") or not state.get("submission_requested"):
            log.warning("ticket_operator  submit rejected: no valid displayed summary")
            return {
                "manager_action": action,
                "manager_reason": "Submit was pressed without a valid displayed summary.",
                "ticket_result": {
                    "submission_requested": True,
                    "submission_succeeded": False,
                    "response_instruction": "Tell the customer there is no valid ticket summary to submit.",
                },
            }
    else:
        scope_routed = "outside the chat scope" in (state.get("route_reason") or "")
        if scope_routed:
            action = "register_issue" if context == "none" else "separate_issue"
            reason = "Customer-specific account or user data must go to ticket handling."
        else:
            schema = manager_schema(context)
            try:
                decision = llm.structured(manager_prompt(state, context), list(state.get("messages", [])), schema, node="ticket_operator")
                action, reason = decision.action, decision.reason
            except llm.ModelFailed as exc:
                log.error("ERROR ticket_operator model call failed: %s", exc)
                action = "not_an_answer" if context == "detail" else "clarify"
                reason = "Ticket Operator could not safely interpret the message."
    if context == "none":
        update = _apply_none(state, action)
    elif context == "offer":
        update = _apply_offer(state, action)
    elif context == "detail":
        update = _apply_detail(state, action)
    else:
        update = _apply_summary(state, action)
    log.info("ticket_operator  action=%s reason=%r", action, reason)
    return {"manager_action": action, "manager_reason": reason, **update,
            # Capture the field answered before _prepare advances or clears it.
            "answered_detail": state.get("pending_customer_response") if context == "detail" and action == "provide" else None}


def ticket_operator_node(state: AgentState) -> dict:
    """Run Ticket Operator and expose its structured result to the orchestrator."""

    result = _ticket_operator_node(state)
    result["ticket_operator_result"] = {
        "action": result.get("manager_action"),
        "reason": result.get("manager_reason"),
        "ticket_result": result.get("ticket_result"),
    }
    return result




