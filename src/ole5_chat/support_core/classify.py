"""classify: one model call that picks the ticket's queue, type, subtype, priority and title from the conversation, checked against values.xlsx and retried once with the refused values named. Nothing calls it yet; the ticket handlers of step 5 do."""

import logging

from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm, products, values
from ole5_chat.support_core import prompt
from ole5_chat.support_core.contracts import Classification
from ole5_chat.support_core.state import ChatState
from ole5_chat.support_core.validate import check_ticket

log = logging.getLogger(__name__)


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def classify(state: ChatState) -> dict:
    catalog = values.catalog(state["identity"].product)
    config = products.load(state["identity"].product)
    messages = list(state["messages"])
    fields, problems = {}, ["classification not run"]
    for attempt in (1, 2):
        try:
            result = llm.structured(prompt.classify(state), messages, Classification, node="classify")
        except llm.ModelFailed as exc:
            log.error("ERROR classify  model call failed, summary not built: %s", _short(str(exc)))
            return {"note": f"classification model call failed: {_short(str(exc), 200)}",
                    "corrections": state["corrections"] + [f"classify failed: {exc}"]}
        fields, problems = check_ticket(result, catalog)
        # The details to ask come from the ticket type, not from the model's list; the model's values are kept.
        keys = config.required.get(config.required_for_type.get(result.type), [])
        result = result.model_copy(update={"required": {key: result.required.get(key) for key in keys}})
        log.info("classify  attempt=%d queue=%s type=%s subtype=%s priority=%s urgent=%s confidence=%s required=%s "
                 "problems=%s", attempt, result.queue, result.type, result.subtype, result.priority, result.urgent,
                 result.confidence, result.required, problems)
        if not problems:
            break
        messages = [*messages, AIMessage(content=result.model_dump_json()),
                    HumanMessage(content="These values were refused: " + "; ".join(problems)
                                 + ". Choose each from its list exactly as written.")]
    if problems:
        log.error("ERROR classify  values still refused, summary not built: %s", "; ".join(problems))
        # A fact for the caller: what failed and why, so it can retry the save or report that no summary was built.
        return {"note": f"classification failed after 2 attempts: {'; '.join(problems)}",
                "corrections": state["corrections"] + [f"classify refused: {'; '.join(problems)}"]}
    missing = [k for k, v in result.required.items() if v is None]
    draft = state["ticket_draft"].model_copy(update={**fields, "collected": result.collected, "missing": missing,
                                                     "required": result.required,
                                                     "unknown": result.unknown, "classified": True,
                                                     "collected_customer": result.collected_customer,
                                                     "unknown_customer": result.unknown_customer})
    log.info("classify  checked queue=%s type=%s subtype=%s priority=%s urgent=%s reason=%r",
             draft.queue, draft.type, draft.subtype, draft.priority, draft.urgent, _short(draft.reason or ""))
    return {"ticket_draft": draft}


