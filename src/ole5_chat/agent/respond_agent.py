"""The respond node that searches for itself: the support agent's tool loop with one tool, 
search_knowledge_base, then two gates. 
Gate 1 nudges a reply written without a search, as the support agent does, and asks the grounding call whether it asserts facts. 
Gate 2 (the grounding call) compares the reply with the DIRECT passages and names the documents it draws on; 
those are the sources, never the respond model's own list. 
Then the language guardrails."""

import logging
import re

from groq import BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Reply
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.validate import NAMES, SORRY, language_of, language_problem
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, merge
from ole5_chat.knowledge.tool import make_search_tool

log = logging.getLogger(__name__)

NOTE = "Note from the system, not the customer: "
NUDGE = ("You have not searched the knowledge base. If this message needs no search (a greeting, thanks, or a "
         "question about the customer's own account or ticket that no knowledge base could answer), reply as you "
         "did. Otherwise search now.")
LANGUAGE_RETRIES = 2
CHINESE = re.compile(r"[一-鿿㐀-䶿]")


class Grounding(BaseModel):
    used: list[str]
    answered: bool
    offered: bool
    unsupported: list[str]


def _invoke(convo: list[BaseMessage], tool, tool_choice: str | None = None) -> AIMessage:
    error = "no attempt made"
    for temperature in (0, 0.3):
        try:
            return llm.tool_client(temperature).bind_tools([tool], tool_choice=tool_choice).invoke(convo)
        except BadRequestError as exc:
            error = str(exc)[:400]
            log.warning("respond_agent  rejected by Groq at temperature %s: %s", temperature, error)
    raise llm.ModelFailed(error)


def _loop(convo: list[BaseMessage], tool, corrections: list[str]) -> str:
    """Model calls until one has no tool call, at most kb_max_searches + 1, then one final call with searching off. Appends every message to convo; returns the last reply's text."""
    for _ in range(get_settings().kb_max_searches + 1):
        reply = _invoke(convo, tool)
        convo.append(reply)
        if not reply.tool_calls:
            return reply.text
        for call in reply.tool_calls:
            content = tool.invoke(call["args"]) if call["name"] == tool.name else f"No tool named {call['name']}."
            convo.append(ToolMessage(content=content, tool_call_id=call["id"]))
    
    log.warning("respond_agent  search cap reached, one final call")
    corrections.append("search cap reached")
    
    convo.append(HumanMessage(content=NOTE + "no more searches; answer from what you have."))
    reply = _invoke(convo, tool, tool_choice="none")
    
    convo.append(reply)
    return reply.text


def _no_answer(convo: list[BaseMessage], tool) -> str:
    """The persona's own no-answer wording, in the customer's language: one call with searching off."""
    convo.append(HumanMessage(content=NOTE + "You have no answer for this. Say so and offer a ticket."))
    reply = _invoke(convo, tool, tool_choice="none")
    convo.append(reply)
    return reply.text


def _ground(customer: str, text: str, retrieval: Retrieval | None) -> Grounding:
    passages = "\n\n".join(f"Document: {e.document}\n{e.text.strip()}" for e in retrieval.direct) if retrieval else ""
    content = f"Customer's message:\n{customer}\n\nReply:\n{text}\n\nPassages:\n{passages or 'none'}"
    return llm.structured(prompt.ground(), [HumanMessage(content=content)], Grounding, node="ground")


def _gates(convo, tool, sink, customer, text, corrections) -> tuple[str, list[str]]:
    """Gate 1, did it search; gate 2, is the reply drawn from a DIRECT passage. Returns the reply and its sources."""
    if not sink:
        convo.append(HumanMessage(content=NUDGE))
        corrections.append("nudged")
        text = _loop(convo, tool, corrections)
    if not sink:
        try:
            grounding = _ground(customer, text, None)
        except llm.ModelFailed as exc:
            log.warning("respond_agent  grounding call failed: %s", str(exc)[:300])
            corrections.append("grounding call failed")
            return text, []
        if grounding.answered:
            corrections.append("answer without searching")
            return _no_answer(convo, tool), []
        corrections.append("replied without searching")
        return text, []
    for attempt in (1, 2):
        retrieval = merge(sink)
        direct = list(dict.fromkeys(e.document for e in retrieval.direct))
        if not direct:
            return text, []
        try:
            grounding = _ground(customer, text, retrieval)
        except llm.ModelFailed as exc:
            log.warning("respond_agent  grounding call failed: %s", str(exc)[:300])
            corrections.append("grounding call failed")
            return text, direct
        used = [d for d in direct if any(d in u for u in grounding.used)]
        log.info("respond_agent  grounding attempt=%d answered=%s offered=%s used=%s unsupported=%s", attempt,
                 grounding.answered, grounding.offered, used, grounding.unsupported)
        if grounding.answered and used and not grounding.unsupported:
            if grounding.offered:
                corrections.append("reply offered a ticket")
            return text, used
        if grounding.unsupported:
            if attempt == 2:
                corrections.append("reply kept unsupported facts")
                return _no_answer(convo, tool), []
            convo.append(HumanMessage(content=NOTE + "Remove these from your reply; no passage states them: "
                                      + "; ".join(grounding.unsupported) + ". Keep only what the passages say."))
            corrections.append("gate 2 retried: unsupported")
            text = _loop(convo, tool, corrections)
            continue
        if attempt == 1:
            convo.append(HumanMessage(content=NOTE + "a passage answers this directly; answer from it."))
            corrections.append("gate 2 retried: declined")
            text = _loop(convo, tool, corrections)
    corrections.append("respond declined a DIRECT passage")
    return _no_answer(convo, tool), []


def respond_agent(state: ChatState) -> dict:
    identity = state["identity"]
    customer = state["messages"][-1].text
    sink: list[Retrieval] = []
    tool = make_search_tool(identity.product, sink)
    corrections: list[str] = []
    language = language_of(customer) or identity.language
    convo: list[BaseMessage] = [SystemMessage(content=prompt.respond_v2(state, language)), *state["messages"]]
    
    try:
        text = _loop(convo, tool, corrections)
        for attempt in range(LANGUAGE_RETRIES + 1):
            text, sources = _gates(convo, tool, sink, customer, text, corrections)
            problem = language_problem(text, language) or (
                CHINESE.search(text) and f"The reply contains Chinese characters; rewrite it in {NAMES[language]}.")
            if not problem:
                break
            if attempt == LANGUAGE_RETRIES:
                corrections.append(f"language: {problem}")
                break

            convo.append(HumanMessage(content=NOTE + problem))
            corrections.append("language retried")
            text = _loop(convo, tool, corrections)
    except llm.ModelFailed as exc:
        log.warning("respond_agent  model failed, sorry sent: %s", str(exc)[:300])
        text, sources = SORRY[language], []
        corrections.append(f"respond failed: {exc}")
    retrieval = merge(sink) if sink else None
    log.info("respond_agent  searches=%d sources=%s say=%r", len(sink), sources, text[:90])
    return {"reply": Reply(say=text, sources=sources), "last_retrieval": retrieval.as_json() if retrieval else None,
            "corrections": state["corrections"] + corrections}
