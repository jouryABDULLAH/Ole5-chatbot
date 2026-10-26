"""The voice WebSocket end to end: the real route and graph with scripted models, and fake speech in and out. The
greeting is spoken on connect, an utterance comes back as heard → turn → view → spoken reply, and a socket for an
unknown session or with voice off is refused."""

import io
import json
import wave

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from ole5_chat import llm, products, sessions
from ole5_chat.agent.contracts import route_schema
from ole5_chat.agent.validate import script_language
from ole5_chat.api import app
from ole5_chat.config import get_settings
from ole5_chat.voice import api as voice_api, session as voice_session
from ole5_chat.voice.session import Backend
from ole5_chat.voice.stt import Heard
from test_agent import ToolModel


def wav_bytes(seconds=0.8) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(seconds * 16000))
    return out.getvalue()


@pytest.fixture
def voice(monkeypatch, model):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    heard: list[str] = []

    async def transcribe(wav, language):
        text = heard.pop(0)
        return Heard(text=text, language=script_language(text), raw=text, seconds=0.8, ms=1, forced=None)

    async def speech(text, language):
        yield b"\x01\x00" * 960

    async def cached(text, language):
        return b"\x02\x00" * 960

    # warm is left out: tests never touch the network.
    monkeypatch.setattr(voice_api, "Backend", lambda **_: Backend(transcribe=transcribe, speech=speech, cached=cached))
    fake = ToolModel()
    monkeypatch.setattr(llm, "tool_client", fake)
    voice_session.GREETED.clear()
    return heard, fake


def read_until(ws, done):
    """Messages until done(message) is true for a JSON one; audio frames come back as "audio"."""
    seen = []
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            seen.append("audio")
            continue
        data = json.loads(message["text"])
        seen.append(data)
        if done(data):
            return seen


def kinds(seen):
    return [m if m == "audio" else m["type"] for m in seen]


def test_the_greeting_then_a_spoken_message_round_trip(voice, model):
    heard, fake = voice
    sid, _ = sessions.start("rich@example.com")
    with TestClient(app).websocket_connect(f"/chat/sessions/{sid}/voice") as ws:
        seen = read_until(ws, lambda m: m["type"] == "tts_end")
        assert seen[0] == {"type": "ready", "language": "en"}
        assert seen[1]["type"] == "tts_start" and seen[1]["text"] == products.load("RICH").greet("en")
        ws.send_json({"type": "played", "id": seen[1]["id"]})

        model.add(route_schema("none")(next="talk"))
        fake.replies.append(AIMessage(content="Hello. How can I help you today?"))
        heard.append("Hello there, good morning")
        ws.send_json({"type": "utterance", "over_bot": False, "ms": 800})
        ws.send_bytes(wav_bytes())
        seen = read_until(ws, lambda m: m["type"] == "tts_end")

    # The turn ends (listening again) just before its reply starts playing; the page shows "speaking" meanwhile.
    assert kinds(seen) == ["heard", "turn", "status", "view", "status", "tts_start", "audio", "tts_end"]
    assert [m["stage"] for m in seen if m != "audio" and m["type"] == "status"] == ["thinking", "listening"]
    assert seen[0]["text"] == "Hello there, good morning" and seen[0]["language"] == "en"
    view = seen[3]["session"]
    assert view["messages"][-2:] == [{"author": "customer", "text": "Hello there, good morning"},
                                     {"author": "bot", "text": "Hello. How can I help you today?"}]
    assert seen[5]["text"] == "Hello. How can I help you today?" and seen[5]["kind"] == "reply"
    state = sessions.get(sid)
    assert state["channel"] == "voice" and state["heard_language"] == "en"


def test_a_socket_for_an_unknown_session_is_refused(voice):
    with pytest.raises(WebSocketDisconnect) as refused:
        with TestClient(app).websocket_connect("/chat/sessions/nope/voice"):
            pass
    assert refused.value.code == 4404


def test_voice_off_refuses_the_socket_and_says_so(voice, monkeypatch):
    sid, _ = sessions.start("rich@example.com")
    client = TestClient(app)
    assert client.get("/chat/voice/config").json()["enabled"] is True
    monkeypatch.setenv("VOICE_ENABLED", "false")
    get_settings.cache_clear()
    assert client.get("/chat/voice/config").json()["enabled"] is False
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"/chat/sessions/{sid}/voice"):
            pass
    assert refused.value.code == 4403
