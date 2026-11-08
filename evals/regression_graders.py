"""Graders for the reg_* suites. All are plain checks on the task's output except grounded, which reuses the
correctness judge."""

import re

from phoenix.client.resources.experiments.types import ExperimentEvaluation as EvaluationResult

from evals.graders import _skip, correctness

NO_ANSWER = re.compile(r"لا (?:تتوفر|يتوفر|توجد|يوجد)[^.؟\n]{0,20}(?:معلومات|إرشاد)|لم أجد|لم أعثر|"
                       r"no information|couldn't find|could not find|don't have information", re.IGNORECASE)
TICKET_QUESTION = re.compile(r"[^.؟?\n]*(?:تذكرة|ticket)[^.؟?\n]*[؟?]", re.IGNORECASE)
FALLBACKS = ("لا أستطيع تأكيد", "لم أتمكن من إعداد إجابة", "I cannot confirm", "couldn't prepare a reliable answer")


def _result(passed: bool, explanation: str) -> EvaluationResult:
    return EvaluationResult(score=float(passed), explanation=explanation)


def provided_answer(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The agent answered: sources shown, and the reply does not say nothing was found."""
    if output.get("empty_slot"):
        return _skip("empty slot")
    said_no = NO_ANSWER.search(output.get("answer") or "")
    passed = bool(output.get("sources")) and not said_no
    return _result(passed, f"sources {output.get('sources')}, said no answer: {said_no.group(0) if said_no else 'no'}")


def search_answered(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """Search found a direct answer (outcome answered), whatever Respond then wrote: the search engine's own result."""
    if output.get("empty_slot"):
        return _skip("empty slot")
    return _result(output.get("search_outcome") == "answered", f"search outcome {output.get('search_outcome')}")


def _flat(text: str) -> str:
    """Text without markdown bold, list dashes or spacing differences, so a quote matches the chunk it came from."""
    return " ".join(re.sub(r"\*\*|^\s*[-*]\s+", "", text or "", flags=re.MULTILINE).split())


def answer_retrieved(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["quote"], the sentence that answers the question, is in a passage Search returned, in any relation.
    No model: it shows whether the search engine found the answer at all."""
    if output.get("empty_slot") or not expected.get("quote"):
        return _skip("no expected quote")
    found = _flat(expected["quote"]) in _flat(output.get("evidence") or "")
    return _result(found, "quote found in the passages" if found else f"quote not in the {len(output.get('chunks') or [])} passages")


def chunk_included(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """At least one of expected["chunks"] (each holds the fact) reached Respond, in any relation. Other chunks may be
    there too."""
    if output.get("empty_slot") or not expected.get("chunks"):
        return _skip("no expected chunk")
    received = output.get("chunks") or []
    got = [(item.get("document"), item.get("chunk"), item.get("relation")) for item in received]
    want = [(item["document"], item["chunk"]) for item in expected["chunks"]]
    found = [item for item in got if item[:2] in want]
    return _result(bool(found), f"found {found}, received {got}")


def no_ticket_offer(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The reply asks nothing about a ticket, unless the Operator's offer is pending or the case allows it."""
    if output.get("empty_slot"):
        return _skip("empty slot")
    if expected.get("offer_allowed") or output.get("pending") == "offer":
        return _skip("an offer is allowed here")
    found = TICKET_QUESTION.search(output.get("answer") or "")
    return _result(not found, found.group(0).strip() if found else "no ticket question")


def grounded(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """Every product fact in the reply comes from the chunks."""
    if output.get("empty_slot"):
        return _skip("empty slot")
    return correctness(input, output, expected)


def required_text(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["must_have"]: groups of alternatives, one of each group must appear. expected["must_not"]: none may."""
    if output.get("empty_slot") or not ("must_have" in expected or "must_not" in expected):
        return _skip("no required text")
    answer = (output.get("answer") or "").lower()
    missing = [group for group in expected.get("must_have", [])
               if not any(text.lower() in answer for text in group)]
    present = [text for text in expected.get("must_not", []) if text.lower() in answer]
    return _result(not missing and not present, f"missing {missing}, should not appear {present}")


def question_count(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The reply asks at most expected["max_questions"] questions."""
    if "max_questions" not in expected:
        return _skip("no question limit")
    count = len(re.findall(r"[؟?]", output.get("answer") or ""))
    return _result(count <= expected["max_questions"], f"{count} questions, limit {expected['max_questions']}")


def pending_matches(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["pending"]: the step waiting after the turn, null for none, or a list of acceptable ones."""
    if "pending" not in expected:
        return _skip("no expected pending step")
    allowed = expected["pending"] if isinstance(expected["pending"], list) else [expected["pending"]]
    return _result(output.get("pending") in allowed, f"expected {allowed}, got {output.get('pending')}")


def counts_match(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """expected["created"] and expected["queued"]: tickets created so far and issues waiting in the queue."""
    wrong = {key: output.get(key) for key in ("created", "queued") if key in expected and output.get(key) != expected[key]}
    if not any(key in expected for key in ("created", "queued")):
        return _skip("no expected counts")
    return _result(not wrong, f"wrong: {wrong or 'nothing'}")


def no_fallback(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The reply is not one of the fixed fallbacks: Validate did not fall back, and no fixed fallback text appears."""
    found = [text for text in FALLBACKS if text in (output.get("answer") or "")]
    if output.get("validation") == "fallback":
        found.append("validation fell back")
    return _result(not found, f"fallback: {found or 'none'}")


def guard_outcome(input: dict, output: dict, expected: dict) -> EvaluationResult:
    """The guard reached expected["validation"]: pass, repair or fallback."""
    return _result(output.get("validation") == expected["validation"],
                   f"expected {expected['validation']}, got {output.get('validation')}: {output.get('note')}")
