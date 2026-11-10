"""Interpret and advance the ticket-preparation workflow.

This step owns ticket state transitions, but not ticket API calls.
"""

import logging
import json

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from ole5_chat import llm
from ole5_chat.agent.contracts import OperatorCompletion
from ole5_chat.agent.prompts.ticket_operator import (
    ticket_operator_result_prompt,
    ticket_operator_tool_prompt,
)
from ole5_chat.agent.state import AgentState
from ole5_chat.agent.tools.ticket_operator import build_ticket_operator_tools
from ole5_chat.identity import Identity

log = logging.getLogger(__name__)


def _pending(kind: str, issue: dict, item: str | None = None):
    from ole5_chat.agent.contracts import PendingCustomerResponse
    return PendingCustomerResponse(kind=kind, issue_id=issue["id"], item=item)


def _tool_report(messages: list) -> str:
    report = []
    for message in messages:
        calls = getattr(message, "tool_calls", None)
        if message.content or calls:
            report.append({
                "type": message.type,
                "name": getattr(message, "name", None),
                "content": message.content,
                "tool_calls": calls,
            })
    return json.dumps(report, ensure_ascii=False, default=str)


def _working_summary(state: AgentState) -> dict:
    return {
        key: state.get(key)
        for key in (
            "active_issue", "pending_issues", "ticket_draft", "pending_customer_response",
            "summary_shown", "submission_requested", "draft_version", "created_tickets",
            "summary_delivered_version", "summary_confirmed_version",
        )
    }


def _tool_results(report: str) -> list[dict]:
    results = []
    entries = json.loads(report or "[]")
    for entry in entries if isinstance(entries, list) else []:
        if entry.get("name") not in {"get_ticket_requirements", "save_ticket_draft", "skip_detail", "manage_issues",
                                     "submit_ticket", "check_submitted_tickets"}:
            continue
        try:
            results.append({
                "name": entry["name"],
                "result": json.loads(entry.get("content") or "{}"),
            })
        except (KeyError, TypeError, json.JSONDecodeError):
            log.error("ERROR ticket_operator invalid tool result name=%s", entry.get("name"))
    return results


def _apply_completion(state: AgentState, completion: OperatorCompletion, report: str) -> dict:
    update = {
        key: state.get(key)
        for key in (
            "active_issue", "pending_issues", "issues_registered", "issues_closed_at", "ticket_draft",
            "created_tickets", "attachments",
            "summary_shown", "submission_requested", "pending_customer_response",
            "draft_version", "summary_delivered_version", "summary_confirmed_version",
        )
        if key in state
    }
    tool_results = _tool_results(report)
    draft_result = next(
        (item["result"] for item in reversed(tool_results) if item["name"] in {"save_ticket_draft", "skip_detail"}),
        None,
    )
    cancelled = next((item["result"].get("cancelled_issue") for item in reversed(tool_results)
                      if item["name"] == "manage_issues" and item["result"].get("operation") == "cancel_preparation"
                      and item["result"].get("success")), None)
    if cancelled and completion.action == "offer" and completion.issue_id in (None, cancelled.get("id")):
        # A cancelled or declined issue is finished; offering it again would undo the customer's answer.
        log.warning("ticket_operator  offer for cancelled issue=%s replaced with cancelled", cancelled.get("id"))
        completion = completion.model_copy(update={"action": "cancelled", "issue_id": cancelled.get("id")})
    # An issue registered or enqueued this turn behind the active one: Respond tells the customer it is noted.
    queued = next((item["result"].get("issue") for item in reversed(tool_results)
                   if item["name"] == "manage_issues" and item["result"].get("success")
                   and item["result"].get("queued")), None)
    registered = any(item["name"] == "manage_issues" and item["result"].get("success")
                     and item["result"].get("operation") == "register" and not item["result"].get("queued")
                     for item in tool_results)
    if queued and completion.action == "offer" and not cancelled and not registered:
        # No issue became active this turn, so there is no new offer: the active issue's pending step stays.
        log.warning("ticket_operator  offer with queued issue=%s replaced with queued", queued.get("id"))
        completion = completion.model_copy(update={"action": "queued", "issue_id": None})
    if completion.action == "show_summary" and not (draft_result or {}).get("summary_ready"):
        missing = (draft_result or {}).get("missing_customer_details") or []
        requested = missing[0] if missing else None
        log.error(
            "ERROR ticket_operator rejected show_summary: classification_succeeded=%s "
            "summary_ready=%s missing_customer_details=%s mandatory_fields_missing=%s",
            (draft_result or {}).get("classification_succeeded"),
            (draft_result or {}).get("summary_ready"),
            missing,
            (draft_result or {}).get("mandatory_fields_missing") or [],
        )
        completion = completion.model_copy(update={
            "action": "request_detail" if requested else "clarify",
            "requested_detail": requested,
            "reason": "The draft is not ready for summary because classification or required fields are incomplete.",
        })
    elif completion.action == "request_detail" and (draft_result or {}).get("summary_ready"):
        # The tool reported every required detail answered; asking again would loop over settled details.
        log.warning("ticket_operator  request_detail with a summary-ready draft replaced with show_summary")
        completion = completion.model_copy(update={"action": "show_summary", "requested_detail": None})
    listed = {ticket.get("ticket_number") for item in tool_results if item["name"] == "check_submitted_tickets"
              for ticket in item["result"].get("tickets") or []}
    if completion.action == "already_submitted" and (registered or queued or completion.existing_ticket not in listed):
        # The issue was registered after all, or the ticket is not one check_submitted_tickets returned.
        replacement = "offer" if registered else "queued" if queued else "clarify"
        log.error("ERROR ticket_operator already_submitted existing_ticket=%s listed=%s, replaced with %s",
                  completion.existing_ticket, sorted(listed), replacement)
        completion = completion.model_copy(update={"action": replacement})
    if completion.action != "already_submitted":
        # Only already_submitted names an existing ticket.
        completion = completion.model_copy(update={"existing_ticket": None})
    issue = state.get("active_issue")
    issue_id = completion.issue_id or (issue or {}).get("id")
    if completion.action == "offer" and issue_id:
        update["pending_customer_response"] = _pending("offer", issue or {"id": issue_id})
    elif completion.action == "request_detail" and issue_id and completion.requested_detail:
        update["pending_customer_response"] = _pending("detail", issue or {"id": issue_id}, completion.requested_detail)
    elif completion.action == "show_summary" and issue_id:
        update["pending_customer_response"] = _pending("summary", issue or {"id": issue_id})
        update["summary_shown"] = True
        update["submission_requested"] = True
    elif completion.action in {"declined", "cancelled"}:
        update["pending_customer_response"] = None

    submission = next((item["result"] for item in reversed(tool_results) if item["name"] == "submit_ticket"), None)
    ticket_result = {}
    if submission is not None:
        ticket_result.update({
            "submission_requested": True,
            "submission_succeeded": bool(submission.get("success")),
            "ticket": submission.get("ticket"),
            "corrections": submission.get("corrections", []),
            "error": submission.get("error"),
        })
    # After a submitted ticket the next queued issue is active: offer it in the same turn, as the old flow did.
    next_issue = (submission or {}).get("active_issue") if (submission or {}).get("success") else None
    if completion.action == "submitted" and next_issue:
        update["pending_customer_response"] = _pending("offer", next_issue)
    compatibility = {
        "offer": "register_issue", "request_detail": "provide", "show_summary": "clarify",
        "declined": "decline", "cancelled": "cancel", "queued": "separate_issue",
        "submitted": "submit", "submission_failed": "submit", "clarify": "clarify",
        "already_submitted": "already_submitted",
    }
    update.update({
        "manager_action": compatibility[completion.action],
        "manager_reason": completion.reason,
        "ticket_result": ticket_result,
        # Facts for Respond, like Search and Ticket Enquiry give: what was done, not what to say.
        "ticket_operator_result": {
            "action": completion.action,
            "reason": completion.reason,
            "issue_id": issue_id,
            "issue": ((cancelled if completion.action in {"declined", "cancelled"} and cancelled else issue)
                      or {}).get("description"),
            "requested_detail": completion.requested_detail,
            "existing_ticket": completion.existing_ticket,
            # What the customer settled this turn without giving it, and an issue they stopped, whatever comes next.
            "skipped": [{"item": item["result"].get("item"), "value": item["result"].get("value")}
                        for item in tool_results if item["name"] == "skip_detail" and item["result"].get("success")],
            "cancelled_issue": (cancelled or {}).get("description"),
            "next_issue": (next_issue or {}).get("description"),
            "queued_issue": (queued or {}).get("description"),
            "ticket_result": ticket_result,
        },
    })
    return update


def _completion_from_tools(state: AgentState, report: str) -> OperatorCompletion:
    """When the interpreter fails, the latest tool result that moved the workflow decides the action, so a submit,
    cancel or registration the tools already made is reported. clarify only when no tool changed anything."""
    reason = "The result could not be interpreted; the action follows the tool results."
    for item in reversed(_tool_results(report)):
        name, result = item["name"], item["result"]
        if name == "submit_ticket":
            return OperatorCompletion(action="submitted" if result.get("success") else "submission_failed",
                                      reason=reason)
        if not result.get("success"):
            continue
        if name == "manage_issues" and result.get("operation") == "cancel_preparation":
            active = result.get("active_issue")
            if active:
                return OperatorCompletion(action="offer", reason=reason, issue_id=active.get("id"))
            declined = getattr(state.get("pending_customer_response"), "kind", None) == "offer"
            return OperatorCompletion(action="declined" if declined else "cancelled", reason=reason)
        if name == "manage_issues":
            if result.get("queued"):
                return OperatorCompletion(action="queued", reason=reason)
            return OperatorCompletion(action="offer", reason=reason, issue_id=(result.get("issue") or {}).get("id"))
        if name in {"save_ticket_draft", "skip_detail"}:
            missing = result.get("missing_customer_details") or []
            if result.get("summary_ready"):
                return OperatorCompletion(action="show_summary", reason=reason)
            if missing:
                return OperatorCompletion(action="request_detail", reason=reason, requested_detail=missing[0])
    return OperatorCompletion(action="clarify", reason="Operator result could not be interpreted safely.")


def _completion_check(working: AgentState, report: str):
    """What is wrong with a completion, told back to the model so it chooses again; None when it fits the tools."""
    required = (working.get("ticket_draft") or {}).get("required") or {}
    missing = [key for key, value in required.items() if value is None]
    activated = next((item["result"].get("active_issue") for item in reversed(_tool_results(report))
                      if item["name"] == "manage_issues" and item["result"].get("operation") == "cancel_preparation"
                      and item["result"].get("success")), None)

    def check(completion: OperatorCompletion) -> str | None:
        if completion.action == "request_detail" and completion.requested_detail not in missing:
            if not working.get("ticket_draft"):
                return ("request_detail is not possible: no draft exists yet. Return offer before the customer "
                        "accepts, or clarify if the draft could not be built.")
            return (f"requested_detail {completion.requested_detail!r} is not a missing item. "
                    f"Choose one of {missing}, or show_summary when none are missing.")
        if activated and completion.action != "offer":
            return (f"cancel_preparation made {activated.get('id')} the active issue; return offer for it. "
                    "The cancellation is already recorded.")
        return None

    return check


def _ticket_operator_node(state: AgentState) -> dict:
    working = dict(state)
    working["pending_issues"] = list(state.get("pending_issues", []))
    working["created_tickets"] = list(state.get("created_tickets", []))
    report = ""
    results: list[dict] = []
    try:
        agent = create_agent(
            model=llm.tool_client(0),
            tools=build_ticket_operator_tools(working, results),
            system_prompt=ticket_operator_tool_prompt(state),
            name="ticket_operator_tools",
        )
        messages = list(state.get("messages", []))
        if state.get("input_event") == "submit":
            # A button turn has no customer message; without this the agent replays the last one.
            messages.append(HumanMessage(content=(
                "Note from the system, not the customer: the customer pressed Submit on the displayed summary "
                f"(draft version {state.get('summary_delivered_version')}). This is their confirmation: call "
                "submit_ticket for the active issue with that version. Do not repeat any earlier step.")))
        tool_state = agent.invoke({"messages": messages})
        handoff = next((message.content for message in reversed(tool_state.get("messages", []))
                        if message.type == "ai" and not getattr(message, "tool_calls", None)), None)
        log.info("ticket_operator handoff=%r", handoff)
        report = _tool_report(tool_state.get("messages", []))
    except Exception as exc:
        log.error("ERROR ticket_operator tool agent failed: %s", exc)
        # The tools may have changed the working state before the failure; their results say what was done.
        report = json.dumps([*results, {"tool_agent_error": str(exc)}], ensure_ascii=False)

    registered = any(item["name"] == "manage_issues" and item["result"].get("operation") == "register"
                     and item["result"].get("success") and not item["result"].get("queued")
                     for item in _tool_results(report))
    if state.get("explicit_ticket_request") and registered and working.get("active_issue") \
            and not working.get("ticket_draft"):
        # The customer asked for this ticket, so there is no offer to accept: the prompt says to save the draft in
        # the same turn, and the tool agent kept offering instead. The draft is saved here, once.
        save = next(t for t in build_ticket_operator_tools(working, results) if t.name == "save_ticket_draft")
        save.invoke({"fields": {}, "issue_id": working["active_issue"]["id"]})
        report = json.dumps([*json.loads(report or "[]"), results[-1]], ensure_ascii=False)
        log.info("ticket_operator  explicit request: draft saved after registration")

    try:
        completion = llm.structured(
            ticket_operator_result_prompt(state, report, _working_summary(working)),
            [], OperatorCompletion, node="ticket_operator", check=_completion_check(working, report),
        )
    except llm.GuardrailFailed as exc:
        # The model kept its choice after being told what was wrong; keep it and let _apply_completion's guards act.
        log.error("ERROR ticket_operator completion still failed its check: %s", exc)
        completion = exc.result
    except llm.ModelFailed as exc:
        log.error("ERROR ticket_operator result interpretation failed: %s", exc)
        completion = _completion_from_tools(state, report)
        log.warning("ticket_operator  completion taken from the tool results action=%s", completion.action)

    update = _apply_completion(working, completion, report)
    log.info("ticket_operator completion action=%s issue_id=%s requested_detail=%s reason=%r",
             completion.action, completion.issue_id, completion.requested_detail, completion.reason)
    log.info("ticket_operator tool_report=%s", report)
    pending = update.get("pending_customer_response")
    log.info("ticket_operator state pending_kind=%s pending_issue=%s active_issue=%s queued=%d manager_action=%s",
             getattr(pending, "kind", None), getattr(pending, "issue_id", None),
             (update.get("active_issue") or {}).get("id"), len(update.get("pending_issues") or []),
             update.get("manager_action"))
    return update


def ticket_operator_node(state: AgentState) -> dict:
    """Run Ticket Operator and expose its structured result to the orchestrator."""

    result = _ticket_operator_node(state)
    result["last_worker"] = "ticket_operator"
    result.setdefault("ticket_operator_result", {
        "action": result.get("manager_action"),
        "reason": result.get("manager_reason"),
        "ticket_result": result.get("ticket_result"),
    })
    return result




