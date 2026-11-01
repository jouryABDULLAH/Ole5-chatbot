"""Typed contracts for the new owner-routing graph.

These contracts describe the graph boundary. Model implementations and state
transitions are added in later implementation steps.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

Route = Literal["question", "pending_response", "ticket_status", "escalation", "other"]
RouteWithoutPending = Literal["question", "ticket_status", "escalation", "other"]
PendingKind = Literal["offer", "detail", "summary"]
ManagerContext = Literal["none", "offer", "detail", "summary"]
SearchOutcome = Literal["answered", "partial", "no_answer", "failed"]
TicketStatusAction = Literal["lookup_status", "explain_status", "clarify"]
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


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Route
    explicit_ticket_request: bool
    reason: str


class RouterDecisionWithoutPending(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: RouteWithoutPending
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


class ValidationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: ValidationOutcome
    reason: str
