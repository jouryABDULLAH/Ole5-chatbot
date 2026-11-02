"""HTTP API of the chat service: start a session with an email, send messages, attach files, read the conversation back, submit the ticket, serve the attached files and the knowledge base documents a source names, list the customer's tickets."""

import re
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from ole5_chat import attachments, products
from ole5_chat.agent import session as agent_sessions
from ole5_chat.attachments import AttachmentRefused
from ole5_chat.config import get_settings, search_settings, set_search_settings
from ole5_chat.agent.state import AgentState
from ole5_chat.support_core.state import TicketDraft
from ole5_chat.identity import NotRegistered
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets import status as ticket_api_status
from ole5_chat.tickets.submit import SubmitRefused

app = FastAPI(title="Ole5 chat", telemetry={"tracing": False})

Phase = Literal["none", "offer_ticket", "collect", "confirm"]


class StartSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class NewMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=4000)
    attachment_ids: list[str] = []


class SearchSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["settings", "find", "agentic_fast", "search"]


@app.get("/chat/search-settings")
def get_search_settings():
    mode, top_k = search_settings()
    return {"mode": "find" if mode == "agentic" else "agentic_fast" if mode == "agentic_fast" else "search", "retrieval_mode": mode, "top_k": top_k}


@app.get("/chat/sessions/{session_id}/progress")
def session_progress(session_id: str):
    try:
        return _session_backend().progress(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


@app.get("/chat/sessions/{session_id}/progress/stream")
def session_progress_stream(session_id: str):
    try:
        backend = _session_backend()
        backend.get(session_id)
        events = backend.progress_stream(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")
    return StreamingResponse(events, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.post("/chat/search-settings")
def update_search_settings(update: SearchSettingsUpdate):
    if update.mode == "settings":
        mode, top_k = set_search_settings()
    else:
        retrieval_mode = {"find": "agentic", "agentic_fast": "agentic_fast", "search": "direct"}[update.mode]
        mode, top_k = set_search_settings(retrieval_mode)
    from ole5_chat.knowledge import retriever
    retriever.reset()
    return {"mode": "find" if mode == "agentic" else "agentic_fast" if mode == "agentic_fast" else "search", "retrieval_mode": mode, "top_k": top_k}


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: Literal["customer", "bot"]
    text: str
    sources: list[str] = []


class AttachmentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    file_name: str
    size: int


class CreatedTicket(BaseModel):
    """A ticket submitted in this session, as the card showed it; message_index is the reply that announced it."""
    model_config = ConfigDict(extra="forbid")

    ticket_number: str
    title: str | None = None
    summary: str | None = None
    title_customer: str | None = None
    summary_customer: str | None = None
    queue: str | None = None
    type: str | None = None
    subtype: str | None = None
    priority: str | None = None
    required: dict[str, str | None] = {}
    collected: dict[str, str] = {}
    unknown: list[str] = []
    collected_customer: dict[str, str] = {}
    unknown_customer: list[str] = []
    attachments: list[str] = []
    message_index: int


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    product: str
    product_name: str
    language: str
    phase: Phase
    messages: list[Line]
    ticket_draft: TicketDraft | None = None
    draft_ready: bool = False
    ticket_number: str | None = None
    ticket_numbers: list[str] = []
    created_tickets: list[CreatedTicket] = []
    attachments: list[AttachmentView] = []


class TicketView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_number: str
    title: str
    product: str
    created_at: str
    state: str | None = None


def _line(message: BaseMessage) -> Line:
    return Line(author="customer" if message.type == "human" else "bot", text=message.text,
                sources=message.additional_kwargs.get("sources", []))


def _attachment(f: dict) -> AttachmentView:
    return AttachmentView(id=f["id"], file_name=f["file_name"], size=f["size"])


def _session_backend():
    """Return the v2 session adapter used by every chat endpoint."""

    return agent_sessions


def _phase(state: dict) -> str:
    pending = state.get("pending_customer_response")
    if pending is not None:
        return {"offer": "offer_ticket", "detail": "collect", "summary": "confirm"}[pending.kind]
    return "none"


def _view(session_id: str, state: AgentState) -> SessionView:
    phase = _phase(state)
    raw_draft = state.get("ticket_draft")
    draft = None
    if raw_draft:
        fields = set(TicketDraft.model_fields)
        values = {key: value for key, value in raw_draft.items() if key in fields}
        values.setdefault("product", state["identity"].product)
        values.setdefault("missing", [])
        draft = TicketDraft(**values)

    who = state["identity"]
    created = []
    for ticket in state.get("created_tickets", []):
        # Persisted graph records can contain API/internal fields such as ticket_id and created_at.
        # Only expose the stable customer card contract; older checkpoints get a safe message index.
        fields = {key: value for key, value in ticket.items() if key in CreatedTicket.model_fields}
        fields.setdefault("message_index", max(len(state.get("messages", [])) - 1, 0))
        created.append(CreatedTicket(**fields))
    numbers = [t.ticket_number for t in created]
    return SessionView(session_id=session_id, product=who.product, product_name=products.load(who.product).name,
                       language=who.language,
                       phase=phase, messages=[_line(m) for m in state["messages"]],
                       ticket_draft=draft, draft_ready=phase == "confirm",
                       ticket_number=numbers[-1] if numbers else None, ticket_numbers=numbers, created_tickets=created,
                       attachments=[_attachment(f) for f in state.get("attachments") or []])



@app.post("/chat/sessions")
def start_session(body: StartSession) -> SessionView:
    try:
        session_id, state = _session_backend().start(body.email)
    except NotRegistered as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _view(session_id, state)


@app.get("/chat/sessions/{session_id}")
def read_session(session_id: str) -> SessionView:
    try:
        return _view(session_id, _session_backend().get(session_id))
    except _session_backend().SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/chat/sessions/{session_id}/messages")
def send_message(session_id: str, body: NewMessage) -> SessionView:
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(status_code=422, detail="The message is empty")
    try:
        return _view(session_id, _session_backend().send(session_id, body.text.strip(), body.attachment_ids))
    except _session_backend().SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except _session_backend().AttachmentNotFound as exc:
        raise HTTPException(status_code=404, detail="Attachment not found") from exc


@app.post("/chat/sessions/{session_id}/submit")
def submit(session_id: str) -> SessionView:
    try:
        return _view(session_id, _session_backend().submit(session_id))
    except _session_backend().SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SubmitRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/chat/sessions/{session_id}/attachments")
async def upload_attachment(session_id: str, file: UploadFile) -> AttachmentView:
    limit = int(get_settings().attachment_max_mb * 1024 * 1024)
    data = await file.read(limit + 1)
    try:
        return _attachment(_session_backend().attach(session_id, file.filename or "file", data))
    except _session_backend().SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except AttachmentRefused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/chat/sessions/{session_id}/attachments/{att_id}", status_code=204)
def delete_attachment(session_id: str, att_id: str) -> Response:
    try:
        removed = _session_backend().detach(session_id, att_id)
    except _session_backend().SessionNotFound as exc:
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
table{{border-collapse:collapse}}td,th{{border:1px solid #DCE3E0;padding:6px 10px}}
.chunk{{padding:4px 16px;margin:8px 0;border-radius:8px;border-inline-start:3px solid #0E5A4C;background:#EAF1EE}}
.chunk:nth-child(even){{background:#F3F6F5;border-inline-start-color:#9FBDB5}}</style></head><body>{body}</body></html>"""

# The export marks where each indexed chunk starts, on its own line. The page shows the chunks as tinted blocks
# instead of printing the marker, so a reader sees what the search returns as one passage.
CHUNK_MARK = re.compile(r"^_chunk_\d+_\s*$", re.MULTILINE)


def _documents(product: str) -> list:
    """The knowledge base documents of a product, as files; empty when the product has no export."""
    tenant = get_settings().kb_tenants.get(product.upper())
    folder = get_settings().kb_export_dir / tenant if tenant else None
    return sorted(folder.glob("*.md")) if folder and folder.is_dir() else []


@app.get("/chat/kb/{product}")
def list_documents(product: str) -> list[str]:
    """The titles of the documents the answers come from; each is a page under this path."""
    files = _documents(product)
    if not files:
        raise HTTPException(status_code=404, detail="No documents")
    return [f.stem for f in files]


@app.get("/chat/kb/{product}/{title}", response_class=HTMLResponse)
def read_document(product: str, title: str) -> HTMLResponse:
    from markdown_it import MarkdownIt

    path = next((f for f in _documents(product) if f.stem == title), None)
    if path is None:
        raise HTTPException(status_code=404, detail="Document not found")
    render = MarkdownIt().render
    head, *chunks = CHUNK_MARK.split(path.read_text(encoding="utf-8"))
    body = render(head) + "".join(f'<a id="chunk-{i}"></a><section class="chunk">{render(c)}</section>'
                                  for i, c in enumerate(chunks) if c.strip())
    return HTMLResponse(KB_PAGE.format(title=title, body=body))


@app.get("/chat/tickets")
def my_tickets(session_id: str) -> list[TicketView]:
    try:
        email = _session_backend().current_identity(session_id).email
    except _session_backend().SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return [TicketView(**t) for t in ticket_api_status.my_tickets(email, ticket_api.ticket_client())]




