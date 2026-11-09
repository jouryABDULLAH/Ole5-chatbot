"""Retrieval evaluation over settings: RAGent2's own evaluate(), for each search mode, top_k and candidate pool.

RAGent2 writes one question per indexed chunk (generate_questions), each anchored to the chunk it came from, so no
golden set is needed. evaluate() then checks whether that chunk is retrieved:
    search  one raw search() call per question, no model: top_k_hit_rate is plain recall within top_k
    agent   the find() flow the chat uses: search_hit_rate is "the agent's searching surfaced the chunk",
            selected_hit_rate is "it also judged the chunk an answer"; the gap is what grading drops

Questions are generated once and saved, so every setting is scored on the same set (and they can be reviewed).
The pool and top_k go through our own settings and the RAGent2 tenant is rebuilt for each combination, exactly as
the chat builds it (knowledge/retriever.py).

Usage:
    python scripts/retrieval_eval.py --generate                      write a question set, then stop
    python scripts/retrieval_eval.py --questions <file> --modes search --top-k 5 8 15 30 --pool 10 30
    python scripts/retrieval_eval.py --questions <file> --modes agent --top-k 8 15 --pool 10 --limit 20

agent mode makes several model calls per question and takes minutes per question set; start with --limit.
Writes to retrieval_results/: questions-<time>.json from --generate, and eval-<time>/ (one JSON per combination,
plus summary.json) from a run."""

import argparse
import json
import time
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from ole5_chat.config import ROOT, get_settings  # noqa: E402
from ole5_chat.knowledge import retriever  # noqa: E402

RESULTS = ROOT / "retrieval_results"


def tenant():
    return retriever.tenant(get_settings().kb_tenants["OLE5"])


def generate() -> Path:
    """One question per indexed chunk, from RAGent2; saved for review and reuse."""
    result = tenant().generate_questions()
    out = RESULTS / f"questions-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(result['questions'])} questions from {result['chunks']} of {result['total_chunks']} chunks "
          f"({len(result['skipped_chunks'])} skipped). Review them, then pass --questions {out}")
    return out


def configure(top_k: int, pool: int) -> None:
    """Apply top_k and pool the way the chat does, then rebuild the cached RAGent2 objects."""
    settings = get_settings()
    settings.kb_retrieval_mode = "agentic"  # not agentic_fast, which swaps in the voice limits
    settings.kb_top_k, settings.kb_candidate_pool = top_k, pool
    retriever.reset()


def main() -> None:
    parser = argparse.ArgumentParser(description="RAGent2 retrieval evaluation over modes, top_k and pool.")
    parser.add_argument("--generate", action="store_true", help="write a question set and stop")
    parser.add_argument("--questions", type=Path, help="a question set written by --generate")
    parser.add_argument("--modes", nargs="+", default=["search"], choices=["search", "agent"])
    parser.add_argument("--top-k", nargs="+", type=int, default=[8])
    parser.add_argument("--pool", nargs="+", type=int, default=[10])
    parser.add_argument("--limit", type=int, help="score only the first N questions")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds to wait between combinations")
    args = parser.parse_args()
    if args.generate:
        generate()
        return
    if not args.questions:
        parser.error("--questions is required (write a set with --generate first)")
    questions = json.loads(args.questions.read_text(encoding="utf-8"))["questions"][:args.limit]
    out = RESULTS / f"eval-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True)
    summary = []
    combos = [(m, k, p) for m in args.modes for p in args.pool for k in args.top_k if p >= k or m == "agent"]
    for n, (mode, top_k, pool) in enumerate(combos):
        if n:
            time.sleep(args.pause)
        configure(top_k, pool)
        started = time.perf_counter()
        report = tenant().evaluate(questions=questions, mode=mode, top_k=top_k)
        seconds = round(time.perf_counter() - started, 1)
        name = f"{mode}_topk{top_k}_pool{pool}"
        (out / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                         encoding="utf-8")
        metrics = report.get("metrics", {})
        summary.append({"mode": mode, "top_k": top_k, "pool": pool, "seconds": seconds,
                        "per_question_s": round(seconds / max(len(questions), 1), 2), "metrics": metrics})
        overall = metrics.get("overall", {})  # evaluate() nests the totals under overall, then per question type
        rates = {k: v for k, v in overall.items() if k.endswith("_rate") or k in ("errors", "outcomes")}
        print(f"{name:24} {seconds:>7}s  {rates}")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(questions)} questions x {len(combos)} combinations. Saved {out}")


if __name__ == "__main__":
    main()
