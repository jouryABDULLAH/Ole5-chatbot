"""Router node: classify the latest customer message and write route state."""

import logging
import re

from ole5_chat import llm
from ole5_chat.agent.contracts import ResponsePacket, router_schema
from ole5_chat.agent.context import router_context
from ole5_chat.agent.prompts.router import router_prompt
from ole5_chat.agent.state import AgentState
from ole5_chat.agent.scope import is_user_specific_request

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


def _worker_result_route(state: AgentState) -> dict | None:
    """Route a completed worker result without asking the Router model to reinterpret it."""

    if state.get("ticket_operator_result") is not None:
        update = {
            "intent": state.get("intent") or "escalation",
            "next_target": "respond",
            "route_reason": "Ticket Operator returned its authorized workflow result; dispatching it to Respond.",
            "explicit_ticket_request": bool(state.get("explicit_ticket_request")),
        }
        update["response_packet"] = _response_packet({**state, **update})
        return update
    if state.get("ticket_enquiry_result") is not None:
        update = {
            "intent": state.get("intent") or "ticket_enquiry",
            "next_target": "respond",
            "route_reason": "Ticket Enquiry returned its authorized result; dispatching it to Respond.",
            "explicit_ticket_request": bool(state.get("explicit_ticket_request")),
        }
        update["response_packet"] = _response_packet({**state, **update})
        return update
    if state.get("search_result") is not None or state.get("search_outcome") is not None:
        outcome = (state.get("search_result") or {}).get("outcome") or state.get("search_outcome")
        target = "ticket_operator" if outcome in {"partial", "no_answer"} else "respond"
        update = {
            "intent": state.get("intent") or "question",
            "next_target": target,
            "route_reason": f"Search returned {outcome}; dispatching the structured result to {target}.",
            "explicit_ticket_request": bool(state.get("explicit_ticket_request")),
        }
        if target == "respond":
            update["response_packet"] = _response_packet({**state, **update})
        return update
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
        intent = "pending_response" if pending is not None else "escalation"
        next_target = "ticket_operator"
        reason = "Customer-specific account or user data is outside the chat scope."
        log.info("router  scope=user_specific intent=%s next_target=%s", intent, next_target)
        return {
            "intent": intent,
            "next_target": next_target,
            "route_reason": reason,
            "explicit_ticket_request": False,
        }
    
    if _CAPABILITY_REQUEST.search(latest):
        log.info("router  capability=other no_search=true")
        update = {
            "intent": "other",
            "next_target": "respond",
            "route_reason": "Capability question is answered from configured assistant information.",
            "explicit_ticket_request": False,
        }
        update["response_packet"] = _response_packet({**state, **update})
        return update
    
    if _TICKET_PROCESS_REQUEST.search(latest) and not re.search(r"\b(?:status|progress|number|receipt)\b", latest, re.IGNORECASE):
        log.info("router  ticket_process=other no_search=true")
        update = {
            "intent": "other",
            "next_target": "respond",
            "route_reason": "Ticketing-process question is answered from configured support information.",
            "explicit_ticket_request": False,
        }
        update["response_packet"] = _response_packet({**state, **update})
        return update
    
    schema = router_schema(pending is not None)
    context = router_context(state)
    log.info("router  pending=%s latest=%r", pending.kind if pending else "none", context["latest_customer_message"])
    
    try:
        router_result = llm.structured(
            router_prompt(state), context["conversation"], schema, node="router"
        )
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




