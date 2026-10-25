"""What each LLM node must return. The router reports facts about the message, code picks the step from them, respond writes the text for that step, and code checks it before anything reaches the customer or a ticket."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

Step = Literal["greet", "answer", "ask", "offer_ticket", "collect", "confirm", "created"]

Phase = Literal["none", "offer_ticket", "collect", "confirm"]
Next = Literal["chat", "ticket", "urgent", "status", "accept", "decline", "details", "done", "cancel", "yes", "change"]

OPTIONS: dict[Phase, tuple[Next, ...]] = {
    "none": ("chat", "ticket", "urgent", "status"),
    "offer_ticket": ("accept", "decline", "chat", "ticket", "urgent", "status"),
    "collect": ("details", "done", "cancel", "chat", "ticket", "urgent", "status"),
    "confirm": ("yes", "change", "cancel", "chat", "ticket", "urgent", "status"),
}


class PendingIssue(BaseModel):
    text: str
    urgent: bool = False
    source: Literal["no_answer", "always_ticket"]


@lru_cache(maxsize=4)
def route_schema(phase: Phase) -> type[BaseModel]:
    """The router's reply for one phase: next may only take that phase's options."""
    return create_model("RouteDecision", __config__=ConfigDict(extra="forbid"), next=(Literal[OPTIONS[phase]], ...))


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["greeting", "question", "ticket_reply", "other"]
    queries: list[str] = []
    always_ticket: bool = False
    urgent: bool = False
    language: Literal["ar", "en"]
    collected: dict[str, str] = {}
    missing: list[str] = []
    unknown: list[str] = []
    cancel_ticket: bool = False


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
    confidence: Literal["high", "medium", "low"] | None = None
    queue_reason: str | None = None
