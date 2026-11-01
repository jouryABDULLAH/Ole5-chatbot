"""Generate the temporary customer-facing reply."""

import logging

from langchain_core.messages import SystemMessage

from ole5_chat import llm
from ole5_chat.agent_v2.prompts.respond import respond_prompt

from ole5_chat.agent_v2.state import AgentState

log = logging.getLogger(__name__)


def respond_node(state: AgentState) -> dict:
    log.info("respond_v2  route=%s search=%s manager=%s status=%s", state.get("router_decision"),
             state.get("search_outcome"), state.get("manager_action"),
             bool(state.get("ticket_status_result")))
    try:
        reply = llm.invoke(
            [SystemMessage(content=respond_prompt(state)), *state.get("messages", [])]
        ).text.strip()
    except llm.ModelFailed:
        language = state.get("language") or state["identity"].language or "ar"
        reply = "عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى." if language == "ar" else "Sorry, something went wrong on our side. Please try again."
    log.info("respond_v2  reply=%r", reply)
    return {"reply": reply}
