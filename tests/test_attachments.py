"""Attachments: upload, the type, size and count limits, removal, the link that serves the file, and Submit writing the links into the internal note and attachments.xlsx."""

from fastapi.testclient import TestClient

from ole5_chat import sessions, sheets
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import RouterDecision
from ole5_chat.agent.state import TicketDraft
from ole5_chat.api import app
from ole5_chat.config import get_settings
from ole5_chat.tickets import client as ticket_api

DRAFT = dict(product="Ole5", queue="Ole5 Technical", type="Mobile App Issue", subtype="Application crash",
             reason="Issue - the app crashes on the Inbox screen", priority="Medium", title="App crashes on Inbox",
             summary="The mobile app crashes on the Inbox screen.", classified=True)

client = TestClient(app)


def session():
    sid, _ = sessions.start("ole5@example.com")
    return sid


def send_file(sid, name="screen.png", data=b"png-bytes"):
    return client.post(f"/chat/sessions/{sid}/attachments", files={"file": (name, data)})


def test_upload_is_listed_and_served_by_its_link():
    sid = session()
    added = send_file(sid).json()
    assert added["file_name"] == "screen.png" and added["size"] == 9
    assert client.get(f"/chat/sessions/{sid}").json()["attachments"] == [added]
    served = client.get(f"/chat/attachments/{added['id']}")
    assert served.status_code == 200 and served.content == b"png-bytes"


def test_type_size_and_count_limits(monkeypatch):
    sid = session()
    assert send_file(sid, "run.exe").status_code == 415
    monkeypatch.setenv("ATTACHMENT_MAX_MB", "0.000005")
    monkeypatch.setenv("ATTACHMENT_MAX_FILES", "1")
    get_settings.cache_clear()
    assert send_file(sid, data=b"x" * 10).status_code == 413
    assert send_file(sid, data=b"x").status_code == 200
    assert send_file(sid, data=b"y").status_code == 409


def test_removed_file_is_gone():
    sid = session()
    att = send_file(sid).json()
    assert client.delete(f"/chat/sessions/{sid}/attachments/{att['id']}").status_code == 204
    assert client.get(f"/chat/sessions/{sid}").json()["attachments"] == []
    assert client.get(f"/chat/attachments/{att['id']}").status_code == 404


def test_unknown_session_and_bad_link_are_404():
    assert send_file("nope").status_code == 404
    assert client.get("/chat/attachments/../../etc").status_code == 404


def test_file_names_reach_the_agent():
    sid = session()
    send_file(sid, "error.png")
    assert "Files the customer attached for the ticket: error.png" in prompt.ticket_status(
        None, sessions.get(sid)["attachments"])


def test_submit_links_the_files_in_the_note_and_the_sheet():
    sid = session()
    att = send_file(sid).json()
    sessions._graph.update_state(sessions._config(sid), {
        "phase": "confirm", "ticket_draft": TicketDraft(**DRAFT),
        "decision": RouterDecision(intent="ticket_reply", language="en"),
    }, as_node="validate")
    view = client.post(f"/chat/sessions/{sid}/submit").json()
    link = f"https://chat.example.com/chat/attachments/{att['id']}"
    note = ticket_api.ticket_client().calls[-1][1]["Article"]["Body"]
    assert note.splitlines()[-2:] == ["Attachments:", f"screen.png: {link}"]
    row = sheets._read("attachments")[-1]
    assert (row["ticket_number"], row["file_name"], row["link"]) == (view["ticket_number"], "screen.png", link)
    assert view["attachments"] == []
