"""The conversation graph, one invoke per customer message. The first run sends the fixed chat.yaml greeting; every later message goes router, search when needed, respond, classify at confirm, validate. A reply that breaks a rule goes back to respond once."""

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from ole5_chat import llm, products, values
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.agent.validate import NO_ANSWER, SORRY, check_ticket
from ole5_chat.knowledge.contracts import Retrieval, merge
from ole5_chat.knowledge.search import search as kb_search

log = logging.getLogger(__name__)

MAX_SEARCHES = 4

RETRY_NO_DIRECT = ("No passage answers this question directly. Do not answer it. Say in one line that you don't "
                   "have an answer for this and ask: \"Would you like me to open a ticket?\". Use step offer_ticket. "
                   "If the customer greeted you, keep addressing the greeting first.")
RETRY_ALWAYS_TICKET = ("This topic is never answered. Do not answer it. Say in one line that you don't have an "
                       "answer for this and ask whether they would like a ticket. Use step offer_ticket. "
                       "If the customer greeted you, keep addressing the greeting first.")
RETRY_CREATED = "The step created is set only when the ticket is submitted. Choose another step."


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _direct_documents(state: ChatState) -> list[str]:
    if not state.get("last_retrieval"):
        return []
    return list(dict.fromkeys(e.document for e in Retrieval.from_json(state["last_retrieval"]).direct))


def route(state: ChatState) -> str:
    return "router" if state["messages"] else "greet"


def greet(state: ChatState) -> dict:
    who = state["identity"]
    log.info("greet  product=%s language=%s", who.product, who.language)
    return {"messages": [AIMessage(content=products.load(who.product).greet(who.language))],
            "phase": "greet", "sources": [], "reply": None}


def router(state: ChatState) -> dict:
    try:
        decision = llm.structured(prompt.router(state), state["messages"], RouterDecision, node="router")
        corrections = []
    except llm.ModelFailed as exc:
        decision = RouterDecision(intent="other", language=state["identity"].language)
        corrections = [f"router failed: {exc}"]
    log.info("router  intent=%s always_ticket=%s urgent=%s language=%s queries=%s",
             decision.intent, decision.always_ticket, decision.urgent, decision.language, decision.queries)
    return {"decision": decision, "last_retrieval": None, "reply": None, "retry_note": None, "retried": False,
            "corrections": state["corrections"] + corrections}


def after_router(state: ChatState) -> str:
    decision = state["decision"]
    if decision.intent == "question" and decision.queries and not decision.always_ticket:
        return "search"
    return "respond"


def search(state: ChatState) -> dict:
    queries = state["decision"].queries
    if len(queries) > MAX_SEARCHES:
        log.warning("search  %d queries, only the first %d run", len(queries), MAX_SEARCHES)
    product = state["identity"].product
    result = merge([kb_search(q, product) for q in queries[:MAX_SEARCHES]])
    log.info("search  verdict=%s passages=%d direct=%d documents=%s error=%s",
             result.verdict.value, len(result.evidence), len(result.direct), result.documents, result.error)
    return {"last_retrieval": result.as_json()}


def respond(state: ChatState) -> dict:
    messages = state["messages"]
    if state.get("retry_note"):
        messages = [*messages,
                    AIMessage(content=state["reply"].say),
                    HumanMessage(content=f"Note from the system, not the customer: {state['retry_note']} "
                                         "Write the reply again.")]
    try:
        reply = llm.structured(prompt.respond(state), messages, Reply, node="respond")
        corrections = []
    except llm.ModelFailed as exc:
        reply = Reply(say=SORRY[state["decision"].language], step="ask")
        corrections = [f"respond failed: {exc}"]
    log.info("respond  retry=%s step=%s say=%r sources=%s collected=%s missing=%s", bool(state.get("retry_note")),
             reply.step, _short(reply.say), reply.sources, reply.collected, reply.missing)
    update = {"reply": reply, "retry_note": None, "corrections": state["corrections"] + corrections}
    collecting = reply.step in ("collect", "confirm")
    if collecting or reply.missing:
        draft = state.get("ticket_draft") or TicketDraft(product=values.catalog(state["identity"].product).service)
        missing = reply.missing if collecting else list(dict.fromkeys([*draft.missing, *reply.missing]))
        update["ticket_draft"] = draft.model_copy(update={"collected": {**draft.collected, **reply.collected},
                                                          "missing": missing})
    return update


def after_respond(state: ChatState) -> str:
    return "classify" if state["reply"].step == "confirm" else "validate"


def classify(state: ChatState) -> dict:
    catalog = values.catalog(state["identity"].product)
    messages = list(state["messages"])
    fields, problems = {}, ["classification not run"]
    for attempt in (1, 2):
        try:
            result = llm.structured(prompt.classify(state), messages, Classification, node="classify")
        except llm.ModelFailed as exc:
            problems = [f"classify failed: {exc}"]
            break
        fields, problems = check_ticket(result, catalog)
        log.info("classify  attempt=%d queue=%s type=%s subtype=%s priority=%s urgent=%s confidence=%s problems=%s",
                 attempt, result.queue, result.type, result.subtype, result.priority, result.urgent,
                 result.confidence, problems)
        if not problems:
            break
        messages = [*messages, AIMessage(content=result.model_dump_json()),
                    HumanMessage(content="These values were refused: " + "; ".join(problems)
                                 + ". Choose each from its list exactly as written.")]
    draft = state["ticket_draft"].model_copy(update=fields)
    return {"ticket_draft": draft, "corrections": state["corrections"] + problems}


def _violation(state: ChatState) -> str | None:
    reply, decision = state["reply"], state["decision"]
    if reply.step == "created":
        return RETRY_CREATED
    if reply.step == "answer" and decision.always_ticket:
        return RETRY_ALWAYS_TICKET
    if reply.step == "answer" and not _direct_documents(state):
        return RETRY_NO_DIRECT
    return None


def validate(state: ChatState) -> dict:
    reply, decision = state["reply"], state["decision"]
    problem = _violation(state)
    if problem and not state["retried"]:
        log.info("validate  model_step=%s sent back to respond: %s", reply.step, problem)
        return {"retry_note": problem, "retried": True,
                "corrections": state["corrections"] + [f"sent back to respond: {problem}"]}

    corrections: list[str] = []
    step, say, sources = reply.step, reply.say, reply.sources
    if problem == RETRY_CREATED:
        corrections.append("step created still set after the retry: changed to ask")
        step = "ask"
    elif problem:
        corrections.append(f"still broken after the retry, standard no-answer text used: {problem}")
        step, say = "offer_ticket", NO_ANSWER[decision.language]
    if step == "answer":
        direct = _direct_documents(state)
        sources = [s for s in sources if s in direct] or direct
    else:
        sources = []

    log.info("validate  model_step=%s final_step=%s sources=%s corrections=%s", reply.step, step, sources, corrections)
    return {"messages": [AIMessage(content=say)], "phase": step, "sources": sources, "retry_note": None,
            "corrections": state["corrections"] + corrections}


def after_validate(state: ChatState) -> str:
    return "respond" if state.get("retry_note") else END


def build(checkpointer):
    g = StateGraph(ChatState)
    for name, node in (("greet", greet), ("router", router), ("search", search), ("respond", respond),
                       ("classify", classify), ("validate", validate)):
        g.add_node(name, node)
    g.add_conditional_edges(START, route, ["greet", "router"])
    g.add_conditional_edges("router", after_router, ["search", "respond"])
    g.add_edge("search", "respond")
    g.add_conditional_edges("respond", after_respond, ["classify", "validate"])
    g.add_edge("classify", "validate")
    g.add_conditional_edges("validate", after_validate, ["respond", END])
    g.add_edge("greet", END)
    return g.compile(checkpointer=checkpointer)
