"""Knowledge search over RAGent2, ported from the support agent's retriever: one find() per question, which rephrases, searches and grades each passage DIRECT or INFERENTIAL. Read-only; never raises."""

import logging
import time
from functools import lru_cache
from typing import Any

from openinference.instrumentation import suppress_tracing

from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Evidence, Retrieval, Verdict
from ole5_chat.knowledge.qdrant_login import SessionLogin

log = logging.getLogger(__name__)

UNRELIABLE_STOPS = {"timeout", "error", "model_failed"}


class KnowledgeUnavailable(RuntimeError):
    """RAGent2 is not installed or not configured."""


def qdrant_client() -> Any:
    from qdrant_client import QdrantClient

    s = get_settings()
    qdrant_key = s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else ""
    extra = {"auth": SessionLogin(s.qdrant_session_cookie)} if s.qdrant_login_email else {}
    return QdrantClient(url=s.qdrant_url, api_key=qdrant_key or None, timeout=int(s.qdrant_timeout), **extra)


class NoRerank:
    """Stands in for ragent2's cross-encoder. Ragent.retriever reads Ragent.reranker, which would download and load BAAI/bge-reranker-v2-m3 even when find(use_reranker=False); passing this keeps the model out entirely."""

    def score(self, query: str, candidates: Any) -> list:
        return list(candidates)


@lru_cache(maxsize=2)
def ragent(voice: bool = False) -> Any:
    """The RAGent2 instance; voice=True is the lighter profile for voice turns (fewer passages graded more at once,
    fewer rounds, D28), sharing the text chat instance's Qdrant client and embedder (the slow part to load)."""
    s = get_settings()
    if not s.groq_api_key or not s.groq_api_key.get_secret_value():
        raise KnowledgeUnavailable("GROQ_API_KEY is not set")
    try:
        from ragent2 import Ragent
        from ragent2.config import Settings as RagSettings
    except ImportError as exc:
        raise KnowledgeUnavailable("ragent2 is not installed in this environment") from exc
    qdrant_key = s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else ""
    options = dict(
        groq_api_key=s.groq_api_key.get_secret_value(),
        groq_api_base=s.groq_api_base,
        answer_model=s.groq_model,
        qdrant_url=s.qdrant_url,
        qdrant_api_key=qdrant_key or None,
        embedding_service_url=s.embedding_service_url,
        embedding_service_timeout=s.kb_embed_timeout,
        candidate_pool_size=s.kb_candidate_pool,
        hybrid_prefetch_limit=s.kb_candidate_pool,
        top_k=s.kb_top_k,
        tool_call_repair_attempts=s.kb_tool_repair_attempts,
        max_search_rounds=s.kb_max_rounds,
        find_timeout_seconds=s.kb_find_timeout,
    )
    if not voice:
        return Ragent(client=qdrant_client(), reranker=NoRerank(), settings=RagSettings(**options))
    options.update(top_k=s.voice_kb_top_k, max_search_rounds=s.voice_kb_max_rounds,
                   grading_max_concurrency=s.voice_kb_grading_concurrency, find_timeout_seconds=s.voice_kb_find_timeout)
    chat = ragent()
    return Ragent(client=chat.client, embedder=chat.embedder, reranker=NoRerank(), settings=RagSettings(**options))


@lru_cache(maxsize=16)
def tenant(name: str, voice: bool = False) -> Any:
    # ragent() and ragent(False) would be two cache entries, so the text chat instance is always ragent().
    return ragent(True).tenant(name) if voice else ragent().tenant(name)


def warm(tenants: list[str]) -> None:
    profiles = (False, True) if get_settings().voice_enabled else (False,)
    for voice in profiles:
        rag = ragent(True) if voice else ragent()
        for part in ("client", "embedder", "retriever", "find_model"):
            getattr(rag, part)
        for name in tenants:
            tenant(name, voice=True) if voice else tenant(name)


def _to_evidence(chunk: Any) -> Evidence:
    meta = getattr(chunk, "metadata", None) or {}
    return Evidence(
        text=getattr(chunk, "text", "") or "",
        document=getattr(chunk, "document_name", None) or meta.get("document_name") or "unknown",
        section=meta.get("section"),
        source_file=meta.get("source_file"),
        relation=(getattr(chunk, "relation", None) or "UNKNOWN").upper(),
        score=int(getattr(chunk, "score", 0) or 0),
        reason=getattr(chunk, "reason", "") or "",
    )


def _verdict(evidence: list[Evidence]) -> Verdict:
    if any(e.relation == "DIRECT" for e in evidence):
        return Verdict.ANSWERABLE
    return Verdict.PARTIAL if evidence else Verdict.NOT_IN_KB


TRANSIENT = ("tool_use_failed", "Failed to parse tool call arguments", "ReadTimeout", "Read timed out")


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _find(name: str, question: str, voice: bool = False):
    """One find() call, repeated once on two transient failures: Groq rejecting the search agent's tool call (the model sometimes puts several queries as a list in one call), and the embedding gateway timing out."""
    retries = get_settings().kb_find_retries
    for attempt in range(retries + 1):
        try:
            with suppress_tracing():
                return (tenant(name, voice=True) if voice else tenant(name)).find(question, use_reranker=False)
        except Exception as exc:
            if attempt == retries or not any(mark in str(exc) for mark in TRANSIENT):
                raise
            log.warning("kb  tenant=%s attempt=%d rejected tool call, trying again: %s", name, attempt + 1,
                        _short(str(exc)))


def retrieve(question: str, tenant_name: str, voice: bool = False) -> Retrieval:
    started = time.perf_counter()
    try:
        result = _find(tenant_name, question, voice)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        log.exception("kb  tenant=%s failed after ms=%d", tenant_name, elapsed)
        return Retrieval(query=question, verdict=Verdict.NOT_IN_KB, elapsed_ms=elapsed,
                         error=f"{type(exc).__name__}: {exc}")

    elapsed = int((time.perf_counter() - started) * 1000)
    evidence = [_to_evidence(c) for c in (getattr(result, "chunks", None) or ())]
    diagnostics = getattr(result, "diagnostics", None)
    retrieval = Retrieval(
        query=question,
        verdict=_verdict(evidence),
        evidence=evidence,
        queries_tried=list(getattr(diagnostics, "queries_tried", ()) or ()),
        stop_reason=getattr(diagnostics, "stop_reason", None),
        warnings=list(getattr(result, "warnings", ()) or ()),
        elapsed_ms=elapsed,
    )

    returned = {e.document for e in evidence}
    seen: dict[str, str] = {}
    for t in getattr(diagnostics, "trace", ()) or ():
        name = getattr(t, "document_name", None)
        if not name or getattr(t, "returned", False) or name in returned:
            continue
        seen[name] = getattr(t, "relation", None) or "ungraded"
    retrieval.considered = sorted(f"{n} ({r.lower()})" for n, r in seen.items())

    if not evidence and (retrieval.stop_reason in UNRELIABLE_STOPS or retrieval.warnings):
        retrieval.error = f"retrieval incomplete: {retrieval.stop_reason}"
        log.warning("kb  incomplete stop=%s warnings=%s query=%r", retrieval.stop_reason, retrieval.warnings,
                    question[:80])

    log.info("kb  tenant=%s verdict=%s chunks=%d direct=%d ms=%d stop=%s queries_tried=%s",
             tenant_name, retrieval.verdict.value, len(evidence), len(retrieval.direct), elapsed,
             retrieval.stop_reason, retrieval.queries_tried)
    return retrieval
