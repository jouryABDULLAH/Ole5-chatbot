"""Router node: classify the latest customer message and write route state."""

import logging
import re

from ole5_chat import llm
from ole5_chat.agent.contracts import NextTarget, ResponsePacket, router_schema
from ole5_chat.agent.context import router_context
from ole5_chat.agent.prompts.router import router_prompt
from ole5_chat.agent.state import AgentState
from ole5_chat.agent.scope import is_user_specific_request

log = logging.getLogger(__name__)

_NEXT_TARGET: dict[str, NextTarget] = {
    "question": "search",
    "pending_response": "ticket_operator",
    "ticket_enquiry": "ticket_enquiry",
    "escalation": "ticket_operator",
    "other": "respond",
}


def _route_update(state: AgentState, decision: str, reason: str, explicit: bool,
                  target: NextTarget | None = None) -> dict:
    """Set the current-turn intent and its next graph destination."""

    update = {
        "next_target": target or _NEXT_TARGET[decision],
        "route_reason": reason,
        "explicit_ticket_request": explicit,
    }
    update["intent"] = decision
    if update["next_target"] == "respond":
        update["response_packet"] = _response_packet({**state, **update})
    return update


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
        ticket_operator=state.get("ticket_operator_result") or ({
            "action": state.get("manager_action"),
            "reason": state.get("manager_reason"),
            "ticket_result": state.get("ticket_result"),
        } if state.get("manager_action") or state.get("ticket_result") else None),
        ticket_enquiry=state.get("ticket_enquiry_result"),
        created_ticket=created[-1] if created else None,
        pending_customer_response=state.get("pending_customer_response"),
        sources=sources or list(state.get("sources", [])),
    )
    return packet.model_dump()


def _worker_result_route(state: AgentState) -> dict | None:
    """Route a completed worker result without asking the Router model to reinterpret it."""

    if state.get("ticket_operator_result") is not None:
        return _route_update(
            state,
            state.get("intent") or "escalation",
            "Ticket Operator returned its authorized workflow result; dispatching it to Respond.",
            bool(state.get("explicit_ticket_request")),
            "respond",
        )
    if state.get("ticket_enquiry_result") is not None:
        return _route_update(
            state,
            state.get("intent") or "ticket_enquiry",
            "Ticket Enquiry returned its authorized result; dispatching it to Respond.",
            bool(state.get("explicit_ticket_request")),
            "respond",
        )
    if state.get("search_result") is not None or state.get("search_outcome") is not None:
        outcome = (state.get("search_result") or {}).get("outcome") or state.get("search_outcome")
        target: NextTarget = "ticket_operator" if outcome in {"partial", "no_answer"} else "respond"
        return _route_update(
            state,
            state.get("intent") or "question",
            f"Search returned {outcome}; dispatching the structured result to {target}.",
            bool(state.get("explicit_ticket_request")),
            target,
        )
    return None

_TICKET_PROCESS_REQUEST = re.compile(
    r"\b(?:how does (?:a )?support ticket work|ticketing process|how long.*(?:reply|response)|"
    r"where will (?:the )?(?:reply|response) arrive|support hours|how do i contact support|"
    r"can i submit a ticket|how do i submit a ticket|what happens after.*ticket)\b",
    re.IGNORECASE)

_CAPABILITY_REQUEST = re.compile(
    r"\b(?:what\s+(?:can|do)\s+you\s+do|what\s+are\s+your\s+capabilit(?:y|ies)|how\s+can\s+you\s+help|who\s+are\s+you)\b"
    r"|(?:ماذا\s+تفعل|وش\s+تسوي|وش\s+تقدر|كيف\s+تساعدني)", re.IGNORECASE)


def router_node(state: AgentState) -> dict:
    worker_route = _worker_result_route(state)
    if worker_route is not None:
        log.info("router  worker_result next_target=%s", worker_route["next_target"])
        return worker_route
    pending = state.get("pending_customer_response")
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
    if is_user_specific_request(latest):
        decision = "pending_response" if pending is not None else "escalation"
        log.info("router  scope=user_specific decision=%s", decision)
        return _route_update(state, decision, "Customer-specific account or user data is outside the chat scope.", False)
    if _CAPABILITY_REQUEST.search(latest):
        log.info("router  capability=other no_search=true")
        return _route_update(state, "other", "Capability question is answered from configured assistant information.", False)
    if _TICKET_PROCESS_REQUEST.search(latest) and not re.search(r"\b(?:status|progress|number|receipt)\b", latest, re.IGNORECASE):
        log.info("router  ticket_process=other no_search=true")
        return _route_update(state, "other", "Ticketing-process question is answered from configured support information.", False)
    schema = router_schema(pending is not None)
    context = router_context(state)
    log.info("router  pending=%s latest=%r", pending.kind if pending else "none", context["latest_customer_message"])
    try:
        decision = llm.structured(
            router_prompt(state), context["conversation"], schema, node="router"
        )
    except llm.ModelFailed as exc:
        log.error("ERROR router model call failed: %s", exc)
        return _route_update(state, "other", "Router could not classify the latest message safely.", False)

    if decision.decision == "pending_response" and pending is None:
        log.warning("router  invalid pending_response without pending state")
        return _route_update(state, "other", "Pending response was selected without pending state.", False)
    log.info("router  decision=%s explicit_ticket=%s reason=%r", decision.decision,
             decision.explicit_ticket_request, decision.reason)
    return _route_update(state, decision.decision, decision.reason, decision.explicit_ticket_request)




