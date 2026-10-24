"""What each LLM node must return. The router reports facts about the message, code picks the step from them, respond writes the text for that step, and code checks it before anything reaches the customer or a ticket."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Step = Literal["greet", "answer", "ask", "offer_ticket", "collect", "confirm", "created"]


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
