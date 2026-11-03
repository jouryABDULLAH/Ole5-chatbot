"""Graph skeleton for the new owner-routing design."""

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from ole5_chat.agent.nodes import (
    finalize_node,
    respond_node,
    router_node,
    search_node,
    ticket_operator_node,
    ticket_enquiry_node,
    validate_node,
)
from ole5_chat.agent.state import AgentState


def after_router(state: AgentState) -> str:
    target = state.get("next_target")
    
    if target:
        return target
    intent = state.get("intent")
    if intent == "question":
        return "search"
    if intent == "pending_response" or intent == "escalation":
        return "ticket_operator"
    if intent == "ticket_enquiry":
        return "ticket_enquiry"
    if intent == "other":
        return "respond"
    raise ValueError("Router must set intent before graph routing")


def entry_route(state: AgentState) -> str:
    """Route an explicit Submit-button event directly to Ticket Operator."""

    return "ticket_operator" if state.get("input_event") == "submit" else "router"


def after_validate(state: AgentState) -> str:
    """Only a requested, budgeted repair returns to Respond."""
    return "respond" if state.get("validation_outcome") == "repair" else "finalize"


def build(checkpointer=None):
    """Compile the graph."""

    graph = StateGraph(AgentState)
    nodes: dict[str, Callable[[AgentState], dict]] = {
        "router": router_node,
        "search": search_node,
        "ticket_operator": ticket_operator_node,
        "ticket_enquiry": ticket_enquiry_node,
        "respond": respond_node,
        "validate": validate_node,
        "finalize": finalize_node,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)

    graph.add_conditional_edges(START, entry_route, ["router", "ticket_operator"])
    graph.add_conditional_edges(
        "router", after_router, ["search", "ticket_operator", "ticket_enquiry", "respond"]
    )
    graph.add_edge("search", "router")
    graph.add_edge("ticket_operator", "router")
    graph.add_edge("ticket_enquiry", "router")
    graph.add_edge("respond", "validate")
    graph.add_conditional_edges("validate", after_validate, ["respond", "finalize"])
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


graph = build()



