"""The state of one conversation, as LangGraph carries it between messages. Field names follow the requirements' model output."""

from typing import Literal

from langgraph.graph import MessagesState
from pydantic import BaseModel, ConfigDict

from ole5_chat.agent.contracts import Next, PendingIssue, Phase, Reply, RouterDecision, Step
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
    confidence: Literal["high", "medium", "low"] | None = None
    queue_reason: str | None = None


class ChatState(MessagesState):
    identity: Identity
    phase: Step
    step: Step | None
    sources: list[str]
    decision: RouterDecision | None
    last_retrieval: dict | None
    reply: Reply | None
    note: str | None
    ticket_draft: TicketDraft | None
    corrections: list[str]
    created_tickets: list[dict]
    attachments: list[dict]
    chat_phase: Phase  # Temporary name; step 4 renames it to phase.
    pending: list[PendingIssue]
    next: Next | None
    task: str | None
    language: str | None
    offered: bool  # Temporary: carries the grounding result from respond_agent to the post-check.


def initial_state(identity: Identity) -> ChatState:
    return ChatState(messages=[], identity=identity, phase="greet", step=None, sources=[], decision=None,
                     last_retrieval=None, reply=None, note=None, ticket_draft=None, corrections=[], created_tickets=[],
                     attachments=[], chat_phase="none", pending=[], next=None, task=None, language=None,
                     offered=False)
