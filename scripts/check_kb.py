"""Checks, in order, that this machine can reach everything RAGent2 needs, then runs one real search. Usage: python scripts/check_kb.py "a question the Ole5 knowledge base covers" """

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from ole5_chat.config import get_settings
from ole5_chat.knowledge import retriever
from ole5_chat.knowledge.qdrant_login import LoginFailed, login


def probe(name: str, url: str, headers: dict | None = None) -> bool:
    try:
        response = httpx.get(url, headers=headers or {}, timeout=15, follow_redirects=True)
        print(f"{name:<12} {response.status_code}  {url}")
        return True
    except httpx.HTTPError as exc:
        print(f"{name:<12} unreachable  {url}  ({type(exc).__name__}: {exc})")
        return False


def main() -> None:
    s = get_settings()
    question = sys.argv[1] if len(sys.argv) > 1 else "How do I reset my password?"
    key = s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else ""

    print("1. Reachability (any status code means reachable; qdrant+login should be 200)")
    probe("qdrant", f"{s.qdrant_url.rstrip('/')}/collections")
    if key:
        probe("qdrant+key", f"{s.qdrant_url.rstrip('/')}/collections", {"api-key": key})
    if s.qdrant_login_email:
        try:
            cookie = login()
            probe("qdrant+login", f"{s.qdrant_url.rstrip('/')}/collections",
                  {"Cookie": f"{s.qdrant_session_cookie}={cookie}"})
        except (LoginFailed, httpx.HTTPError) as exc:
            print(f"qdrant+login failed: {exc}")
    probe("embeddings", s.embedding_service_url)
    probe("huggingface", "https://huggingface.co")
    print(f"   groq key from the environment: {'set' if s.groq_api_key and s.groq_api_key.get_secret_value() else 'MISSING'}")

    tenants = sorted(set(s.kb_tenants.values()))
    print(f"\n2. Loading RAGent2 models (the first run downloads the reranker, several GB) for {tenants}")
    started = time.perf_counter()
    retriever.warm(tenants)
    print(f"   loaded in {time.perf_counter() - started:.1f}s")

    for name in tenants:
        print(f"\n3. find() on tenant {name!r}: {question!r}")
        result = retriever.retrieve(question, name)
        print(f"   verdict={result.verdict.value} ms={result.elapsed_ms} stop={result.stop_reason} error={result.error}")
        print(f"   queries tried: {result.queries_tried}")
        for e in result.evidence:
            print(f"   [{e.relation}] {e.document} / {e.section}: {e.text[:160]!r}")
        if result.considered:
            print(f"   considered, not returned: {result.considered}")


if __name__ == "__main__":
    main()
