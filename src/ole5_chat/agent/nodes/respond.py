"""Generate the temporary customer-facing reply."""

import logging

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import Grounding
from ole5_chat.agent.prompts.guards import grounding_prompt
from ole5_chat.agent.prompts.respond import respond_prompt

from ole5_chat.agent.state import AgentState
from ole5_chat.agent.guards import NAMES, language_of, language_problem, plain
from ole5_chat.agent.nodes.validate import SAFE_FALLBACK

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
    """Recognize a pending ticket value without treating normal sentences as values."""
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
    if re.search(r"[\u0600-\u06ff]", latest):
        return "ar"
    if re.search(r"[A-Za-z]", latest):
        return "en"
    return state.get("language") or state["identity"].language or "ar"


def _language_mismatch(text: str, expected: str) -> bool:
    has_arabic = bool(re.search(r"[\u0600-\u06ff]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    return (expected == "ar" and has_latin and not has_arabic) or (expected == "en" and has_arabic and not has_latin)


def _correct_language(state: AgentState, reply: str, language: str) -> str:
    """Keep the cheap script check and add semantic detection for mixed scripts."""
    conversation = [SystemMessage(content=respond_prompt(state, language)), *state.get("messages", [])]
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


def _grounding_input(state: AgentState, reply: str) -> str:
    customer = next((str(message.content) for message in reversed(state.get("messages", []))
                     if message.type == "human"), "")
    before = state.get("messages", [])[:-1]
    previous = next((str(message.content) for message in reversed(before) if message.type == "ai"), "none")
    passages = "\n\n".join(
        f"Document: {item.get('document', 'unknown')}\n{item.get('text', '').strip()}"
        for item in state.get("current_references", []) if item.get("relation") == "DIRECT"
    )
    return f"Assistant's previous message:\n{previous}\n\nCustomer's message:\n{customer}\n\nReply:\n{reply}\n\nPassages:\n{passages}"


def _grounded_reply(state: AgentState, reply: str, language: str | None = None) -> str | None:
    """Keep an answered product reply tied to this turn's retrieved passages."""
    if state.get("intent") != "question" or state.get("search_outcome") != "answered":
        return reply
    if not any(item.get("relation") == "DIRECT" for item in state.get("current_references", [])):
        log.warning("grounding skipped enforcement: answered without DIRECT evidence")
        return reply if not GROUNDING_ENFORCE else None
    for attempt in range(1 if not GROUNDING_ENFORCE else 2):
        try:
            check = llm.structured(
                grounding_prompt(), [HumanMessage(content=_grounding_input(state, reply))],
                Grounding, node="ground"
            )
        except llm.ModelFailed as exc:
            log.error("ERROR respond grounding check failed: %s", exc)
            return reply if not GROUNDING_ENFORCE else None
        if not check.unsupported:
            return reply
        log.warning("grounding unsupported=%s enforcement=%s", check.unsupported, GROUNDING_ENFORCE)
        if not GROUNDING_ENFORCE:
            return reply
        if attempt == 1:
            log.error("ERROR respond grounding failed after correction; suppressing unsupported reply")
            return None
        try:
            reply = llm.invoke([
                SystemMessage(content=respond_prompt(state, language)), *state.get("messages", []),
                AIMessage(content=reply),
                HumanMessage(content=(
                    "Rewrite the reply using only the current retrieved passages. Remove every unsupported "
                    f"claim: {'; '.join(check.unsupported)}. Do not use prior conversation answers as evidence."
                )),
            ]).text.strip()
            reply = _correct_language(state, reply, language or _expected_language(state))
        except llm.ModelFailed as exc:
            log.error("ERROR respond grounding correction failed: %s", exc)
            return None
    return None


def respond_node(state: AgentState) -> dict:
    log.info("respond  intent=%s search=%s manager=%s status=%s", state.get("intent"),
             state.get("search_outcome"), state.get("manager_action"),
             bool(state.get("ticket_enquiry_result")))
    language = _expected_language(state)
    try:
        reply = llm.invoke(
            [SystemMessage(content=respond_prompt(state, language)), *state.get("messages", [])]
        ).text.strip()
    except llm.ModelFailed as exc:
        log.error("ERROR respond model call failed; sending safe fallback: %s", exc)
        reply = "عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى." if language == "ar" else "Sorry, something went wrong on our side. Please try again."
    reply = _correct_language(state, reply, language)
    reply = _grounded_reply(state, reply, language)
    if reply is None:
        # Fixed wording avoids another model call reintroducing unsupported facts.
        return {"reply": SAFE_FALLBACK[language], "language": language, "grounding_failed": True}
    cleaned = plain(reply)
    if cleaned != reply:
        log.info("respond markdown=stripped")
    reply = cleaned
    log.info("respond  reply=%r", reply)
    return {"reply": reply, "language": language}




