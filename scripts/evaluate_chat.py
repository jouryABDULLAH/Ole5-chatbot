"""Run live routing, guard, bilingual response and conversation checks with isolated mock tickets.

No evalkit installation is required. Writes raw outputs and a readable report without changing production sheets.
python scripts/evaluate_chat.py --output reports/bilingual_validation_2026-11-17
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
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
SUITES = ("reg_router", "reg_guards", "reg_respond", "bilingual_voice", "bilingual_conversations")


def checks(expected, result):
    failures = []
    for field in ("stage", "language", "validation"):
        if field in expected:
            allowed = expected[field] if isinstance(expected[field], list) else [expected[field]]
            if result.get(field) not in allowed:
                failures.append(f"{field}: expected {allowed}, got {result.get(field)!r}")
    for field, want in expected.get("facts", {}).items():
        if want == "UNDECIDED":
            continue
        if result.get(field) != want:
            failures.append(f"{field}: expected {want!r}, got {result.get(field)!r}")
    answer = result.get("answer", "")
    for group in expected.get("must_have", []):
        if not any(text.casefold() in answer.casefold() for text in group):
            failures.append(f"missing one of {group}")
    for text in expected.get("must_not", []):
        if text.casefold() in answer.casefold():
            failures.append(f"unexpected text: {text}")
    if "max_questions" in expected and len(re.findall(r"[?؟]", answer)) > expected["max_questions"]:
        failures.append("too many questions")
    if result.get("validation") == "fallback" and "validation" not in expected:
        failures.append("response used a fallback")
    return failures


def run(suite, case, output):
    started = time.monotonic()
    record = {"suite": suite, "id": case["id"], "category": case.get("category"),
              "input": case["input"], "expected": case.get("expected", {})}
    with tempfile.TemporaryDirectory(prefix="ole5-eval-") as folder:
        os.environ.update(TRACING="false", KB_BACKEND="stub", KB_RETRIEVAL_MODE="direct",
                          TICKET_API_URL="", SHEETS_DIR=folder, ATTACHMENTS_DIR=f"{folder}/uploads")
        from openpyxl import Workbook
        from ole5_chat import products, sessions, sheets
        from ole5_chat.config import get_settings
        from ole5_chat.tickets.client import ticket_client
        logging.basicConfig(filename=Path(output) / "cases" / f"{suite}-{case['id']}.log",
                            level=logging.WARNING, force=True)
        shutil.copy(ROOT / "sheets/values.xlsx", folder)
        shutil.copy(ROOT / "evals/fixtures/proof_tickets.xlsx", Path(folder) / "tickets.xlsx")
        workbook = Workbook()
        workbook.active.append(["email", "name", "organization", "product", "language", "active"])
        workbook.active.append(["ole5.test@example.com", "Test Customer", "Test Organization", "OLE5", "ar", "yes"])
        workbook.active.append(["rich.test@example.com", "Test Customer", "Test Organization", "RICH", "en", "yes"])
        workbook.save(Path(folder) / "customers.xlsx")
        get_settings.cache_clear()
        products.load.cache_clear()
        sheets._rows.cache_clear()
        ticket_client.cache_clear()
        sheets.initialize_runtime_sheets()
        from evals.regression_task import _state, _turn, router_task, guard_task
        from ole5_chat.agent.nodes.respond import respond_node
        from ole5_chat.agent.nodes.validate import validate_node
        from ole5_chat.agent.turn_sheet import build_turn_sheet
        settings = get_settings()
        assert not settings.ticket_api_url and settings.sheets_dir == Path(folder)
        record["model"] = settings.groq_model
        try:
            if suite == "reg_router":
                result = router_task(case["input"])
            elif suite == "reg_guards":
                result = guard_task(case["input"])
            elif suite == "bilingual_conversations":
                email = "rich.test@example.com" if case["input"].get("product") == "RICH" else "ole5.test@example.com"
                sid, initial = sessions.start(email)
                turns = []
                failures = []
                for index, message in enumerate(case["input"]["turns"]):
                    state = sessions.submit(sid) if message == "[submit]" else sessions.send(sid, message)
                    turn = _turn(sid, state)
                    turn.update(customer=message, language=state.get("language"))
                    expected = case.get("expected", {}).get("turns", [])
                    problems = checks(expected[index] if index < len(expected) else {}, turn)
                    failures.extend(f"turn {index + 1}: {problem}" for problem in problems)
                    turns.append(turn)
                result = {"greeting": initial["messages"][-1].text, "turns": turns}
                record["failures"] = failures
            else:
                state = _state(case["input"]["state"])
                state["identity"] = state["identity"].model_copy(update={"product": case["input"].get("product", "OLE5")})
                for _ in range(4):
                    state.update(respond_node(state))
                    state.update(validate_node(state))
                    if state["validation_outcome"] != "repair":
                        break
                result = {"answer": state["reply"], "language": state["language"],
                          "stage": build_turn_sheet(state, state["language"])["stage"],
                          "validation": state["validation_outcome"],
                          "validation_reason": state.get("validation_reason")}
            record["result"] = result
            if "failures" not in record:
                record["failures"] = checks(record["expected"], result)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"[:600]
            record["failures"] = [record["error"]]
    undecided = "UNDECIDED" in record["expected"].get("facts", {}).values()
    record.update(passed=None if undecided else not record["failures"],
                  seconds=round(time.monotonic() - started, 2))
    if undecided:
        record["review_note"] = "Expected behavior has not been decided; this case is unscored."
    (Path(output) / "cases" / f"{suite}-{case['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def render(records, output):
    from collections import Counter
    lines = ["**Bilingual chat validation**", "Live model calls with fixed evidence, isolated sheets and mock tickets. "
             "Checks cover facts, routing, language and workflow behavior. Passing them is not a native-speaker naturalness score.",
             "| Suite | Passed | Scored | Unscored |\n|---|---:|---:|---:|"]
    for suite in sorted({r["suite"] for r in records}):
        group = [r for r in records if r["suite"] == suite]
        lines.append(f"| {suite} | {sum(r['passed'] is True for r in group)} | "
                     f"{sum(r['passed'] is not None for r in group)} | {sum(r['passed'] is None for r in group)} |")
    coverage = Counter((r["result"]["stage"], r["result"]["language"]) for r in records
                       if r["suite"] == "bilingual_voice" and "result" in r)
    lines.extend(["", "**Response-stage coverage**", "| Stage | Arabic | English |\n|---|---:|---:|"])
    for stage in sorted({stage for stage, _ in coverage}):
        lines.append(f"| {stage} | {coverage[stage, 'ar']} | {coverage[stage, 'en']} |")
    lines.extend(["", "**Cases needing review**"])
    for record in records:
        if not record["passed"]:
            lines.append(f"- {record['suite']}/{record['id']}: " +
                         "; ".join(record["failures"] or [record.get("review_note", "Needs review")]))
    lines.extend(["", "**Recorded responses and conversations**"])
    for record in records:
        if record["suite"] not in {"reg_respond", "bilingual_voice", "bilingual_conversations"}:
            continue
        lines.extend(["", f"**{record['suite']} / {record['id']} — {'PASS' if record['passed'] else 'REVIEW'}**"])
        result = record.get("result", {})
        if "turns" in result:
            for turn in result["turns"]:
                lines.extend(["", f"Customer: {turn['customer']}", "", f"Assistant: {turn['answer']}"])
        else:
            messages = record["input"].get("state", {}).get("messages", [])
            lines.extend(["", "Customer: " + (messages[-1]["content"] if messages else "(button event)"),
                          "", "Assistant: " + result.get("answer", record.get("error", "No response"))])
    (output / "results.md").write_text("\n".join(line.rstrip() for line in "\n".join(lines).splitlines()) + "\n", encoding="utf-8")


def render_comparison(previous, records, output):
    """Compare saved model replies, without regenerating or hand-editing either version."""
    old = {(r["suite"], r["id"]): r for r in previous}
    sections = []
    for record in records:
        baseline = old.get((record["suite"], record["id"]))
        if not baseline or record["suite"] not in {"reg_respond", "bilingual_voice", "bilingual_conversations"}:
            continue
        before, after = baseline.get("result", {}), record.get("result", {})
        if "turns" in after:
            rows = [(turn["customer"], before.get("turns", [])[i].get("answer", ""), turn["answer"])
                    for i, turn in enumerate(after["turns"]) if i < len(before.get("turns", []))]
        else:
            messages = record["input"].get("state", {}).get("messages", [])
            rows = [(messages[-1]["content"] if messages else "(button event)",
                     before.get("answer", ""), after.get("answer", ""))]
        sections.append(f"<section><h2>{escape(record['suite'] + '/' + record['id'])}</h2>")
        for customer, left, right in rows:
            sections.append(f'<p class="customer" dir="auto">{escape(customer)}</p><div class="pair">'
                            f'<article><h3>Previous reply</h3><p dir="auto">{escape(left)}</p></article>'
                            f'<article><h3>Updated reply</h3><p dir="auto">{escape(right)}</p></article></div>')
        sections.append("</section>")
    document = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bilingual response comparison</title>
<style>body{font:17px/1.65 system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 20px;color:#172b36;background:#f6f8fa}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}article{background:white;border:1px solid #ccd6df;border-radius:10px;padding:18px}
h3{font-size:14px;color:#526778;margin:0}p{white-space:pre-wrap}.customer{font-weight:600;border-left:4px solid #24736b;padding:12px;background:#e5f3ef}
section{margin:36px 0}h2{font-size:19px;overflow-wrap:anywhere}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>
<h1>Previous and updated conversations</h1><p>Exact recorded model output from the broad run and targeted recheck.
Only rechecked scenarios appear here; this is a selected diagnostic comparison, not a quality score.
Full conversations run independently, so their workflow can diverge. Read results.md for remaining failures.</p>
""" + "\n".join(sections) + "</html>"
    (output / "comparison.html").write_text("\n".join(line.rstrip() for line in document.splitlines()) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suites", nargs="+", choices=SUITES, default=list(SUITES))
    parser.add_argument("--workers", type=int, default=1, help="Keep low to avoid shared model token limits")
    parser.add_argument("--case-ids", nargs="+", help="Run only these case IDs")
    parser.add_argument("--rerun-from", type=Path, help="Rerun cases needing review in an earlier results.json")
    args = parser.parse_args()
    if (args.output / "cases").exists():
        parser.error("Choose a fresh output directory to preserve previous results")
    (args.output / "cases").mkdir(parents=True)
    jobs = [(suite, case) for suite in args.suites for case in
            json.loads((ROOT / "evals/datasets" / f"{suite}.json").read_text())["cases"]]
    if args.case_ids:
        jobs = [(suite, case) for suite, case in jobs if case["id"] in args.case_ids]
    if args.rerun_from:
        previous = json.loads(args.rerun_from.read_text())["cases"]
        retry = {(r["suite"], r["id"]) for r in previous if not r["passed"]}
        jobs = [(suite, case) for suite, case in jobs if (suite, case["id"]) in retry]
    if not jobs or args.workers < 1:
        parser.error("Select at least one case and a positive worker count")
    paths = [*ROOT.glob("src/ole5_chat/**/*.py"), *ROOT.glob("products/*/chat.yaml"),
             *(ROOT / "evals/datasets" / f"{suite}.json" for suite in args.suites)]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, suite, case, str(args.output.resolve())) for suite, case in jobs]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"{len(results)}/{len(jobs)} {result['suite']}/{result['id']}: {'PASS' if result['passed'] else 'REVIEW'}", flush=True)
    results.sort(key=lambda r: (r["suite"], r["id"]))
    from ole5_chat.config import get_settings
    payload = {"metadata": {"model": results[0]["model"], "endpoint": get_settings().groq_api_base,
                            "response_temperature": 0.1, "structured_first_attempt_temperature": 0,
                            "kb": "fixed/stub", "tickets": "mock", "source_hashes": hashes}, "cases": results}
    (args.output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    render(results, args.output)
    if args.rerun_from:
        render_comparison(previous, results, args.output)
    print(f"Finished: {sum(r['passed'] is True for r in results)}/{len(results)} passed. See {args.output / 'results.md'}")


if __name__ == "__main__":
    main()
