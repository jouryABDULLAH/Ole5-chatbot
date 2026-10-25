"""The respond node that searches for itself: the support agent's tool loop with one tool, 
search_knowledge_base, then two gates. 
Gate 1 nudges a reply written without a search, as the support agent does, and asks the grounding call whether it asserts facts. 
Gate 2 (the grounding call) compares the reply with the DIRECT passages and names the documents it draws on; 
those are the sources, never the respond model's own list. 
Then the language guardrails."""

import json
import logging
import re

from groq import BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Reply
from ole5_chat.agent.handlers import OWN_ISSUE
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
    """One model call; with no tool (every turn but a chat turn) the model cannot search."""
    error = "no attempt made"
    for temperature in (0, 0.3):
        try:
            client = llm.tool_client(temperature)
            return (client.bind_tools([tool], tool_choice=tool_choice) if tool else client).invoke(convo)
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
            content = tool.invoke(call["args"]) if tool and call["name"] == tool.name else f"No tool named {call['name']}."
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


def _is_json(text: str) -> bool:
    text = text.strip()
    if text.startswith("{"):
        return True
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False


def _gates(convo, tool, sink, customer, text, corrections, chat) -> tuple[str, list[str], bool]:
    """Gate 1, did it search, runs only on the chat path: the offer, accept and decline tasks need no search. Gate 2, is the reply drawn from a DIRECT passage. A reply with no DIRECT passage behind it is grounded with no passages. Returns the reply, its sources and whether it offers a ticket."""
    if not sink and not chat:
        return text, [], False
    if not sink:
        first = text
        convo.append(HumanMessage(content=NUDGE))
        corrections.append("nudged")
        text = _loop(convo, tool, corrections)
        if not sink:
            text = first  # It answered the nudge without searching: the second reply adds nothing.
    for attempt in (1, 2):
        retrieval = merge(sink) if sink else None
        direct = list(dict.fromkeys(e.document for e in retrieval.direct)) if retrieval else []
        try:
            grounding = _ground(customer, text, retrieval)
        except llm.ModelFailed as exc:
            log.warning("respond_agent  grounding call failed: %s", str(exc)[:300])
            corrections.append("grounding call failed")
            return text, direct, False
        if not direct:
            if grounding.answered:
                corrections.append("answer without a DIRECT passage" if sink else "answer without searching")
                return _no_answer(convo, tool), [], True
            if not sink:
                corrections.append("replied without searching")
            return text, [], grounding.offered
        used = [d for d in direct if any(d in u for u in grounding.used)]
        log.info("respond_agent  grounding attempt=%d answered=%s offered=%s used=%s unsupported=%s", attempt,
                 grounding.answered, grounding.offered, used, grounding.unsupported)
        if grounding.answered and used and not grounding.unsupported:
            if grounding.offered:
                corrections.append("reply offered a ticket")
            return text, used, grounding.offered
        if grounding.unsupported:
            if attempt == 2:
                corrections.append("reply kept unsupported facts")
                return _no_answer(convo, tool), [], True
            convo.append(HumanMessage(content=NOTE + "Remove these from your reply; no passage states them: "
                                      + "; ".join(grounding.unsupported) + ". Keep only what the passages say."))
            corrections.append("gate 2 retried: unsupported")
            text = _loop(convo, tool, corrections)
            continue
        if attempt == 1:
            convo.append(HumanMessage(content=NOTE + "A passage answers this directly. Write what it says, even if it is one sentence, "
                                      "and nothing more."))
            corrections.append("gate 2 retried: declined")
            text = _loop(convo, tool, corrections)
    corrections.append("respond declined a DIRECT passage")
    return _no_answer(convo, tool), [], True


def respond_agent(state: ChatState) -> dict:
    identity = state["identity"]
    customer = next((m.text for m in reversed(state["messages"]) if m.type == "human"), "")
    chat = state.get("next") not in OWN_ISSUE
    sink: list[Retrieval] = []
    tool = make_search_tool(identity.product, sink) if chat else None
    corrections: list[str] = []
    language = language_of(customer) or identity.language
    convo: list[BaseMessage] = [SystemMessage(content=prompt.respond(state, language)), *state["messages"]]
    if state["messages"][-1].type != "human":
        # A button turn adds no message; without this the model would continue the assistant's last reply.
        convo.append(HumanMessage(content=NOTE + "the customer pressed Submit."))
    
    try:
        text = _loop(convo, tool, corrections)
        json_retried = False
        for attempt in range(LANGUAGE_RETRIES + 1):
            text, sources, offered = _gates(convo, tool, sink, customer, text, corrections, chat)
            if _is_json(text) and not json_retried:
                json_retried = True
                convo.append(HumanMessage(content=NOTE + "Write a plain chat message to the customer, not JSON."))
                corrections.append("reply was JSON")
                text = _loop(convo, tool, corrections)
                text, sources, offered = _gates(convo, tool, sink, customer, text, corrections, chat)
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
        text, sources, offered = SORRY[language], [], False
        corrections.append(f"respond failed: {exc}")
    retrieval = merge(sink) if sink else None
    log.info("respond_agent  searches=%d sources=%s say=%r", len(sink), sources, text[:90])
    return {"reply": Reply(say=text, sources=sources), "last_retrieval": retrieval.as_json() if retrieval else None,
            "corrections": state["corrections"] + corrections, "offered": offered, "language": language}
