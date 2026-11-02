"""Shared ticket-classification models used by the active agent and submission flow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class PendingIssue(BaseModel):
    text: str
    urgent: bool = False
    source: Literal["no_answer", "always_ticket"]


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: str | None = None
    type: str | None = None
    subtype: str | None = None
    reason: str | None = None
    priority: str | None = None
    urgent: bool = False
    urgent_reason: str | None = None
    title: str | None = None
    summary: str | None = None
    # The same two in the customer's language, shown on the card; the ticket itself is sent in English.
    title_customer: str | None = None
    summary_customer: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    queue_reason: str | None = None
    collected: dict[str, str] = {}
    unknown: list[str] = []
    # The product's required items for this kind of problem: the value given, "unknown", "declined", or None when
    # not given yet. missing is not a model field: code derives it from the None entries.
    required: dict[str, str | None] = {}
    # collected and unknown again in the customer's language, for the card.
    collected_customer: dict[str, str] = {}
    unknown_customer: list[str] = []
