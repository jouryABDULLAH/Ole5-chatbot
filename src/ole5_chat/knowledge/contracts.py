"""What a knowledge search returns, in our own shape rather than RAGent2's, ported from the support agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Verdict(str, Enum):
    ANSWERABLE = "answerable"
    PARTIAL = "partial"
    NOT_IN_KB = "not_in_kb"


@dataclass
class Evidence:
    text: str
    document: str
    section: str | None
    source_file: str | None
    relation: str
    score: int
    reason: str

    @property
    def language(self) -> str | None:
        if not self.source_file:
            return None
        for part in self.source_file.upper().split("-"):
            if part in ("AR", "EN"):
                return part.lower()
        return None


@dataclass
class Retrieval:
    query: str
    verdict: Verdict
    evidence: list[Evidence] = field(default_factory=list)
    queries_tried: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    considered: list[str] = field(default_factory=list)

    @property
    def direct(self) -> list[Evidence]:
        return [e for e in self.evidence if e.relation == "DIRECT"]

    @property
    def documents(self) -> list[str]:
        seen: list[str] = []
        for e in self.evidence:
            if e.document not in seen:
                seen.append(e.document)
        return seen

    def render(self) -> str:
        if not self.evidence:
            return "KNOWLEDGE BASE\n  Nothing found. This is not covered."
        lines = [f"KNOWLEDGE BASE ({self.verdict.value})"]
        for i, e in enumerate(self.evidence, 1):
            where = e.document + (f" / {e.section}" if e.section else "")
            lines += ["", f"  [{i}] {where}  ({e.relation.lower()})", "  " + e.text.strip().replace("\n", "\n  ")]
        return "\n".join(lines)

    def as_json(self) -> dict:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        return data

    @classmethod
    def from_json(cls, data: dict) -> Retrieval:
        return cls(**{**data, "verdict": Verdict(data["verdict"]),
                      "evidence": [Evidence(**e) for e in data.get("evidence", [])]})


def strongest(retrievals: list[Retrieval]) -> Verdict:
    if any(r.verdict == Verdict.ANSWERABLE for r in retrievals):
        return Verdict.ANSWERABLE
    if any(r.verdict == Verdict.PARTIAL for r in retrievals):
        return Verdict.PARTIAL
    return Verdict.NOT_IN_KB


def all_evidence(retrievals: list[Retrieval]) -> list[Evidence]:
    seen: set[str] = set()
    out: list[Evidence] = []
    for r in retrievals:
        for e in r.evidence:
            if e.text[:200] not in seen:
                seen.add(e.text[:200])
                out.append(e)
    return out


def merge(retrievals: list[Retrieval]) -> Retrieval:
    errors = [r.error for r in retrievals if r.error]
    return Retrieval(
        query=" | ".join(r.query for r in retrievals),
        verdict=strongest(retrievals),
        evidence=all_evidence(retrievals),
        queries_tried=[q for r in retrievals for q in r.queries_tried],
        elapsed_ms=sum(r.elapsed_ms for r in retrievals),
        error="; ".join(errors) if errors and len(errors) == len(retrievals) else None,
    )
