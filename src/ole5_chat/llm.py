"""The model, behind one small wrapper. Groq today; the model name is a setting. Every call returns a validated pydantic object; an invalid reply, or one a caller's check refuses, is sent back to the model with the reason, up to RETRIES times."""

import json
import logging
import time
from typing import Callable, TypeVar

from groq import BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from openinference.instrumentation.langchain import get_current_span
from pydantic import BaseModel, ValidationError

from ole5_chat.config import get_settings

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)
RETRIES = 2


class ModelFailed(RuntimeError):
    """The model did not return a valid object after every attempt."""


class GuardrailFailed(ModelFailed):
    """The model returned valid objects, but every one failed the check. result is the last one."""

    def __init__(self, problem: str, result):
        super().__init__(problem)
        self.result = result


def _client(temperature: float):
    s = get_settings()
    if not s.groq_api_key:
        raise ModelFailed("GROQ_API_KEY is not set")
    return ChatGroq(model=s.groq_model, api_key=s.groq_api_key.get_secret_value(),
                    temperature=temperature).bind(response_format={"type": "json_object"})


def schema_text(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)


def _clean(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def structured(system: str, messages: list[BaseMessage], schema: type[T], node: str = "llm",
               check: Callable[[T], str | None] | None = None) -> T:
    convo: list[BaseMessage] = [SystemMessage(content=system), *messages]
    error, last = "no attempt made", None
    for attempt in range(1, RETRIES + 2):
        started = time.perf_counter()
        try:
            reply = _client(0 if attempt == 1 else 0.3).invoke(convo)
        except BadRequestError as exc:
            error = str(exc)[:400]
            log.warning("%s  llm schema=%s attempt=%d rejected by Groq: %s", node, schema.__name__, attempt, error)
            continue
        ms = int((time.perf_counter() - started) * 1000)
        if (reasoning := reply.additional_kwargs.get("reasoning_content")) and (span := get_current_span()):
            span.set_attribute("llm.reasoning", reasoning)
        try:
            result = schema.model_validate_json(_clean(reply.text))
        except ValidationError as exc:
            error = str(exc)[:400]
            log.warning("%s  llm schema=%s attempt=%d ms=%d invalid: %s", node, schema.__name__, attempt, ms, error)
            convo += [AIMessage(content=reply.text),
                      HumanMessage(content=f"That was not valid: {error}. Reply with JSON only, matching the schema.")]
            continue
        problem = check(result) if check else None
        if problem is None:
            log.info("%s  llm schema=%s attempt=%d ms=%d", node, schema.__name__, attempt, ms)
            return result
        error, last = problem, result
        log.warning("%s  llm schema=%s attempt=%d ms=%d guardrail: %s", node, schema.__name__, attempt, ms, problem)
        convo += [AIMessage(content=reply.text), HumanMessage(content=f"Note from the system, not the customer: {problem}")]
    if last is not None:
        raise GuardrailFailed(error, last)
    raise ModelFailed(error)
