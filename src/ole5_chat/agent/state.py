"""The state of one conversation, as LangGraph carries it between messages. Field names follow the requirements' model output."""

from typing import Literal

from langgraph.graph import MessagesState
from pydantic import BaseModel, ConfigDict

from ole5_chat.agent.contracts import Reply, RouterDecision, Step
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
    collected: dict[str, str] = {}
    missing: list[str] = []
    confidence: Literal["high", "medium", "low"] | None = None
    queue_reason: str | None = None


class ChatState(MessagesState):
    identity: Identity
    phase: Step
    sources: list[str]
    decision: RouterDecision | None
    last_retrieval: dict | None
    reply: Reply | None
    retry_note: str | None
    retried: bool
    ticket_draft: TicketDraft | None
    corrections: list[str]
    created_ticket: dict | None


def initial_state(identity: Identity) -> ChatState:
    return ChatState(messages=[], identity=identity, phase="greet", sources=[], decision=None, last_retrieval=None,
                     reply=None, retry_note=None, retried=False, ticket_draft=None, corrections=[],
                     created_ticket=None)
