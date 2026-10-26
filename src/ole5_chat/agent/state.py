"""The state of one conversation, as LangGraph carries it between messages. Field names follow the requirements' model output."""

from typing import Literal

from langgraph.graph import MessagesState
from pydantic import BaseModel, ConfigDict

from ole5_chat.agent.contracts import Next, PendingIssue, Phase, Reply
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
    unknown: list[str] = []
    classified: bool = False
    rounds: int = 0
    confidence: Literal["high", "medium", "low"] | None = None
    queue_reason: str | None = None


class ChatState(MessagesState):
    identity: Identity
    sources: list[str]
    last_retrieval: dict | None
    reply: Reply | None
    note: str | None
    ticket_draft: TicketDraft | None
    corrections: list[str]
    created_tickets: list[dict]
    attachments: list[dict]
    phase: Phase
    pending: list[PendingIssue]
    next: Next | None
    task: str | None
    language: str | None
    offered: bool  # Set by respond_agent on a question turn it had no answer for; read by post_check.
    channel: str  # "chat" or "voice": how this turn's message arrived. Passed on every turn; LangGraph keeps the last value.
    heard_language: str | None  # On a voice turn, the language speech-to-text heard ("ar" or "en").


def initial_state(identity: Identity) -> ChatState:
    return ChatState(messages=[], identity=identity, sources=[],
                     last_retrieval=None, reply=None, note=None, ticket_draft=None, corrections=[], created_tickets=[],
                     attachments=[], phase="none", pending=[], next=None, task=None, language=None,
                     offered=False, channel="chat", heard_language=None)
