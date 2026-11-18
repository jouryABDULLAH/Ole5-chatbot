"""Replay an archived voice audit and write Markdown/HTML before-and-after conversations.

Uses the real configured LLM, isolated sheets, mock tickets and fixed knowledge passages.
The baseline is never regenerated or overwritten. Run from any directory:
  python scripts/compare_voice.py --before reports/.../before.json --output reports/.../after
Use --render-only to rebuild the readable files after adding notes.json in the output folder.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
from html import escape
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SOURCE_FILES = (
    "products/ole5/chat.yaml", "products/rich/chat.yaml",
    "src/ole5_chat/agent/prompts/respond.py", "src/ole5_chat/agent/prompts/router.py",
    "src/ole5_chat/agent/prompts/ticket_operator.py",
    "src/ole5_chat/products.py", "src/ole5_chat/agent/turn_sheet.py",
    "src/ole5_chat/agent/prompts/guards.py",
)


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def run_case(case, output):
    with tempfile.TemporaryDirectory(prefix="ole5-voice-compare-") as temp:
        folder = Path(temp)
        os.environ.update(TRACING="false", KB_BACKEND="stub", KB_RETRIEVAL_MODE="direct",
                          TICKET_API_URL="", SHEETS_DIR=temp, ATTACHMENTS_DIR=str(folder / "uploads"))
        from openpyxl import Workbook
        from langchain_core.messages import AIMessage, HumanMessage
        from ole5_chat import products, sessions, sheets
        from ole5_chat.agent import session as agent_session
        from ole5_chat.agent.nodes.respond import respond_node
        from ole5_chat.agent.nodes.validate import validate_node
        from ole5_chat.agent.state import initial_state
        from ole5_chat.agent.turn_sheet import build_turn_sheet
        from ole5_chat.config import get_settings
        from ole5_chat.identity import Identity
        from ole5_chat.tickets.client import ticket_client

        logging.basicConfig(filename=Path(output) / "cases" / f"{case['id']}.log",
                            level=logging.WARNING, force=True)
        shutil.copy(ROOT / "sheets" / "values.xlsx", folder)
        shutil.copy(ROOT / "evals/fixtures/proof_tickets.xlsx", folder / "tickets.xlsx")
        workbook = Workbook()
        workbook.active.append(["email", "name", "organization", "product", "language", "active"])
        workbook.active.append(["ole5.test@example.com", "Test Customer", "Test Organization", "OLE5", "ar", "yes"])
        workbook.save(folder / "customers.xlsx")
        get_settings.cache_clear()
        sheets._rows.cache_clear()
        products.load.cache_clear()
        ticket_client.cache_clear()
        settings = get_settings()
        assert not settings.ticket_api_url and settings.sheets_dir == folder
        sheets.initialize_runtime_sheets()
        result = {"id": case["id"], "mode": case["mode"], "product": case.get("product", "OLE5"),
                  "model": settings.groq_model, "turns": [], "completed": False}
        try:
            if case["mode"] == "full_graph_stub_kb":
                sid, initial = sessions.start("ole5.test@example.com")
                result["greeting"] = initial["messages"][-1].text
                for old_turn in case["turns"]:
                    started = time.monotonic()
                    state = sessions.send(sid, old_turn["customer"])
                    history = list(agent_session._graph.get_state_history(agent_session._config(sid)))
                    before = next(s.values for s in history if s.next == ("finalize",))
                    result["turns"].append({
                        "customer": old_turn["customer"], "answer": state["messages"][-1].text,
                        "stage": build_turn_sheet(before, before.get("language") or "ar")["stage"],
                        "validation": before.get("validation_outcome"),
                        "validation_reason": before.get("validation_reason"),
                        "search_outcome": before.get("search_outcome"), "intent": before.get("intent"),
                        "route_reason": before.get("route_reason"),
                        "operator_action": (before.get("ticket_operator_result") or {}).get("action"),
                        "queued": len(state.get("pending_issues") or []),
                        "retained_evidence": bool(state.get("last_answer_evidence")),
                        "seconds": round(time.monotonic() - started, 2),
                    })
            else:
                assert case["mode"] == "respond_validate_fixed_evidence"
                product = result["product"]
                state = initial_state(Identity(customer_id="voice-test", email="voice-test@example.com",
                    name="Test Customer", organization="Test Organization", product=product, language="ar"))
                refs = case.get("evidence", [])
                message = case["turns"][0]["customer"]
                state.update(language="ar", intent="question" if refs else "other",
                    search_outcome="answered" if refs else None, current_references=refs,
                    messages=[AIMessage(content=products.load(product).greet("ar")), HumanMessage(content=message)])
                started = time.monotonic()
                for _ in range(4):
                    state.update(respond_node(state))
                    state.update(validate_node(state))
                    if state["validation_outcome"] != "repair":
                        break
                if state["validation_outcome"] == "repair":
                    raise RuntimeError("Response exceeded the validation repair budget")
                result["evidence"] = refs
                result["turns"].append({"customer": message, "answer": state["reply"],
                    "stage": build_turn_sheet(state, "ar")["stage"], "validation": state["validation_outcome"],
                    "seconds": round(time.monotonic() - started, 2)})
            result["completed"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"[:500]
        path = Path(output) / "cases" / f"{case['id']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def review_flags(cases):
    """Transparent review aids, not a dialect/naturalness score."""
    flags = []
    for case in cases:
        previous = None
        for index, turn in enumerate(case["turns"], 1):
            answer = turn["answer"]
            normalized = " ".join(answer.split())
            labels = []
            if case.get("product", "OLE5") == "OLE5" and re.search(
                r"(?<!\w)(?:لم يتم|ما تم|تم)\s+(?:إلغاء|رفع|إرسال|تسجيل|إنشاء|إنشاؤ|تجهيز)", answer
            ):
                labels.append("formal passive ticket wording")
            if previous == normalized:
                labels.append("identical consecutive reply")
            if turn["customer"] == "يعطيك العافية" and answer.startswith("يعطيك العافية"):
                labels.append("courtesy echoed unchanged")
            if turn.get("validation") != "pass":
                labels.append("application validation did not pass")
            for label in labels:
                flags.append({"case": case["id"], "turn": index, "flag": label})
            previous = normalized
    return flags


def render(before, after, output):
    new_by_id = {c["id"]: c for c in after["cases"]}
    notes_file = output / "notes.json"
    notes = json.loads(notes_file.read_text()) if notes_file.exists() else {}
    total = sum(len(c["turns"]) for c in before["cases"])
    introduction = (
        f"The same {total} customer messages, shown in the same order for both versions. "
        "Before is the original recorded audit; After is a new live run after the prompt edits. "
        "The model and response temperature are unchanged. Full conversations develop independently, so "
        "workflow states and generated mock ticket numbers may differ. Knowledge is fixed/stubbed and tickets are mocked; this does not test "
        "production retrieval. These are recorded replies, not rewritten demonstrations. One sample per version "
        "does not establish a statistical improvement."
    )
    markdown = ["**Saudi voice: before and after**", introduction]
    cards = []
    if notes.get("summary"):
        markdown.append(notes["summary"])
        cards.append(f'<p class="summary">{escape(notes["summary"])}</p>')
    for old in before["cases"]:
        new = new_by_id.get(old["id"], {})
        title = old["id"].replace("-", " ").capitalize()
        note = notes.get(old["id"], "")
        markdown.append(f"**{title}**")
        card = [f'<section id="{escape(old["id"])}"><h2>{escape(title)}</h2>']
        if note:
            markdown.append(note)
            card.append(f'<p class="note">{escape(note)}</p>')
        pairs = []
        if old.get("greeting"):
            pairs.append(("Opening message", old["greeting"], new.get("greeting", "Not recorded")))
        for index, turn in enumerate(old["turns"]):
            new_turns = new.get("turns", [])
            candidate = new_turns[index] if index < len(new_turns) else None
            if candidate is not None:
                assert candidate["customer"] == turn["customer"], "Customer messages differ"
            pairs.append((turn["customer"], turn["answer"], candidate["answer"] if candidate else "NOT COMPLETED"))
        for customer, old_answer, new_answer in pairs:
            markdown.append("**Opening message**" if customer == "Opening message" else f"Customer: **{customer}**")
            def cell(value):
                return value.replace("|", "\\|").replace("\n", "<br>")
            markdown.append("| Before | After |\n|---|---|\n"
                            f"| {cell(old_answer)} | {cell(new_answer)} |")
            card.append(f'<div class="customer" dir="auto">{escape(customer)}</div>'
                        '<div class="pair">'
                        f'<article><h3>Before · قبل</h3><div dir="auto">{escape(old_answer)}</div></article>'
                        f'<article class="after"><h3>After · بعد</h3><div dir="auto">{escape(new_answer)}</div></article>'
                        '</div>')
        card.append('</section>')
        cards.append("".join(card))
    flags = {"before": review_flags(before["cases"]), "after": review_flags(after["cases"])}
    (output / "review_flags.json").write_text(json.dumps(flags, ensure_ascii=False, indent=2))
    markdown.append("Automatic review flags are in review_flags.json. They identify a few visible patterns; "
                    "they do not measure Saudi authenticity or replace reading the conversations.")
    (output / "comparison.md").write_text("\n".join(line.rstrip() for line in "\n\n".join(markdown).splitlines()) + "\n", encoding="utf-8")
    style = """
    :root{font-family:Arial,Tahoma,sans-serif;color:#18302d;background:#f3f5f2;line-height:1.7}
    body{max-width:1180px;margin:0 auto;padding:32px 20px}h1{line-height:1.2;font-size:32px}
    header p{max-width:950px;color:#4a605a}section{margin:36px 0;border-top:1px solid #cdd6d0;padding-top:16px}
    h2{font-size:21px}h3{margin:0 0 10px;font-size:13px;color:#63706b;direction:ltr}
    .customer{background:#e3ebe6;border-radius:10px;padding:12px 18px;margin-top:22px;font-weight:700}
    .pair{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px}
    article{padding:18px;background:white;border:1px solid #d8ddd9;border-radius:10px;min-width:0}
    article div{white-space:pre-wrap;overflow-wrap:anywhere;font-size:18px}.after{border-top:3px solid #257665}
    .note,.summary{background:#fff7df;border-left:3px solid #cfa83d;padding:12px 16px}
    nav{display:flex;flex-wrap:wrap;gap:8px}nav a{color:#225e52;background:white;padding:4px 10px;border-radius:6px}
    @media(max-width:700px){.pair{grid-template-columns:1fr}body{padding:18px 12px}}
    @media print{body{max-width:none;padding:0}nav{display:none}.pair{break-inside:avoid}section{break-before:auto}}
    """
    nav = " ".join(f'<a href="#{escape(c["id"])}">{escape(c["id"].replace("-", " "))}</a>' for c in before["cases"])
    document = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>Saudi voice — Before and after</title><style>{style}</style><body>'
                f'<header><h1>Saudi voice · Before and after</h1><p>{escape(introduction)}</p><nav>{nav}</nav></header>'
                + "".join(cards) + '</body></html>')
    (output / "comparison.html").write_text("\n".join(line.rstrip() for line in document.splitlines()) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    before = json.loads(args.before.read_text(encoding="utf-8"))
    if args.render_only:
        render(before, json.loads((output / "after.json").read_text()), output)
        return
    if (output / "after.json").exists() or (output / "cases").exists():
        parser.error("Use a fresh output folder to preserve earlier results, or --render-only.")
    (output / "cases").mkdir(parents=True)
    hashes = source_hashes()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(run_case, case, str(output)) for case in before["cases"]]
        for job in as_completed(jobs):
            result = job.result()
            results.append(result)
            print(json.dumps({"case": result["id"], "completed": result["completed"],
                              "replies": len(result["turns"])}, ensure_ascii=False), flush=True)
    assert source_hashes() == hashes, "Source changed during replay"
    after = {"metadata": {"source_hashes": hashes, "model": results[0]["model"],
                          "response_temperature": 0.1, "baseline": str(args.before),
                          "method": "same archived customer messages/evidence; isolated sheets and mock tickets"},
             "cases": sorted(results, key=lambda c: c["id"])}
    (output / "after.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    render(before, after, output)
    if not all(c["completed"] for c in results):
        raise SystemExit("Some cases did not complete; their errors are preserved in after.json.")


if __name__ == "__main__":
    main()
