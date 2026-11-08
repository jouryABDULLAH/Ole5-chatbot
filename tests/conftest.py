"""Test setup: a temporary sheets folder and uploads folder with known customers, the real values.xlsx, fresh caches, and a scripted model instead of Groq."""

import re
import shutil

import pytest
from langchain_core.messages import AIMessage
from openpyxl import Workbook

from ole5_chat import llm, products, sheets
from ole5_chat.agent.contracts import Language
from ole5_chat.agent.contracts import TicketActionCheck
from ole5_chat.tickets import client as ticket_api
from ole5_chat.config import ROOT, get_settings

CUSTOMERS = [
    ("email", "name", "organization", "product", "language", "active"),
    ("ole5@example.com", "Sara Omar", "Test Org", "OLE5", "ar", "yes"),
    ("rich@example.com", "Ahmed Ali", "Example Co", "RICH", "en", "yes"),
    ("gone@example.com", "Old User", "Test Org", "OLE5", "en", "no"),
]


@pytest.fixture(autouse=True)
def sheets_dir(tmp_path, monkeypatch):
    for name in ("values", "tickets", "attachments"):
        shutil.copy(ROOT / "sheets" / f"{name}.xlsx", tmp_path)
    wb = Workbook()
    for row in CUSTOMERS:
        wb.active.append(row)
    wb.save(tmp_path / "customers.xlsx")
    monkeypatch.setenv("SHEETS_DIR", str(tmp_path))
    monkeypatch.setenv("KB_BACKEND", "stub")
    monkeypatch.setenv("TRACING", "false")
    monkeypatch.setenv("TICKET_API_URL", "")
    monkeypatch.setenv("ATTACHMENTS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://chat.example.com")
    get_settings.cache_clear()
    sheets._rows.cache_clear()
    products.load.cache_clear()
    ticket_api.ticket_client.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


class ScriptedModel:
    """Returns queued objects per schema, in order, and records every call. Runs the caller's check like llm.structured does: a refused object is replaced by the next queued one, or GuardrailFailed when none is left. Language calls, when not scripted, are answered from the letters in the text."""

    def __init__(self):
        self.queue: dict[type, list] = {}
        self.calls: list[tuple[type, str]] = []
        self.problems: list[str] = []

    def add(self, *objects):
        for obj in objects:
            self.queue.setdefault(type(obj), []).append(obj)

    def __call__(self, system, messages, schema, node=None, check=None):
        self.calls.append((schema, system))
        if schema is TicketActionCheck and not self.queue.get(schema):
            return TicketActionCheck(violations=[])
        if schema is Language and not self.queue.get(schema):
            text = messages[-1].text
            return Language(language="ar" if re.search(r"[\u0600-\u06FF]", text) else "en" if re.search(r"[A-Za-z]", text) else "none")
        if not self.queue.get(schema):
            if schema.__name__.startswith("RouterDecision"):
                raise AssertionError(f"Unexpected Router call: nothing scripted for {schema.__name__}")
            raise llm.ModelFailed(f"nothing scripted for {schema.__name__}")
        result = self.queue[schema].pop(0)
        problem = check(result) if check else None
        if problem is not None:
            self.problems.append(problem)
            if self.queue.get(schema):
                return self(system, messages, schema, node, check)
            raise llm.GuardrailFailed(problem, result)
        return result


@pytest.fixture(autouse=True)
def model(monkeypatch):
    scripted = ScriptedModel()
    monkeypatch.setattr(llm, "structured", scripted)
    return scripted




class ScriptedToolModel:
    """Stands in for llm.tool_client in the Ticket Operator's tool agent: returns the queued tool calls in order, then a final message without calls."""

    def __init__(self):
        self.replies: list[AIMessage] = []
        self.bound: list[str] = []  # names of every tool the agent was given

    def call(self, name, **args):
        self.replies.append(AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{len(self.replies)}"}]))

    def __call__(self, temperature):
        return self

    def bind_tools(self, tools, **kwargs):
        self.bound += [tool.name for tool in tools]
        return self

    def fail(self, error: Exception):
        """The next model call raises error, as when Groq fails partway through the tool agent."""
        self.replies.append(error)

    def invoke(self, messages, *args, **kwargs):
        reply = self.replies.pop(0) if self.replies else AIMessage(content="Done.")
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def operator_model(monkeypatch):
    scripted = ScriptedToolModel()
    monkeypatch.setattr(llm, "tool_client", scripted)
    return scripted
