"""The voice routes: whether voice is on, and one WebSocket per conversation (protocol in VOICE_PLAN.md section 5).

Browser to server: {"type": "utterance", "over_bot", "ms"} followed by one binary frame, a 16 kHz mono WAV of what the
speech detector cut; {"type": "played", "id"} when an item finished playing; {"type": "stop"} (the Stop button);
{"type": "barge_in"}; {"type": "speech", "on"} when the speech detector starts hearing the customer, and off when that
was not speech or will not be sent; {"type": "submit"} (the Submit button in voice mode); {"type": "text", "text"}
(typed in voice mode); {"type": "latency", "gap_ms" or "longest_ms", "id"}, the silences the customer heard, for the
log; {"type": "mute", "on"} when the customer mutes or unmutes the microphone. Server to browser: ready, heard,
ignored, status, tts_start (with "whole" when the audio is complete, sent in 250 ms pieces) / binary PCM / tts_end,
stop_audio, view and error."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ole5_chat import sessions
from ole5_chat.config import get_settings
from ole5_chat.voice import stt, tts
from ole5_chat.voice.session import Backend, VoiceSession

log = logging.getLogger(__name__)
router = APIRouter()


async def _warm() -> None:
    await asyncio.gather(stt.warm(), tts.warm())


async def _touch() -> None:
    await asyncio.gather(stt.touch(), tts.touch())


@router.get("/chat/voice/config")
def voice_config() -> dict:
    s = get_settings()
    return {"enabled": bool(s.voice_enabled and s.groq_api_key), "sample_rate": 24000}


@router.websocket("/chat/sessions/{session_id}/voice")
async def voice(websocket: WebSocket, session_id: str) -> None:
    s = get_settings()
    if not (s.voice_enabled and s.groq_api_key):
        await websocket.close(code=4403, reason="Voice is off")
        return
    try:
        sessions.get(session_id)
    except sessions.SessionNotFound:
        await websocket.close(code=4404, reason="Session not found")
        return
    await websocket.accept()
    lock = asyncio.Lock()

    async def send_json(data: dict) -> None:
        async with lock:
            await websocket.send_text(json.dumps(data, ensure_ascii=False))

    async def send_bytes(data: bytes) -> None:
        async with lock:
            await websocket.send_bytes(data)

    session = VoiceSession(session_id, send_json, send_bytes, Backend(warm=_warm, touch=_touch))
    log.info("voice  connected session=%s", session_id[:8])
    pending: dict | None = None
    try:
        await session.start()
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if pending is not None:
                    session.utterance(pending, message["bytes"])
                pending = None
                continue
            try:
                data = json.loads(message.get("text") or "")
            except json.JSONDecodeError:
                continue
            kind = data.get("type") if isinstance(data, dict) else None
            if kind == "utterance":
                pending = data
            elif kind == "played":
                session.played(str(data.get("id")))
            elif kind == "stop":
                await session.stop()
            elif kind == "barge_in":
                await session.barge_in()
            elif kind == "speech":
                session.speech(bool(data.get("on")))
            elif kind == "submit":
                session.submit()
            elif kind == "text":
                session.typed(str(data.get("text") or "")[:4000])
            elif kind == "latency":
                session.latency(data)
            elif kind == "mute":
                session.mute(bool(data.get("on")))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await session.close()
        log.info("voice  closed session=%s", session_id[:8])
