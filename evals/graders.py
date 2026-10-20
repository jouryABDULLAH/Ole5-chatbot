"""Graders for the Ole5 chat suites. Each reads the task's output for one turn and compares it with the case's expected: the step the agent ended on, the language of its reply, the documents it cited, and the ticket draft's state. All are code; none calls a model."""

import re

from phoenix.client.resources.experiments.types import ExperimentEvaluation as EvaluationResult

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")


def script_of(text: str) -> str | None:
    """The language a text is written in: the script of its first word that has letters. Product and screen names in the other script later on do not count."""
    words = [w for w in text.split() if ARABIC.search(w) or LATIN.search(w)]
    if not words:
        return None
    return "ar" if ARABIC.search(words[0]) else "en"


def _skip(reason: str) -> EvaluationResult:
    return EvaluationResult(label="not checked", explanation=reason)


def phase_matches(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The agent ended on expected["phase"] (a step name, or a list of acceptable ones)."""
    want = expected.get("phase")
    if not want:
        return _skip("no expected phase")
    allowed = want if isinstance(want, list) else [want]
    got = output.get("phase")
    return EvaluationResult(score=float(got in allowed), explanation=f"expected {allowed}, got {got!r}")


def language_matches(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The reply is written in the language of the customer's message (by script), or in
    expected["language"] when the case sets it."""
    message = input.get("message") or ""
    want = expected.get("language") or script_of(message)
    got = script_of(output.get("answer") or "")
    if want is None or got is None:
        return _skip("no letters to judge")
    return EvaluationResult(score=float(got == want), explanation=f"message {want}, reply {got}")


def sources_match(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["sources"]: a list means at least one of those documents is cited; [] means
    nothing is cited."""
    if "sources" not in expected:
        return _skip("no expected sources")
    want, got = expected["sources"], output.get("sources") or []
    if not want:
        return EvaluationResult(score=float(not got), explanation=f"expected none, got {got}")
    hit = [s for s in got if s in want]
    return EvaluationResult(score=float(bool(hit)), explanation=f"expected one of {want}, got {got}")


def draft_matches(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["draft"]: null means no ticket draft; otherwise each key given (ready, urgent,
    missing_empty) must match the draft."""
    if "draft" not in expected:
        return _skip("no expected draft")
    want, got = expected["draft"], output.get("draft")
    if want is None:
        return EvaluationResult(score=float(got is None), explanation=f"expected no draft, got {got}")
    if got is None:
        return EvaluationResult(score=0.0, explanation=f"expected a draft {want}, got none")
    checks = {"ready": got["ready"], "urgent": got["urgent"], "missing_empty": not got["missing"]}
    wrong = {k: checks[k] for k, v in want.items() if checks.get(k) != v}
    return EvaluationResult(score=float(not wrong), explanation=f"expected {want}, wrong {wrong or 'nothing'}")
