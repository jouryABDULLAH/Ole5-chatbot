"""The conversation graph, one invoke per customer message. The first run sends the persona's introduction from chat.yaml, with no model call. Every later message goes to the router, the only node that routes: its model call reports facts about the message, code picks the step from them, and search and classify hand back to it. Then respond writes the text for that step and validate checks it. Two guardrails run inside their nodes: the router's language must match the customer's message, and respond's reply must be in that language; each is sent back to the model with the reason, twice at most."""

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from ole5_chat import llm, products, values
from ole5_chat.agent import lanes, prompt
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision, Step
from ole5_chat.agent.lanes import _new_draft
from ole5_chat.agent.respond_agent import respond_agent
from ole5_chat.agent.router_v2 import router_v2
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.agent.validate import NAMES, SORRY, check_ticket, language_of, language_problem
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, merge
from ole5_chat.knowledge.search import search as kb_search

log = logging.getLogger(__name__)


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _direct_documents(state: ChatState) -> list[str]:
    if not state.get("last_retrieval"):
        return []
    return list(dict.fromkeys(e.document for e in Retrieval.from_json(state["last_retrieval"]).direct))


def _with_details(draft: TicketDraft, decision: RouterDecision) -> TicketDraft:
    unknown = list(dict.fromkeys([*draft.unknown, *decision.unknown]))
    return draft.model_copy(update={"collected": {**draft.collected, **decision.collected}, "unknown": unknown,
                                    "missing": [m for m in decision.missing if m not in unknown],
                                    "classified": False})


def _ticket_step(draft: TicketDraft, decision: RouterDecision) -> Step:
    return "confirm" if decision.urgent or not draft.missing else "collect"


def _no_answer() -> Step:
    return "offer_ticket"


def route(state: ChatState) -> str:
    if not state["messages"]:
        return "greet"
    return "router_v2" if get_settings().respond_agent else "router"


def greet(state: ChatState) -> dict:
    who = state["identity"]
    log.info("greet  product=%s language=%s", who.product, who.language)
    return {"messages": [AIMessage(content=products.load(who.product).greet(who.language))],
            "phase": "greet", "step": None, "sources": [], "reply": None}


def _decide(state: ChatState) -> dict:
    expected = language_of(state["messages"][-1].text)

    def language_check(d: RouterDecision) -> str | None:
        if expected and d.language != expected:
            return f"The customer's message is in {NAMES[expected]}, so language must be \"{expected}\"."
        return None

    try:
        decision = llm.structured(prompt.router(state), state["messages"], RouterDecision, node="router",
                                  check=language_check)
        corrections = []
    except llm.GuardrailFailed as exc:
        decision, corrections = exc.result, []
    except llm.ModelFailed as exc:
        language = expected or state["identity"].language
        log.warning("router  model failed, fixed apology sent, state unchanged: %s", _short(str(exc)))
        return {"decision": RouterDecision(intent="other", language=language), "step": "ask",
                "reply": Reply(say=SORRY[language]), "corrections": state["corrections"] + [f"router failed: {exc}"]}
    if expected and decision.language != expected:
        log.warning("router  language overridden model=%s detected=%s", decision.language, expected)
        corrections.append(f"router language {decision.language!r} overridden with {expected!r}")
        decision = decision.model_copy(update={"language": expected})

    draft = state.get("ticket_draft")
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
    elif not decision.queries or (decision.urgent and draft is None):
        step = _no_answer()
    else:
        step = None

    log.info("router  phase=%s intent=%s step=%s always_ticket=%s urgent=%s cancel=%s language=%s queries=%s "
             "missing=%s unknown=%s", state["phase"], decision.intent, step or "search", decision.always_ticket, decision.urgent,
             decision.cancel_ticket, decision.language, decision.queries,
             draft.missing if draft else None, draft.unknown if draft else None)
    return {"decision": decision, "step": step, "ticket_draft": draft, "reply": None, "note": None,
            "corrections": state["corrections"] + corrections}


def _after_search(state: ChatState) -> dict:
    retrieval = Retrieval.from_json(state["last_retrieval"])
    if retrieval.error:
        log.warning("router  search failed error=%s, treated as no answer", retrieval.error)
    step = "answer" if retrieval.direct else _no_answer()
    log.info("router  after search step=%s direct=%d", step, len(retrieval.direct))
    return {"step": step}


def router(state: ChatState) -> dict:
    if state["step"] is None and state.get("last_retrieval") is None:
        return _decide(state)
    if state["step"] is None:
        return _after_search(state)
    if not state["ticket_draft"].classified:
        log.info("router  after classify: summary not built, step=ask")
        return {"step": "ask"}
    log.info("router  after classify step=%s", state["step"])
    return {}


def after_router(state: ChatState) -> str:
    if state["step"] is None:
        return "search"
    if state["step"] == "confirm" and not state["ticket_draft"].classified:
        return "classify"
    return "respond"


def search(state: ChatState) -> dict:
    queries, max_searches = state["decision"].queries, get_settings().kb_max_searches
    if len(queries) > max_searches:
        log.warning("search  %d queries, only the first %d run", len(queries), max_searches)
    product = state["identity"].product
    result = merge([kb_search(q, product) for q in queries[:max_searches]])
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
            log.warning("classify  model failed, summary not built: %s", _short(str(exc)))
            return {"note": "The ticket summary could not be built this time. Say so in one line, keep what the "
                            "customer gave, and ask them to send their last message again.",
                    "corrections": state["corrections"] + [f"classify failed: {exc}"]}
        fields, problems = check_ticket(result, catalog)
        log.info("classify  attempt=%d queue=%s type=%s subtype=%s priority=%s urgent=%s confidence=%s problems=%s",
                 attempt, result.queue, result.type, result.subtype, result.priority, result.urgent,
                 result.confidence, problems)
        if not problems:
            break
        messages = [*messages, AIMessage(content=result.model_dump_json()),
                    HumanMessage(content="These values were refused: " + "; ".join(problems)
                                 + ". Choose each from its list exactly as written.")]
    if problems:
        log.warning("classify  values still refused, summary not built: %s", "; ".join(problems))
        return {"note": "The ticket summary could not be built this time. Say so in one line, keep what the "
                        "customer gave, and ask them to send their last message again.",
                "corrections": state["corrections"] + [f"classify refused: {'; '.join(problems)}"]}
    draft = state["ticket_draft"].model_copy(update={**fields, "classified": True})
    log.info("classify  checked queue=%s type=%s subtype=%s priority=%s urgent=%s reason=%r",
             draft.queue, draft.type, draft.subtype, draft.priority, draft.urgent, _short(draft.reason or ""))
    return {"ticket_draft": draft}


def respond(state: ChatState) -> dict:
    step, language = state["step"], state["decision"].language
    if state.get("reply") is not None:
        log.info("respond  step=%s preset=%r", step, _short(state["reply"].say))
        return {"step": step}
    try:
        reply = llm.structured(prompt.respond(state), state["messages"], Reply, node="respond",
                               check=lambda r: language_problem(r.say, language))
        corrections = []
    except llm.GuardrailFailed as exc:
        reply, corrections = exc.result, [f"respond sent in the wrong language: {exc}"]
    except llm.ModelFailed as exc:
        reply = Reply(say=SORRY[language])
        corrections = [f"respond failed: {exc}"]
        if step == "answer":
            step = "ask"
    if step == "answer" and not reply.sources:
        step = _no_answer()
        corrections.append(f"answer without a source: step changed to {step}")
        log.info("respond  no source cited at answer, step changed to %s", step)
    log.info("respond  step=%s say=%r sources=%s", step, _short(reply.say), reply.sources)
    return {"reply": reply, "step": step, "corrections": state["corrections"] + corrections}


def validate(state: ChatState) -> dict:
    reply, step = state["reply"], state["step"]
    if step is None:
        log.info("validate  respond_agent reply phase=%s pending=%d sources=%s", state["chat_phase"],
                 len(state["pending"]), reply.sources)
        return {"messages": [AIMessage(content=reply.say)], "sources": reply.sources, "last_retrieval": None,
                "note": None, "task": None, "next": None, "offered": False}
    sources: list[str] = []
    if step == "answer":
        direct = _direct_documents(state)
        sources = [s for s in reply.sources if s in direct] or (direct if reply.sources else [])
        dropped = [s for s in reply.sources if s not in direct]
        if dropped:
            log.info("validate  dropped sources not in a DIRECT passage: %s", dropped)
    log.info("validate  phase %s -> %s sources=%s", state["phase"], step, sources)
    return {"messages": [AIMessage(content=reply.say)], "phase": step, "step": None, "sources": sources,
            "last_retrieval": None, "note": None}


def build(checkpointer):
    g = StateGraph(ChatState)
    g.add_node("greet", greet)
    g.add_node("validate", validate)
    if get_settings().respond_agent:
        for name, node in (("router_v2", router_v2), ("chat", lanes.chat), ("queue", lanes.queue),
                           ("accept", lanes.accept), ("decline", lanes.decline), ("respond_agent", respond_agent),
                           ("post_check", lanes.post_check)):
            g.add_node(name, node)
        g.add_conditional_edges(START, route, ["greet", "router_v2"])
        g.add_conditional_edges("router_v2", lanes.lane, ["chat", "queue", "accept", "decline"])
        for name in ("chat", "queue", "accept", "decline"):
            g.add_edge(name, "respond_agent")
        g.add_edge("respond_agent", "post_check")
        g.add_edge("post_check", "validate")
    else:
        for name, node in (("router", router), ("search", search), ("classify", classify), ("respond", respond)):
            g.add_node(name, node)
        g.add_conditional_edges(START, route, ["greet", "router"])
        g.add_conditional_edges("router", after_router, ["search", "classify", "respond"])
        g.add_edge("search", "router")
        g.add_edge("classify", "router")
        g.add_edge("respond", "validate")
    g.add_edge("validate", END)
    g.add_edge("greet", END)
    return g.compile(checkpointer=checkpointer)
