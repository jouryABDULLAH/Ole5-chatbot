"""The search node and the respond node.
search: on a question turn one model call writes the searches for the customer's message, and code runs them; on
details and change it searches only if the message also asks a question. Every other turn passes through.
respond_agent writes the reply with no tool. With DIRECT passages it answers from them with a status, and the
grounding call names the documents the reply draws on (those are the sources) and any statement no passage makes.
A question with no DIRECT passage gets the no-answer wording. Code, not the model, sets whether the reply offers a
ticket: only on a question turn, when the status is partial or no_answer. Then the language guardrails."""

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
# Ticket turns where the customer may also ask a question; the search is there if they do. An accept that asks
# something is routed to question.
SEARCHES = ("details", "change")
# Turns answered as a question: status has no handler of its own yet.
QUESTIONS = ("question", "status")
CHINESE = re.compile(r"[一-鿿㐀-䶿]")
STATUS = ("Reply with JSON only, matching this schema: {schema}\nreply is your message to the customer. status is "
          "answered when the passages answer what the customer asked, partial when they answer only part of it and the "
          "reply offers a ticket for the rest, no_answer when they don't answer it and the reply says so and offers a "
          "ticket. On a turn where the customer asked nothing, status is answered.")


class Answer(BaseModel):
    status: Literal["answered", "partial", "no_answer"]
    reply: str


class Grounding(BaseModel):
    used: list[str]
    unsupported: list[str]


def _invoke(convo: list[BaseMessage], tool=None, tool_choice: str | None = None) -> AIMessage:
    """One model call; the search node binds the tool, respond_agent never does."""
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
        if call["name"] == tool.name:
            tool.invoke(call["args"])
            if sink[-1].error:
                # RAGent2's own model call sometimes fails; one more try usually gets through.
                sink.pop()
                tool.invoke(call["args"])
    log.info("search  next=%s searches=%d", state.get("next"), len(sink))
    return {"last_retrieval": merge(sink).as_json() if sink else None}


def _is_json(text: str) -> bool:
    text = text.strip()
    if text.startswith("{"):
        return True
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False


def _problem(text: str, language: str) -> str | None:
    return language_problem(text, language) or (
        CHINESE.search(text) and f"The reply contains Chinese characters; rewrite it in {NAMES[language]}.") or None


def _write(convo: list[BaseMessage], language: str, corrections: list[str]) -> str:
    """A plain reply, retried once if it comes back as JSON and up to LANGUAGE_RETRIES times for its language."""
    json_retried = False
    for attempt in range(LANGUAGE_RETRIES + 2):
        reply = _invoke(convo)
        convo.append(reply)
        text = reply.text
        if _is_json(text) and not json_retried:
            json_retried = True
            convo.append(HumanMessage(content=NOTE + "Write a plain chat message to the customer, not JSON."))
            corrections.append("reply was JSON")
            continue
        problem = _problem(text, language)
        if not problem:
            return text
        if attempt >= LANGUAGE_RETRIES:
            corrections.append(f"language: {problem}")
            return text
        convo.append(HumanMessage(content=NOTE + problem))
        corrections.append("language retried")
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
    """The reply from DIRECT passages, its sources and its status. A reply with statements no passage makes is retried
    once, then becomes the no-answer wording. On a ticket turn the
    passages are only for a question asked on the side: the reply need not use them, and one that still states
    unsupported facts is written again without product facts."""
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
        if answer.status == "no_answer" and question:
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
        if attempt == 2:
            break
        if grounding.unsupported:
            convo.append(HumanMessage(content=NOTE + "Remove these from your reply; no passage states them: "
                                      + "; ".join(grounding.unsupported) + ". Keep only what the passages say."))
            corrections.append("retried: unsupported")
        else:
            convo.append(HumanMessage(content=NOTE + "Your reply draws on none of the passages. Write what they say "
                                      "about the customer's question, and nothing more."))
            corrections.append("retried: no passage used")
    corrections.append("reply not grounded")
    if not question:
        convo.append(HumanMessage(content=NOTE + "Write your reply again without stating anything about the product; "
                                  "do only what the task says."))
        return _write(convo, language, corrections), [], "answered"
    return _no_answer(convo, language, corrections), [], "no_answer"


def respond_agent(state: ChatState) -> dict:
    customer = next((m.text for m in reversed(state["messages"]) if m.type == "human"), "")
    before = state["messages"][:-1] if state["messages"][-1].type == "human" else state["messages"]
    previous = next((m.text for m in reversed(before) if m.type == "ai"), "none")
    exchange = f"Assistant's previous message:\n{previous}\n\nCustomer's message:\n{customer}"
    question = state.get("next") in QUESTIONS
    retrieval = Retrieval.from_json(state["last_retrieval"]) if state.get("last_retrieval") else None
    corrections: list[str] = []
    language = language_of(customer) or state["identity"].language
    convo: list[BaseMessage] = [SystemMessage(content=prompt.respond(state, language)), *state["messages"]]
    if state["messages"][-1].type != "human":
        # A button turn adds no message; without this the model would continue the assistant's last reply.
        convo.append(HumanMessage(content=NOTE + "the customer pressed Submit."))
    if retrieval and not retrieval.error:
        convo.append(HumanMessage(content=NOTE + "The search for this message returned:\n" + retrieval.render()))
    if state.get("task") and not (state.get("next") in (*QUESTIONS, "talk") and state["phase"] == "none"):
        # The task last, where the model weighs it most; only an ordinary chat turn goes by the general rules alone.
        convo.append(HumanMessage(content=NOTE + state["task"]))
    sources, status = [], "answered"
    try:
        if retrieval and retrieval.direct:
            text, sources, status = _answer(convo, retrieval, exchange, language, corrections, question)
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
    offered = question and status != "answered"
    log.info("respond_agent  next=%s status=%s sources=%s say=%r", state.get("next"), status, sources, text[:90])
    return {"reply": Reply(say=text, sources=sources), "corrections": state["corrections"] + corrections,
            "offered": offered, "language": language}
