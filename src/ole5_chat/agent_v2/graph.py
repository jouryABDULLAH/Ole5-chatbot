"""Graph skeleton for the new owner-routing design."""

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from ole5_chat.agent_v2.nodes import (
    finalize_node,
    respond_node,
    router_node,
    search_node,
    ticket_manager_node,
    ticket_status_node,
    validate_node,
)
from ole5_chat.agent_v2.state import AgentState


def after_router(state: AgentState) -> str:
    decision = state.get("router_decision")
    if decision == "question":
        return "search"
    if decision == "pending_response" or decision == "escalation":
        return "ticket_manager"
    if decision == "ticket_status":
        return "ticket_status"
    if decision == "other":
        return "respond"
    raise ValueError("Router must set router_decision before graph routing")


def entry_route(state: AgentState) -> str:
    """Route an explicit Submit-button event directly to Ticket Manager."""

    return "ticket_manager" if state.get("input_event") == "submit" else "router"


def after_search(state: AgentState) -> str:
    outcome = state.get("search_outcome")
    if outcome in {"answered", "failed"}:
        return "respond"
    if outcome in {"partial", "no_answer"}:
        return "ticket_manager"
    raise ValueError("Search must set search_outcome before graph routing")


def build(checkpointer=None):
    """Compile the new graph. Node bodies are replaced in later steps."""

    graph = StateGraph(AgentState)
    nodes: dict[str, Callable[[AgentState], dict]] = {
        "router": router_node,
        "search": search_node,
        "ticket_manager": ticket_manager_node,
        "ticket_status": ticket_status_node,
        "respond": respond_node,
        "validate": validate_node,
        "finalize": finalize_node,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)

    graph.add_conditional_edges(START, entry_route, ["router", "ticket_manager"])
    graph.add_conditional_edges(
        "router", after_router, ["search", "ticket_manager", "ticket_status", "respond"]
    )
    graph.add_conditional_edges("search", after_search, ["respond", "ticket_manager"])
    graph.add_edge("ticket_manager", "respond")
    graph.add_edge("ticket_status", "respond")
    graph.add_edge("respond", "validate")
    graph.add_edge("validate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


graph = build()
