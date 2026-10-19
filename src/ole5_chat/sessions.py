"""Conversations. One LangGraph thread per session id, kept by MemorySaver; the session is also what identifies the customer. Submit writes its result into the session with update_state."""

import threading
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ole5_chat.agent.graph import build
from ole5_chat.agent.contracts import Reply, RouterDecision
from ole5_chat.agent.state import ChatState, TicketDraft, initial_state
from ole5_chat.identity import Identity, lookup
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets.submit import submit as submit_ticket

CHECKPOINTED = (Identity, RouterDecision, Reply, TicketDraft)
_serde = JsonPlusSerializer(allowed_msgpack_modules=[(c.__module__, c.__name__) for c in CHECKPOINTED])
_graph = build(MemorySaver(serde=_serde))


class SessionNotFound(KeyError):
    """No conversation with this session id."""


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def start(email: str) -> tuple[str, ChatState]:
    identity = lookup(email)
    session_id = uuid.uuid4().hex
    return session_id, _graph.invoke(initial_state(identity), _config(session_id))


def get(session_id: str) -> ChatState:
    values = _graph.get_state(_config(session_id)).values
    if not values:
        raise SessionNotFound(session_id)
    return values


def current_identity(session_id: str) -> Identity:
    return get(session_id)["identity"]


def send(session_id: str, text: str) -> ChatState:
    get(session_id)
    return _graph.invoke({"messages": [HumanMessage(content=text)]}, _config(session_id))


_submit_lock = threading.Lock()


def submit(session_id: str) -> ChatState:
    with _submit_lock:
        state = get(session_id)
        ticket, closing, corrections = submit_ticket(state, ticket_api.ticket_client())
        _graph.update_state(_config(session_id), {
            "messages": [AIMessage(content=closing)], "phase": "created", "sources": [],
            "created_ticket": ticket, "corrections": state["corrections"] + corrections,
        }, as_node="validate")
    return get(session_id)
