"""Task for the observable suite (docs/eval_spec_observable.md): each case on a fresh copy of the sheets, Search
returning the case's passages whatever the query, the chat driven through the HTTP API functions. It returns what the
customer sees per turn, plus diagnostics read from the checkpoints that no grader reads."""

import re
import shutil
import tempfile
import uuid
from functools import cache
from pathlib import Path

from evals.regression_task import SUBMIT
from evals.task import _email
from ole5_chat import api, sheets
from ole5_chat.agent import session as agent_session
from ole5_chat.agent.nodes import search as search_node
from ole5_chat.config import get_settings
from ole5_chat.knowledge import search as knowledge_search
from ole5_chat.knowledge.contracts import Evidence, Retrieval, Verdict


@cache
def _chunks(document: str) -> dict:
    """A kb_export document's chunks as exported, by chunk name; split as _chunk_numbers in evals/regression_task.py
    does, with the text kept as written so Respond sees its lists and lines."""
    text = (Path("kb_export/ole5") / f"{document}.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^_(chunk_\d+)(?:, page \d+)?_$", text)
    return {chunk: body.strip() for chunk, body in zip(parts[1::2], parts[2::2])}


def _sheets(tickets: list[dict]) -> None:
    """A fresh copy of the sheets with the fixed tickets and the case's own."""
    folder = Path(tempfile.mkdtemp(prefix="observable-sheets-"))
    for name in ("customers", "values", "attachments"):
        shutil.copy(Path("sheets") / f"{name}.xlsx", folder)
    shutil.copy(Path("evals/fixtures/proof_tickets.xlsx"), folder / "tickets.xlsx")
    get_settings().sheets_dir = folder
    for ticket in tickets:
        sheets.add_ticket({"ticket_id": str(uuid.uuid4()), "customer_email": _email(), "product": "OLE5", **ticket})


def _search(passages: list[str]):
    def search(question: str, product: str) -> Retrieval:
        return Retrieval(
            query=question, verdict=Verdict.ANSWERABLE if passages else Verdict.NOT_IN_KB,
            evidence=[Evidence(text=text, document=document, section=None, source_file=None, relation="DIRECT",
                               score=3, reason="the case's passage") for document, text in passages],
            queries_tried=[question])
    return search


def _diagnostics(sid: str, answer: str) -> dict:
    """The turn's replies and checks from its checkpoints, oldest first: Respond's first reply before validate, each
    rejection, each rewrite, and the last outcome."""
    history = list(agent_session._graph.get_state_history(agent_session._config(sid)))
    start = next(i for i, snapshot in enumerate(history) if snapshot.metadata.get("source") == "input")
    turn = list(reversed(history[:start + 1]))
    replies = [snapshot.values.get("reply") for snapshot in turn if snapshot.next == ("validate",)]
    if not replies:
        return {"draft_reply": answer, "rejections": [], "rewrites": [], "outcome": "pass"}
    checked = next(i for i, snapshot in enumerate(turn) if snapshot.next == ("validate",))
    rejections = [snapshot.values.get("validation_note") for snapshot in turn[checked:]
                  if snapshot.next == ("respond",)]
    outcome = next((snapshot.values.get("validation_outcome") for snapshot in reversed(turn)
                    if snapshot.next == ("finalize",)), None)
    return {"draft_reply": replies[0], "rejections": rejections, "rewrites": replies[1:], "outcome": outcome}


def observable_task(input: dict, metadata: dict) -> dict:
    """metadata holds the case's "kb" (the passages Search returns) and "tickets" (rows added to the ticket sheet)."""
    if get_settings().ticket_api_url:
        raise ValueError("observable runs on mock tickets: TICKET_API_URL must be empty")
    _sheets(metadata.get("tickets") or [])
    get_settings().kb_retrieval_mode = "direct"
    passages = [(item["document"], _chunks(item["document"])[item["chunk"]])
                for item in metadata.get("kb") or []]
    knowledge_search.search = search_node.knowledge_search = _search(passages)
    view = api.start_session(api.StartSession(email=_email()))
    sid = view.session_id
    turns = []
    for text in input["turns"]:
        before = len(view.created_tickets)
        view = api.submit(sid) if text == SUBMIT else api.send_message(sid, api.NewMessage(text=text))
        answer = view.messages[-1].text
        turns.append({"answer": answer, "phase": view.phase, "created": len(view.created_tickets) - before,
                      "ticket_number": view.ticket_number, "diagnostics": _diagnostics(sid, answer)})
    return {"turns": turns}
