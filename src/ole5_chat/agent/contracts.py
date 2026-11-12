"""Typed contracts for the owner-routing graph.

These contracts describe the graph boundary. Model implementations and state
transitions are added in later implementation steps.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

MANAGER_ACTIONS: dict[Literal["none", "offer", "detail", "summary"], tuple[str, ...]] = {
    "none": ("register_issue", "urgent_issue", "failed_solution", "clarify"),
    "offer": ("accept", "decline", "update_impact", "separate_issue", "clarify"),
    "detail": ("provide", "skip_detail", "not_an_answer", "cancel", "update_impact", "separate_issue"),
    "summary": ("submit", "edit_summary", "cancel", "separate_issue", "clarify"),
}


class PendingCustomerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["offer", "detail", "summary"]
    issue_id: str
    item: str | None = None


class ResponsePacket(BaseModel):
    """Structured, non-rephrased context that Router sends to Respond."""

    model_config = ConfigDict(extra="forbid")

    customer_message: str
    language: Literal["ar", "en"] | None = None
    intent: Literal["question", "pending_response", "ticket_enquiry", "needs_ticket", "other"] | None = None
    search: dict | None = None
    ticket_operator: dict | None = None
    ticket_enquiry: dict | None = None
    created_ticket: dict | None = None
    pending_customer_response: PendingCustomerResponse | None = None
    sources: list[str] = []


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal["question", "pending_response", "ticket_enquiry", "needs_ticket", "other"]
    next_target: Literal["search", "ticket_operator", "ticket_enquiry", "respond"]
    explicit_ticket_request: bool
    reason: str


class RouterDecisionWithoutPending(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal["question", "ticket_enquiry", "needs_ticket", "other"]
    next_target: Literal["search", "ticket_operator", "ticket_enquiry", "respond"]
    explicit_ticket_request: bool
    reason: str


class ReturnDecision(BaseModel):
    """The Router's return pass, after a worker: where the result goes. The message is not classified again."""
    model_config = ConfigDict(extra="forbid")

    next_target: Literal["respond", "ticket_operator"] = Field(
        description="Where the completed worker's result goes next.")
    reason: str = Field(description="One sentence: the worker result that decides it.")


@lru_cache(maxsize=2)
def router_schema(has_pending: bool) -> type[BaseModel]:
    """Return the Router schema for the current pending-state shape."""

    return RouterDecision if has_pending else RouterDecisionWithoutPending


@lru_cache(maxsize=4)
def manager_schema(context: Literal["none", "offer", "detail", "summary"]) -> type[BaseModel]:
    """Return the Manager action schema for one pending interaction kind."""

    return create_model(
        f"ManagerDecision_{context}",
        __config__=ConfigDict(extra="forbid", strict=True),
        action=(Literal[MANAGER_ACTIONS[context]], ...),
        reason=(str, ...),
    )


class TicketEnquiryCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["lookup_status", "explain_status", "clarify"]
    reason: str
    selected_ticket_numbers: list[str] = []
    clarification_question: str | None = None


class OperatorCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal[
        "offer", "request_detail", "show_summary", "declined", "cancelled",
        "queued", "submitted", "submission_failed", "clarify", "already_submitted",
    ]
    reason: str
    issue_id: str | None = None
    requested_detail: str | None = None
    existing_ticket: str | None = None  # already_submitted: the number of the ticket that has this problem


class GroundingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(description="The unsupported sentence, copied exactly as it is written in the reply.")
    problem: str = Field(description="What in it no passage or company fact says.")
    passage: str = Field(description="The index of the closest passage, e.g. 2, or none.")


class Grounding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unsupported: list[GroundingIssue] = Field(description="One entry per unsupported product sentence; empty when "
                                                          "every product sentence is supported.")


class TicketViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(description="The wrong sentence, copied exactly as it is written in the reply.")
    problem: str = Field(description="What is false or unverified about that sentence.")
    fact: str = Field(description="The payload value that settles it, named and quoted, e.g. created_tickets has "
                                  "T-1 for that request. If no payload value contradicts the sentence, it is not a "
                                  "violation.")


class TicketActionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violations: list[TicketViolation] = Field(description="One entry per wrong sentence; empty when the reply is "
                                                          "consistent. Respond rewrites only these sentences.")


class Language(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["ar", "en", "none"]


