"""Test setup: a temporary sheets folder and uploads folder with known customers, the real values.xlsx, fresh caches, and a scripted model instead of Groq."""

import re
import shutil

import pytest
from openpyxl import Workbook

from ole5_chat import llm, products, sheets
from ole5_chat.agent.validate import Language
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
        if schema is Language and not self.queue.get(schema):
            text = messages[-1].text
            return Language(language="ar" if re.search(r"[\u0600-\u06FF]", text) else "en" if re.search(r"[A-Za-z]", text) else "none")
        if not self.queue.get(schema):
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
