"""classify: one model call that picks the ticket's queue, type, subtype, priority and title from the conversation, checked against values.xlsx and retried once with the refused values named. Nothing calls it yet; the ticket handlers of step 5 do."""

import logging

from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm, values
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Classification
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.validate import check_ticket

log = logging.getLogger(__name__)


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def classify(state: ChatState) -> dict:
    catalog = values.catalog(state["identity"].product)
    messages = list(state["messages"])
    fields, problems = {}, ["classification not run"]
    for attempt in (1, 2):
        try:
            result = llm.structured(prompt.classify(state), messages, Classification, node="classify")
        except llm.ModelFailed as exc:
            log.warning("classify  model failed, summary not built: %s", _short(str(exc)))
            return {"note": "The ticket summary could not be built this time. Say so in one line, keep what the "
                            "customer gave, and ask them to send their last message again.",
                    "corrections": state["corrections"] + [f"classify failed: {exc}"]}
        fields, problems = check_ticket(result, catalog)
        log.info("classify  attempt=%d queue=%s type=%s subtype=%s priority=%s urgent=%s confidence=%s problems=%s",
                 attempt, result.queue, result.type, result.subtype, result.priority, result.urgent,
                 result.confidence, problems)
        if not problems:
            break
        messages = [*messages, AIMessage(content=result.model_dump_json()),
                    HumanMessage(content="These values were refused: " + "; ".join(problems)
                                 + ". Choose each from its list exactly as written.")]
    if problems:
        log.warning("classify  values still refused, summary not built: %s", "; ".join(problems))
        return {"note": "The ticket summary could not be built this time. Say so in one line, keep what the "
                        "customer gave, and ask them to send their last message again.",
                "corrections": state["corrections"] + [f"classify refused: {'; '.join(problems)}"]}
    draft = state["ticket_draft"].model_copy(update={**fields, "classified": True})
    log.info("classify  checked queue=%s type=%s subtype=%s priority=%s urgent=%s reason=%r",
             draft.queue, draft.type, draft.subtype, draft.priority, draft.urgent, _short(draft.reason or ""))
    return {"ticket_draft": draft}
