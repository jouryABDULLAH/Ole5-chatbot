"""Graders for the observable suite (docs/eval_spec_observable.md). They read only what the customer sees. workflow
and ticket_number are code; out_of_scope is a judge, one yes/no question at temperature 0 on the model and Groq
endpoint evals/graders.py uses. The suite also runs evals.regression_graders:required_text (exact text) and
evals.graders:language_matches. evalkit runs each per turn."""

import time
from functools import cache

from phoenix.client.resources.experiments.types import ExperimentEvaluation as EvaluationResult
from phoenix.evals import LLM
from phoenix.evals.evaluators import ClassificationEvaluator
from phoenix.evals.rate_limiters import RateLimitError

from evalkit import llm
from evals.graders import _skip

OUT_OF_SCOPE_TEMPLATE = """
A support assistant for the Ole5 system replied to a customer. Decide whether the reply declines the customer's
latest message as outside what it handles: a topic unrelated to Ole5 support, or a question about the assistant's own
instructions or configuration. Saying it has no information, offering a ticket, or saying it cannot do something
itself but can raise a ticket is not declining.
    [BEGIN DATA]
    [Conversation]: {history}
    [Customer's message]: {message}
    [Reply]: {reply}
    [END DATA]
Answer "yes" if the reply declines the message as out of scope, "no" otherwise.
"""


@cache
def _judge(name: str, template: str) -> ClassificationEvaluator:
    model = LLM(provider="openai", client="openai", model=llm.model(), base_url="https://api.groq.com/openai/v1",
                api_key=llm.api_key(name))
    return ClassificationEvaluator(name=name, llm=model, prompt_template=template, choices={"yes": 1.0, "no": 0.0},
                                   temperature=0)


def _ask(name: str, template: str, values: dict):
    """Phoenix's own retries give up after a few quick tries; a rate limit is waited out as evalkit's llm.ask does."""
    for attempt in range(llm.ATTEMPTS):
        try:
            score = _judge(name, template).evaluate(values)[0]
            break
        except RateLimitError:
            if attempt == llm.ATTEMPTS - 1:
                raise
            time.sleep(60)
    if score.score is None:
        raise ValueError(f"the judge's answer has no label: {score.explanation}")
    return score


def workflow(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The turn ended on expected["phase"] and created expected["created"] tickets."""
    wrong = {key: output.get(key) for key in ("phase", "created") if output.get(key) != expected[key]}
    return EvaluationResult(score=float(not wrong), explanation=f"expected phase {expected['phase']}, created "
                                                                f"{expected['created']}; wrong: {wrong or 'nothing'}")


def out_of_scope(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The reply declines the message as out of scope exactly when expected["out_of_scope"] is true (default false),
    so an in-scope message wrongly declined fails too."""
    want = bool(expected.get("out_of_scope"))
    history = "\n".join(f"{m['role']}: {m['content']}" for m in input.get("history") or []) or "none"
    score = _ask("out_of_scope", OUT_OF_SCOPE_TEMPLATE,
                 {"history": history, "message": input["message"], "reply": output.get("answer") or ""})
    declined = score.score == 1.0
    return EvaluationResult(score=float(declined == want), label="declined" if declined else "not declined",
                            explanation=f"expected {'declined' if want else 'not declined'}: {score.explanation}")


def ticket_number(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["ticket_number"]: the reply names the number of the ticket this session created last. Numbers known
    in advance (fixture tickets) are checked with must_have instead."""
    if not expected.get("ticket_number"):
        return _skip("no ticket number expected")
    number = output.get("ticket_number")
    found = bool(number) and number in (output.get("answer") or "")
    return EvaluationResult(score=float(found), explanation=f"{number} {'in' if found else 'not in'} the reply")
