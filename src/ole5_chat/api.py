"""HTTP API of the chat service: start a session with an email, send messages, attach files, read the conversation back, submit the ticket, serve the attached files and the knowledge base documents a source names, list the customer's tickets."""

from typing import Literal

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from ole5_chat import attachments, products, sessions, sheets
from ole5_chat.attachments import AttachmentRefused
from ole5_chat.config import get_settings
from ole5_chat.agent.contracts import Phase
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.identity import NotRegistered
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets.client import TicketApiError
from ole5_chat.tickets.submit import SubmitRefused

app = FastAPI(title="Ole5 chat", telemetry={"tracing": False})


class StartSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class NewMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=4000)
    attachment_ids: list[str] = []


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: Literal["customer", "bot"]
    text: str


class AttachmentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    file_name: str
    size: int


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    product: str
    product_name: str
    language: str
    phase: Phase
    messages: list[Line]
    sources: list[str]
    ticket_draft: TicketDraft | None = None
    draft_ready: bool = False
    ticket_number: str | None = None
    ticket_numbers: list[str] = []
    attachments: list[AttachmentView] = []


class TicketView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_number: str
    title: str
    product: str
    created_at: str
    state: str | None = None


def _line(message: BaseMessage) -> Line:
    return Line(author="customer" if message.type == "human" else "bot", text=message.text)


def _attachment(f: dict) -> AttachmentView:
    return AttachmentView(id=f["id"], file_name=f["file_name"], size=f["size"])


def _view(session_id: str, state: ChatState) -> SessionView:
    who, draft = state["identity"], state.get("ticket_draft")
    numbers = [t["ticket_number"] for t in state["created_tickets"]]
    return SessionView(session_id=session_id, product=who.product, product_name=products.load(who.product).name,
                       language=who.language,
                       phase=state["phase"], messages=[_line(m) for m in state["messages"]],
                       sources=state["sources"], ticket_draft=draft, draft_ready=state["phase"] == "confirm",
                       ticket_number=numbers[-1] if numbers else None, ticket_numbers=numbers,
                       attachments=[_attachment(f) for f in state.get("attachments") or []])



@app.post("/chat/sessions")
def start_session(body: StartSession) -> SessionView:
    try:
        session_id, state = sessions.start(body.email)
    except NotRegistered as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _view(session_id, state)


@app.get("/chat/sessions/{session_id}")
def read_session(session_id: str) -> SessionView:
    try:
        return _view(session_id, sessions.get(session_id))
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/chat/sessions/{session_id}/messages")
def send_message(session_id: str, body: NewMessage) -> SessionView:
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(status_code=422, detail="The message is empty")
    try:
        return _view(session_id, sessions.send(session_id, body.text.strip(), body.attachment_ids))
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except sessions.AttachmentNotFound as exc:
        raise HTTPException(status_code=404, detail="Attachment not found") from exc


@app.post("/chat/sessions/{session_id}/submit")
def submit(session_id: str) -> SessionView:
    try:
        return _view(session_id, sessions.submit(session_id))
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SubmitRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/chat/sessions/{session_id}/attachments")
async def upload_attachment(session_id: str, file: UploadFile) -> AttachmentView:
    limit = int(get_settings().attachment_max_mb * 1024 * 1024)
    data = await file.read(limit + 1)
    try:
        return _attachment(sessions.attach(session_id, file.filename or "file", data))
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except AttachmentRefused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/chat/sessions/{session_id}/attachments/{att_id}", status_code=204)
def delete_attachment(session_id: str, att_id: str) -> Response:
    try:
        removed = sessions.detach(session_id, att_id)
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(status_code=204)


@app.get("/chat/attachments/{att_id}")
def read_attachment(att_id: str) -> FileResponse:
    path = attachments.find(att_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(path, filename=attachments.file_name(path))


KB_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{max-width:860px;margin:40px auto;padding:0 20px;font-family:"IBM Plex Sans Arabic",system-ui,sans-serif;
line-height:1.8;color:#1E2A2C}}h1,h2,h3{{line-height:1.4}}code{{background:#EAF1EE;padding:1px 5px;border-radius:4px}}
table{{border-collapse:collapse}}td,th{{border:1px solid #DCE3E0;padding:6px 10px}}</style></head><body>{body}</body></html>"""


@app.get("/chat/kb/{product}/{title}", response_class=HTMLResponse)
def read_document(product: str, title: str) -> HTMLResponse:
    from markdown_it import MarkdownIt

    tenant = get_settings().kb_tenants.get(product.upper())
    folder = get_settings().kb_export_dir / tenant if tenant else None
    wanted = f"{title}.md"
    path = next((f for f in folder.iterdir() if f.name == wanted), None) if folder and folder.is_dir() else None
    if path is None:
        raise HTTPException(status_code=404, detail="Document not found")
    body = MarkdownIt().render(path.read_text(encoding="utf-8"))
    return HTMLResponse(KB_PAGE.format(title=title, body=body))


@app.get("/chat/tickets")
def my_tickets(session_id: str) -> list[TicketView]:
    try:
        email = sessions.current_identity(session_id).email.casefold()
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    api = ticket_api.ticket_client()
    rows = [r for r in sheets.tickets() if str(r.get("customer_email") or "").casefold() == email]
    views = []
    for r in rows:
        try:
            state = api.get(str(r["ticket_id"])).get("state")
        except TicketApiError:
            state = None
        views.append(TicketView(ticket_number=str(r["ticket_number"]), title=str(r.get("title") or ""),
                                product=str(r.get("product") or ""), created_at=str(r.get("created_at") or ""),
                                state=state))
    return views
