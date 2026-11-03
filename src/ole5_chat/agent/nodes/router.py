"""Router node: classify the latest customer message and write route state."""

import logging

from langchain_core.messages import HumanMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import ResponsePacket, router_schema
from ole5_chat.agent.context import router_context
from ole5_chat.agent.prompts.router import router_prompt
from ole5_chat.agent.state import AgentState

log = logging.getLogger(__name__)

NOTE = "Note from the system, not the customer: "


def _handoff_note(context: dict) -> HumanMessage | None:
    """On a worker return, the worker result as the last message the Router reads."""
    worker = context.get("last_worker")
    if not worker:
        return None
    summary = {
        "search": context.get("search_summary"),
        "ticket_operator": context.get("ticket_operator_summary"),
        "ticket_enquiry": context.get("ticket_enquiry_summary"),
    }[worker]
    return HumanMessage(content=(
        f"{NOTE}last_worker={worker} has completed for the customer message above: {summary}. "
        "That message was already handled by this worker; choose next_target by the worker result rules."))

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


def router_node(state: AgentState) -> dict:
    pending = state.get("pending_customer_response")
    
    schema = router_schema(pending is not None)
    context = router_context(state)
    log.info("router  pending=%s latest=%r", pending.kind if pending else "none", context["latest_customer_message"])
    
    messages = context["conversation"]
    note = _handoff_note(context)
    if note:
        messages = [*messages, note]
    try:
        router_result = llm.structured(router_prompt(state), messages, schema, node="router")
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
    if update["next_target"] == "respond":
        update["response_packet"] = _response_packet({**state, **update})
    return update




