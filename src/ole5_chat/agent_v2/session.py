"""Session/checkpoint adapter for the new owner-routing graph.

This runs alongside ``ole5_chat.sessions`` until the application entry point
is migrated explicitly.
"""

import logging
import threading
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from openinference.instrumentation import using_session

from ole5_chat import attachments, products
from ole5_chat.agent_v2.contracts import PendingCustomerResponse
from ole5_chat.agent_v2.graph import build
from ole5_chat.agent_v2.state import AgentState, initial_state
from ole5_chat.identity import Identity, lookup

log = logging.getLogger(__name__)

_serde = JsonPlusSerializer(
    allowed_msgpack_modules=[
        (Identity.__module__, Identity.__name__),
        (PendingCustomerResponse.__module__, PendingCustomerResponse.__name__),
    ]
)
_graph = build(MemorySaver(serde=_serde))


class SessionNotFound(KeyError):
    """No v2 conversation exists for this session id."""


class AttachmentNotFound(KeyError):
    """A message named an attachment that is not in this session."""


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _state(session_id: str) -> AgentState:
    values = _graph.get_state(_config(session_id)).values
    if not values:
        raise SessionNotFound(session_id)
    return values


def start(email: str) -> tuple[str, AgentState]:
    """Create a checkpointed v2 session and store the configured greeting."""

    identity = lookup(email)
    session_id = uuid.uuid4().hex
    state = initial_state(identity)
    state["messages"] = [AIMessage(content=products.load(identity.product).greet(identity.language))]
    with using_session(session_id):
        _graph.update_state(_config(session_id), state, as_node="finalize")
    log.info("v2 greet  session=%s product=%s language=%s", session_id[:8], identity.product, identity.language)
    return session_id, _state(session_id)


def get(session_id: str) -> AgentState:
    return _state(session_id)


def current_identity(session_id: str) -> Identity:
    return _state(session_id)["identity"]


ATTACHED = "[attached: {}]"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


def send(session_id: str, text: str, attachment_ids: list[str] = ()) -> AgentState:
    with _lock(session_id):
        state = _state(session_id)
        names = [f["file_name"] for f in state.get("attachments", []) if f["id"] in attachment_ids]
        if len(names) != len(set(attachment_ids)):
            raise AttachmentNotFound()
        if names:
            text = f"{text}\n{ATTACHED.format(', '.join(names))}".strip()
        log.info("v2 message session=%s %r", session_id[:8], text)
        with using_session(session_id):
            return _graph.invoke(
                {"input_event": "message", "messages": [HumanMessage(content=text)]},
                _config(session_id),
            )


def attach(session_id: str, file_name: str, data: bytes) -> dict:
    with _lock(session_id):
        state = _state(session_id)
        added = attachments.save(session_id, file_name, data, len(state.get("attachments", [])))
        _graph.update_state(
            _config(session_id),
            {"attachments": [*state.get("attachments", []), added]},
            as_node="finalize",
        )
        log.info("v2 attach  session=%s file=%s", session_id[:8], added["file_name"])
        return added


def detach(session_id: str, att_id: str) -> bool:
    with _lock(session_id):
        state = _state(session_id)
        kept = [f for f in state.get("attachments", []) if f["id"] != att_id]
        if len(kept) == len(state.get("attachments", [])):
            return False
        attachments.delete(att_id)
        _graph.update_state(_config(session_id), {"attachments": kept}, as_node="finalize")
        log.info("v2 detach  session=%s attachment=%s", session_id[:8], att_id[:8])
        return True


def submit(session_id: str) -> AgentState:
    """Run the explicit Submit event; the graph performs its own summary guard."""

    with _lock(session_id), using_session(session_id):
        _state(session_id)
        log.info("v2 submit  session=%s", session_id[:8])
        return _graph.invoke({"input_event": "submit"}, _config(session_id))
