"""The model, behind one small wrapper. Groq today; the model name is a setting. Every call returns a validated pydantic object; an invalid reply, or one a caller's check refuses, is sent back to the model with the reason, up to RETRIES times."""

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, TypeVar

from groq import APIError, BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from openinference.instrumentation.langchain import get_current_span
from opentelemetry import trace as trace_api
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


_VOICE: ContextVar[bool] = ContextVar("ole5_voice_turn", default=False)


@contextmanager
def voice_turn(on: bool = True):
    """Marks the model calls of one voice turn: a short timeout, no transport retries, and a fresh first prompt line."""
    token = _VOICE.set(on)
    try:
        yield
    finally:
        _VOICE.reset(token)


def voice_active() -> bool:
    return _VOICE.get()


def prepare(convo: list[BaseMessage]) -> list[BaseMessage]:
    """The conversation as sent. On a voice turn the system prompt starts with a random line: requests whose start
    matches Groq's prompt cache were often seconds slower on gpt-oss-120b (measured 2026-10-25 and 2026-10-26)."""
    if not voice_active() or not convo or convo[0].type != "system":
        return convo
    return [SystemMessage(content=f"request {uuid.uuid4().hex}\n\n{convo[0].content}"), *convo[1:]]


def tool_client(temperature: float) -> ChatGroq:
    """The model without JSON mode, for a node that binds tools."""
    s = get_settings()
    if not s.groq_api_key:
        raise ModelFailed("GROQ_API_KEY is not set")
    limits = {"timeout": s.voice_model_timeout, "max_retries": 0} if voice_active() else {}
    return ChatGroq(model=s.groq_model, api_key=s.groq_api_key.get_secret_value(), temperature=temperature, **limits)


# Groq's codes for a generation it rejected: the model's own output is in failed_generation and it can correct itself.
GENERATION_REJECTED = ("tool_use_failed", "output_parse_failed", "json_validate_failed")


def _correction(exc: BadRequestError) -> list[BaseMessage]:
    """What to send back when Groq rejected the generation: the model's output and the reason, so it corrects itself.
    Empty for a rejection the model can't act on (a bad request, an unknown model, too long a context)."""
    body = exc.body.get("error") if isinstance(exc.body, dict) else None
    if not isinstance(body, dict) or body.get("code") not in GENERATION_REJECTED:
        return []
    return [AIMessage(content=body.get("failed_generation") or ""),
            HumanMessage(content=f"That was not valid: {body.get('message')}. Return only what the schema or the tool "
                                 "call requires.")]


def invoke(convo: list[BaseMessage], tool=None, tool_choice: str | None = None) -> AIMessage:
    """One plain model call. If Groq rejects the generation, it is retried warmer with the rejection sent back to the
    model. Only the search node binds a tool."""
    error, correction = "no attempt made", []
    for temperature in (0, 0.3):
        try:
            client = tool_client(temperature)
            return (client.bind_tools([tool], tool_choice=tool_choice) if tool else client).invoke(
                prepare([*convo, *correction]))
        except BadRequestError as exc:
            error, correction = str(exc)[:400], _correction(exc)
            log.warning("llm  rejected by Groq at temperature %s: %s", temperature, error)
        except APIError as exc:
            # A timeout, connection error, rate limit or Groq 5xx: no warmer retry, the caller's fallback answers.
            raise ModelFailed(f"Groq unavailable: {str(exc)[:300]}") from exc
    raise ModelFailed(error)


def _client(temperature: float):
    return tool_client(temperature).bind(response_format={"type": "json_object"})


def schema_text(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)


def _clean(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def _attempt_span(name: str):
    """A span under the current graph node for one model attempt. Phoenix's LangChain tracer ends the LLM span before any other callback runs, so the reasoning cannot go on that span and is recorded here instead."""
    parent = get_current_span() if get_settings().tracing else None
    return trace_api.get_tracer(__name__).start_as_current_span(
        name, context=trace_api.set_span_in_context(parent) if parent else None,
        attributes={"openinference.span.kind": "CHAIN"})


def structured(system: str, messages: list[BaseMessage], schema: type[T], node: str = "llm",
               check: Callable[[T], str | None] | None = None) -> T:
    convo: list[BaseMessage] = [SystemMessage(content=system), *messages]
    error, last = "no attempt made", None
    begun, attempt = time.perf_counter(), 0
    try:
        for attempt in range(1, RETRIES + 2):
            started = time.perf_counter()
            with _attempt_span(schema.__name__) as span:
                try:
                    reply = _client(0 if attempt == 1 else 0.3).invoke(prepare(convo), config={"run_name": node})
                except BadRequestError as exc:
                    error = str(exc)[:400]
                    log.warning("%s  llm schema=%s attempt=%d rejected by Groq: %s", node, schema.__name__, attempt, error)
                    convo += _correction(exc)
                    continue
                except APIError as exc:
                    # A timeout, connection error, rate limit or Groq 5xx: no retry here, the caller's fallback answers.
                    raise ModelFailed(f"Groq unavailable: {str(exc)[:300]}") from exc
                if reasoning := reply.additional_kwargs.get("reasoning_content"):
                    span.set_attribute("output.value", reasoning)
            ms = int((time.perf_counter() - started) * 1000)
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
    finally:
        total = int((time.perf_counter() - begun) * 1000)
        if attempt > 1 or total > 5000:
            log.warning("%s  llm slow call: attempts=%d total_ms=%d", node, attempt, total)
