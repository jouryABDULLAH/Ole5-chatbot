"""The model, behind one small wrapper. Groq today; the model name is a setting. Every call returns a validated pydantic object."""

import json
import logging
import time
from typing import TypeVar

from groq import BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError

from ole5_chat.config import get_settings

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)
ATTEMPTS = 3


class ModelFailed(RuntimeError):
    """The model did not return a valid object after every attempt."""


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


def structured(system: str, messages: list[BaseMessage], schema: type[T], node: str = "llm") -> T:
    convo: list[BaseMessage] = [SystemMessage(content=system), *messages]
    error = "no attempt made"
    for attempt in range(ATTEMPTS):
        started = time.perf_counter()
        try:
            reply = _client(0 if attempt == 0 else 0.3).invoke(convo)
        except BadRequestError as exc:
            error = str(exc)[:400]
            log.warning("%s  llm schema=%s attempt=%d rejected by Groq: %s", node, schema.__name__, attempt + 1, error)
            continue
        ms = int((time.perf_counter() - started) * 1000)
        try:
            result = schema.model_validate_json(_clean(reply.text))
            log.info("%s  llm schema=%s attempt=%d ms=%d", node, schema.__name__, attempt + 1, ms)
            return result
        except ValidationError as exc:
            error = str(exc)[:400]
            log.warning("%s  llm schema=%s attempt=%d ms=%d invalid: %s", node, schema.__name__, attempt + 1, ms, error)
            convo += [AIMessage(content=reply.text),
                      HumanMessage(content=f"That was not valid: {error}. Reply with JSON only, matching the schema.")]
    raise ModelFailed(error)
