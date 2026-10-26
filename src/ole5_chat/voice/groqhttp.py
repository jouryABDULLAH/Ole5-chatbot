"""The HTTP client under the voice channel's Groq clients: connections kept open between utterances.

httpx closes an idle connection after 5 s, so every utterance paid new TLS handshakes: the two "parallel" Whisper
calls took about the sum of both (0.67-1.25 s, against 0.26-0.75 s each), and a reply's first sound after a 15 s
search took 1.1-1.3 s instead of 0.3 s. Kept open for 5 minutes: 0.41-0.71 s and 0.23-0.25 s after up to 45 s idle
(2026-10-26, voice-lab/latency_probe2.txt). HTTP/2 was no faster."""

import httpx
from groq import DefaultAsyncHttpxClient

KEEP_ALIVE_S = 300.0


def http_client() -> httpx.AsyncClient:
    return DefaultAsyncHttpxClient(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10,
                                                       keepalive_expiry=KEEP_ALIVE_S))
