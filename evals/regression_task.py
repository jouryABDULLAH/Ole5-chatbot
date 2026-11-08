"""Tasks for the reg_* suites. agent_task and flow_task run the graph; respond_task and guard_task run one node on a
frozen state, so a change to Respond or a guard is measured without Router and Search varying around it."""

import re
from functools import cache
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from evals.task import _email
from ole5_chat import llm, sessions
from ole5_chat.agent import session as agent_session
from ole5_chat.agent.contracts import Grounding, PendingCustomerResponse
from ole5_chat.agent.guards import language_problem
from ole5_chat.agent.nodes import search as search_module
from ole5_chat.agent.nodes.respond import _grounding_input, respond_node
from ole5_chat.agent.nodes.validate import validate_node
from ole5_chat.agent.prompts.guards import grounding_prompt
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.turn_sheet import build_turn_sheet
from ole5_chat.config import get_settings
from ole5_chat.identity import lookup
from ole5_chat.knowledge.contracts import Retrieval, Verdict

SUBMIT = "[submit]"  # a flow turn that presses the Submit button instead of sending a message


def _stage(state: dict) -> str:
    """Logged for diagnosis only; no grader reads it. If stages are removed, return None here and drop the import."""
    return build_turn_sheet(state, state.get("language") or "ar")["stage"]


def _kind(state: dict) -> str | None:
    return getattr(state.get("pending_customer_response"), "kind", None)


def _direct(state: dict) -> list[dict]:
    return [r for r in state.get("current_references") or [] if r.get("relation") == "DIRECT"]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@cache
def _chunk_numbers() -> dict:
    """Chunk text to {document, chunk}, from kb_export. Retrieved references carry no chunk number, but their text is
    the exported text."""
    numbers = {}
    for path in Path("kb_export/ole5").glob("*.md"):
        parts = re.split(r"(?m)^_(chunk_\d+)(?:, page \d+)?_$", path.read_text(encoding="utf-8"))
        for chunk, body in zip(parts[1::2], parts[2::2]):
            numbers[_plain(body)] = {"document": path.stem, "chunk": chunk}
    return numbers


def _chunks(references: list[dict]) -> list[dict]:
    return [{**_chunk_numbers().get(_plain(r.get("text") or ""), {"document": r.get("document"), "chunk": None}),
             "relation": r.get("relation")} for r in references]


def _turn(sid: str, state: dict) -> dict:
    """finalize clears the turn's facts, so they are read from the checkpoint just before it."""
    history = agent_session._graph.get_state_history(agent_session._config(sid))
    before = next(snapshot.values for snapshot in history if snapshot.next == ("finalize",))
    return {
        "answer": state["messages"][-1].text,
        "stage": _stage(before),
        "intent": before.get("intent"),
        "search_outcome": before.get("search_outcome"),
        "operator_action": (before.get("ticket_operator_result") or {}).get("action"),
        "validation": before.get("validation_outcome"),
        "sources": list(before.get("sources") or []),
        "evidence": "\n\n".join(r.get("text") or "" for r in _direct(before)),
        "chunks": _chunks(before.get("current_references") or []),  # all Respond received; sources list only DIRECT
        "pending": _kind(state),
        "queued": len(state.get("pending_issues") or []),
        "created": len(state.get("created_tickets") or []),
    }


def agent_task(input: dict) -> dict:
    """The full agent on one message. An empty message is a slot not filled in yet."""
    if not input.get("message"):
        return {"answer": "", "empty_slot": True}
    sid, _ = sessions.start(_email())
    return _turn(sid, sessions.send(sid, input["message"]))


def flow_task(input: dict) -> dict:
    """A ticket conversation. Search always finds nothing, so the flow does not depend on the knowledge base."""
    get_settings().kb_retrieval_mode = "direct"
    search_module.knowledge_search = lambda query, product: Retrieval(query=query, verdict=Verdict.NOT_IN_KB)
    sid, _ = sessions.start(_email())
    turns = []
    for text in input["turns"]:
        state = sessions.submit(sid) if text == SUBMIT else sessions.send(sid, text)
        turns.append(_turn(sid, state))
    return {"turns": turns}


def _state(fixture: dict) -> dict:
    state = initial_state(lookup(_email()))
    state.update(fixture)
    state["messages"] = [(HumanMessage if m["role"] == "user" else AIMessage)(content=m["content"])
                         for m in fixture.get("messages", [])]
    if fixture.get("pending_customer_response"):
        state["pending_customer_response"] = PendingCustomerResponse(**fixture["pending_customer_response"])
    return state


def _respond(state: dict) -> dict:
    result = respond_node(state)
    direct = _direct(state)
    return {
        "answer": result.get("reply") or "",
        "stage": _stage(state),
        "sources": [r.get("document") for r in direct],
        "evidence": "\n\n".join(r.get("text") or "" for r in direct),
        "pending": _kind(state),
    }


def respond_task(input: dict) -> dict:
    """Respond alone on a frozen state, before the guards."""
    return _respond(_state(input["state"]))


def direct_chunks_task(input: dict) -> dict:
    """Respond alone, with the case's chunks returned by Search as DIRECT."""
    if not input.get("message"):
        return {"answer": "", "empty_slot": True}
    return _respond(_state({
        "messages": [{"role": "user", "content": input["message"]}],
        "language": input.get("language", "ar"),
        "intent": "question",
        "search_outcome": "answered",
        "current_references": [{**chunk, "relation": "DIRECT"} for chunk in input["chunks"]],
    }))


def guard_task(input: dict) -> dict:
    """One guard on a fixed reply. Respond does not run. validation: pass, repair or fallback."""
    state = _state(input.get("state") or {})
    reply = input["reply"]
    state["reply"] = reply
    if input["guard"] == "language":
        return {"answer": reply, "validation": "repair" if language_problem(reply, state["language"]) else "pass"}
    if input["guard"] == "grounding":
        check = llm.structured(grounding_prompt(), [HumanMessage(content=_grounding_input(state, reply))],
                               Grounding, node="ground")
        return {"answer": reply, "validation": "repair" if check.unsupported else "pass",
                "note": "; ".join(check.unsupported)}
    result = validate_node(state)
    return {"answer": result.get("reply", reply), "validation": result["validation_outcome"],
            "note": result.get("validation_note")}
