"""State-bound tools for the Ticket Operator agent."""

import json
import logging
from functools import wraps
from inspect import signature
from typing import Any, Literal

from langchain_core.tools import tool

from ole5_chat import products, values
from ole5_chat.agent.contracts import PendingCustomerResponse
from ole5_chat.agent.state import AgentState
from ole5_chat.support_core import classify as ticket_classifier
from ole5_chat.support_core.contracts import PendingIssue
from ole5_chat.support_core.state import ChatState, TicketDraft
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets import submit as ticket_submit

log = logging.getLogger(__name__)

SKIPPED = ("unknown", "declined")  # values of a required detail the customer answered without giving it


def _logged_tool(sink: list):
    """Log execution at the tool boundary while preserving its argument schema. Each result also goes to sink, so
    the Operator still knows what the tools did if the tool agent fails after they ran."""
    return lambda function: _logged(function, sink)


def _logged(function, sink: list):
    @wraps(function)
    def wrapped(*args, **kwargs):
        arguments = signature(function).bind(*args, **kwargs)
        arguments.apply_defaults()
        details = dict(arguments.arguments)
        # Log field names rather than customer-provided draft contents.
        if "fields" in details:
            details["fields"] = sorted(details["fields"])
        details.pop("description", None)
        log.info("ticket_operator tool_call name=%s args=%s", function.__name__, details)
        try:
            payload = function(*args, **kwargs)
            result = json.loads(payload)
        except Exception:
            log.exception("ERROR ticket_operator tool_exception name=%s", function.__name__)
            raise
        logger = log.info if result.get("success") else log.error
        logger(
            "ticket_operator tool_result name=%s operation=%s success=%s "
            "issue_id=%s active_issue=%s queued=%d draft_version=%s missing_fields=%s ticket=%s error=%s",
            function.__name__, result.get("operation"), result.get("success"),
            result.get("issue_id"), (result.get("active_issue") or {}).get("id"),
            len(result.get("pending_issues") or []), result.get("draft_version"),
            result.get("missing_fields"), (result.get("ticket") or {}).get("ticket_number"),
            result.get("error"),
        )
        if function.__name__ == "save_ticket_draft":
            log.info(
                "ticket_operator draft_status classification_succeeded=%s "
                "summary_ready=%s missing_customer_details=%s mandatory_fields_missing=%s",
                result.get("classification_succeeded"), result.get("summary_ready"),
                result.get("missing_customer_details"), result.get("mandatory_fields_missing"),
            )
        sink.append({"type": "tool", "name": function.__name__, "content": payload})
        return payload
    return wrapped


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _next_issue_id(state: AgentState) -> str:
    used = {str(issue.get("id")) for issue in state.get("pending_issues", [])}
    if state.get("active_issue"):
        used.add(str(state["active_issue"].get("id")))
    number = 1
    while f"issue-{number}" in used:
        number += 1
    return f"issue-{number}"


def _draft(state: AgentState) -> TicketDraft:
    current = state.get("ticket_draft")
    if current:
        return TicketDraft.model_validate(current)
    return TicketDraft(product=values.catalog(state["identity"].product).service)


def _legacy_submit_state(state: AgentState, draft: TicketDraft) -> ChatState:
    issue = state.get("active_issue") or {
        "id": "issue-1",
        "description": "",
        "source": "no_answer",
    }
    pending = [PendingIssue(
        text=issue.get("description", ""),
        urgent=bool(issue.get("urgent")),
        source="always_ticket" if issue.get("source") in {"always_ticket", "urgent"} else "no_answer",
    )]
    return ChatState(
        messages=list(state.get("messages", [])),
        identity=state["identity"],
        ticket_draft=draft,
        corrections=list(state.get("corrections", [])),
        attachments=list(state.get("attachments", [])),
        phase="confirm",
        pending=pending,
    )


def _classification_state(state: AgentState, draft: TicketDraft) -> ChatState:
    """Build classifier input without changing the graph's live state."""
    issue = state.get("active_issue") or {
        "id": "issue-1",
        "description": "",
        "source": "no_answer",
    }
    pending = [PendingIssue(
        text=issue.get("description", ""),
        urgent=bool(issue.get("urgent")),
        source="always_ticket" if issue.get("source") in {"always_ticket", "urgent"} else "no_answer",
    )]
    return ChatState(
        messages=list(state.get("messages", [])),
        identity=state["identity"],
        ticket_draft=draft,
        corrections=list(state.get("corrections", [])),
        attachments=list(state.get("attachments", [])),
        phase="collect",
        pending=pending,
    )


def build_ticket_operator_tools(state: AgentState, sink: list | None = None):
    """Build tools bound to one graph invocation's mutable working state; sink collects their results."""

    sink = [] if sink is None else sink

    @tool("get_ticket_requirements")
    @_logged_tool(sink)
    def get_ticket_requirements() -> str:
        """Return the configured ticket categories and required customer details."""

        config = products.load(state["identity"].product)
        catalog = values.catalog(state["identity"].product)
        return _json({
            "success": True,
            "operation": "get_ticket_requirements",
            "product": config.name,
            "required_by_kind": config.required,
            "queues": [item.value for item in catalog.queues],
            "types": [item.value for item in catalog.types],
            "subtypes": [item.value for item in catalog.subtypes],
            "priorities": [item.value for item in catalog.priorities],
        })

    @tool("save_ticket_draft")
    @_logged_tool(sink)
    def save_ticket_draft(fields: dict, issue_id: str | None = None) -> str:
        """Merge draft updates, classify them, and report whether a summary is safe."""

        active = state.get("active_issue")
        if active is None:
            return _json({"success": False, "operation": "save_ticket_draft", "error": "no active issue"})
        if issue_id and issue_id != active.get("id"):
            return _json({"success": False, "operation": "save_ticket_draft", "error": "issue is not active"})
        try:
            current = _draft(state)
            # An empty save of a ready draft is a read, not a new revision.
            if (current.classified and not current.missing
                    and all(getattr(current, key, None) for key in ticket_submit.REQUIRED)
                    and all(current.model_dump().get(key) == value for key, value in fields.items())):
                return _json({
                    "success": True, "operation": "save_ticket_draft", "unchanged": True,
                    "issue_id": active.get("id"), "draft_version": state.get("draft_version"),
                    "classification_succeeded": True, "summary_ready": True,
                    "missing_customer_details": [], "missing_fields": [],
                    "mandatory_fields_missing": [], "draft": current.model_dump(),
                })
            draft = current.model_copy(update=fields)
        except Exception as exc:
            return _json({"success": False, "operation": "save_ticket_draft", "error": str(exc)})

        classification = ticket_classifier.classify(_classification_state(state, draft))
        classified = classification.get("ticket_draft")
        if not isinstance(classified, TicketDraft):
            error = classification.get("note") or "ticket classification did not produce a valid draft"
            log.error("ERROR ticket_operator classification failed issue_id=%s error=%s", active.get("id"), error)
            return _json({
                "success": False,
                "operation": "save_ticket_draft",
                "issue_id": active.get("id"),
                "classification_succeeded": False,
                "summary_ready": False,
                "missing_customer_details": [],
                "missing_fields": [],
                "error": error,
            })

        # A detail the customer already said they don't have or won't give stays settled when classify runs again.
        required = {**classified.required, **{key: value for key, value in current.required.items()
                                              if value in SKIPPED and classified.required.get(key) is None
                                              and key in classified.required}}
        classified = classified.model_copy(update={"required": required})
        mandatory_missing = [
            key for key in ticket_submit.REQUIRED
            if not getattr(classified, key, None)
        ]
        missing_customer_details = [key for key, value in required.items() if value is None]
        # An urgent issue goes to the summary without the customer details; the classification is still required.
        summary_ready = bool(
            classified.classified
            and not mandatory_missing
            and (not missing_customer_details or active.get("urgent"))
        )
        classified = classified.model_copy(update={"missing": missing_customer_details})
        state["ticket_draft"] = classified.model_dump()
        state["draft_version"] = int(state.get("draft_version") or 0) + 1
        state["summary_shown"] = False
        state["submission_requested"] = False
        state["summary_delivered_version"] = None
        state["summary_confirmed_version"] = None
        return _json({
            "success": True,
            "operation": "save_ticket_draft",
            "issue_id": active.get("id"),
            "draft_version": state["draft_version"],
            "classification_succeeded": True,
            "missing_customer_details": missing_customer_details,
            "missing_fields": missing_customer_details,
            "mandatory_fields_missing": mandatory_missing,
            "summary_ready": summary_ready,
            "unknown_fields": classified.unknown,
            "draft": classified.model_dump(),
        })

    @tool("skip_detail")
    @_logged_tool(sink)
    def skip_detail(item: str, value: Literal["unknown", "declined"], issue_id: str | None = None) -> str:
        """Record that the customer does not know (unknown) or will not give (declined) one required detail, then
        report the next missing detail or that the summary is ready. Use it instead of asking for that detail again."""

        active = state.get("active_issue")
        if active is None:
            return _json({"success": False, "operation": "skip_detail", "error": "no active issue"})
        if issue_id and issue_id != active.get("id"):
            return _json({"success": False, "operation": "skip_detail", "error": "issue is not active"})
        draft = _draft(state)
        if not state.get("ticket_draft"):
            return _json({"success": False, "operation": "skip_detail",
                          "error": "no draft yet; call save_ticket_draft first, then skip_detail"})
        if item not in draft.required:
            return _json({"success": False, "operation": "skip_detail",
                          "error": f"{item!r} is not a required detail; required: {sorted(draft.required)}"})
        if draft.required.get(item) in SKIPPED:
            # Already settled: a repeat changes nothing, so a summary already shown stays valid.
            return _json({"success": True, "operation": "skip_detail", "unchanged": True, "issue_id": active.get("id"),
                          "item": item, "value": draft.required[item], "draft_version": state.get("draft_version"),
                          "missing_customer_details": draft.missing, "missing_fields": draft.missing,
                          "summary_ready": bool(draft.classified and not draft.missing)})
        required = {**draft.required, item: value}
        missing = [key for key, given in required.items() if given is None]
        mandatory_missing = [key for key in ticket_submit.REQUIRED if not getattr(draft, key, None)]
        draft = draft.model_copy(update={"required": required, "missing": missing,
                                         "unknown": list(dict.fromkeys([*draft.unknown, item]))})
        summary_ready = bool(draft.classified and not mandatory_missing and not missing)
        state["ticket_draft"] = draft.model_dump()
        state["draft_version"] = int(state.get("draft_version") or 0) + 1
        state["summary_shown"] = False
        state["submission_requested"] = False
        state["summary_delivered_version"] = None
        state["summary_confirmed_version"] = None
        return _json({
            "success": True,
            "operation": "skip_detail",
            "issue_id": active.get("id"),
            "item": item,
            "value": value,
            "draft_version": state["draft_version"],
            "classification_succeeded": bool(draft.classified),
            "missing_customer_details": missing,
            "missing_fields": missing,
            "mandatory_fields_missing": mandatory_missing,
            "summary_ready": summary_ready,
            "draft": draft.model_dump(),
        })

    @tool("manage_issues")
    @_logged_tool(sink)
    def manage_issues(
        operation: Literal["register", "enqueue", "cancel_preparation"],
        description: str | None = None,
        issue_id: str | None = None,
        urgent: bool = False,
    ) -> str:
        """Register, enqueue, or cancel preparation of a local issue. The next queued issue becomes active on its
        own when the current one is submitted or cancelled."""

        queue = list(state.get("pending_issues", []))
        active = state.get("active_issue")
        if operation in {"register", "enqueue"}:
            if not description or not description.strip():
                return _json({"success": False, "operation": operation, "error": "description is required"})
            issue = {
                "id": issue_id or _next_issue_id(state),
                "description": description.strip(),
                "source": "urgent" if urgent else "no_answer",
                "urgent": urgent,
            }
            if operation == "register" and active is None:
                state["active_issue"] = issue
            else:
                queue.append(issue)
            state["pending_issues"] = queue
            return _json({
                "success": True,
                "operation": operation,
                "issue": issue,
                "queued": state.get("active_issue") is not issue,  # waits behind the active issue
                "active_issue": state.get("active_issue"),
                "pending_issues": queue,
            })
        elif operation == "cancel_preparation":
            # A cancelled or declined issue is finished: it stops being active and the next queued issue takes its place.
            if active is None:
                return _json({"success": False, "operation": operation, "error": "nothing to cancel: no active issue"})
            cancelled = active
            state["active_issue"] = queue.pop(0) if queue else None
            state["pending_issues"] = queue
            state["ticket_draft"] = None
            state["pending_customer_response"] = None
            state["summary_shown"] = False
            state["submission_requested"] = False
            state["summary_delivered_version"] = None
            state["summary_confirmed_version"] = None
            return _json({
                "success": True,
                "operation": operation,
                "cancelled_issue": cancelled,
                "active_issue": state.get("active_issue"),
                "pending_issues": queue,
            })
        else:
            return _json({"success": False, "operation": operation, "error": "unsupported operation"})
        return _json({
            "success": True,
            "operation": operation,
            "active_issue": state.get("active_issue"),
            "pending_issues": state.get("pending_issues", []),
        })

    @tool("submit_ticket")
    @_logged_tool(sink)
    def submit_ticket(issue_id: str, draft_version: int) -> str:
        """Submit the displayed, confirmed draft and return the actual ticket result."""

        pending = state.get("pending_customer_response")


        # Submission may be confirmed through the chat or the UI button. The
        # Operator model is responsible for recognizing an affirmative reply;
        # this tool only verifies that the current summary is eligible.
        if not state.get("summary_shown") or not state.get("submission_requested"):
            return _json({"success": False, "operation": "submit_ticket", "error": "no displayed summary is awaiting confirmation"})
        if getattr(pending, "kind", None) != "summary":
            return _json({"success": False, "operation": "submit_ticket", "error": "submission requires a pending summary confirmation"})
        if not state.get("active_issue") or state["active_issue"].get("id") != issue_id:
            return _json({"success": False, "operation": "submit_ticket", "error": "issue is not active"})
        if int(state.get("draft_version") or 0) != draft_version:
            return _json({"success": False, "operation": "submit_ticket",
                          "issue_id": issue_id, "draft_version": state.get("draft_version"),
                          "summary_delivered_version": state.get("summary_delivered_version"),
                          "error": "draft version is no longer current; use the current version only if its summary was delivered, without saving the draft again"})
        if state.get("summary_delivered_version") != draft_version:
            return _json({"success": False, "operation": "submit_ticket", "error": "the current summary has not been delivered"})
        try:
            draft = _draft(state)
            created, corrections = ticket_submit.submit(_legacy_submit_state(state, draft), ticket_api.ticket_client())
        except (ticket_submit.SubmitRefused, ticket_api.TicketApiError) as exc:
            log.error("ERROR ticket_operator tool submission failed: %s", exc)
            return _json({"success": False, "operation": "submit_ticket", "error": str(exc)})
        
        # The receipt (number, id, date) plus the submitted content; the UI shows this record after submit.
        record = {
            **created,
            **draft.model_dump(include={"title", "summary", "title_customer", "summary_customer", "queue", "type",
                                        "subtype", "priority", "required", "collected", "unknown",
                                        "collected_customer", "unknown_customer"}),
            "attachments": [f["file_name"] for f in state.get("attachments", [])],
            "message_index": len(state.get("messages", [])),  # the reply announcing it goes here
        }
        state["created_tickets"] = [*state.get("created_tickets", []), record]
        state["attachments"] = []
        state["summary_confirmed_version"] = draft_version
        state["pending_customer_response"] = None
        state["summary_shown"] = False
        state["submission_requested"] = False
        # The submitted issue is finished: the next queued issue takes its place, as after a cancel.
        queue = list(state.get("pending_issues", []))
        state["active_issue"] = queue.pop(0) if queue else None
        state["pending_issues"] = queue
        state["ticket_draft"] = None
        return _json({
            "success": True,
            "operation": "submit_ticket",
            "ticket": created,
            "corrections": corrections,
            "draft_version": draft_version,
            "active_issue": state.get("active_issue"),
            "pending_issues": queue,
        })

    return [get_ticket_requirements, save_ticket_draft, skip_detail, manage_issues, submit_ticket]
