"""Conversations. One LangGraph thread per session id, kept by MemorySaver; the session is also what identifies the customer, and every trace of it carries the session id so Phoenix groups them. The Submit button runs the graph with next set to yes, so the submit handler creates the ticket and respond_agent writes the reply; a second Submit is refused because the draft is cleared."""

import logging
import threading
import uuid

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from openinference.instrumentation import using_session

from ole5_chat import attachments
from ole5_chat.agent.graph import build
from ole5_chat.agent.contracts import PendingIssue, Reply
from ole5_chat.agent.state import ChatState, TicketDraft, initial_state
from ole5_chat.identity import Identity, lookup
from ole5_chat.tickets.submit import check

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


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(session_id: str) -> threading.Lock:
    """One lock per session: a typed yes and the Submit button cannot both pass check() and create two tickets."""
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


def send(session_id: str, text: str, attachment_ids: list[str] = ()) -> ChatState:
    with _lock(session_id):
        state = get(session_id)
        names = [f["file_name"] for f in state["attachments"] if f["id"] in attachment_ids]
        if len(names) != len(set(attachment_ids)):
            raise AttachmentNotFound()
        if names:
            text = f"{text}\n{ATTACHED.format(', '.join(names))}".strip()
        log.info("message  session=%s %r", session_id[:8], text)
        with using_session(session_id):
            # next cleared: after a turn that failed past the router, this message must not go straight to a handler.
            return _graph.invoke({"messages": [HumanMessage(content=text)], "next": None}, _config(session_id))


def attach(session_id: str, file_name: str, data: bytes) -> dict:
    with _lock(session_id):
        state = get(session_id)
        added = attachments.save(session_id, file_name, data, len(state["attachments"]))
        _graph.update_state(_config(session_id), {"attachments": [*state["attachments"], added]}, as_node="validate")
    return added


def detach(session_id: str, att_id: str) -> bool:
    with _lock(session_id):
        state = get(session_id)
        kept = [f for f in state["attachments"] if f["id"] != att_id]
        if len(kept) == len(state["attachments"]):
            return False
        attachments.delete(att_id)
        _graph.update_state(_config(session_id), {"attachments": kept}, as_node="validate")
    return True


def submit(session_id: str) -> ChatState:
    with _lock(session_id), using_session(session_id):
        check(get(session_id))
        return _graph.invoke({"next": "yes"}, _config(session_id))
