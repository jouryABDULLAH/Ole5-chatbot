"""Conversations. One LangGraph thread per session id, kept by MemorySaver; the session is also what identifies the customer, and every trace of it carries the session id so Phoenix groups them. Submit writes its result into the session with update_state: the closing message, the ticket added to created_tickets, and the draft cleared so a second Submit is refused and a new problem starts a new ticket."""

import logging
import threading
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from openinference.instrumentation import using_session

from ole5_chat import attachments
from ole5_chat.agent.graph import build
from ole5_chat.agent.handlers import advance
from ole5_chat.agent.contracts import PendingIssue, Reply
from ole5_chat.agent.state import ChatState, TicketDraft, initial_state
from ole5_chat.identity import Identity, lookup
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets.submit import submit as submit_ticket

log = logging.getLogger(__name__)

CHECKPOINTED = (Identity, Reply, TicketDraft, PendingIssue)
_serde = JsonPlusSerializer(allowed_msgpack_modules=[(c.__module__, c.__name__) for c in CHECKPOINTED])
_graph = build(MemorySaver(serde=_serde))


class SessionNotFound(KeyError):
    """No conversation with this session id."""


class AttachmentNotFound(KeyError):
    """A message named an attachment this session does not have."""


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def start(email: str) -> tuple[str, ChatState]:
    identity = lookup(email)
    session_id = uuid.uuid4().hex
    with using_session(session_id):
        return session_id, _graph.invoke(initial_state(identity), _config(session_id))


def get(session_id: str) -> ChatState:
    values = _graph.get_state(_config(session_id)).values
    if not values:
        raise SessionNotFound(session_id)
    return values


def current_identity(session_id: str) -> Identity:
    return get(session_id)["identity"]


ATTACHED = "[attached: {}]"


def send(session_id: str, text: str, attachment_ids: list[str] = ()) -> ChatState:
    state = get(session_id)
    names = [f["file_name"] for f in state["attachments"] if f["id"] in attachment_ids]
    if len(names) != len(set(attachment_ids)):
        raise AttachmentNotFound()
    if names:
        text = f"{text}\n{ATTACHED.format(', '.join(names))}".strip()
    log.info("message  session=%s %r", session_id[:8], text)
    with using_session(session_id):
        return _graph.invoke({"messages": [HumanMessage(content=text)]}, _config(session_id))


_submit_lock = threading.Lock()


def attach(session_id: str, file_name: str, data: bytes) -> dict:
    with _submit_lock:
        state = get(session_id)
        added = attachments.save(session_id, file_name, data, len(state["attachments"]))
        _graph.update_state(_config(session_id), {"attachments": [*state["attachments"], added]}, as_node="validate")
    return added


def detach(session_id: str, att_id: str) -> bool:
    with _submit_lock:
        state = get(session_id)
        kept = [f for f in state["attachments"] if f["id"] != att_id]
        if len(kept) == len(state["attachments"]):
            return False
        attachments.delete(att_id)
        _graph.update_state(_config(session_id), {"attachments": kept}, as_node="validate")
    return True


def submit(session_id: str) -> ChatState:
    with _submit_lock, using_session(session_id):
        state = get(session_id)
        ticket, closing, corrections = submit_ticket(state, ticket_api.ticket_client())
        rest = state["pending"][1:]
        _graph.update_state(_config(session_id), {
            "messages": [AIMessage(content=closing)], "pending": rest, "phase": advance(rest), "sources": [],
            "ticket_draft": None, "created_tickets": [*state["created_tickets"], ticket], "attachments": [],
            "corrections": state["corrections"] + corrections,
        }, as_node="validate")
    return get(session_id)
