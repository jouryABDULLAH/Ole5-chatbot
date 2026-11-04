"""One TurnSheet per Respond turn: the facts of this turn and a stage, computed from state in code.

The model never infers the workflow from flags; it reads the sheet as a note after the chat messages."""

from typing import Literal, TypedDict

from ole5_chat.agent.state import AgentState

Stage = Literal[
    "answered", "no_answer", "ticket_status",
    "offer", "detail_requested", "summary_shown",
    "submitted", "submission_failed", "preparation_stopped",
    "queued", "clarify", "other",
]


class TurnSheet(TypedDict):
    stage: Stage
    language: str
    customer_message: str | None    # this turn's message; None on a button turn
    button: str | None             # "submit" on a Submit-button turn (input_event), else None
    search: str                     # "answered with N passages" | "no answer" | "not run"
    search_no_answer: bool          # outcome partial or no_answer and no DIRECT passage
    operator: str                   # what the ticket workflow did, in plain words, or "not run"
    issue: str | None
    requested_detail: str | None
    skipped: str | None             # e.g. "page (unknown)"
    cancelled_issue: str | None
    queued_issue: str | None
    next_issue: str | None          # the queued issue offered after a submit
    transition: str | None          # one sentence when one issue stopped and the next one is offered
    ticket_number: str | None       # only when the submission succeeded
    pending_kind: str | None        # offer | detail | summary pending now, after the workflow ran
    pending_item: str | None
    remind_pending: bool            # a step is pending and this turn did not answer it
    draft: dict | None              # the ticket being prepared, when a step is pending
    explicit_ticket_request: bool
    correction: str | None          # validation note from a rejected reply
    rejected_reply: str | None


OPERATOR_FACT = {
    "offer": "offered to prepare a support ticket for the issue",
    "request_detail": "needs one more detail before the ticket is complete",
    "show_summary": "prepared the ticket summary; it is shown on a card and waits for the customer's confirmation",
    "submitted": "submitted the ticket",
    "submission_failed": "tried to submit the ticket; the ticket was not created",
    "declined": "stopped preparing the ticket because the customer declined",
    "cancelled": "stopped preparing the ticket because the customer cancelled",
    "queued": "will handle the new issue after the current one",
    "clarify": "needs a clarification before it can act",
}


def build_turn_sheet(state: AgentState, language: str) -> TurnSheet:
    intent = state.get("intent") or "other"
    outcome = state.get("search_outcome") or "none"
    direct = [r for r in state.get("current_references", []) if r.get("relation") == "DIRECT"]
    # ticket_operator_result is turn-scoped (finalize clears it), so its action is this turn's whatever the intent:
    # a Submit-button turn has no customer message and no Router intent, but its action is still "submitted".
    op = state.get("ticket_operator_result") or {}
    action = op.get("action")
    result = op.get("ticket_result") or {}
    succeeded = bool(result.get("submission_succeeded"))
    pending = state.get("pending_customer_response")
    messages = state.get("messages", [])
    last = messages[-1] if messages else None

    if intent == "question" and outcome == "answered":
        stage = "answered"
    elif intent == "ticket_enquiry" and state.get("ticket_enquiry_result"):
        stage = "ticket_status"
    elif action == "submitted" and succeeded:
        stage = "submitted"
    elif action in {"submission_failed", "submitted"}:
        stage = "submission_failed"
    elif action == "show_summary":
        stage = "summary_shown"
    elif action == "request_detail":
        stage = "detail_requested"
    elif action in {"declined", "cancelled"}:
        stage = "preparation_stopped"
    elif action in {"offer", "queued", "clarify"}:
        stage = action
    elif intent == "other":
        stage = "other"
    elif outcome in {"failed", "no_answer", "partial"}:
        stage = "no_answer"
    else:
        stage = "other"

    draft = state.get("ticket_draft") or {}
    return TurnSheet(
        stage=stage,
        language=language,
        customer_message=last.text if last is not None and last.type == "human" else None,
        button="submit" if state.get("input_event") == "submit" else None,
        search=(f"answered with {len(direct)} passages" if direct
                else "no answer" if outcome in {"partial", "no_answer", "failed"} else "not run"),
        search_no_answer=outcome in {"partial", "no_answer"} and not direct,
        operator=OPERATOR_FACT.get(action, "not run"),
        issue=op.get("issue") or (state.get("active_issue") or {}).get("description"),
        requested_detail=op.get("requested_detail"),
        skipped=", ".join(f"{s.get('item')} ({s.get('value')})" for s in op.get("skipped") or []) or None,
        cancelled_issue=op.get("cancelled_issue"),
        queued_issue=op.get("queued_issue"),
        next_issue=op.get("next_issue"),
        transition=(f"Preparation for «{op['cancelled_issue']}» was stopped; the next issue «{op.get('issue')}» "
                    "is now offered." if op.get("cancelled_issue") and action == "offer" else None),
        ticket_number=(result.get("ticket") or {}).get("ticket_number") if succeeded else None,
        pending_kind=getattr(pending, "kind", None),
        pending_item=getattr(pending, "item", None),
        remind_pending=bool(pending) and intent != "pending_response" and not op
                       and state.get("input_event") != "submit",
        draft=({key: draft.get(key) for key in ("title_customer", "queue", "type", "subtype", "priority", "missing")}
               if draft and pending else None),
        explicit_ticket_request=bool(state.get("explicit_ticket_request")),
        correction=state.get("validation_note"),
        rejected_reply=state.get("reply") if state.get("validation_note") else None,
    )
