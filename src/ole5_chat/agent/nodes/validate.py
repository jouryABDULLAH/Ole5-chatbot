"""Check the reply Respond wrote: its language, its product facts against the passages Respond saw (grounding), and
its ticket claims (the ticket-action guard). The three checks run at the same time. Every sentence they reject goes
back to Respond in one note, for a rewrite, and the rewrite is checked the same way, up to REWRITES times. A reply
still rejected after them, or a check that failed, sends the fixed reply built from this turn's workflow facts."""

import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage

from ole5_chat import llm, products
from ole5_chat.agent import ticket_action_guard
from ole5_chat.agent.contracts import Grounding, GroundingIssue
from ole5_chat.agent.guards import NAMES, canonical_ticket_numbers, language_problem, plain, sentences
from ole5_chat.agent.prompts.guards import grounding_prompt
from ole5_chat.agent.prompts.respond import ACCOUNT_ACCESS, CAPABILITIES, _evidence, _ticket_records
from ole5_chat.agent.state import AgentState
from ole5_chat.agent.turn_sheet import TurnSheet, build_turn_sheet

log = logging.getLogger(__name__)

GROUNDING_ENFORCE = True
# Rewrites of a rejected reply before the fixed reply is sent. Each is checked by all three checks again.
REWRITES = 2

SAFE_FALLBACK = {
    "ar": "السموحة، صار خلل عندي وأنا أجهز الرد. أرسل رسالتك مرة ثانية لو سمحت.",
    "en": "Sorry, something went wrong on my side while preparing the reply. Please send your message again.",
}


def _safe_fallback(state: AgentState) -> str:
    language = state.get("language") or state["identity"].language or "ar"
    return SAFE_FALLBACK.get(language, SAFE_FALLBACK["ar"])


def _language_mismatch(text: str, expected: str) -> bool:
    has_arabic = bool(re.search(r"[؀-ۿ]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    return (expected == "ar" and has_latin and not has_arabic) or (expected == "en" and has_arabic and not has_latin)


def _expected_language(state: AgentState) -> str:
    # Respond has already resolved this turn's sentence language with the model.
    if state.get("language"):
        return state["language"]
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
    if re.search(r"[؀-ۿ]", latest):
        return "ar"
    if re.search(r"[A-Za-z]", latest):
        return "en"
    return state.get("language") or state["identity"].language or "ar"


def _seen_passages(state: AgentState, sheet: TurnSheet) -> list[dict]:
    """The passages Respond saw this turn, as system_prompt shows them: the evidence, plus the retained one on other."""
    last = state.get("last_answer_evidence")
    retained = (last or {}).get("evidence", []) if sheet["stage"] == "other" and isinstance(last, dict) else []
    return [*state.get("current_references", []), *retained]


def _grounding_input(state: AgentState, sheet: TurnSheet, reply: str, passages: list[dict]) -> str:
    """What the check reads: the passages as Respond saw them, the company, assistant and ticket facts Respond may
    also state, this turn's workflow facts, the exchange, and last the reply as numbered sentences. Facts only:
    Respond's turn note is not passed, because its guidance and "write the reply" line are instructions, and the
    check followed them instead of judging."""
    language = sheet["language"]
    customer = next((str(message.content) for message in reversed(state.get("messages", []))
                     if message.type == "human"), "")
    before = state.get("messages", [])[:-1]
    previous = next((str(message.content) for message in reversed(before) if message.type == "ai"), "none")
    config = products.load(state["identity"].product)
    company = "\n".join(f"{name}: {value}" for name, value in (
        ("About", config.about.get(language)), ("Support hours", config.support_hours.get(language)),
        ("Support channel", config.support_channel.get(language)), ("Reply channel", config.reply_channel.get(language)),
    ) if value) or "none"
    assistant = (f"Identity: {config.persona.identity}\nProduct scope: {config.scope or config.name}\n"
                 f"Capabilities: {CAPABILITIES}\nAccount access: {ACCOUNT_ACCESS}")
    workflow = "\n".join(f"{label}: {sheet[key]}" for key, label in (
        ("operator", "Ticket workflow this turn"), ("search", "Search"), ("issue", "Request acted on"),
        ("current_issue", "Current request (named by the problem the customer reported)"),
        ("pending_kind", "Step waiting for the customer"), ("pending_item", "Detail waiting"),
        ("requested_detail", "Requested detail"), ("skipped", "Skipped detail"),
        ("confirmed_changes", "Confirmed draft changes this turn"),
        ("cancelled_issue", "Stopped request"), ("queued_issue", "Queued request"),
        ("next_issue", "Next request, now offered"), ("next_in_queue", "Requests in the queue"),
        ("ticket_number", "Ticket created, number"), ("existing_ticket", "Ticket already submitted, number"),
        ("clarification_question", "Question the workflow needs answered"),
    ) if sheet.get(key))
    numbered = "\n".join(f"[{number}] {sentence}" for number, sentence in enumerate(sentences(reply), 1))
    return (f"Passages the reply's author saw (it need not use them all):\n{_evidence(passages)}\n\n"
            f"Company facts the reply may also state:\n{company}\n\n"
            f"What the assistant is and can do, which the reply may also state (\"you\" is the assistant):\n"
            f"{assistant}\n\n"
            f"Ticket records the reply may also state:\n{_ticket_records(state)}\n\n"
            f"This turn's ticket workflow facts:\n{workflow}\n\n"
            f"Assistant's previous message:\n{previous}\n\n"
            f"Customer's message (a source for what the customer reported or asked, which the reply may repeat):\n"
            f"{customer}\n\n"
            f"<reply>\n{numbered}\n</reply>\n\n"
            "Return the JSON with one verdict for each numbered sentence of the reply.")


def _unsupported(state: AgentState, sheet: TurnSheet, text: str, passages: list[dict]) -> list[GroundingIssue]:
    """One grounding check of text: a verdict for each of its numbered sentences; returns the unsupported ones, with
    the sentence's own text taken from the reply by number, so nothing depends on the model quoting it. Verdicts that
    still do not cover the sentences after the correction retries raise GuardrailFailed, a ModelFailed: a failed check
    is never a passed one, so the caller sends the fixed reply. The guard eval (evals/regression_task.py) runs this
    same check."""
    parts = sentences(text)

    def complete(result: Grounding) -> str | None:
        numbers = sorted(verdict.number for verdict in result.verdicts)
        if numbers != list(range(1, len(parts) + 1)):
            return (f"the reply has sentences 1 to {len(parts)}; your verdicts are for {numbers}. Give exactly one "
                    "verdict for each number.")
        blank = [v.number for v in result.verdicts if v.verdict == "unsupported" and not v.problem.strip()]
        return f"unsupported sentences {blank} need a problem: what in each no passage says." if blank else None

    result = llm.structured(grounding_prompt(), [HumanMessage(content=_grounding_input(
        state, sheet, text, passages))], Grounding, node="ground", check=complete)
    return [GroundingIssue(sentence=parts[v.number - 1], problem=v.problem, passage=v.passage)
            for v in result.verdicts if v.verdict == "unsupported" and 1 <= v.number <= len(parts)]


def _wrong_language(reply: str, language: str) -> str | None:
    """What is wrong with the reply's language, or None. The script check is free; the model reads mixed scripts."""
    if _language_mismatch(reply, language):
        return f"The reply is not in {NAMES[language]}. Write the same reply in {NAMES[language]}."
    return language_problem(reply, language)


def _checks(state: AgentState, sheet: TurnSheet, reply: str, passages: list[dict]):
    """The three checks of one reply, at the same time: none depends on another. Returns the language problem (or
    None), the grounding issues (None when that check failed) and the ticket-action violations (None when that check
    failed). Each thread runs in its own copy of the context, so its model calls stay under this node's trace."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        def run(function, *args):
            return pool.submit(contextvars.copy_context().run, function, *args)
        language = run(_wrong_language, reply, sheet["language"])
        grounding = run(_unsupported, state, sheet, reply, passages)
        ticket = run(ticket_action_guard.violations, state, reply)
        try:
            issues = grounding.result()
        except llm.ModelFailed as exc:
            log.error("ERROR validate grounding check failed: %s", exc)
            issues = None
        return language.result(), issues, ticket.result()


def _fallback(state: AgentState, reason: str) -> dict:
    """The fixed reply built from the workflow facts: no answer, so no sources."""
    return {"reply": ticket_action_guard.fallback(state), "validation_outcome": "fallback",
            "validation_reason": reason, "sources": [], "source_details": []}


def validate_node(state: AgentState) -> dict:
    reply = (state.get("reply") or "").strip()
    if not reply:
        # Respond's model call failed (or wrote nothing). The fixed reply keeps the workflow facts (a created ticket's
        # number, the question still waiting).
        log.error("ERROR validate  fallback=empty_reply")
        return _fallback(state, "Reply was empty.")
    if state.get("search_outcome") == "answered" and not state.get("current_references"):
        log.error("ERROR validate  fallback=answered_without_evidence")
        return {"reply": _safe_fallback(state), "validation_outcome": "fallback", "validation_reason": "Answered search had no retained evidence.",
                "sources": [], "source_details": []}

    operator = state.get("ticket_operator_result") or {}
    records = [*state.get("created_tickets", []),
               *((state.get("ticket_enquiry_result") or {}).get("tickets") or []),
               *((state.get("last_lookup") or {}).get("tickets") or []),
               (state.get("ticket_result") or {}).get("ticket") or {},
               (operator.get("ticket_result") or {}).get("ticket") or {}]
    numbers = [str(record["ticket_number"]) for record in records if record.get("ticket_number")]
    if operator.get("existing_ticket"):
        numbers.append(str(operator["existing_ticket"]))
    cleaned = canonical_ticket_numbers(plain(reply), numbers)
    if cleaned != reply:
        log.info("validate  markdown=stripped")
        reply = cleaned

    sheet = build_turn_sheet(state, _expected_language(state))
    passages = _seen_passages(state, sheet)
    wrong, issues, problems = _checks(state, sheet, reply, passages)
    if problems is None or (issues is None and GROUNDING_ENFORCE):
        # A failed check says nothing about the reply, and is never a passed one.
        log.error("ERROR validate  fallback=check_failed grounding_failed=%s ticket_failed=%s",
                  issues is None, problems is None)
        return _fallback(state, "A reply check failed.")
    if issues:
        log.warning("grounding unsupported=%s enforcement=%s", issues, GROUNDING_ENFORCE)
    if wrong:
        log.warning("validate language problem=%s", wrong)
    if problems:
        log.error("ERROR validate ticket-action violations=%s retry_count=%d", problems, state.get("validation_retries", 0))

    # An unsupported sentence has no source by definition, so no passage text goes with it: the closest passage's text
    # led a rewrite to swap the sentence for an unrelated procedure from that passage. The passages are in Respond's
    # <evidence> for correcting a changed name, value, step or order.
    unsourced = ("no source states it. Drop it, or keep only what the customer reported, as their report. Correct a "
                 "changed name, value, step or order only from <evidence>; add nothing else from it.")

    # One line per rejected sentence: the sentence, what is wrong, and the fact to rewrite it from. A wrong language
    # rejects the whole reply.
    rejected = ([f"- «{reply}»: {wrong} Fact: the customer writes in {NAMES[sheet['language']]}."] if wrong else [])
    rejected += [f"- «{i.sentence}»: {i.problem} Fact: {unsourced}" for i in (issues or []) if GROUNDING_ENFORCE]
    rejected += [f"- «{v.sentence}»: {v.problem} Fact: {v.fact}" for v in problems]
    rewrites = state.get("validation_retries", 0)
    if rejected and wrong and len(rejected) == 1 and rewrites >= REWRITES:
        # Only the language is still wrong after the last rewrite. As before the checks moved here, that reply is
        # sent (a detector's false alarm on product names must not withhold a correct reply); the log records it.
        log.error("ERROR validate language mismatch after the rewrites expected=%s", sheet["language"])
        rejected = []
    if rejected:
        if rewrites < REWRITES:
            return {"validation_outcome": "repair", "validation_note": "\n".join(rejected),
                    "validation_reason": "Reply correction required.", "validation_retries": rewrites + 1,
                    "sources": [], "source_details": []}
        log.error("ERROR validate  fallback=still_rejected_after_rewrites")
        return _fallback(state, "Reply still rejected after its rewrites.")

    references = state.get("current_references", []) if state.get("search_outcome") == "answered" else []
    direct = [item for item in references if item.get("relation") == "DIRECT"]
    sources = list(dict.fromkeys(item.get("document", "unknown") for item in direct if item.get("document")))
    details = []
    for index, item in enumerate(direct):
        details.append({"document": item.get("document", "unknown"), "section": item.get("section"),
                        "source_file": item.get("source_file"), "chunk_index": item.get("chunk_index"),
                        "text": item.get("text", "")})
    log.info("validate  outcome=pass evidence=%d sources=%s", len(references), sources)
    return {"reply": reply, "validation_outcome": "pass", "validation_reason": "Basic reply checks passed.",
            "sources": sources, "source_details": details}
