"""What each LLM node must return. The model fills these; code checks them before anything reaches the customer or a ticket."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Step = Literal["greet", "answer", "ask", "offer_ticket", "collect", "confirm", "created"]


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["greeting", "question", "ticket_reply", "other"]
    queries: list[str] = []
    always_ticket: bool = False
    urgent: bool = False
    language: Literal["ar", "en"]


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str
    step: Step
    sources: list[str] = []
    collected: dict[str, str] = {}
    missing: list[str] = []


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
