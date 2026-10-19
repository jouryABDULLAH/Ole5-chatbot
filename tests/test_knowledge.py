"""RAGent2 results mapped to our Retrieval shape, with a fake find(), and the switch between RAGent2 and the stand-in."""

from types import SimpleNamespace

import pytest

from ole5_chat.config import get_settings
from ole5_chat.knowledge import retriever, search
from ole5_chat.knowledge.contracts import Verdict


def chunk(relation, document="KB-001 Signing in", text="passage"):
    return SimpleNamespace(text=text, document_name=document, relation=relation, score=3, reason="why",
                           metadata={"section": "S", "source_file": "KB-001-EN-signing-in.md"})


def result(*chunks, stop="done", trace=()):
    return SimpleNamespace(chunks=chunks, warnings=(),
                           diagnostics=SimpleNamespace(queries_tried=("q1", "q2"), stop_reason=stop, trace=trace))


@pytest.fixture
def fake_find(monkeypatch):
    holder = {}

    class Tenant:
        def find(self, question):
            if isinstance(holder["value"], Exception):
                raise holder["value"]
            return holder["value"]

    monkeypatch.setattr(retriever, "tenant", lambda name: Tenant())
    return holder


def test_direct_is_answerable(fake_find):
    fake_find["value"] = result(chunk("direct"), chunk("INFERENTIAL", "KB-002"))
    r = retriever.retrieve("q", "ole5")
    assert r.verdict == Verdict.ANSWERABLE
    assert [e.relation for e in r.evidence] == ["DIRECT", "INFERENTIAL"]
    assert r.evidence[0].language == "en"
    assert r.queries_tried == ["q1", "q2"]


def test_inferential_only_is_partial(fake_find):
    fake_find["value"] = result(chunk("INFERENTIAL"))
    assert retriever.retrieve("q", "ole5").verdict == Verdict.PARTIAL


def test_nothing_is_not_in_kb_and_timeout_is_flagged(fake_find):
    fake_find["value"] = result(stop="timeout")
    r = retriever.retrieve("q", "ole5")
    assert r.verdict == Verdict.NOT_IN_KB
    assert r.error == "retrieval incomplete: timeout"


def test_failure_never_raises(fake_find):
    fake_find["value"] = ConnectionError("qdrant down")
    r = retriever.retrieve("q", "ole5")
    assert r.verdict == Verdict.NOT_IN_KB
    assert r.error == "ConnectionError: qdrant down"


def test_considered_lists_graded_documents_not_returned(fake_find):
    trace = (SimpleNamespace(document_name="KB-009", returned=False, relation="IGNORE"),
             SimpleNamespace(document_name="KB-001 Signing in", returned=True, relation="DIRECT"))
    fake_find["value"] = result(chunk("DIRECT"), trace=trace)
    assert retriever.retrieve("q", "ole5").considered == ["KB-009 (ignore)"]


def test_backend_switch(monkeypatch, fake_find):
    fake_find["value"] = result(chunk("DIRECT", "KB-REAL"))
    assert search.search("password", "OLE5").documents == ["STUB-01 Signing in"]

    monkeypatch.setenv("KB_BACKEND", "ragent2")
    get_settings.cache_clear()
    assert search.search("password", "OLE5").documents == ["KB-REAL"]
    assert search.search("password", "RICH").documents == ["STUB-01 Signing in"]
