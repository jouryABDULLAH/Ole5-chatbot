"""The handlers: the nodes that apply the router's next. Each applies the router's next to the state and sets the task respond_agent carries out this turn. post_check runs after respond_agent and queues a question the reply offered a ticket for. pending[0] is always the issue being handled; a new issue waits behind it."""

import logging
from datetime import datetime, timezone

from ole5_chat import values
from ole5_chat.agent import classify as classifier
from ole5_chat.agent import prompt, tasks
from ole5_chat.agent.contracts import PendingIssue, Phase
from ole5_chat.agent.tasks import CURRENT, QUEUED_URGENT, TASKS, URGENT
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.config import get_settings
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets import status as ticket_status
from ole5_chat.tickets import submit as tickets
from ole5_chat.tickets.client import TicketApiError
from ole5_chat.voice import rules as voice_rules

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
    return {"pending": pending, "phase": "confirm", "ticket_draft": draft}, _read_back(state, "urgent_ready", draft,
                                                                                        issue.text)


def _then(task: str, fragment: str) -> str:
    return f"{task} {fragment}" if fragment else task


# Voice turns (D4, D12): the confirm step is said, not only shown, so its tasks are built from the draft in the
# reply's language and end with the fixed closing words. Text chat keeps TASKS unchanged.

def _about(draft: TicketDraft | None, issue: str, language: str = "en") -> str:
    """What the ticket is about, for the model to say in its own words. In Arabic only the summary: an English title
    given with it was read out as it is ("عنوان التذكرة: Employees unable to log in")."""
    title = (draft.title or "").strip() if draft else ""
    summary = (draft.summary or "").strip() if draft else ""
    if language == "ar":
        return summary or title or issue
    if title and summary:
        return f"{title}. {summary}"
    return title or summary or issue


def _voice_task(state: ChatState, key: str, draft: TicketDraft | None = None, issue: str = "", **fields) -> str:
    language = prompt.voice_language(state)
    priority = (draft.priority if draft else None) or "not set"
    if language == "ar" and priority in tasks.PRIORITY_AR:
        priority = f"{priority} (in Arabic: {tasks.PRIORITY_AR[priority]})"
    urgent = ", and that it is marked urgent" if draft and draft.urgent and key != "urgent_ready" else ""
    text = tasks.VOICE_TASKS[key].format(
        issue=issue, about=_about(draft, issue, language), priority=priority, urgent=urgent,
        close=tasks.VOICE_CLOSE[language],
        phrase=tasks.VOICE_SUBMIT_PHRASE[language], line=voice_rules.line("just_confirm", language), **fields)
    if language == "ar" and key in ("confirm", "urgent_ready", "changed"):
        text += tasks.VOICE_ARABIC
    return text


def _read_back(state: ChatState, key: str, draft: TicketDraft | None, issue: str) -> str:
    """The confirm, urgent_ready or changed task: read back on voice, as today in text chat."""
    if prompt.voice(state):
        return _voice_task(state, key, draft, issue)
    return TASKS[key].format(issue=issue)


def _changes(old: TicketDraft | None, new: TicketDraft) -> str:
    """What the customer's change did to the summary, worked out here so the reply cannot claim more."""
    if old is None:
        return ""
    said = []
    if old.priority != new.priority:
        said.append(f"priority {old.priority or 'not set'} -> {new.priority or 'not set'}")
    if old.urgent != new.urgent:
        said.append("now marked urgent" if new.urgent else "no longer marked urgent")
    if (old.title or "") != (new.title or "") or (old.summary or "") != (new.summary or ""):
        said.append(f"the description was rewritten and now reads: {_about(new, '')}")
    if (old.queue, old.type, old.subtype) != (new.queue, new.type, new.subtype):
        said.append("the category was updated")
    return "; ".join(said)


def _current(state: ChatState, phase: str, issue: str) -> str:
    if phase == "confirm" and prompt.voice(state):
        return _voice_task(state, "current_confirm", issue=issue)
    return CURRENT[phase].format(issue=issue)


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
    return {"task": TASKS["chat_ticket"].format(current=_current(state, phase, issue))}


def queue(state: ChatState) -> dict:
    text, pending = state["messages"][-1].text, list(state["pending"])
    if not any(p.text == text for p in pending):
        pending.append(PendingIssue(text=text, urgent=state["next"] == "urgent", source="always_ticket"))
    if state["phase"] == "none":
        update, fragment = next_issue(state, pending)
        return {**update, "task": fragment}
    issue = pending[0].text
    current = _current(state, state["phase"], issue)
    urgent = QUEUED_URGENT if state["next"] == "urgent" else ""
    if prompt.voice(state) and state["phase"] == "confirm" and urgent:
        # The closing words must stay last (D31): the urgent sentence goes before them.
        return {"pending": pending, "task": TASKS["queued"].format(new=text, issue=issue,
                                                                  current=f"{urgent.strip()} {current}")}
    queued = TASKS["queued"].format(new=text, issue=issue, current=current)
    return {"pending": pending, "task": queued + urgent}


def accept(state: ChatState) -> dict:
    task = TASKS["accept"].format(issue=state["pending"][0].text)
    return {"ticket_draft": _new_draft(state), "phase": "collect",
            "task": task + tasks.VOICE_FEW_QUESTIONS if prompt.voice(state) else task}


def decline(state: ChatState) -> dict:
    update, fragment = next_issue(state, state["pending"][1:])
    return {**update, "task": _then(TASKS["declined"], fragment)}


def details(state: ChatState) -> dict:
    draft = state["ticket_draft"]
    rounds = draft.rounds + 1
    key = "open_question" if rounds >= get_settings().collect_rounds else "details"
    task = TASKS[key].format(issue=state["pending"][0].text)
    if key == "details" and prompt.voice(state):
        task += tasks.VOICE_FEW_QUESTIONS
    return {"ticket_draft": draft.model_copy(update={"rounds": rounds}), "task": task}


def _nothing_missing(draft: TicketDraft) -> TicketDraft:
    """done means the customer has nothing more to give: what is still missing is what they don't know."""
    return draft.model_copy(update={"unknown": list(dict.fromkeys([*draft.unknown, *draft.missing])), "missing": []})


def done(state: ChatState) -> dict:
    out = classifier.classify(state)
    if "ticket_draft" in out:
        draft = _nothing_missing(out["ticket_draft"])
        return {"ticket_draft": draft, "phase": "confirm",
                "task": _read_back(state, "confirm", draft, state["pending"][0].text)}
    return {**out, "task": TASKS["open_question"].format(issue=state["pending"][0].text)}


def change(state: ChatState) -> dict:
    issue = state["pending"][0].text
    out = classifier.classify(state)
    if "ticket_draft" not in out:
        if prompt.voice(state):
            return {**out, "task": _voice_task(state, "change_failed", state["ticket_draft"], issue)}
        return {**out, "task": TASKS["confirm"].format(issue=issue)}
    draft, old = out["ticket_draft"], state["ticket_draft"]
    if state["pending"][0].urgent:  # An urgent ticket stays urgent and keeps what is missing: it skipped the questions.
        draft = draft.model_copy(update={"urgent": True, "urgent_reason": draft.urgent_reason or old.urgent_reason})
    else:
        draft = _nothing_missing(draft)
    if not prompt.voice(state):
        return {"ticket_draft": draft, "task": TASKS["changed"].format(issue=issue)}
    change = _changes(old, draft)
    task = _voice_task(state, "changed", draft, issue, change=change) if change else \
        _voice_task(state, "unchanged", draft, issue)
    return {"ticket_draft": draft, "task": task}


def cancel(state: ChatState) -> dict:
    update, fragment = next_issue({**state, "ticket_draft": None}, state["pending"][1:])
    return {"ticket_draft": None, **update, "task": _then(TASKS["cancelled"], fragment)}


def _unconfirmed_voice_yes(state: ChatState) -> bool:
    """The graph-side guard (D4): a spoken turn the router read as yes must say so in words. The Submit button and
    the second acknowledgement after "just to confirm" (D15) arrive confirmed; text chat and typed messages are not
    guarded."""
    last = state["messages"][-1] if state["messages"] else None
    return (prompt.spoken(state) and not state.get("confirmed") and last is not None and last.type == "human"
            and not voice_rules.explicit_submit(last.text))


def submit(state: ChatState) -> dict:
    issue = state["pending"][0].text if state["pending"] else ""
    if _unconfirmed_voice_yes(state):
        text = state["messages"][-1].text
        log.info("handlers  voice yes not explicit, asked again: %r", text)
        plain = voice_rules.content_free(text)
        return {"task": _voice_task(state, "reconfirm_line" if plain else "reconfirm", state["ticket_draft"], issue),
                "reconfirmed": plain, "corrections": state["corrections"] + [f"voice yes not explicit: {text}"]}
    try:
        tickets.check(state)
        ticket, corrections = tickets.submit(state, ticket_api.ticket_client())
    except (tickets.SubmitRefused, TicketApiError) as exc:
        log.warning("handlers  ticket not created: %s", exc)
        corrections = state["corrections"] + [f"submit failed: {exc}"]
        if prompt.voice(state):
            draft = state["ticket_draft"]
            failures = (draft.submit_failures if draft else 0) + 1  # this ticket's, not the conversation's
            return {"note": None, "corrections": corrections,
                    "ticket_draft": draft.model_copy(update={"submit_failures": failures}) if draft else draft,
                    "task": _voice_task(state, "submit_failed_again" if failures > 1 else "submit_failed", draft,
                                        issue)}
        return {"note": NOT_CREATED, "task": TASKS["confirm"].format(issue=state["pending"][0].text),
                "corrections": corrections}
    # The card the customer submitted stays in the chat: its fields, the files, and the index of the reply that
    # announces the ticket (validate appends it right after this turn), so the widget can show it above that reply.
    draft = state["ticket_draft"]
    ticket = {**ticket, "title": draft.title, "summary": draft.summary, "queue": draft.queue, "type": draft.type,
              "subtype": draft.subtype, "priority": draft.priority,
              "attachments": [f["file_name"] for f in state["attachments"]], "message_index": len(state["messages"])}
    done_state = {**state, "ticket_draft": None, "corrections": state["corrections"] + corrections}
    update, fragment = next_issue(done_state, state["pending"][1:])
    if prompt.voice(state):
        language = prompt.voice_language(state)
        created = _voice_task(state, "created", number=ticket["ticket_number"],
                              number_sentence=tasks.VOICE_NUMBER[language].format(number=ticket["ticket_number"]))
        created += tasks.VOICE_CREATED_THEN if fragment else tasks.VOICE_CREATED_ALONE
    else:
        created = TASKS["created"].format(number=ticket["ticket_number"])
    return {"created_tickets": [*state["created_tickets"], ticket], "ticket_draft": None, "attachments": [],
            "corrections": done_state["corrections"], **update, "task": _then(created, fragment)}


def status(state: ChatState) -> dict:
    """The customer's tickets from tickets.xlsx with their states. Phase, pending and draft stay as they are; in a ticket
    phase the reply then goes back to the open ticket."""
    phase = state["phase"]
    back = "" if phase == "none" else \
        "Then continue the current ticket: " + _current(state, phase, state["pending"][0].text)
    try:
        found = ticket_status.my_tickets(state["identity"].email, ticket_api.ticket_client())
    except Exception as exc:  # A missing, locked or corrupt sheet: the only thing to do is say try again.
        log.warning("handlers  tickets not read: %s", exc)
        return {"task": _then(NOT_READ, back), "corrections": state["corrections"] + [f"status lookup failed: {exc}"]}
    recent = sorted(found, key=lambda t: t["created_at"], reverse=True)
    lines = "\n".join(f"- {t['ticket_number']}, {t['title']}, {t['created_at'][:10]}, {t['state'] or 'unknown'}"
                      for t in recent)
    today = datetime.now(timezone.utc).date().isoformat()
    task = TASKS["status"].format(tickets=lines, today=today) if found else TASKS["status_none"]
    if found and prompt.voice(state):
        task += tasks.VOICE_STATUS + (tasks.VOICE_STATUS_ARABIC if prompt.voice_language(state) == "ar" else "")
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
