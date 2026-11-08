"""A correction sent back to the model must not read as the customer's latest message."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from ole5_chat import llm

real_structured = llm.structured  # bound at import, before conftest's autouse fixture replaces it with the script


class Answer(BaseModel):
    value: str


def test_groq_rejection_is_labelled_as_a_system_note():
    rejected = SimpleNamespace(body={"error": {"code": "output_parse_failed", "failed_generation": "",
                                               "message": "Parsing failed."}})
    messages = llm._correction(rejected)
    assert messages[-1].content.startswith(llm.NOTE)
    assert "Parsing failed." in messages[-1].content


def test_invalid_json_retry_is_labelled_and_the_customer_message_stays_last_human():
    sent = []

    class Client:
        def invoke(self, convo, config=None):
            sent.append(list(convo))
            return AIMessage(content="not json" if len(sent) == 1 else '{"value": "ok"}')

    with patch.object(llm, "_client", lambda temperature: Client()):
        result = real_structured("system", [HumanMessage(content="customer text")], Answer)
    assert result.value == "ok"
    retry = sent[1]
    assert retry[-1].content.startswith(llm.NOTE)
    assert [m.content for m in retry if m.type == "human" and not m.content.startswith(llm.NOTE)] == ["customer text"]


def _connection_error():
    import httpx
    from groq import APIConnectionError
    return APIConnectionError(request=httpx.Request("POST", "https://api.groq.com"))


def test_groq_error_after_client_retries_is_model_failed_without_a_correction():
    sent = []

    class Client:
        def invoke(self, convo, config=None):
            sent.append(list(convo))
            raise _connection_error()

    with patch.object(llm, "_client", lambda temperature: Client()), pytest.raises(llm.ModelFailed):
        real_structured("system", [HumanMessage(content="customer text")], Answer)
    assert len(sent) == 1  # the Groq client already retried; nothing goes back to the model


def test_plain_call_groq_error_is_model_failed():
    class Client:
        def invoke(self, convo):
            raise _connection_error()

    with patch.object(llm, "tool_client", lambda temperature: Client()), pytest.raises(llm.ModelFailed):
        llm.invoke([HumanMessage(content="customer text")])


def test_groq_rejection_is_still_sent_back_to_the_model():
    import httpx
    from groq import BadRequestError
    sent = []
    body = {"error": {"code": "json_validate_failed", "failed_generation": "{bad", "message": "Invalid JSON."}}

    class Client:
        def invoke(self, convo, config=None):
            sent.append(list(convo))
            if len(sent) == 1:
                raise BadRequestError("rejected", response=httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com")), body=body)
            return AIMessage(content='{"value": "ok"}')

    with patch.object(llm, "_client", lambda temperature: Client()):
        assert real_structured("system", [HumanMessage(content="customer text")], Answer).value == "ok"
    assert "Invalid JSON." in sent[1][-1].content
