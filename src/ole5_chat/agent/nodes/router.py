"""Router node: classify the latest customer message and write route state."""

import logging

from ole5_chat import llm
from ole5_chat.agent.contracts import ResponsePacket, ReturnDecision, router_schema
from ole5_chat.agent.context import router_context
from ole5_chat.agent.prompts.router import router_prompt, router_return_prompt
from ole5_chat.agent.state import AgentState

log = logging.getLogger(__name__)

def _response_packet(state: AgentState) -> dict:
    """Collect the structured facts Router has gathered without rewriting them."""

    references = state.get("current_references", [])
    sources = list(dict.fromkeys(
        item.get("document") for item in references
        if item.get("relation") == "DIRECT" and item.get("document")
    ))
    created = state.get("created_tickets", [])
    packet = ResponsePacket(
        customer_message=next(
            (str(message.content) for message in reversed(state.get("messages", [])) if message.type == "human"),
            "",
        ),
        language=state.get("language") or state.get("identity").language,
        intent=state.get("intent"),
        search=state.get("search_result") or ({
            "outcome": state.get("search_outcome"),
            "references": references,
            "scope": state.get("search_scope"),
        } if state.get("search_outcome") else None),
        # Only the structured result emitted by the current Ticket Operator
        # worker is an authorization for Respond. Do not resurrect the
        # turn-scoped ticket_result from an earlier turn.
        ticket_operator=state.get("ticket_operator_result"),
        ticket_enquiry=state.get("ticket_enquiry_result"),
        created_ticket=created[-1] if created else None,
        pending_customer_response=state.get("pending_customer_response"),
        sources=sources or list(state.get("sources", [])),
    )
    return packet.model_dump()


def _return_pass(state: AgentState) -> dict:
    """After a worker: route its result. The message is not classified again, so it cannot be sent to a worker a
    second time as if it were new; the first pass's intent stays."""
    try:
        decision = llm.structured(router_return_prompt(state), [], ReturnDecision, node="router")
        target, reason = decision.next_target, decision.reason
    except llm.ModelFailed as exc:
        log.error("ERROR router return pass failed: %s", exc)
        target, reason = "respond", "The worker's result could not be routed; it is delivered as it is."
    log.info("router  return pass worker=%s next_target=%s reason=%r", state["last_worker"], target, reason)
    update = {"next_target": target, "route_reason": reason}
    if target == "respond":
        update["response_packet"] = _response_packet({**state, **update})
    return update


def router_node(state: AgentState) -> dict:
    if state.get("last_worker"):
        return _return_pass(state)
    pending = state.get("pending_customer_response")

    schema = router_schema(pending is not None)
    context = router_context(state)
    log.info("router  pending=%s latest=%r", pending.kind if pending else "none", context["latest_customer_message"])

    try:
        router_result = llm.structured(router_prompt(state), context["conversation"], schema, node="router")
    except llm.ModelFailed as exc:
        log.error("ERROR router model call failed: %s", exc)
        update = {
            "intent": "other",
            "next_target": "respond",
            "route_reason": "Router could not classify the latest message safely.",
            "explicit_ticket_request": False,
        }
        update["response_packet"] = _response_packet({**state, **update})
        return update
    
    log.info("router  intent=%s next_target=%s explicit_ticket=%s reason=%r", router_result.intent,
             router_result.next_target, router_result.explicit_ticket_request, router_result.reason)
    
    update = {
        "intent": router_result.intent,
        "next_target": router_result.next_target,
        "route_reason": router_result.reason,
        "explicit_ticket_request": router_result.explicit_ticket_request,
    }
    # The step the customer's message answers, kept so the return pass can tell it from a step a worker creates.
    update["pending_before_worker"] = pending
    if update["next_target"] == "respond":
        update["response_packet"] = _response_packet({**state, **update})
    return update




