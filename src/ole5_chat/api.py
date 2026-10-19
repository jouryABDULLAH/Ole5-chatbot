"""HTTP API of the chat service: start a session with an email, send messages, read the conversation back, submit the ticket, list the customer's tickets."""

from typing import Literal

from fastapi import FastAPI, HTTPException
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from ole5_chat import products, sessions, sheets
from ole5_chat.agent.state import ChatState, Step, TicketDraft
from ole5_chat.identity import NotRegistered
from ole5_chat.tickets import client as ticket_api
from ole5_chat.tickets.client import TicketApiError
from ole5_chat.tickets.submit import SubmitRefused

app = FastAPI(title="Ole5 chat")


class StartSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class NewMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: Literal["customer", "bot"]
    text: str


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    product_name: str
    language: str
    phase: Step
    messages: list[Line]
    sources: list[str]
    ticket_draft: TicketDraft | None = None
    ticket_number: str | None = None


class TicketView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_number: str
    title: str
    product: str
    created_at: str
    state: str | None = None


def _line(message: BaseMessage) -> Line:
    return Line(author="customer" if message.type == "human" else "bot", text=message.text)


def _view(session_id: str, state: ChatState) -> SessionView:
    who = state["identity"]
    return SessionView(session_id=session_id, product_name=products.load(who.product).name, language=who.language,
                       phase=state["phase"], messages=[_line(m) for m in state["messages"]],
                       sources=state["sources"], ticket_draft=state.get("ticket_draft"),
                       ticket_number=(state.get("created_ticket") or {}).get("ticket_number"))



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
    try:
        return _view(session_id, sessions.send(session_id, body.text))
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/chat/sessions/{session_id}/submit")
def submit(session_id: str) -> SessionView:
    try:
        return _view(session_id, sessions.submit(session_id))
    except sessions.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SubmitRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TicketApiError as exc:
        raise HTTPException(status_code=502, detail="The ticket could not be created. Try again.") from exc


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
