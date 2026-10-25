"""The Submit flow: checks the ready draft again, creates the ticket with the summary and the conversation, classifies it with an internal note that links the attached files, records it in tickets.xlsx and the files in attachments.xlsx, and returns the ticket."""

import logging
from datetime import datetime, timezone

from langchain_core.messages import BaseMessage

from ole5_chat import attachments, sheets, values
from ole5_chat.agent.contracts import Classification
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.validate import check_ticket
from ole5_chat.tickets.client import TicketApi, TicketApiError

log = logging.getLogger(__name__)

REQUIRED = ("title", "summary", "queue", "type", "subtype", "reason", "priority")


class SubmitRefused(Exception):
    """The ticket cannot be created from this session as it stands. The message says why."""


def conversation_text(messages: list[BaseMessage]) -> str:
    return "\n".join(f"{'Customer' if m.type == 'human' else 'Assistant'}: {m.text}" for m in messages)


def internal_note(draft, corrections: list[str], files: list[dict] = ()) -> str:
    collected = "; ".join(f"{k}: {v}" for k, v in draft.collected.items()) or "nothing"
    urgent = f"yes, {draft.urgent_reason}" if draft.urgent else "no"
    lines = [f"Collected: {collected}", f"Missing: {'; '.join(draft.missing) or 'nothing'}",
             f"Customer does not know: {'; '.join(draft.unknown) or 'nothing'}",
             f"Confidence: {draft.confidence or 'not set'}", f"Why this queue: {draft.queue_reason or 'not given'}",
             f"Urgent: {urgent}"]
    if corrections:
        lines.append("Corrections: " + "; ".join(corrections))
    if files:
        lines += ["Attachments:", *(f"{f['file_name']}: {attachments.link(f['id'])}" for f in files)]
    return "\n".join(lines)


def check(state: ChatState) -> None:
    draft = state.get("ticket_draft")
    if state["phase"] != "confirm" or draft is None or not draft.classified:
        raise SubmitRefused("there is no ticket summary to submit")
    empty = [f for f in REQUIRED if not getattr(draft, f)]
    if empty:
        raise SubmitRefused("the ticket is missing: " + ", ".join(empty))
    fields = draft.model_dump(include=set(Classification.model_fields))
    _, problems = check_ticket(Classification(**fields), values.catalog(state["identity"].product))
    if problems:
        raise SubmitRefused("values not on the lists: " + "; ".join(problems))


def submit(state: ChatState, api: TicketApi) -> tuple[dict, list[str]]:
    check(state)
    who, draft = state["identity"], state["ticket_draft"]
    log.info("tickets  sending queue=%s type=%s subtype=%s priority=%s urgent=%s reason=%r title=%r",
             draft.queue, draft.type, draft.subtype, draft.priority, draft.urgent, draft.reason, draft.title)
    created = api.create(
        ticket={"Title": draft.title, "Service": draft.product, "Queue": draft.queue, "State": "new",
                "Priority": draft.priority, "Type": draft.type, "CustomerUser": who.email},
        article={"Subject": draft.title, "Body": f"{draft.summary}\n\n{conversation_text(state['messages'])}",
                 "SenderType": "customer", "IsVisibleForCustomer": 1, "From": who.email},
    )
    corrections: list[str] = []
    try:
        api.update(created["TicketID"],
                   dynamic_fields=[{"Name": "SubType", "Value": draft.subtype},
                                   {"Name": "Conclusions", "Value": draft.reason}],
                   article={"Subject": "Chatbot: classification", "IsVisibleForCustomer": 0,
                            "Body": internal_note(draft, state["corrections"], state["attachments"])})
    except TicketApiError as exc:
        log.exception("tickets  TicketUpdate failed for %s", created["TicketNumber"])
        corrections.append(f"TicketUpdate failed, the ticket is not classified: {exc}")

    sheets.add_ticket({"ticket_id": created["TicketID"], "ticket_number": created["TicketNumber"],
                       "customer_email": who.email, "product": who.product, "title": draft.title,
                       "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    for f in state["attachments"]:
        sheets.add_attachment({"ticket_number": created["TicketNumber"], "file_name": f["file_name"],
                               "link": attachments.link(f["id"]), "uploaded_at": f["uploaded_at"]})
    log.info("tickets  created number=%s product=%s queue=%s", created["TicketNumber"], who.product, draft.queue)
    ticket = {"ticket_id": created["TicketID"], "ticket_number": created["TicketNumber"]}
    return ticket, corrections
