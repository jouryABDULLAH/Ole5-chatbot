"""Router node: classify the latest customer message and write route state."""

import logging

from ole5_chat import llm
from ole5_chat.agent_v2.contracts import router_schema
from ole5_chat.agent_v2.context import router_context
from ole5_chat.agent_v2.prompts.router import router_prompt
from ole5_chat.agent_v2.state import AgentState

log = logging.getLogger(__name__)


def router_node(state: AgentState) -> dict:
    pending = state.get("pending_customer_response")
    schema = router_schema(pending is not None)
    context = router_context(state)
    log.info("router_v2  pending=%s latest=%r", pending.kind if pending else "none", context["latest_customer_message"])
    try:
        decision = llm.structured(
            router_prompt(state), context["conversation"], schema, node="router_v2"
        )
    except llm.ModelFailed as exc:
        log.warning("router_v2 model failed: %s", exc)
        return {
            "router_decision": "other",
            "route_reason": "Router could not classify the latest message safely.",
            "explicit_ticket_request": False,
        }

    if decision.decision == "pending_response" and pending is None:
        log.warning("router_v2  invalid pending_response without pending state")
        return {
            "router_decision": "other",
            "route_reason": "Pending response was selected without pending state.",
            "explicit_ticket_request": False,
        }
    log.info("router_v2  decision=%s explicit_ticket=%s reason=%r", decision.decision,
             decision.explicit_ticket_request, decision.reason)
    return {
        "router_decision": decision.decision,
        "route_reason": decision.reason,
        "explicit_ticket_request": decision.explicit_ticket_request,
    }
