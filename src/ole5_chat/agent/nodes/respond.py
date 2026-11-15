"""Generate the temporary customer-facing reply."""

import logging

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ole5_chat import llm, products
from ole5_chat.agent.contracts import Grounding, GroundingIssue
from ole5_chat.agent.prompts.guards import grounding_prompt
from ole5_chat.agent import ticket_action_guard
from ole5_chat.agent.prompts.respond import ACCOUNT_ACCESS, CAPABILITIES, _evidence, _ticket_records, system_prompt, turn_note
from ole5_chat.agent.turn_sheet import TurnSheet, build_turn_sheet

from ole5_chat.agent.state import AgentState
from ole5_chat.agent.guards import NAMES, language_of, language_problem, plain, sentences

log = logging.getLogger(__name__)

LANGUAGE_RETRIES = 2
# Temporary diagnostic mode: run the grounding check once, log unsupported
# claims, and keep the generated reply. Set to True to restore retries/fallbacks.
GROUNDING_ENFORCE = True 


def _expected_language(state: AgentState) -> str:
    """The language of the customer's latest message. A message that names no language (a bare value such as a
    username or an error code) keeps the conversation's language."""
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
    return language_of(latest) or state.get("language") or state["identity"].language or "ar"


def _language_mismatch(text: str, expected: str) -> bool:
    has_arabic = bool(re.search(r"[\u0600-\u06ff]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    return (expected == "ar" and has_latin and not has_arabic) or (expected == "en" and has_arabic and not has_latin)


def _messages(state: AgentState, sheet: TurnSheet) -> list:
    """The chat, then this turn's facts as the last message (the turn sheet)."""
    return [*state.get("messages", []), HumanMessage(content=turn_note(sheet))]


def _correct_language(state: AgentState, sheet: TurnSheet, reply: str) -> str:
    """Keep the cheap script check and add semantic detection for mixed scripts."""
    language = sheet["language"]
    conversation = [SystemMessage(content=system_prompt(state, sheet)), *_messages(state, sheet)]
    for attempt in range(LANGUAGE_RETRIES + 1):
        mismatch = _language_mismatch(reply, language) or language_problem(reply, language)
        if not mismatch:
            return reply
        if attempt == LANGUAGE_RETRIES:
            log.error("ERROR respond language mismatch after retries expected=%s", language)
            break
        log.warning("respond language retry=%d expected=%s", attempt + 1, language)
        try:
            reply = llm.invoke([
                *conversation,
                AIMessage(content=reply),
                HumanMessage(content=(
                    f"Rewrite the previous reply in {NAMES[language]}. Preserve its meaning and workflow result. "
                    "Return only the customer-facing plain-text reply."
                )),
            ]).text.strip()
        except llm.ModelFailed as exc:
            log.error("ERROR respond language correction failed: %s", exc)
            break
    return reply


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
            f"Assistant's previous message:\n{previous}\n\nCustomer's message:\n{customer}\n\n"
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


def _grounded_reply(state: AgentState, sheet: TurnSheet, reply: str) -> tuple[str, bool]:
    """Check the reply's product sentences against the passages Respond saw, also when it saw none. With enforcement
    on, the unsupported sentences are rewritten once and the rewrite is checked again. If unsupported sentences
    remain, or a check or the rewrite fails, the reply is the fixed one built from this turn's workflow facts.
    Returns the reply and whether it is that fixed one (withheld), so Validate attaches no sources to it."""
    passages = _seen_passages(state, sheet)

    try:
        issues = _unsupported(state, sheet, reply, passages)
        if not issues:
            return reply, False
        log.warning("grounding unsupported=%s enforcement=%s", issues, GROUNDING_ENFORCE)
        if not GROUNDING_ENFORCE:
            return reply, False
        # The same repair turn note the ticket guard uses: the reply, then each rejected sentence with its problem
        # and the fact to rewrite it from (the closest passage's text, or that no passage supports it).
        def fact(issue) -> str:
            index = int(issue.passage) - 1 if issue.passage.strip().isdigit() else -1
            return passages[index].get("text", "").strip() if 0 <= index < len(passages) else "no passage supports it."
        note = "\n".join(f"- «{issue.sentence}»: {issue.problem} Fact: {fact(issue)}" for issue in issues)
        repair = {**sheet, "correction": note, "rejected_reply": reply}
        rewritten = _correct_language(state, sheet, llm.invoke(
            [SystemMessage(content=system_prompt(state, repair)), *_messages(state, repair)]).text.strip())
        issues = _unsupported(state, sheet, rewritten, passages)
        if not issues:
            return rewritten, False
        log.error("ERROR respond grounding rewrite still unsupported=%s; sending the workflow-facts reply", issues)
    except llm.ModelFailed as exc:
        log.error("ERROR respond grounding check failed: %s", exc)
        if not GROUNDING_ENFORCE:
            return reply, False
    # state["language"] is set only when this node returns; the fixed reply needs this turn's language.
    return ticket_action_guard.fallback({**state, "language": sheet["language"]}), True


def respond_node(state: AgentState) -> dict:
    language = _expected_language(state)
    sheet = build_turn_sheet(state, language) # turns state into facts and one stage
    log.info("respond  intent=%s search=%s stage=%s button=%s", state.get("intent"),
             state.get("search_outcome"), sheet["stage"], sheet["button"])
    try:
        reply = llm.invoke(
            [SystemMessage(content=system_prompt(state, sheet)), *_messages(state, sheet)]
        ).text.strip()
    except llm.ModelFailed as exc:
        log.error("ERROR respond model call failed; sending safe fallback: %s", exc)
        reply = "عذراً، حدث خطأ عندي. يرجى المحاولة مرة أخرى." if language == "ar" else "Sorry, something went wrong on my side. Please try again."
    # Language Check (2 rewrites)
    reply = _correct_language(state, sheet, reply)
    # Grounded Check
    reply, withheld = _grounded_reply(state, sheet, reply)
    cleaned = plain(reply)
    if cleaned != reply:
        log.info("respond markdown=stripped")
    reply = cleaned
    log.info("respond  reply=%r", reply)
    return {"reply": reply, "language": language, "reply_withheld": withheld}




