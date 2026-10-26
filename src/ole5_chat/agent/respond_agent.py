"""The search node and the respond node.

search: on a question turn one model call writes the searches and code runs them. On details and change it searches
only if the customer also asked something. Every other turn passes through.

respond_agent: writes the reply, with no tool.
- DIRECT passages: answer from them with a status. A grounding call names the documents the reply uses (the sources)
  and any statement no passage makes.
- A question with no DIRECT passage: the no-answer wording.
- Anything else: a plain reply.
Code, not the model, decides whether the reply offers a ticket: on a question turn whose status is not answered."""

import json
import logging
import re
from typing import Literal

from groq import BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Reply
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.validate import NAMES, SORRY, language_of, language_problem
from ole5_chat.config import get_settings
from ole5_chat.knowledge.contracts import Retrieval, Verdict, merge
from ole5_chat.knowledge.tool import make_search_tool

log = logging.getLogger(__name__)

NOTE = "Note from the system, not the customer: "
LANGUAGE_RETRIES = 2
# Turns answered as a question. status has no handler of its own yet.
QUESTIONS = ("question", "status")
# Ticket turns that search only if the customer also asks something. An accept that asks something is routed to
# question.
SEARCHES = ("details", "change")
CHINESE = re.compile(r"[一-鿿㐀-䶿]")
STATUS = ("Reply with JSON only, matching this schema: {schema}\nreply is your message to the customer. status is "
          "answered when the passages answer what the customer asked, partial when they answer only part of it and the "
          "reply offers a ticket for the rest, no_answer when they don't answer it and the reply says so and offers a "
          "ticket. On a turn where the customer asked nothing, status is answered.")
UNSUPPORTED = "Remove these from your reply; no passage states them: {}. Keep only what the passages say."
UNUSED = "Your reply draws on none of the passages. Write what they say about the customer's question, and nothing more."
NO_FACTS = "Write your reply again without stating anything about the product; do only what the task says."


class Answer(BaseModel):
    status: Literal["answered", "partial", "no_answer"]
    reply: str


class Grounding(BaseModel):
    used: list[str]
    unsupported: list[str]


# The search node.

def _invoke(convo: list[BaseMessage], tool=None, tool_choice: str | None = None) -> AIMessage:
    """One model call, retried warmer if Groq rejects it. Only the search node binds a tool."""
    error = "no attempt made"
    for temperature in (0, 0.3):
        try:
            client = llm.tool_client(temperature)
            return (client.bind_tools([tool], tool_choice=tool_choice) if tool else client).invoke(convo)
        except BadRequestError as exc:
            error = str(exc)[:400]
            log.warning("respond_agent  rejected by Groq at temperature %s: %s", temperature, error)
    raise llm.ModelFailed(error)


def search(state: ChatState) -> dict:
    question = state.get("next") in QUESTIONS
    if not question and state.get("next") not in SEARCHES:
        return {}
    sink: list[Retrieval] = []
    tool = make_search_tool(state["identity"].product, sink)
    convo = [SystemMessage(content=prompt.search(state, question)), *state["messages"]]
    try:
        reply = _invoke(convo, tool, "required" if question else "auto")
    except llm.ModelFailed as exc:
        log.warning("search  model failed: %s", str(exc)[:300])
        failed = Retrieval(query="", verdict=Verdict.NOT_IN_KB, error=f"search call failed: {exc}")
        return {"last_retrieval": failed.as_json()}
    for call in reply.tool_calls[:get_settings().kb_max_searches]:
        if call["name"] != tool.name:
            continue
        tool.invoke(call["args"])
        if sink[-1].error:
            # RAGent2's own model call sometimes fails; one more try usually gets through.
            sink.pop()
            tool.invoke(call["args"])
    log.info("search  next=%s searches=%d", state.get("next"), len(sink))
    return {"last_retrieval": merge(sink).as_json() if sink else None}


# Writing the reply.

def _is_json(text: str) -> bool:
    text = text.strip()
    if text.startswith("{"):
        return True
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False


def _problem(text: str, language: str) -> str | None:
    """What is wrong with the reply's language, or None."""
    return language_problem(text, language) or (
        CHINESE.search(text) and f"The reply contains Chinese characters; rewrite it in {NAMES[language]}.") or None


def _say(convo: list[BaseMessage], note: str | None = None) -> str:
    """Adds the note, if any, then one plain model reply to the conversation."""
    if note:
        convo.append(HumanMessage(content=NOTE + note))
    reply = _invoke(convo)
    convo.append(reply)
    return reply.text


def _write(convo: list[BaseMessage], language: str, corrections: list[str]) -> str:
    """A plain reply: written again once if it comes back as JSON, and up to LANGUAGE_RETRIES times for its language."""
    text = _say(convo)
    if _is_json(text):
        corrections.append("reply was JSON")
        text = _say(convo, "Write a plain chat message to the customer, not JSON.")
    for _ in range(LANGUAGE_RETRIES):
        problem = _problem(text, language)
        if not problem:
            return text
        corrections.append("language retried")
        text = _say(convo, problem)
    if problem := _problem(text, language):
        corrections.append(f"language: {problem}")
    return text


def _no_answer(convo: list[BaseMessage], language: str, corrections: list[str]) -> str:
    """The persona's own no-answer wording, in the customer's language."""
    convo.append(HumanMessage(content=NOTE + "You have no answer for this. Say so and offer a ticket."))
    return _write(convo, language, corrections)


def _ground(exchange: str, text: str, retrieval: Retrieval) -> Grounding:
    """exchange is the assistant's previous message and the customer's."""
    passages = "\n\n".join(f"Document: {e.document}\n{e.text.strip()}" for e in retrieval.direct)
    content = f"{exchange}\n\nReply:\n{text}\n\nPassages:\n{passages}"
    return llm.structured(prompt.ground(), [HumanMessage(content=content)], Grounding, node="ground")


def _answer(convo, retrieval: Retrieval, exchange: str, language: str, corrections: list[str],
            question: bool) -> tuple[str, list[str], str]:
    """The reply from DIRECT passages, with its sources and status.

    A reply that states what no passage says, or on a question turn uses no passage, is written once more. If it still
    fails, a question gets the no-answer wording and a ticket turn gets a reply with no product facts."""
    direct = list(dict.fromkeys(e.document for e in retrieval.direct))
    rule = HumanMessage(content=NOTE + STATUS.format(schema=llm.schema_text(Answer)))
    for attempt in (1, 2):
        try:
            answer = llm.structured(convo[0].content, [*convo[1:], rule], Answer, node="respond",
                                    check=lambda a: _problem(a.reply, language))
        except llm.GuardrailFailed as exc:
            answer = exc.result
            corrections.append(f"language: {exc}")
        convo.append(AIMessage(content=answer.reply))
        if question and answer.status == "no_answer":
            return answer.reply, [], "no_answer"
        try:
            grounding = _ground(exchange, answer.reply, retrieval)
        except llm.ModelFailed as exc:
            log.warning("respond_agent  grounding call failed: %s", str(exc)[:300])
            corrections.append("grounding call failed")
            return answer.reply, direct, answer.status
        used = [d for d in direct if any(d in u for u in grounding.used)]
        log.info("respond_agent  attempt=%d status=%s used=%s unsupported=%s", attempt, answer.status, used,
                 grounding.unsupported)
        if not grounding.unsupported and (used or not question):
            return answer.reply, used, answer.status
        if attempt == 1 and grounding.unsupported:
            convo.append(HumanMessage(content=NOTE + UNSUPPORTED.format("; ".join(grounding.unsupported))))
            corrections.append("retried: unsupported")
        elif attempt == 1:
            convo.append(HumanMessage(content=NOTE + UNUSED))
            corrections.append("retried: no passage used")
    corrections.append("reply not grounded")
    if question:
        return _no_answer(convo, language, corrections), [], "no_answer"
    convo.append(HumanMessage(content=NOTE + NO_FACTS))
    return _write(convo, language, corrections), [], "answered"


# The respond node.

def _exchange(messages: list[BaseMessage]) -> str:
    """The assistant's previous message and the customer's latest, for the grounding call."""
    customer = next((m.text for m in reversed(messages) if m.type == "human"), "")
    before = messages[:-1] if messages[-1].type == "human" else messages
    previous = next((m.text for m in reversed(before) if m.type == "ai"), "none")
    return f"Assistant's previous message:\n{previous}\n\nCustomer's message:\n{customer}"


def _convo(state: ChatState, language: str, retrieval: Retrieval | None) -> list[BaseMessage]:
    """System prompt, chat, then the system notes: Submit pressed, the passages, the task."""
    convo: list[BaseMessage] = [SystemMessage(content=prompt.respond(state, language)), *state["messages"]]
    if state["messages"][-1].type != "human":
        # A button turn adds no message; without this the model would continue the assistant's last reply.
        convo.append(HumanMessage(content=NOTE + "the customer pressed Submit."))
    if retrieval and not retrieval.error:
        convo.append(HumanMessage(content=NOTE + "The search for this message returned:\n" + retrieval.render()))
    ordinary = state.get("next") in (*QUESTIONS, "talk") and state["phase"] == "none"
    if state.get("task") and not ordinary:
        # The task goes last, where the model weighs it most. An ordinary chat turn goes by the general rules alone.
        convo.append(HumanMessage(content=NOTE + state["task"]))
    return convo


def respond_agent(state: ChatState) -> dict:
    question = state.get("next") in QUESTIONS
    retrieval = Retrieval.from_json(state["last_retrieval"]) if state.get("last_retrieval") else None
    customer = next((m.text for m in reversed(state["messages"]) if m.type == "human"), "")
    language = language_of(customer) or state["identity"].language
    convo = _convo(state, language, retrieval)
    corrections: list[str] = []
    sources, status = [], "answered"
    try:
        if retrieval and retrieval.direct:
            text, sources, status = _answer(convo, retrieval, _exchange(state["messages"]), language, corrections,
                                            question)
        elif question and retrieval and retrieval.error:
            text = SORRY[language]
            corrections.append("search failed")
        elif question:
            corrections.append("no DIRECT passage")
            text, status = _no_answer(convo, language, corrections), "no_answer"
        else:
            text = _write(convo, language, corrections)
    except llm.ModelFailed as exc:
        log.warning("respond_agent  model failed, sorry sent: %s", str(exc)[:300])
        text, sources, status = SORRY[language], [], "answered"
        corrections.append(f"respond failed: {exc}")
    
    log.info("respond_agent  next=%s status=%s sources=%s say=%r", state.get("next"), status, sources, text[:90])
    
    return {"reply": Reply(say=text, sources=sources), "corrections": state["corrections"] + corrections,
            "offered": question and status != "answered", "language": language}
