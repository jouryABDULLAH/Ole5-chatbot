"""The task evalkit runs on each case: a fresh session for an Ole5 customer, the case's message(s) sent through the agent, and what the graders read returned: the reply, the step, the sources, the language the router read, and the ticket draft's state. A multi-turn case returns one such dict per turn under "turns". Nothing is submitted."""

import os
from ole5_chat import sessions, sheets

EMAIL = os.environ.get("EVAL_EMAIL")


def _email() -> str:
    if EMAIL:
        return EMAIL
    return next(str(r["email"]) for r in sheets.customers()
                if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")


def _turn(state) -> dict:
    draft, decision = state.get("ticket_draft"), state.get("decision")
    return {
        "answer": state["messages"][-1].text,
        "phase": state["phase"],
        "sources": list(state["sources"]),
        "language": decision.language if decision else None,
        "draft": None if draft is None else {"ready": draft.classified, "missing": list(draft.missing),
                                             "unknown": list(draft.unknown), "urgent": draft.urgent},
        "corrections": list(state["corrections"]),
    }


def task(input: dict) -> dict:
    sid, _ = sessions.start(_email())
    if "turns" in input:
        return {"turns": [_turn(sessions.send(sid, text)) for text in input["turns"]]}
    for line in input.get("history") or []:
        if line["role"] == "user":
            sessions.send(sid, line["content"])
    return _turn(sessions.send(sid, input["message"]))
