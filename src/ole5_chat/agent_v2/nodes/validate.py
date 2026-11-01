"""Run the initial deterministic reply checks."""

import logging

from ole5_chat.agent_v2.state import AgentState

log = logging.getLogger(__name__)

SAFE_FALLBACK = {
    "ar": "عذراً، لم أتمكن من إعداد إجابة موثوقة الآن. يرجى المحاولة مرة أخرى.",
    "en": "Sorry, I couldn't prepare a reliable answer right now. Please try again.",
}


def _safe_fallback(state: AgentState) -> str:
    language = state.get("language") or state["identity"].language or "ar"
    return SAFE_FALLBACK.get(language, SAFE_FALLBACK["ar"])


def validate_node(state: AgentState) -> dict:
    reply = (state.get("reply") or "").strip()
    if not reply:
        reply = _safe_fallback(state)
        log.warning("validate_v2  fallback=empty_reply")
        return {"reply": reply, "validation_outcome": "fallback", "validation_reason": "Reply was empty."}
    if state.get("search_outcome") == "answered" and not state.get("current_references"):
        log.warning("validate_v2  fallback=answered_without_evidence")
        return {"reply": _safe_fallback(state), "validation_outcome": "fallback", "validation_reason": "Answered search had no retained evidence."}
    log.info("validate_v2  outcome=pass evidence=%d", len(state.get("current_references", [])))
    return {"reply": reply, "validation_outcome": "pass", "validation_reason": "Basic reply checks passed."}
