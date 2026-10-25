"""The lanes of the new graph. Each applies the router's next to the state and sets the task respond_agent carries out this turn. post_check runs after respond_agent and queues a question the reply offered a ticket for. pending[0] is always the issue being handled; a new issue waits behind it."""

import logging

from ole5_chat import values
from ole5_chat.agent.contracts import PendingIssue, Phase
from ole5_chat.agent.prompt import TASKS, URGENT
from ole5_chat.agent.state import ChatState, TicketDraft

log = logging.getLogger(__name__)

# Lanes that queue, accept or decline an issue themselves; post_check leaves their turns alone.
OWN_ISSUE = ("ticket", "urgent", "accept", "decline")


def advance(pending: list[PendingIssue]) -> Phase:
    return "none" if not pending else "offer_ticket"


def _new_draft(state: ChatState) -> TicketDraft:
    return TicketDraft(product=values.catalog(state["identity"].product).service)


def _offer(issue: PendingIssue) -> str:
    return (URGENT if issue.urgent else "") + TASKS["offer"].format(issue=issue.text)


def lane(state: ChatState) -> str:
    next_ = state["next"]
    if next_ in ("ticket", "urgent"):
        return "queue"
    if next_ in ("chat", "accept", "decline"):
        return next_
    log.info("lanes  lane not built yet: %s", next_)
    return "chat"


def chat(state: ChatState) -> dict:
    phase, pending = state["chat_phase"], state["pending"]
    if phase == "none":
        return {"task": TASKS["chat"]}
    key = "chat_offer_ticket" if phase == "offer_ticket" else "chat_ticket"
    return {"task": TASKS[key].format(issue=pending[0].text)}


def queue(state: ChatState) -> dict:
    text, pending = state["messages"][-1].text, list(state["pending"])
    if not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, urgent=state["next"] == "urgent", source="always_ticket"))
    if state["chat_phase"] == "none":
        return {"pending": pending, "chat_phase": advance(pending), "task": _offer(pending[0])}
    return {"pending": pending, "task": TASKS["queued"].format(new=text, issue=pending[0].text)}


def accept(state: ChatState) -> dict:
    return {"ticket_draft": _new_draft(state), "chat_phase": "collect",
            "task": TASKS["accept"].format(issue=state["pending"][0].text)}


def decline(state: ChatState) -> dict:
    pending = state["pending"][1:]
    task = TASKS["decline_next"].format(issue=pending[0].text) if pending else TASKS["decline"]
    return {"pending": pending, "chat_phase": advance(pending), "task": task}


def post_check(state: ChatState) -> dict:
    """Queues the customer's message when the reply offered a ticket for it. 
    In offer_ticket the chat task makes every reply ask about the open offer again, 
    so there a reply with sources is a reminder, not an offer."""
    text, before, pending = state["messages"][-1].text, state["chat_phase"], list(state["pending"])
    offered = state["offered"] and state["next"] not in OWN_ISSUE
    if before == "offer_ticket":
        offered = offered and not state["reply"].sources
    if offered and not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, source="no_answer"))
    after = advance(pending) if before == "none" else before
    log.info("post_check  phase %s -> %s pending=%d", before, after, len(pending))
    return {"pending": pending, "chat_phase": after}
