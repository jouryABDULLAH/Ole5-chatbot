"""The conversation graph, one invoke per customer message. The first run sends the persona's introduction from chat.yaml, with no model call. Every later message goes to the router, which picks next from the options of the conversation's phase. The handler for next applies it to the state and sets the task (a Submit button turn arrives with next already set and skips the router), search runs the searches the message needs, respond_agent writes the reply, post_check queues a question the reply offered a ticket for, and validate posts the reply."""

import logging

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from ole5_chat import products
from ole5_chat.agent import handlers
from ole5_chat.agent.respond import respond_agent
from ole5_chat.agent.search import search
from ole5_chat.agent.router import router
from ole5_chat.agent.state import ChatState

log = logging.getLogger(__name__)

HANDLER_NODES = tuple(dict.fromkeys(handlers.HANDLERS.values()))


def route(state: ChatState) -> str:
    """greet on the first run; a button turn arrives with next already set and skips the router."""
    if not state["messages"]:
        return "greet"
    return handlers.handler(state) if state.get("next") else "router"


def after_router(state: ChatState) -> str:
    """A failed route has no next and goes straight to validate with the try-again reply."""
    return handlers.handler(state) if state.get("next") else "validate"


def greet(state: ChatState) -> dict:
    who = state["identity"]
    log.info("greet  product=%s language=%s", who.product, who.language)
    return {"messages": [AIMessage(content=products.load(who.product).greet(who.language))],
            "sources": [], "reply": None}


def validate(state: ChatState) -> dict:
    reply = state["reply"]
    log.info("validate  phase=%s pending=%d sources=%s", state["phase"], len(state["pending"]), reply.sources)
    return {"messages": [AIMessage(content=reply.say)], "sources": reply.sources, "last_retrieval": None,
            "note": None, "task": None, "next": None, "offered": False}


def build(checkpointer):
    g = StateGraph(ChatState)
    for name, node in (("greet", greet), ("router", router), ("search", search), ("respond_agent", respond_agent),
                       ("post_check", handlers.post_check), ("validate", validate)):
        g.add_node(name, node)
    for name in HANDLER_NODES:
        g.add_node(name, getattr(handlers, name))
        g.add_edge(name, "search")
    g.add_conditional_edges(START, route, ["greet", "router", *HANDLER_NODES])
    g.add_conditional_edges("router", after_router, [*HANDLER_NODES, "validate"])
    g.add_edge("search", "respond_agent")
    g.add_edge("respond_agent", "post_check")
    g.add_edge("post_check", "validate")
    g.add_edge("validate", END)
    g.add_edge("greet", END)
    return g.compile(checkpointer=checkpointer)
