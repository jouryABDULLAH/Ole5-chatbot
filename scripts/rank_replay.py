"""Rank replay: where does the answering passage rank, before any model judges it?

RAGent2's raw search (tenant.search: hybrid dense and keyword retrieval, no model, no grading, no rephrasing) gives
the same ranking for the same wording every time. This replays wordings against it, 30 deep, and records the rank of
the first passage that holds the case's expected quote (reg_kb_answers expected.quote).

Two uses:
    python scripts/rank_replay.py [ids...]
        Stable facts per case: the rank for the customer's own message, and the rank when searching with the quote
        itself. A quote that cannot even find itself is missing from the index.
    python scripts/rank_replay.py --results exp_results/<run>/results.json [ids...]
        One run's failures: every case of that run where Search did not answer or the quote was not retrieved, with
        each wording that run actually searched (output.search_queries) replayed.

Reading a rank (today's top_k is 8):
    1-8     within the cut-off: if the run still missed it, the grading or the chosen wording dropped it
    9-30    found, below the cut-off: a wider top_k or pool would recover it
    none    not in the top 30 for that wording

Writes exp_results/rank_replay-<time>.json."""

import argparse
import json
import re
import time
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from ole5_chat.config import ROOT, get_settings  # noqa: E402
from ole5_chat.knowledge import retriever  # noqa: E402

DEPTH = 30


def flat(text: str) -> str:
    """The same normalisation as the answer_retrieved check: no bold, list dashes or spacing differences."""
    return " ".join(re.sub(r"\*\*|^\s*[-*]\s+", "", text or "", flags=re.MULTILINE).split())


def rank(tenant, query: str, quote: str) -> int | None:
    """1-based rank of the first passage holding the quote among the top DEPTH, or None."""
    wanted = flat(quote)
    passages = tenant.search(query, top_k=DEPTH, use_reranker=False)
    return next((i for i, passage in enumerate(passages, 1) if wanted in flat(passage.text)), None)


def failed_cases(results: Path) -> dict[str, list[str]]:
    """Case id to the wordings that run searched, for every case where Search missed or the quote was not retrieved."""
    failed = {}
    for run in json.loads(results.read_text(encoding="utf-8")):
        scores = {e["name"]: e.get("score") for e in run.get("evaluations") or []}
        if scores.get("search_answered") == 1 and scores.get("answer_retrieved") in (1, None):
            continue
        failed[run["metadata"]["id"]] = (run.get("output") or {}).get("search_queries") or []
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank of the answering passage in RAGent2's raw search.")
    parser.add_argument("ids", nargs="*", help="case ids; all (or all failed) when none are given")
    parser.add_argument("--results", type=Path, help="a reg_kb_answers results.json: replay its failed cases")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds to wait between cases (default 3)")
    args = parser.parse_args()
    cases = {c["id"]: c for c in json.loads((ROOT / "evals/datasets/reg_kb_answers.json").read_text(
        encoding="utf-8"))["cases"] if c["expected"].get("quote")}
    wordings = failed_cases(args.results) if args.results else {i: [] for i in cases}
    ids = [i for i in wordings if i in cases and (not args.ids or i in args.ids)]
    tenant = retriever.tenant(get_settings().kb_tenants["OLE5"])
    rows = []
    for n, case_id in enumerate(ids):
        if n:
            time.sleep(args.pause)  # spaces the calls out so the search service is not hit back to back
        case = cases[case_id]
        quote, message = case["expected"]["quote"], case["input"]["message"]
        row = {"id": case_id, "message_rank": rank(tenant, message, quote), "quote_rank": rank(tenant, quote, quote),
               "queries": [{"query": q, "rank": rank(tenant, q, quote)} for q in wordings[case_id]]}
        rows.append(row)
        searched = "  ".join(f"{q['rank'] or 'none'}" for q in row["queries"]) or "-"
        print(f"{case_id:7} message {row['message_rank'] or 'none':>4}  quote {row['quote_rank'] or 'none':>4}  "
              f"run's queries {searched}")
        for query in row["queries"]:
            print(f"          {query['rank'] or 'none':>4}  {query['query'][:90]}")
    out = ROOT / "exp_results" / f"rank_replay-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    band = lambda r: "none" if r is None else "1-8" if r <= 8 else "9-30"  # noqa: E731
    bands = {"1-8": 0, "9-30": 0, "none": 0}
    for row in rows:
        bands[band(row["message_rank"])] += 1
    print(f"\n{len(rows)} cases, customer's message: {bands}. Missing from the index (the quote does not find "
          f"itself): {[r['id'] for r in rows if r['quote_rank'] is None]}\nSaved {out}")


if __name__ == "__main__":
    main()
