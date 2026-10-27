"""What each LLM node must return, and the phases and router options they work with. The router picks next from the options of the phase, respond_agent writes the reply, and classify fills the ticket's values."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

Phase = Literal["none", "offer_ticket", "collect", "confirm"]
Next = Literal["question", "talk", "ticket", "urgent", "status", "accept", "decline", "details", "done", "cancel", "yes", "change"]

OPTIONS: dict[Phase, tuple[Next, ...]] = {
    "none": ("question", "talk", "ticket", "urgent", "status"),
    "offer_ticket": ("accept", "decline", "question", "talk", "ticket", "urgent", "status"),
    "collect": ("details", "done", "cancel", "question", "talk", "ticket", "urgent", "status"),
    "confirm": ("yes", "change", "cancel", "question", "talk", "ticket", "urgent", "status"),
}


class PendingIssue(BaseModel):
    text: str
    urgent: bool = False
    source: Literal["no_answer", "always_ticket"]


@lru_cache(maxsize=4)
def route_schema(phase: Phase) -> type[BaseModel]:
    """The router's reply for one phase: next may only take that phase's options."""
    return create_model("RouteDecision", __config__=ConfigDict(extra="forbid"), next=(Literal[OPTIONS[phase]], ...))


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str
    sources: list[str] = Field([], description="Document names exactly as written before the slash in the evidence, one per document used.")


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
    missing: list[str] = []
    unknown: list[str] = []
