"""Interpret and advance the ticket-preparation workflow.

This step owns ticket state transitions, but not ticket API calls.
"""

import logging
import json

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
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
    derived = _completion_from_tools(state, report)
    draft_steps = [item["result"] for item in tool_results if item["name"] in {"save_ticket_draft", "skip_detail"}]
    draft_failed = derived.action == "workflow_failed" and bool(draft_steps) and not draft_steps[-1].get("success")
    # Tool results that give the active issue's next step win over clarify, workflow_failed and a bare queued. A draft
    # step that failed last is a workflow failure whatever the interpreter chose.
    if (completion.action == "offer" and derived.action in {"request_detail", "show_summary"}) \
            or (completion.action in {"clarify", "workflow_failed", "queued"}
            and derived.action not in {"workflow_failed", completion.action}) \
            or (draft_failed and completion.action != "workflow_failed"):
        log.warning("ticket_operator  %s replaced with %s from the tool results", completion.action, derived.action)
        completion = derived
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
    # A summary already waiting for confirmation, on a turn that did not touch the draft, is still that summary.
    preserved = draft_result is None and getattr(state.get("pending_customer_response"), "kind", None) == "summary"
    if completion.action == "show_summary" and not (draft_result or {}).get("summary_ready") and not preserved:
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
            # No detail is missing, so the draft itself is not ready: nothing the customer can clear up.
            "action": "request_detail" if requested else "workflow_failed",
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
        replacement = "offer" if registered else "queued" if queued else "workflow_failed"
        log.error("ERROR ticket_operator already_submitted existing_ticket=%s listed=%s, replaced with %s",
                  completion.existing_ticket, sorted(listed), replacement)
        completion = completion.model_copy(update={"action": replacement})
    if completion.action != "already_submitted":
        # Only already_submitted names an existing ticket.
        completion = completion.model_copy(update={"existing_ticket": None})
    if completion.action != "clarify":
        # Only clarify asks a question of its own.
        completion = completion.model_copy(update={"clarification_question": None})
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
    elif (completion.action == "workflow_failed" and issue and not state.get("ticket_draft")
          and not update.get("pending_customer_response")):
        # An issue registered before the failure keeps a way forward: its offer waits, so a yes prepares it again.
        update["pending_customer_response"] = _pending("offer", issue)

    submission = next((item["result"] for item in reversed(tool_results) if item["name"] == "submit_ticket"), None)
    ticket_result = {}
    if submission is not None and submission.get("nothing_to_submit"):
        # No summary was waiting: nothing was attempted, so there is no failed submission to report.
        ticket_result["nothing_to_submit"] = True
    elif submission is not None:
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
        "already_submitted": "already_submitted", "workflow_failed": "workflow_failed",
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
            # Its title, so the reply can name the topic of the ticket that already covers the problem.
            "existing_ticket_title": next((ticket.get("title_customer") or ticket.get("title")
                                           for item in tool_results if item["name"] == "check_submitted_tickets"
                                           for ticket in item["result"].get("tickets") or []
                                           if completion.existing_ticket
                                           and ticket.get("ticket_number") == completion.existing_ticket), None),
            "clarification_question": completion.clarification_question,
            "confirmed_changes": _saved_changes(tool_results, issue_id) if issue else [],
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


def _saved_changes(tool_results: list[dict], issue_id: str | None) -> list[dict]:
    """Net successful edits for the active issue this turn, excluding failed saves and reverted edits."""
    changes = {}
    for item in tool_results:
        result = item["result"]
        if (item["name"] == "skip_detail" and result.get("success")
                and result.get("issue_id") == issue_id):
            # The separate skipped fact now describes this detail; do not report its superseded saved value.
            changes.pop(f"required.{result.get('item')}", None)
        if (item["name"] != "save_ticket_draft" or not result.get("success")
                or result.get("unchanged") or result.get("issue_id") != issue_id):
            continue
        for change in result.get("confirmed_changes") or []:
            field = change["field"]
            changes[field] = {**change, "before": changes.get(field, change)["before"]}
    return [change for change in changes.values() if change["before"] != change["after"]]


def _completion_from_tools(state: AgentState, report: str) -> OperatorCompletion:
    """The action the tool results alone give: the latest tool result that moved the workflow decides it, so a
    submit, cancel or registration the tools already made is reported. workflow_failed when no tool gives a next
    step. The interpreter's answer is checked against it, and it stands in when the interpreter fails."""
    reason = "The result could not be interpreted; the action follows the tool results."
    queued = False
    for item in reversed(_tool_results(report)):
        name, result = item["name"], item["result"]
        if name == "submit_ticket":
            return OperatorCompletion(action="submitted" if result.get("success") else "submission_failed",
                                      reason=reason)
        if name in {"save_ticket_draft", "skip_detail"} and not result.get("success"):
            # The latest draft step failed. A registration before it does not complete that step.
            return OperatorCompletion(action="workflow_failed", reason=f"{name} failed: {result.get('error')}")
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
                # A queued issue is an extra fact. The earlier results say what the active issue needs: an offer or
                # its draft step, whether it became active this turn or before it.
                queued = True
                continue
            return OperatorCompletion(action="offer", reason=reason, issue_id=(result.get("issue") or {}).get("id"))
        if name in {"save_ticket_draft", "skip_detail"}:
            missing = result.get("missing_customer_details") or []
            if result.get("summary_ready"):
                return OperatorCompletion(action="show_summary", reason=reason)
            if missing:
                return OperatorCompletion(action="request_detail", reason=reason, requested_detail=missing[0])
    if queued:
        return OperatorCompletion(action="queued", reason=reason)
    return OperatorCompletion(action="workflow_failed", reason="No tool result gives a next workflow step.")


def _completion_check(working: AgentState, report: str, matches: set[str] | None = None):
    """What is wrong with a completion, told back to the model so it chooses again; None when it fits the tools.

    matches is given on a Search handoff where no tool changed the workflow: the only actions that need no tool
    there, each one a match of the request to a submitted ticket or to an issue that already existed."""
    required = (working.get("ticket_draft") or {}).get("required") or {}
    missing = [key for key, value in required.items() if value is None]
    tool_results = _tool_results(report)
    activated = next((item["result"].get("active_issue") for item in reversed(tool_results)
                      if item["name"] == "manage_issues" and item["result"].get("operation") == "cancel_preparation"
                      and item["result"].get("success")), None)
    expected = _completion_from_tools(working, report)
    listed = sorted(str(ticket.get("ticket_number")) for item in tool_results
                    if item["name"] == "check_submitted_tickets" for ticket in item["result"].get("tickets") or [])
    submit_skipped = working.get("input_event") == "submit" and not any(
        item["name"] == "submit_ticket" for item in tool_results)
    draft_steps = [item["result"] for item in tool_results if item["name"] in {"save_ticket_draft", "skip_detail"}]
    draft_failed = expected.action == "workflow_failed" and bool(draft_steps) and not draft_steps[-1].get("success")
    active_id = (working.get("active_issue") or {}).get("id")
    queue_ids = [issue.get("id") for issue in working.get("pending_issues") or []]

    def check(completion: OperatorCompletion) -> str | None:
        if completion.action == "offer" and expected.action in {"request_detail", "show_summary"}:
            return (f"The draft tool result already gives {expected.action}. Return that action; offer means "
                    "asking whether to begin preparation, not asking to submit a prepared summary.")
        if completion.action == "request_detail" and completion.requested_detail not in missing:
            if not working.get("ticket_draft"):
                return ("request_detail is not possible: no draft exists yet. Return offer before the customer "
                        "accepts, or workflow_failed if the draft could not be built.")
            return (f"requested_detail {completion.requested_detail!r} is not a missing item. "
                    f"Choose one of {missing}, or show_summary when none are missing.")
        if activated and completion.action != "offer":
            return (f"cancel_preparation made {activated.get('id')} the active issue; return offer for it. "
                    "The cancellation is already recorded.")
        if (completion.action in {"clarify", "workflow_failed", "queued"}
                and expected.action not in {"workflow_failed", completion.action}):
            return (f"The tool results changed the workflow and give the active issue's next step: "
                    f"{expected.action}. {completion.action} does not report it; return {expected.action}. An issue "
                    "queued this turn is recorded separately.")
        if completion.action == "clarify" and not (completion.clarification_question or "").strip():
            return ("clarify needs clarification_question: the one question whose answer settles how to read the "
                    "customer's reply, naming the request and the step that is waiting.")
        if draft_failed and completion.action != "workflow_failed":
            return ("The latest draft step failed, so the draft was not prepared or changed. That is an internal "
                    "failure, whatever was registered before it: return workflow_failed.")
        if completion.action == "already_submitted" and completion.existing_ticket not in listed:
            return (f"existing_ticket {completion.existing_ticket!r} is not a ticket check_submitted_tickets "
                    f"returned ({listed}). Name one of those, or return workflow_failed when the request is in none.")
        if submit_skipped and expected.action == "workflow_failed" and completion.action != "workflow_failed":
            return ("The customer pressed Submit and submit_ticket was not called, so nothing was submitted and the "
                    "summary still waits. That is an internal failure: return workflow_failed.")
        if matches is not None and completion.action not in matches | {"clarify", "workflow_failed"}:
            return (f"No tool changed the workflow this turn, so {completion.action} is not possible. Possible "
                    f"when the request matches that ticket or existing issue: {sorted(matches)}. Otherwise return "
                    "workflow_failed.")
        if matches is not None and completion.action == "queued" and completion.issue_id not in queue_ids:
            return (f"queued with no tool run means the request matches an issue already in the queue: set issue_id "
                    f"to that issue, one of {queue_ids}. Otherwise return workflow_failed.")
        if (matches is not None and completion.action in {"offer", "request_detail", "show_summary"}
                and completion.issue_id not in (None, active_id)):
            return (f"{completion.action} with no tool run means the request matches the active issue {active_id}; "
                    f"issue_id {completion.issue_id!r} is not it. Otherwise return workflow_failed.")
        return None

    return check


def _ticket_operator_node(state: AgentState) -> dict:
    working = dict(state)
    working["pending_issues"] = list(state.get("pending_issues", []))
    working["created_tickets"] = list(state.get("created_tickets", []))
    report = ""
    results: list[dict] = []
    search_handoff = state.get("last_worker") == "search" and state.get("search_outcome") in {"partial", "no_answer"}
    agent = None
    messages = list(state.get("messages", []))
    # The second attempt runs only when a Search handoff ended with no workflow step (see the end of the loop).
    for attempt in (1, 2):
        tool_state = None
        try:
            if agent is None:
                tools = build_ticket_operator_tools(working, results)
                agent = create_agent(
                    model=llm.tool_client(0),
                    tools=tools,
                    system_prompt=ticket_operator_tool_prompt(state),
                    name="ticket_operator_tools",
                )
                if search_handoff:
                    # The duplicate lookup is a fixed step of this handoff, so it runs here and not as the model's
                    # choice. The agent finds it in its history as a call it already made, and the report picks it
                    # up from there.
                    call = {"name": "check_submitted_tickets", "args": {}, "id": "search_handoff_lookup",
                            "type": "tool_call"}
                    lookup = next(t for t in tools if t.name == "check_submitted_tickets")
                    messages += [AIMessage(content="", tool_calls=[call]), lookup.invoke(call)]
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
            # The customer asked for this ticket, so there is no offer to accept: the prompt says to save the draft
            # in the same turn, and the tool agent kept offering instead. The draft is saved here, once.
            save = next(t for t in build_ticket_operator_tools(working, results) if t.name == "save_ticket_draft")
            save.invoke({"fields": {}, "issue_id": working["active_issue"]["id"]})
            report = json.dumps([*json.loads(report or "[]"), results[-1]], ensure_ascii=False)
            log.info("ticket_operator  explicit request: draft saved after registration")

        tool_results = _tool_results(report)
        # A tool that changed the workflow: the agent is never run again after one, which would repeat the operation.
        # Reading the requirements or the submitted tickets changes nothing, so neither counts.
        worked = any(item["name"] in {"manage_issues", "save_ticket_draft", "skip_detail", "submit_ticket"}
                     and item["result"].get("success") for item in tool_results)
        # Every Search-handoff rule ends in a workflow step. With no tool run, the only valid results are matches of
        # the request to a ticket the lookup returned or to an issue that existed before this turn.
        matches = None
        if search_handoff and tool_state is not None and not worked:
            matches = set()
            if any(item["result"].get("tickets") for item in tool_results if item["name"] == "check_submitted_tickets"):
                matches.add("already_submitted")
            if state.get("active_issue"):
                draft = state.get("ticket_draft") or {}
                kind = getattr(state.get("pending_customer_response"), "kind", None)
                matches.add({"offer": "offer", "detail": "request_detail", "summary": "show_summary"}.get(kind)
                            or ("offer" if not draft else "request_detail" if draft.get("missing") else "show_summary"))
            if state.get("pending_issues"):
                matches.add("queued")

        completion = None
        if tool_state is None:
            # The tool agent failed: there is no handoff to interpret, so the tool results alone decide.
            completion = _completion_from_tools(state, report)
            log.warning("ticket_operator  completion taken from the tool results action=%s", completion.action)
        elif matches != set():
            try:
                completion = llm.structured(
                    ticket_operator_result_prompt(state, report, _working_summary(working)),
                    [], OperatorCompletion, node="ticket_operator",
                    check=_completion_check(working, report, matches),
                )
            except llm.ModelFailed as exc:
                # No valid object, or one that still failed its check after being told what was wrong: neither is
                # kept. The tool results decide.
                log.error("ERROR ticket_operator result interpretation failed: %s", exc)
                completion = _completion_from_tools(state, report)
                log.warning("ticket_operator  completion taken from the tool results action=%s", completion.action)
        # else: nothing the request could match and nothing registered, so there is no result to interpret.

        if matches is None or (completion is not None and completion.action in matches):
            break
        if attempt == 2:
            log.error("ERROR ticket_operator search handoff still ended with no workflow step after the retry")
            completion = OperatorCompletion(
                action="workflow_failed",
                reason="The Search handoff ended twice with no ticket-workflow step and no valid match.")
            break
        log.warning("ticket_operator  search handoff ended with no workflow step (matches=%s); running the agent again",
                    sorted(matches))
        looked_up = any(item["name"] == "check_submitted_tickets" and item["result"].get("success")
                        for item in tool_results)
        messages = [*tool_state["messages"], HumanMessage(content=(
            "Note from the system, not the customer: this Search handoff ended without a successful workflow "
            "operation. The request reached you unanswered by Search, so it is a support request. " + (
                "No submitted ticket or existing issue was validly identified for it. Apply the Search-handoff rules "
                "now: identify the matching ticket or issue from the lookup result and the current state, or call "
                "manage_issues with operation=\"register\" for the new request." if matches else
                ("The lookup returned no submitted tickets" if looked_up
                 else "The lookup failed, so the submitted tickets are unknown")
                + " and no issue exists in this chat, so the request is new: call manage_issues with "
                  "operation=\"register\" for it.")))]

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




