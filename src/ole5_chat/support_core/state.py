"""The state of one conversation, as LangGraph carries it between messages. Field names follow the requirements' model output."""

from typing import Literal

from langgraph.graph import MessagesState
from pydantic import BaseModel, ConfigDict

from ole5_chat.support_core.contracts import PendingIssue
from ole5_chat.identity import Identity


class TicketDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str
    queue: str | None = None
    type: str | None = None
    subtype: str | None = None
    reason: str | None = None
    priority: str | None = None
    urgent: bool = False
    urgent_reason: str | None = None
    title: str | None = None
    summary: str | None = None
    title_customer: str | None = None
    summary_customer: str | None = None
    collected: dict[str, str] = {}
    missing: list[str] = []  # the required items still None, kept for the internal note
    unknown: list[str] = []
    required: dict[str, str | None] = {}
    collected_customer: dict[str, str] = {}
    unknown_customer: list[str] = []
    classified: bool = False
    rounds: int = 0
    confidence: Literal["high", "medium", "low"] | None = None
    queue_reason: str | None = None


class ChatState(MessagesState):
    identity: Identity
    ticket_draft: TicketDraft | None
    corrections: list[str]
    attachments: list[dict]
    phase: Literal["none", "offer_ticket", "collect", "confirm"]
    pending: list[PendingIssue]


