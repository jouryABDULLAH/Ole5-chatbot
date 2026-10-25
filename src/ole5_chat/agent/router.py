"""The router: one model call that picks which handler takes the customer's message, from the options of the conversation's phase."""

import logging

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import route_schema
from ole5_chat.agent.state import ChatState

log = logging.getLogger(__name__)


def router(state: ChatState) -> dict:
    phase = state["phase"]
    try:
        decision = llm.structured(prompt.router(state), state["messages"], route_schema(phase), node="router")
    except llm.ModelFailed as exc:
        log.warning("router  phase=%s next=question: model failed: %s", phase, exc)
        return {"next": "question", "corrections": state["corrections"] + [f"router failed: {exc}"]}
    log.info("router  phase=%s next=%s", phase, decision.next)
    return {"next": decision.next}
