"""The handlers: the nodes that apply the router's next. Each applies the router's next to the state and sets the task respond_agent carries out this turn. post_check runs after respond_agent and queues a question the reply offered a ticket for. pending[0] is always the issue being handled; a new issue waits behind it."""

import logging

from ole5_chat import values
from ole5_chat.agent import classify as classifier
from ole5_chat.agent.contracts import PendingIssue, Phase
from ole5_chat.agent.prompt import TASKS, THEN_OFFER, URGENT
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.config import get_settings
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets import submit as tickets
from ole5_chat.tickets.client import TicketApiError

log = logging.getLogger(__name__)

# Handlers that work on an issue themselves; post_check leaves their turns alone.
OWN_ISSUE = ("ticket", "urgent", "accept", "decline", "details", "done", "change", "cancel", "yes")
HANDLERS = {"ticket": "queue", "urgent": "queue", "chat": "chat", "accept": "accept", "decline": "decline",
            "details": "details", "done": "done", "change": "change", "cancel": "cancel", "yes": "submit"}
NOT_CREATED = "The ticket could not be created. Say so in one line and ask them to try again."


def advance(pending: list[PendingIssue]) -> Phase:
    return "none" if not pending else "offer_ticket"


def _new_draft(state: ChatState) -> TicketDraft:
    return TicketDraft(product=values.catalog(state["identity"].product).service)


def _offer(issue: PendingIssue) -> str:
    return (URGENT if issue.urgent else "") + TASKS["offer"].format(issue=issue.text)


def _then_offer(task: str, pending: list[PendingIssue]) -> str:
    """The task, followed by the offer of the next issue when one is waiting."""
    if not pending:
        return task
    return f"{task} {URGENT if pending[0].urgent else ''}{THEN_OFFER.format(next=pending[0].text)}"


def handler(state: ChatState) -> str:
    name = HANDLERS.get(state["next"])
    if name is None:
        log.info("handlers  handler not built yet: %s", state["next"])
        return "chat"
    return name


def chat(state: ChatState) -> dict:
    phase, pending = state["phase"], state["pending"]
    if phase == "none":
        return {"task": TASKS["chat"]}
    key = "chat_offer_ticket" if phase == "offer_ticket" else "chat_ticket"
    return {"task": TASKS[key].format(issue=pending[0].text)}


def queue(state: ChatState) -> dict:
    text, pending = state["messages"][-1].text, list(state["pending"])
    if not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, urgent=state["next"] == "urgent", source="always_ticket"))
    if state["phase"] == "none":
        return {"pending": pending, "phase": advance(pending), "task": _offer(pending[0])}
    return {"pending": pending, "task": TASKS["queued"].format(new=text, issue=pending[0].text)}


def accept(state: ChatState) -> dict:
    return {"ticket_draft": _new_draft(state), "phase": "collect",
            "task": TASKS["accept"].format(issue=state["pending"][0].text)}


def decline(state: ChatState) -> dict:
    pending = state["pending"][1:]
    task = TASKS["decline_next"].format(issue=pending[0].text) if pending else TASKS["decline"]
    return {"pending": pending, "phase": advance(pending), "task": task}


def details(state: ChatState) -> dict:
    draft = state["ticket_draft"]
    rounds = draft.rounds + 1
    key = "open_question" if rounds >= get_settings().collect_rounds else "details"
    return {"ticket_draft": draft.model_copy(update={"rounds": rounds}),
            "task": TASKS[key].format(issue=state["pending"][0].text)}


def done(state: ChatState) -> dict:
    out = classifier.classify(state)
    if "ticket_draft" in out:
        return {**out, "phase": "confirm", "task": TASKS["confirm"]}
    return {**out, "task": TASKS["open_question"].format(issue=state["pending"][0].text)}


def change(state: ChatState) -> dict:
    out = classifier.classify(state)
    return {**out, "task": TASKS["changed"] if "ticket_draft" in out else TASKS["confirm"]}


def cancel(state: ChatState) -> dict:
    pending = state["pending"][1:]
    return {"ticket_draft": None, "pending": pending, "phase": advance(pending),
            "task": _then_offer(TASKS["cancelled"], pending)}


def submit(state: ChatState) -> dict:
    try:
        tickets.check(state)
        ticket, corrections = tickets.submit(state, ticket_api.ticket_client())
    except (tickets.SubmitRefused, TicketApiError) as exc:
        log.warning("handlers  ticket not created: %s", exc)
        return {"note": NOT_CREATED, "task": TASKS["confirm"],
                "corrections": state["corrections"] + [f"submit failed: {exc}"]}
    pending = state["pending"][1:]
    return {"created_tickets": [*state["created_tickets"], ticket], "ticket_draft": None, "attachments": [],
            "pending": pending, "phase": advance(pending),
            "task": _then_offer(TASKS["created"].format(number=ticket["ticket_number"]), pending),
            "corrections": state["corrections"] + corrections}


def post_check(state: ChatState) -> dict:
    """Queues the customer's message when the reply offered a ticket for it. 
    In offer_ticket the chat task makes every reply ask about the open offer again, 
    so there a reply with sources is a reminder, not an offer."""
    text, before, pending = state["messages"][-1].text, state["phase"], list(state["pending"])
    offered = state["offered"] and state["next"] not in OWN_ISSUE
    if before == "offer_ticket":
        offered = offered and not state["reply"].sources
    if offered and not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, source="no_answer"))
    after = advance(pending) if before == "none" else before
    log.info("post_check  phase %s -> %s pending=%d", before, after, len(pending))
    return {"pending": pending, "phase": after}
