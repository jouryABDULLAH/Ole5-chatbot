"""Generate the temporary customer-facing reply."""

import logging

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ole5_chat import llm, products
from ole5_chat.agent.contracts import Grounding
from ole5_chat.agent.prompts.guards import grounding_prompt
from ole5_chat.agent.prompts.respond import _evidence, system_prompt, turn_note
from ole5_chat.agent.turn_sheet import TurnSheet, build_turn_sheet

from ole5_chat.agent.state import AgentState
from ole5_chat.agent.guards import NAMES, language_of, language_problem, plain

log = logging.getLogger(__name__)

LANGUAGE_RETRIES = 2
# Temporary diagnostic mode: run the grounding check once, log unsupported
# claims, and keep the generated reply. Set to True to restore retries/fallbacks.
GROUNDING_ENFORCE = False


_VALUE_FIELD_RE = re.compile(
    r"username|user name|browser|file|filename|email|error|code|identifier|version|page",
    re.IGNORECASE,
)


def _is_detail_value(state: AgentState, latest: str) -> bool:
    """Recognize a short pending ticket value such as a username without treating normal sentences as values."""
    if state.get("intent") != "pending_response" or state.get("manager_action") != "provide":
        return False
    pending = state.get("answered_detail")
    item = str(getattr(pending, "item", "") or "")
    if not pending or getattr(pending, "kind", None) != "detail" or not item:
        return False
    value = latest.strip()
    if not value or len(value) > 160 or "\n" in value:
        return False
    if value.casefold() in {"yes", "no", "hello", "hi", "thanks", "english", "arabic", "why", "cancel"}:
        return False
    if re.search(r"browser", item, re.IGNORECASE):
        return bool(re.fullmatch(r"(?:google chrome|chrome|microsoft edge|edge|firefox|safari|opera)(?: [\d.]+)?", value, re.IGNORECASE))
    if re.search(r"error|message|text", item, re.IGNORECASE):
        # Explicitly quoted error text or a compact code, never a word-count guess.
        quoted = any(value.startswith(a) and value.endswith(b) for a, b in [('"', '"'), ('“', '”'), ('`', '`')])
        return (quoted and len(value) > 2) or bool(re.fullmatch(r"(?=[A-Za-z0-9_:-]*\d)[A-Za-z0-9_:-]+", value))
    return bool(_VALUE_FIELD_RE.search(item)) and bool(re.fullmatch(r"[\w@+.-]+", value)) and value[-1:] not in "."


def _expected_language(state: AgentState) -> str:
    """Use sentence-level detection; retain character checks as fallback."""
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
    if _is_detail_value(state, latest):
        # An isolated ticket value (for example, ``ahmad123``) is not a
        # conversation-language change. Keep the language already established.
        return state.get("language") or state["identity"].language or "ar"
    detected = language_of(latest)
    if detected:
        return detected
    # regex fallback 
    if re.search(r"[\u0600-\u06ff]", latest):
        return "ar"
    if re.search(r"[A-Za-z]", latest):
        return "en"
    return state.get("language") or state["identity"].language or "ar"


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


def _grounding_input(state: AgentState, reply: str, passages: list[dict], language: str) -> str:
    """What the check reads: the reply, the exchange it answers, the passages as Respond saw them, and the company
    facts Respond may also state."""
    customer = next((str(message.content) for message in reversed(state.get("messages", []))
                     if message.type == "human"), "")
    before = state.get("messages", [])[:-1]
    previous = next((str(message.content) for message in reversed(before) if message.type == "ai"), "none")
    config = products.load(state["identity"].product)
    company = "\n".join(f"{name}: {value}" for name, value in (
        ("About", config.about.get(language)), ("Support hours", config.support_hours.get(language)),
        ("Support channel", config.support_channel.get(language)), ("Reply channel", config.reply_channel.get(language)),
    ) if value) or "none"
    return (f"Assistant's previous message:\n{previous}\n\nCustomer's message:\n{customer}\n\nReply:\n{reply}\n\n"
            f"Passages the reply's author saw (it need not use them all):\n{_evidence(passages)}\n\n"
            f"Company facts the reply may also state:\n{company}")


def _grounded_reply(state: AgentState, sheet: TurnSheet, reply: str) -> str:
    """Check the reply's product sentences against the passages Respond saw. With enforcement on, the unsupported
    sentences are rewritten once; the rewrite is not checked again, so a second failure passes."""
    passages = _seen_passages(state, sheet)
    if not passages:
        return reply

    def quoted(result: Grounding) -> str | None:
        missing = [issue.sentence for issue in result.unsupported if issue.sentence.strip() not in reply]
        return (f"these sentences are not in the reply: {missing}. Copy each reported sentence exactly from the "
                "reply." if missing else None)

    try:
        check = llm.structured(grounding_prompt(), [HumanMessage(content=_grounding_input(
            state, reply, passages, sheet["language"]))], Grounding, node="ground", check=quoted)
    except llm.GuardrailFailed as exc:
        check = exc.result  # the quotes still did not match; the findings stand
    except llm.ModelFailed as exc:
        log.error("ERROR respond grounding check failed: %s", exc)
        return reply
    if not check.unsupported:
        return reply
    log.warning("grounding unsupported=%s enforcement=%s", check.unsupported, GROUNDING_ENFORCE)
    if not GROUNDING_ENFORCE:
        return reply
    rejected = "\n".join(f"- «{issue.sentence}»: {issue.problem} Closest passage: {issue.passage}"
                         for issue in check.unsupported)
    try:
        rewritten = llm.invoke([
            SystemMessage(content=system_prompt(state, sheet)), *_messages(state, sheet), AIMessage(content=reply),
            HumanMessage(content=(
                "Note from the system, not the customer. Only these sentences of your reply are not supported by "
                f"the passages:\n{rejected}\nRewrite each from the passage named, or drop it if no passage supports "
                "it. Keep every other sentence as it was, in the same order. Return only the reply.")),
        ]).text.strip()
    except llm.ModelFailed as exc:
        log.error("ERROR respond grounding rewrite failed, the reply passes as it is: %s", exc)
        return reply
    return _correct_language(state, sheet, rewritten)


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
        reply = "عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى." if language == "ar" else "Sorry, something went wrong on our side. Please try again."
    # Language Check (2 rewrites)
    reply = _correct_language(state, sheet, reply)
    # Grounded Check
    reply = _grounded_reply(state, sheet, reply)
    cleaned = plain(reply)
    if cleaned != reply:
        log.info("respond markdown=stripped")
    reply = cleaned
    log.info("respond  reply=%r", reply)
    return {"reply": reply, "language": language}




