"""The handlers: the nodes that apply the router's next. Each applies the router's next to the state and sets the task respond_agent carries out this turn. post_check runs after respond_agent and queues a question the reply offered a ticket for. pending[0] is always the issue being handled; a new issue waits behind it."""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ole5_chat import products, values
from ole5_chat.agent import classify as classifier
from ole5_chat.agent.contracts import PendingIssue, Phase
from ole5_chat.agent.tasks import CHANNEL, CURRENT, HOURS, QUEUED_URGENT, STATE_MEANINGS, TASKS, URGENT
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.config import get_settings
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets import status as ticket_status
from ole5_chat.tickets import submit as tickets
from ole5_chat.tickets.client import TicketApiError

log = logging.getLogger(__name__)

HANDLERS = {"ticket": "queue", "urgent": "queue", "question": "chat", "talk": "chat", "accept": "accept", "decline": "decline",
            "details": "details", "done": "done", "change": "change", "cancel": "cancel", "yes": "submit",
            "status": "status"}
NOT_CREATED = "The ticket could not be created. Say so in one line and ask them to try again."
NOT_READ = "The list of their tickets could not be read right now. Say so in one line and ask them to try again later."

def advance(pending: list[PendingIssue]) -> Phase:
    return "none" if not pending else "offer_ticket"


def _new_draft(state: ChatState) -> TicketDraft:
    return TicketDraft(product=values.catalog(state["identity"].product).service)


def _offer(issue: PendingIssue) -> str:
    return TASKS["offer"].format(issue=issue.text)


def next_issue(state: ChatState, pending: list[PendingIssue]) -> tuple[dict, str]:
    """The state update and task fragment for pending[0] after pending changed. An urgent issue skips the offer and
    the questions: its draft is classified now and shown for Submit."""
    if not pending:
        return {"pending": pending, "phase": "none"}, ""
    issue = pending[0]
    if not issue.urgent:
        return {"pending": pending, "phase": "offer_ticket"}, _offer(issue)
    out = classifier.classify({**state, "pending": pending, "ticket_draft": _new_draft(state)})
    if "ticket_draft" not in out:
        return {**out, "pending": pending, "phase": "offer_ticket"}, URGENT + _offer(issue)
    draft = out["ticket_draft"].model_copy(update={"urgent": True,
                                                   "urgent_reason": out["ticket_draft"].urgent_reason or issue.text})
    return {"pending": pending, "phase": "confirm", "ticket_draft": draft}, TASKS["urgent_ready"].format(issue=issue.text)


def _then(task: str, fragment: str) -> str:
    return f"{task} {fragment}" if fragment else task


def _support(state: ChatState, field: str, sentence: str) -> str:
    """The product's support hours or channel in the customer's language, set in sentence; empty when it has none."""
    texts = getattr(products.load(state["identity"].product), field)
    text = texts.get(state.get("language") or state["identity"].language) or texts.get("en")
    return sentence.format(text) if text else ""


def handler(state: ChatState) -> str:
    name = HANDLERS.get(state["next"])
    if name is None:
        log.info("handlers  handler not built yet: %s", state["next"])
        return "chat"
    return name


def chat(state: ChatState) -> dict:
    phase, pending = state["phase"], state["pending"]
    if phase == "none":
        return {"task": TASKS["talk" if state["next"] == "talk" else "question"]}
    issue = pending[0].text
    if phase == "offer_ticket":
        return {"task": TASKS["chat_offer_ticket"].format(issue=issue)}
    return {"task": TASKS["chat_ticket"].format(current=CURRENT[phase].format(issue=issue))}


def queue(state: ChatState) -> dict:
    text, pending = state["messages"][-1].text, list(state["pending"])
    if not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, urgent=state["next"] == "urgent", source="always_ticket"))
    if state["phase"] == "none":
        update, fragment = next_issue(state, pending)
        return {**update, "task": fragment}
    issue = pending[0].text
    current = CURRENT[state["phase"]].format(issue=issue)
    queued = TASKS["queued"].format(new=text, issue=issue, current=current)
    return {"pending": pending, "task": queued + (QUEUED_URGENT if state["next"] == "urgent" else "")}


def _ask_or_confirm(state: ChatState, draft: TicketDraft, first: bool) -> dict:
    """Collect asks for the required items one at a time, and stops when none is missing or the rounds are used up:
    then the summary is shown, with what is still missing kept in the draft for the internal note."""
    issue = state["pending"][0].text
    missing = [k for k, v in draft.required.items() if v is None]
    if not missing or draft.rounds >= get_settings().collect_rounds:
        return {"ticket_draft": draft, "phase": "confirm", "task": TASKS["confirm"].format(issue=issue)}
    key = "ask_direct" if first and len(draft.required) == 1 else "ask_first" if first else "ask_next"
    return {"ticket_draft": draft, "phase": "collect", "task": TASKS[key].format(issue=issue, item=missing[0])}


def _classified(state: ChatState, draft: TicketDraft, first: bool) -> dict:
    """classify on the draft, then ask or confirm. When classify fails the draft stays as it is, collect goes on, and
    the customer is asked to send their last message again (the task says it, so classify's note is dropped)."""
    out = classifier.classify({**state, "ticket_draft": draft})
    if "ticket_draft" not in out:
        return {**out, "note": None, "ticket_draft": draft, "phase": "collect", "task": TASKS["retry_summary"]}
    return _ask_or_confirm(state, out["ticket_draft"], first)


def accept(state: ChatState) -> dict:
    return _classified(state, _new_draft(state), first=True)


def decline(state: ChatState) -> dict:
    update, fragment = next_issue(state, state["pending"][1:])
    declined = TASKS["declined"].format(channel=_support(state, "support_channel", CHANNEL))
    return {**update, "task": _then(declined, fragment)}


def details(state: ChatState) -> dict:
    draft = state["ticket_draft"]
    return _classified(state, draft.model_copy(update={"rounds": draft.rounds + 1}), first=False)


def _nothing_missing(draft: TicketDraft) -> TicketDraft:
    """done means the customer has nothing more to give: what is still missing is what they don't know."""
    required = {k: "unknown" if v is None else v for k, v in draft.required.items()}
    return draft.model_copy(update={"unknown": list(dict.fromkeys([*draft.unknown, *draft.missing])), "missing": [],
                                    "required": required})


def done(state: ChatState) -> dict:
    """When classify fails, a draft classified earlier is shown as it is; only a draft never classified asks again."""
    out = classifier.classify(state)
    issue = state["pending"][0].text
    if "ticket_draft" in out:
        return {"ticket_draft": _nothing_missing(out["ticket_draft"]), "phase": "confirm",
                "task": TASKS["confirm"].format(issue=issue)}
    if state["ticket_draft"].classified:
        return {**out, "note": None, "ticket_draft": _nothing_missing(state["ticket_draft"]), "phase": "confirm",
                "task": TASKS["confirm"].format(issue=issue)}
    return {**out, "note": None, "task": TASKS["retry_summary"]}


def change(state: ChatState) -> dict:
    out = classifier.classify(state)
    if "ticket_draft" not in out:
        return {**out, "task": TASKS["confirm"].format(issue=state["pending"][0].text)}
    draft, old = out["ticket_draft"], state["ticket_draft"]
    if state["pending"][0].urgent:  # An urgent ticket stays urgent and keeps what is missing: it skipped the questions.
        draft = draft.model_copy(update={"urgent": True, "urgent_reason": draft.urgent_reason or old.urgent_reason})
    else:
        draft = _nothing_missing(draft)
    return {"ticket_draft": draft, "task": TASKS["changed"].format(issue=state["pending"][0].text)}


def cancel(state: ChatState) -> dict:
    update, fragment = next_issue({**state, "ticket_draft": None}, state["pending"][1:])
    cancelled = TASKS["cancelled"].format(channel=_support(state, "support_channel", CHANNEL))
    return {"ticket_draft": None, **update, "task": _then(cancelled, fragment)}


def submit(state: ChatState) -> dict:
    try:
        tickets.check(state)
        ticket, corrections = tickets.submit(state, ticket_api.ticket_client())
    except (tickets.SubmitRefused, TicketApiError) as exc:
        log.warning("handlers  ticket not created: %s", exc)
        return {"note": NOT_CREATED, "task": TASKS["confirm"].format(issue=state["pending"][0].text),
                "corrections": state["corrections"] + [f"submit failed: {exc}"]}
    # The creation time in the product's timezone; tickets.xlsx keeps it in UTC.
    zone = products.load(state["identity"].product).timezone
    created_at = datetime.fromisoformat(ticket.pop("created_at")).astimezone(ZoneInfo(zone))
    created = TASKS["created"].format(number=ticket["ticket_number"],
                                      created_at=f"{created_at:%d/%m/%Y %H:%M} ({zone})",
                                      hours=_support(state, "support_hours", HOURS["created"]))
    # The card the customer submitted stays in the chat: its fields, the files, and the index of the reply that
    # announces the ticket (validate appends it right after this turn), so the widget can show it above that reply.
    draft = state["ticket_draft"]
    ticket = {**ticket, "title": draft.title, "summary": draft.summary, "title_customer": draft.title_customer,
              "summary_customer": draft.summary_customer, "queue": draft.queue, "type": draft.type,
              "subtype": draft.subtype, "priority": draft.priority, "required": draft.required,
              "collected": draft.collected,
              "unknown": draft.unknown, "collected_customer": draft.collected_customer,
              "unknown_customer": draft.unknown_customer,
              "attachments": [f["file_name"] for f in state["attachments"]], "message_index": len(state["messages"])}
    done_state = {**state, "ticket_draft": None, "corrections": state["corrections"] + corrections}
    update, fragment = next_issue(done_state, state["pending"][1:])
    return {"created_tickets": [*state["created_tickets"], ticket], "ticket_draft": None, "attachments": [],
            "corrections": done_state["corrections"], **update,
            "task": _then(created, fragment)}


def status(state: ChatState) -> dict:
    """The customer's tickets from tickets.xlsx with their states. Phase, pending and draft stay as they are; in a ticket
    phase the reply then goes back to the open ticket."""
    phase = state["phase"]
    back = "" if phase == "none" else \
        "Then continue the current ticket: " + CURRENT[phase].format(issue=state["pending"][0].text)
    try:
        found = ticket_status.my_tickets(state["identity"].email, ticket_api.ticket_client())
    except Exception as exc:  # A missing, locked or corrupt sheet: the only thing to do is say try again.
        log.warning("handlers  tickets not read: %s", exc)
        return {"task": _then(NOT_READ, back), "corrections": state["corrections"] + [f"status lookup failed: {exc}"]}
    recent = sorted(found, key=lambda t: t["created_at"], reverse=True)
    lines = "\n".join(f"- {t['ticket_number']}, {t['title']}, {t['created_at'][:10]}, {t['state'] or 'unknown'}"
                      for t in recent)
    today = datetime.now(timezone.utc).date().isoformat()
    if found:
        # Each state with what values.xlsx says (open or closed) and what it means for the customer.
        states = "\n".join(
            f"- {o.value}: {o.description or ''}"
            + (f", {STATE_MEANINGS[o.value.casefold()]}" if o.value.casefold() in STATE_MEANINGS
               and (o.description or "").casefold() in ("open", "closed") else "")
            for o in values.catalog(state["identity"].product).statuses)
        task = TASKS["status"].format(tickets=lines, today=today, states=states,
                                      hours=_support(state, "support_hours", HOURS["status"]))
    else:
        task = TASKS["status_none"].format(channel=_support(state, "support_channel", CHANNEL))
    log.info("handlers  status tickets=%d phase=%s", len(found), phase)
    return {"task": _then(task, back)}


def post_check(state: ChatState) -> dict:
    """Queues the customer's message when respond_agent set offered: a question the search had no answer for."""
    text, before, pending = state["messages"][-1].text, state["phase"], list(state["pending"])
    if state["offered"] and not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, source="no_answer"))
    after = advance(pending) if before == "none" else before
    log.info("post_check  phase %s -> %s pending=%d", before, after, len(pending))
    return {"pending": pending, "phase": after}
