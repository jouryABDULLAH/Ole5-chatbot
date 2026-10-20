"""Sign-in against customers.xlsx, value lists, chat.yaml, and the session API."""

import pytest
from fastapi.testclient import TestClient

from ole5_chat import products, sessions, sheets, values
from ole5_chat.api import app
from ole5_chat.identity import NotRegistered, lookup


def test_active_customer_signs_in():
    who = lookup("  OLE5@example.com ")
    assert (who.product, who.language, who.name) == ("OLE5", "ar", "Sara Omar")


@pytest.mark.parametrize("email", ["gone@example.com", "nobody@example.com", ""])
def test_inactive_or_unknown_email_is_refused(email):
    with pytest.raises(NotRegistered):
        lookup(email)


def test_sheet_is_reread_when_it_changes(sheets_dir):
    from openpyxl import load_workbook
    wb = load_workbook(sheets_dir / "customers.xlsx")
    wb.active.append(("new@example.com", "New", "Org", "OLE5", "en", "yes"))
    wb.save(sheets_dir / "customers.xlsx")
    assert lookup("new@example.com").email == "new@example.com"


def test_ole5_catalog():
    c = values.catalog("ole5")
    assert c.service == "Ole5"
    assert [q.value for q in c.queues] == ["Ole5 Support", "Ole5 Technical"]
    assert not c.has_reason_catalog
    assert [p.value for p in c.priorities] == ["Low", "Medium", "High", "Critical"]


def test_same_subtype_name_under_two_types():
    c = values.catalog("OLE5")
    assert [o.value for o in c.subtypes_of("Bug")] == ["Software malfunction"]
    assert "Software malfunction" in [o.value for o in c.subtypes_of("Mobile App Issue")]


def test_rich_reason_comes_from_catalog():
    c = values.catalog("RICH")
    assert c.has_reason_catalog
    assert len(c.subtypes) == 112
    assert c.reason_for("DLR - Not Delivered- STC") == "Use when a customer reports that messages are not being delivered"


def test_unknown_product():
    with pytest.raises(values.UnknownProduct):
        values.catalog("NOPE")


def test_products_differ_only_by_chat_yaml():
    ole5, rich = products.load("OLE5"), products.load("rich")
    assert ole5.greet("ar") != rich.greet("ar")
    assert ole5.always_ticket != rich.always_ticket
    assert ole5.greet("fr") == ole5.greeting["en"]


def test_ticket_row_is_appended():
    sheets.add_ticket({"ticket_id": "g1", "ticket_number": "T-1", "customer_email": "ole5@example.com",
                       "product": "OLE5", "title": "t", "created_at": "2026-10-19"})
    assert sheets.tickets()[-1]["ticket_number"] == "T-1"


def test_session_api_flow():
    client = TestClient(app)
    refused = client.post("/chat/sessions", json={"email": "gone@example.com"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "This email is not registered for support chat"

    start = client.post("/chat/sessions", json={"email": "ole5@example.com"}).json()
    assert start["phase"] == "greet"
    assert start["messages"] == [{"author": "bot", "text": products.load("OLE5").greet("ar")}]

    sid = start["session_id"]
    reply = client.post(f"/chat/sessions/{sid}/messages", json={"text": "hi"}).json()
    assert [m["author"] for m in reply["messages"]] == ["bot", "customer", "bot"]
    assert client.get(f"/chat/sessions/{sid}").json() == reply


def test_unknown_session():
    client = TestClient(app)
    assert client.get("/chat/sessions/nope").status_code == 404
    assert client.post("/chat/sessions/nope/messages", json={"text": "hi"}).status_code == 404


def test_identity_comes_from_the_session_not_the_body():
    client = TestClient(app)
    sid = client.post("/chat/sessions", json={"email": "ole5@example.com"}).json()["session_id"]
    extra = client.post(f"/chat/sessions/{sid}/messages", json={"text": "hi", "email": "rich@example.com"})
    assert extra.status_code == 422
    assert sessions.current_identity(sid).product == "OLE5"


def test_knowledge_document_is_served_rendered(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from ole5_chat.api import app
    from ole5_chat.config import get_settings
    (tmp_path / "kb" / "ole5").mkdir(parents=True)
    (tmp_path / "kb" / "ole5" / "نظام OLE5 — قاعدة المعرفة.md").write_text("# عنوان\n\n- بند", encoding="utf-8")
    monkeypatch.setenv("KB_EXPORT_DIR", str(tmp_path / "kb"))
    get_settings.cache_clear()
    client = TestClient(app)
    page = client.get("/chat/kb/OLE5/نظام OLE5 — قاعدة المعرفة")
    assert page.status_code == 200 and "<h1>عنوان</h1>" in page.text and "<li>بند</li>" in page.text
    assert client.get("/chat/kb/OLE5/missing").status_code == 404
    assert client.get("/chat/kb/OLE5/..%2F..%2Fetc").status_code == 404
