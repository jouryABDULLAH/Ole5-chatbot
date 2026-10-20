"""The conversation graph, one invoke per customer message. The first run sends the fixed chat.yaml greeting. Every later message goes to the router, the only node that routes: its model call reports facts about the message, code picks the step from them, and search and classify hand back to it. Then respond writes the text for that step and validate checks it."""

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from ole5_chat import llm, products, values
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision, Step
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.agent.validate import SORRY, check_ticket
from ole5_chat.knowledge.contracts import Retrieval, merge
from ole5_chat.knowledge.search import search as kb_search

log = logging.getLogger(__name__)

MAX_SEARCHES = 4
EXTRA_QUESTION = "unanswered question"


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _direct_documents(state: ChatState) -> list[str]:
    if not state.get("last_retrieval"):
        return []
    return list(dict.fromkeys(e.document for e in Retrieval.from_json(state["last_retrieval"]).direct))


def _new_draft(state: ChatState) -> TicketDraft:
    return TicketDraft(product=values.catalog(state["identity"].product).service)


def _with_details(draft: TicketDraft, decision: RouterDecision) -> TicketDraft:
    unknown = list(dict.fromkeys([*draft.unknown, *decision.unknown]))
    return draft.model_copy(update={"collected": {**draft.collected, **decision.collected}, "unknown": unknown,
                                    "missing": [m for m in decision.missing if m not in unknown],
                                    "classified": False})


def _ticket_step(draft: TicketDraft, decision: RouterDecision) -> Step:
    return "confirm" if decision.urgent or decision.proceed or not draft.missing else "collect"


def _no_answer(draft: TicketDraft | None, decision: RouterDecision, question: str) -> tuple[Step, TicketDraft | None]:
    if draft is None:
        return "offer_ticket", None
    asked = draft.collected.get(EXTRA_QUESTION)
    collected = {**draft.collected, EXTRA_QUESTION: f"{asked}; {question}" if asked else question}
    draft = draft.model_copy(update={"collected": collected, "classified": False})
    return ("confirm" if not draft.missing else "collect"), draft


def route(state: ChatState) -> str:
    return "router" if state["messages"] else "greet"


def greet(state: ChatState) -> dict:
    who = state["identity"]
    log.info("greet  product=%s language=%s", who.product, who.language)
    return {"messages": [AIMessage(content=products.load(who.product).greet(who.language))],
            "phase": "greet", "step": None, "sources": [], "reply": None}


def _decide(state: ChatState) -> dict:
    try:
        decision = llm.structured(prompt.router(state), state["messages"], RouterDecision, node="router")
        corrections = []
    except llm.ModelFailed as exc:
        decision = RouterDecision(intent="other", language=state["identity"].language)
        corrections = [f"router failed: {exc}"]

    draft = state.get("ticket_draft")
    question = state["messages"][-1].text
    step: Step | None
    if decision.cancel_ticket:
        step, draft = "ask", None
    elif decision.intent == "greeting":
        step = "greet"
    elif decision.intent == "other":
        step = "ask"
    elif decision.always_ticket and draft is None and state["phase"] != "offer_ticket":
        step = "offer_ticket"
    elif decision.intent == "ticket_reply":
        draft = _with_details(draft or _new_draft(state), decision)
        step = _ticket_step(draft, decision)
    elif decision.always_ticket or not decision.queries or (decision.urgent and draft is None):
        step, draft = _no_answer(draft, decision, question)
    else:
        step = None

    log.info("router  intent=%s step=%s always_ticket=%s urgent=%s proceed=%s cancel=%s language=%s queries=%s "
             "missing=%s unknown=%s", decision.intent, step or "search", decision.always_ticket, decision.urgent,
             decision.proceed, decision.cancel_ticket, decision.language, decision.queries,
             draft.missing if draft else None, draft.unknown if draft else None)
    return {"decision": decision, "step": step, "ticket_draft": draft, "reply": None,
            "corrections": state["corrections"] + corrections}


def _after_search(state: ChatState) -> dict:
    retrieval = Retrieval.from_json(state["last_retrieval"])
    if retrieval.error:
        log.warning("router  search failed error=%s, treated as no answer", retrieval.error)
    if retrieval.direct:
        step, draft = "answer", state.get("ticket_draft")
    else:
        step, draft = _no_answer(state.get("ticket_draft"), state["decision"], state["messages"][-1].text)
    log.info("router  after search step=%s direct=%d", step, len(retrieval.direct))
    return {"step": step, "ticket_draft": draft}


def router(state: ChatState) -> dict:
    if state["step"] is None and state.get("last_retrieval") is None:
        return _decide(state)
    if state["step"] is None:
        return _after_search(state)
    log.info("router  after classify step=%s", state["step"])
    return {}


def after_router(state: ChatState) -> str:
    if state["step"] is None:
        return "search"
    if state["step"] == "confirm" and not state["ticket_draft"].classified:
        return "classify"
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
    draft = state["ticket_draft"].model_copy(update={**fields, "classified": True})
    log.info("classify  checked queue=%s type=%s subtype=%s priority=%s urgent=%s reason=%r refused=%s",
             draft.queue, draft.type, draft.subtype, draft.priority, draft.urgent, _short(draft.reason or ""),
             problems)
    return {"ticket_draft": draft, "corrections": state["corrections"] + problems}


def respond(state: ChatState) -> dict:
    step = state["step"]
    try:
        reply = llm.structured(prompt.respond(state), state["messages"], Reply, node="respond")
        corrections = []
    except llm.ModelFailed as exc:
        reply = Reply(say=SORRY[state["decision"].language])
        corrections = [f"respond failed: {exc}"]
        if step == "answer":
            step = "ask"
    log.info("respond  step=%s say=%r sources=%s", step, _short(reply.say), reply.sources)
    return {"reply": reply, "step": step, "corrections": state["corrections"] + corrections}


def validate(state: ChatState) -> dict:
    reply, step = state["reply"], state["step"]
    sources: list[str] = []
    if step == "answer":
        direct = _direct_documents(state)
        sources = [s for s in reply.sources if s in direct] or direct
        dropped = [s for s in reply.sources if s not in direct]
        if dropped:
            log.info("validate  dropped sources not in a DIRECT passage: %s", dropped)
    log.info("validate  step=%s sources=%s", step, sources)
    return {"messages": [AIMessage(content=reply.say)], "phase": step, "step": None, "sources": sources,
            "last_retrieval": None}


def build(checkpointer):
    g = StateGraph(ChatState)
    for name, node in (("greet", greet), ("router", router), ("search", search), ("classify", classify),
                       ("respond", respond), ("validate", validate)):
        g.add_node(name, node)
    g.add_conditional_edges(START, route, ["greet", "router"])
    g.add_conditional_edges("router", after_router, ["search", "classify", "respond"])
    g.add_edge("search", "router")
    g.add_edge("classify", "router")
    g.add_edge("respond", "validate")
    g.add_edge("validate", END)
    g.add_edge("greet", END)
    return g.compile(checkpointer=checkpointer)
