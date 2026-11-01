"""Generate the temporary customer-facing reply."""

import logging

import re

from langchain_core.messages import HumanMessage, SystemMessage

from ole5_chat import llm
from ole5_chat.agent import prompt as legacy_prompt
from ole5_chat.agent.respond import Grounding
from ole5_chat.agent_v2.prompts.respond import respond_prompt

from ole5_chat.agent_v2.state import AgentState
from ole5_chat.agent.validate import NAMES, plain

log = logging.getLogger(__name__)

LANGUAGE_RETRIES = 2


def _expected_language(state: AgentState) -> str:
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
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
    conversation = [SystemMessage(content=respond_prompt(state)), *state.get("messages", [])]
    for attempt in range(LANGUAGE_RETRIES):
        if not _language_mismatch(reply, language):
            return reply
        log.warning("respond_v2 language retry=%d expected=%s", attempt + 1, language)
        try:
            reply = llm.invoke([
                *conversation,
                HumanMessage(content=(
                    f"Rewrite the previous reply in {NAMES[language]}. Preserve its meaning and workflow result. "
                    "Return only the customer-facing plain-text reply."
                )),
            ]).text.strip()
        except llm.ModelFailed as exc:
            log.error("ERROR respond_v2 language correction failed: %s", exc)
            break
    if _language_mismatch(reply, language):
        log.error("ERROR respond_v2 language mismatch after retries expected=%s", language)
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


def _grounded_reply(state: AgentState, reply: str) -> str:
    """Keep an answered product reply tied to this turn's retrieved passages."""
    if state.get("router_decision") != "question" or state.get("search_outcome") != "answered":
        return reply
    if not any(item.get("relation") == "DIRECT" for item in state.get("current_references", [])):
        return reply
    for attempt in range(2):
        try:
            check = llm.structured(
                legacy_prompt.ground(), [HumanMessage(content=_grounding_input(state, reply))],
                Grounding, node="ground_v2"
            )
        except llm.ModelFailed as exc:
            log.error("ERROR respond_v2 grounding check failed: %s", exc)
            return reply
        if not check.unsupported:
            return reply
        log.warning("respond_v2 grounding retry=%d unsupported=%s", attempt + 1, check.unsupported)
        if attempt == 1:
            return reply
        try:
            reply = llm.invoke([
                SystemMessage(content=respond_prompt(state)), *state.get("messages", []),
                HumanMessage(content=(
                    "Rewrite the reply using only the current retrieved passages. Remove every unsupported "
                    f"claim: {'; '.join(check.unsupported)}. Do not use prior conversation answers as evidence."
                )),
            ]).text.strip()
        except llm.ModelFailed as exc:
            log.error("ERROR respond_v2 grounding correction failed: %s", exc)
            return reply
    return reply


def respond_node(state: AgentState) -> dict:
    log.info("respond_v2  route=%s search=%s manager=%s status=%s", state.get("router_decision"),
             state.get("search_outcome"), state.get("manager_action"),
             bool(state.get("ticket_status_result")))
    try:
        reply = llm.invoke(
            [SystemMessage(content=respond_prompt(state)), *state.get("messages", [])]
        ).text.strip()
    except llm.ModelFailed as exc:
        log.error("ERROR respond_v2 model call failed; sending safe fallback: %s", exc)
        language = state.get("language") or state["identity"].language or "ar"
        reply = "عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى." if language == "ar" else "Sorry, something went wrong on our side. Please try again."
    reply = _grounded_reply(state, reply)
    reply = _correct_language(state, reply, _expected_language(state))
    cleaned = plain(reply)
    if cleaned != reply:
        log.info("respond_v2 markdown=stripped")
    reply = cleaned
    log.info("respond_v2  reply=%r", reply)
    return {"reply": reply}
