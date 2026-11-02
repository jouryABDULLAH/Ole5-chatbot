"""Session/checkpoint adapter for the owner-routing graph.

This runs alongside ``ole5_chat.sessions`` until the application entry point
is migrated explicitly.
"""

import logging
import json
import queue
import threading
import uuid
from collections.abc import Iterator

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from openinference.instrumentation import using_session

from ole5_chat import attachments, products
from ole5_chat.agent.contracts import PendingCustomerResponse
from ole5_chat.agent.graph import build
from ole5_chat.agent.state import AgentState, initial_state
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
_progress: dict[str, dict] = {}
_progress_guard = threading.Lock()
_progress_subscribers: dict[str, set[queue.Queue[dict]]] = {}

_PROGRESS_TEXT = {
    "understanding": ("Understanding your request…", "أفهم طلبك…"),
    "searching": ("Searching the knowledge base…", "أبحث في قاعدة المعرفة…"),
    "ticket": ("Preparing support details…", "أجهز تفاصيل طلب الدعم…"),
    "answering": ("Preparing the answer…", "أجهز الإجابة…"),
    "checking": ("Checking the answer…", "أتحقق من الإجابة…"),
    "done": ("Completed", "اكتمل الطلب"),
    "error": ("Unable to complete the request.", "تعذر إكمال الطلب."),
}


def _lock(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


def progress(session_id: str) -> dict:
    with _progress_guard:
        return dict(_progress.get(session_id, {"key": "done", "en": "Completed", "ar": "اكتمل الطلب"}))


def _set_progress(session_id: str, key: str, language: str) -> None:
    en, ar = _PROGRESS_TEXT[key]
    value = {"key": key, "en": en, "ar": ar, "language": language}
    with _progress_guard:
        _progress[session_id] = value
        for subscriber in _progress_subscribers.get(session_id, set()):
            subscriber.put_nowait(value)


def progress_stream(session_id: str) -> Iterator[str]:
    """Yield progress updates as server-sent events for one in-flight request."""

    _state(session_id)  # fail before opening a stream for an unknown session
    subscriber: queue.Queue[dict] = queue.Queue()
    with _progress_guard:
        _progress_subscribers.setdefault(session_id, set()).add(subscriber)
        current = _progress.get(session_id)
        # A terminal value may belong to the previous request. Wait for the next
        # non-terminal update instead of closing the new request's stream early.
        if current and current.get("key") not in {"done", "error"}:
            subscriber.put_nowait(current)
    try:
        while True:
            try:
                value = subscriber.get(timeout=25)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(value, ensure_ascii=False)}\n\n"
            if value.get("key") in {"done", "error"}:
                break
    finally:
        with _progress_guard:
            subscribers = _progress_subscribers.get(session_id)
            if subscribers:
                subscribers.discard(subscriber)
                if not subscribers:
                    _progress_subscribers.pop(session_id, None)


def _run(session_id: str, payload: dict, language: str) -> AgentState:
    _set_progress(session_id, "understanding", language)
    try:
        with using_session(session_id):
            # Task-start events arrive before work; completed updates arrive too late.
            # Only public labels are exposed, never debug inputs.
            for event in _graph.stream(payload, _config(session_id), stream_mode="debug"):
                if event.get("type") != "task":
                    continue
                task = event["payload"]
                node = task["name"]
                key = {"router": "understanding", "search": "searching", "ticket_operator": "ticket",
                       "ticket_enquiry": "searching", "respond": "answering", "validate": "checking",
                       "finalize": "checking"}.get(node)
                if key:
                    current = task.get("input") or {}
                    _set_progress(session_id, key, current.get("language") or language)
        _set_progress(session_id, "done", _state(session_id).get("language") or language)
        return _state(session_id)
    except Exception:
        _set_progress(session_id, "error", language)
        raise


def send(session_id: str, text: str, attachment_ids: list[str] = ()) -> AgentState:
    with _lock(session_id):
        state = _state(session_id)
        names = [f["file_name"] for f in state.get("attachments", []) if f["id"] in attachment_ids]
        if len(names) != len(set(attachment_ids)):
            raise AttachmentNotFound()
        if names:
            text = f"{text}\n{ATTACHED.format(', '.join(names))}".strip()
        log.info("v2 message session=%s %r", session_id[:8], text)
        return _run(session_id, {"input_event": "message", "messages": [HumanMessage(content=text)]},
                    state.get("language") or state["identity"].language)


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
        state = _state(session_id)
        return _run(session_id, {"input_event": "submit"}, state.get("language") or state["identity"].language)



