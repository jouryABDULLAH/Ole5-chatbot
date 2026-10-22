"""Graders for the Ole5 chat suites. Each reads the task's output for one turn. Two judges read the reply: correctness, whether every fact in it comes from the passages the agent was shown or from the conversation; completeness, whether it gives all of the expected answer. The rest are code and compare with the case's expected: the step the agent ended on, the language of its reply, the documents it cited, and the ticket draft's state."""

import re

from phoenix.client.resources.experiments.types import ExperimentEvaluation as EvaluationResult
from phoenix.evals import LLM, create_classifier

from evalkit import graders, llm

GROUNDED_TEMPLATE = """
You are given the passages a support assistant was shown, the conversation so far, the customer's
latest message and the assistant's reply. Decide whether every fact the reply states about the
product comes from the passages, and every claim it makes about what the customer said or asked
comes from the conversation. Nothing else is checked. What the assistant says about itself (that
it can or cannot answer, that it is here to help, that the customer can contact support), about
the ticket (offered, being prepared, summary ready, cancelled), about urgency, and the questions
it asks are the support system's own decisions, not facts to verify. How the reply is worded and
what it leaves out do not matter. A reply with no product fact and no claim about the
conversation is grounded.
    [BEGIN DATA]
    ************
    [Passages]: {reference}
    ************
    [Conversation]: {history}
    ************
    [Message]: {input}
    ************
    [Reply]: {output}
    [END DATA]
"grounded" means every product fact is in the passages and every claim about what the customer
said or asked is in the conversation. Statements about the assistant, the ticket or urgency never
make a reply "made up", whatever the passages and conversation contain.
"made up" means the reply states a product fact that is not in the passages, or misreports what
the customer said or asked.
"""

def correctness(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """Every fact in the reply comes from the passages the agent was shown (output["evidence"]) or from the conversation. 1 = grounded. The greeting is fixed text from the product config, not written by the model, so it is not checked."""
    if output.get("phase") == "greet":
        return EvaluationResult(label="not checked", explanation="the greeting is fixed text")
    judge = create_classifier(name="correctness", prompt_template=GROUNDED_TEMPLATE,
                              llm=LLM(provider="openai", client="openai", model=llm.model(),
                                      base_url="https://api.groq.com/openai/v1", api_key=llm.api_key("correctness")),
                              choices={"grounded": 1.0, "made up": 0.0})
    history = "\n".join(f"{m['role']}: {m['content']}" for m in input.get("history") or []) or "none"
    score = judge.evaluate({"input": input.get("message") or "", "output": output.get("answer") or "",
                            "reference": output.get("evidence") or "none", "history": history})[0]
    if score.score is None:
        raise ValueError(f"the judge's answer has no label: {score.explanation}")
    return EvaluationResult(score=score.score, label=score.label, explanation=score.explanation)


def completeness(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The reply gives all of expected["answer"]."""
    return graders.correctness(input, output, expected)

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
