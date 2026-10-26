"""The router: one model call that picks which handler takes the customer's message, from the options of the conversation's phase."""

import logging

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Reply, route_schema
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.validate import SORRY

log = logging.getLogger(__name__)


def router(state: ChatState) -> dict:
    phase = state["phase"]
    try:
        decision = llm.structured(prompt.router(state), state["messages"], route_schema(phase), node="router")
    except llm.ModelFailed as exc:
        # No guess at the route: the customer is asked to send the message again and the state stays as it was.
        log.warning("router  phase=%s try again: model failed: %s", phase, exc)
        language = state.get("language") or state["identity"].language
        return {"next": None, "reply": Reply(say=SORRY[language], sources=[]),
                "corrections": state["corrections"] + [f"router failed: {exc}"]}
    log.info("router  phase=%s next=%s", phase, decision.next)
    return {"next": decision.next}
