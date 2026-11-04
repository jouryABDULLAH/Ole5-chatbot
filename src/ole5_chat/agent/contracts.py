"""Typed contracts for the owner-routing graph.

These contracts describe the graph boundary. Model implementations and state
transitions are added in later implementation steps.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

Route = Literal["question", "pending_response", "ticket_enquiry", "escalation", "other"]
RouteWithoutPending = Literal["question", "ticket_enquiry", "escalation", "other"]
NextTarget = Literal["search", "ticket_operator", "ticket_enquiry", "respond"]
WorkerName = Literal["search", "ticket_operator", "ticket_enquiry"]
PendingKind = Literal["offer", "detail", "summary"]
ManagerContext = Literal["none", "offer", "detail", "summary"]
SearchOutcome = Literal["answered", "partial", "no_answer", "failed"]
TicketStatusAction = Literal["lookup_status", "explain_status", "clarify"]
TicketListOutcome = Literal["success", "empty", "error"]
TicketLookupOutcome = Literal["found", "not_found", "status_unavailable", "error"]
IssueOperation = Literal["register", "enqueue", "cancel_preparation"]
OperatorAction = Literal[
    "offer", "request_detail", "show_summary", "declined", "cancelled",
    "queued", "submitted", "submission_failed", "clarify",
]
ValidationOutcome = Literal["pass", "repair", "fallback"]

MANAGER_ACTIONS: dict[ManagerContext, tuple[str, ...]] = {
    "none": ("register_issue", "urgent_issue", "failed_solution", "clarify"),
    "offer": ("accept", "decline", "update_impact", "separate_issue", "clarify"),
    "detail": ("provide", "skip_detail", "not_an_answer", "cancel", "update_impact", "separate_issue"),
    "summary": ("submit", "edit_summary", "cancel", "separate_issue", "clarify"),
}


class PendingCustomerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PendingKind
    issue_id: str
    item: str | None = None


class ResponsePacket(BaseModel):
    """Structured, non-rephrased context that Router sends to Respond."""

    model_config = ConfigDict(extra="forbid")

    customer_message: str
    language: Literal["ar", "en"] | None = None
    intent: Route | None = None
    search: dict | None = None
    ticket_operator: dict | None = None
    ticket_enquiry: dict | None = None
    created_ticket: dict | None = None
    pending_customer_response: PendingCustomerResponse | None = None
    sources: list[str] = []


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Route
    next_target: NextTarget
    explicit_ticket_request: bool
    reason: str


class RouterDecisionWithoutPending(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: RouteWithoutPending
    next_target: NextTarget
    explicit_ticket_request: bool
    reason: str


@lru_cache(maxsize=2)
def router_schema(has_pending: bool) -> type[BaseModel]:
    """Return the Router schema for the current pending-state shape."""

    return RouterDecision if has_pending else RouterDecisionWithoutPending


@lru_cache(maxsize=4)
def manager_schema(context: ManagerContext) -> type[BaseModel]:
    """Return the Manager action schema for one pending interaction kind."""

    return create_model(
        f"ManagerDecision_{context}",
        __config__=ConfigDict(extra="forbid", strict=True),
        action=(Literal[MANAGER_ACTIONS[context]], ...),
        reason=(str, ...),
    )


class TicketStatusDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: TicketStatusAction
    reason: str


class TicketListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ticket_number: str
    title: str
    product: str
    created_at: str


class TicketListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: TicketListOutcome
    tickets: list[TicketListItem] = []
    error: str | None = None


class TicketLookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: TicketLookupOutcome
    ticket: dict | None = None
    status_available: bool = False
    error: str | None = None


class TicketEnquiryCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: TicketStatusAction
    reason: str
    selected_ticket_numbers: list[str] = []
    clarification_question: str | None = None


class OperatorCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: OperatorAction
    reason: str
    issue_id: str | None = None
    requested_detail: str | None = None


class OperatorToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    success: bool
    operation: str
    issue_id: str | None = None
    draft_version: int | None = None
    missing_fields: list[str] = []
    unknown_fields: list[str] = []
    active_issue: dict | None = None
    pending_issues: list[dict] = []
    draft: dict | None = None
    ticket: dict | None = None
    error: str | None = None


class Grounding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unsupported: list[str]


class TicketActionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violations: list[str]


class Language(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["ar", "en", "none"]


class ValidationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: ValidationOutcome
    reason: str
