"""Run the initial deterministic reply checks."""

import logging

import re

from ole5_chat.agent.guards import plain
from ole5_chat.agent.state import AgentState
from ole5_chat.agent import ticket_action_guard

log = logging.getLogger(__name__)

SAFE_FALLBACK = {
    "ar": "عذراً، لم أتمكن من إعداد إجابة موثوقة الآن. يرجى المحاولة مرة أخرى.",
    "en": "Sorry, I couldn't prepare a reliable answer right now. Please try again.",
}


CLAIMS = ("تم إنشاء", "تم فتح", "تم إرسال", "تم رفع", "تم تصعيد", "تم تحويل", "تم تحديث", "تم إلغاء",
          "created", "submitted", "opened", "escalated", "forwarded", "updated", "cancelled")


def _safe_fallback(state: AgentState) -> str:
    language = state.get("language") or state["identity"].language or "ar"
    return SAFE_FALLBACK.get(language, SAFE_FALLBACK["ar"])


def _language_mismatch(text: str, expected: str) -> bool:
    """Cheap final guard; previous implementation's model guard is avoided here to prevent a second LLM turn."""
    has_arabic = bool(re.search(r"[\u0600-\u06ff]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    return (expected == "ar" and has_latin and not has_arabic) or (expected == "en" and has_arabic and not has_latin)


def _expected_language(state: AgentState) -> str:
    # Respond has already resolved this turn's sentence language with the model.
    if state.get("language"):
        return state["language"]
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
    if re.search(r"[\u0600-\u06ff]", latest):
        return "ar"
    if re.search(r"[A-Za-z]", latest):
        return "en"
    return state.get("language") or state["identity"].language or "ar"


def validate_node(state: AgentState) -> dict:
    reply = (state.get("reply") or "").strip()
    if state.get("grounding_failed"):
        log.error("ERROR validate fallback=grounding_failed")
        return {"reply": _safe_fallback(state), "validation_outcome": "fallback",
                "validation_reason": "Product reply could not be grounded.",
                "sources": [], "source_details": []}
    if not reply:
        reply = _safe_fallback(state)
        log.error("ERROR validate  fallback=empty_reply")
        return {"reply": reply, "validation_outcome": "fallback", "validation_reason": "Reply was empty.",
                "sources": [], "source_details": []}
    if state.get("search_outcome") == "answered" and not state.get("current_references"):
        log.error("ERROR validate  fallback=answered_without_evidence")
        return {"reply": _safe_fallback(state), "validation_outcome": "fallback", "validation_reason": "Answered search had no retained evidence.",
                "sources": [], "source_details": []}

    cleaned = plain(reply)
    if cleaned != reply:
        log.info("validate  markdown=stripped")
        reply = cleaned

    expected = _expected_language(state)
    if _language_mismatch(reply, expected):
        log.error("ERROR validate  language mismatch expected=%s", expected)
        # Keep the generated response available for the customer; the mismatch is
        # recorded for correction while the v2 response path is still plain-text.

    # Only a turn with ticket activity, or a reply claiming a completed action, can make a ticket claim.
    ticket_turn = (state.get("ticket_operator_result") or state.get("ticket_enquiry_result")
                   or state.get("pending_customer_response") or any(c in reply.lower() for c in CLAIMS))
    problems = ticket_action_guard.violations(state, reply) if ticket_turn else []
    if problems:
        log.error("ERROR validate ticket-action violations=%s retry_count=%d", problems, state.get("validation_retries", 0))
        if state.get("validation_retries", 0) < 1:
            return {"validation_outcome": "repair", "validation_note": "; ".join(problems),
                    "validation_reason": "Ticket-action correction required.", "validation_retries": 1,
                    "sources": [], "source_details": []}
        return {"reply": ticket_action_guard.fallback(state), "validation_outcome": "fallback",
                "validation_reason": "Ticket-action correction exhausted.", "sources": [], "source_details": []}

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





