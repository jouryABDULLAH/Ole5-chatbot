"""The task evalkit runs on each case: a fresh session for an Ole5 customer, the case's message(s) sent through the agent, and what the graders read returned: the reply, the step, the sources, the passages shown to the agent when it answered, the language the router read, and the ticket draft's state. A multi-turn case returns one such dict per turn under "turns". Nothing is submitted."""

import os

from ole5_chat import sessions, sheets
from ole5_chat.knowledge.contracts import Retrieval

EMAIL = os.environ.get("EVAL_EMAIL")


def _email() -> str:
    if EMAIL:
        return EMAIL
    return next(str(r["email"]) for r in sheets.customers()
                if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")


def _evidence(sid: str, state) -> str:
    """The passages shown to the agent in the turn just run, or "" when it did not search. validate clears them from the state, so they are read from the newest checkpoint of this turn that still has them: one that already holds the customer's message."""
    turn = len(state["messages"]) - 1
    return next((Retrieval.from_json(snapshot.values["last_retrieval"]).render()
                 for snapshot in sessions._graph.get_state_history(sessions._config(sid))
                 if len(snapshot.values.get("messages") or []) >= turn and snapshot.values.get("last_retrieval")), "")


def _phase(state) -> str:
    """The old step name, so the current datasets still grade the new path. Removed in step 8."""
    if state["phase"] == "none":
        return "answer" if state["sources"] else "ask"
    return state["phase"]


def _turn(sid: str, state) -> dict:
    draft = state.get("ticket_draft")
    return {
        "state_phase": state["phase"],
        "pending": [p.text for p in state["pending"]],
        "answer": state["messages"][-1].text,
        "phase": _phase(state),
        "sources": list(state["sources"]),
        "evidence": _evidence(sid, state),
        "language": state["language"],
        "draft": None if draft is None else {"ready": draft.classified, "missing": list(draft.missing),
                                             "unknown": list(draft.unknown), "urgent": draft.urgent},
        "corrections": list(state["corrections"]),
    }


def task(input: dict) -> dict:
    sid, _ = sessions.start(_email())
    if "turns" in input:
        return {"turns": [_turn(sid, sessions.send(sid, text)) for text in input["turns"]]}
    for line in input.get("history") or []:
        if line["role"] == "user":
            sessions.send(sid, line["content"])
    return _turn(sid, sessions.send(sid, input["message"]))
