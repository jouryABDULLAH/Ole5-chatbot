"""A correction sent back to the model must not read as the customer's latest message."""
from types import SimpleNamespace
from unittest.mock import patch

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
