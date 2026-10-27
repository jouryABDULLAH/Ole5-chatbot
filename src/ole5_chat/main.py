"""The process to run: the chat API, plus the web/ folder served as static files for local testing. The page only talks to the API over HTTP. Logs go to the terminal through rich; traces go to Phoenix when TRACING is on; RAGent2 loads its models in the background when KB_BACKEND is ragent2, and the voice channel's fixed lines are synthesized in the background."""

import logging
import mimetypes
import os
import threading

from fastapi.staticfiles import StaticFiles
from opentelemetry.sdk.trace.sampling import Decision, ParentBased, Sampler, SamplingResult
from rich.console import Console
from rich.highlighter import ReprHighlighter
from rich.logging import RichHandler
from rich.theme import Theme

from ole5_chat.config import ROOT, get_settings


class LogHighlighter(ReprHighlighter):
    highlights = [
        *ReprHighlighter.highlights,
        r"(?P<key>\b[a-z_]+)=",
        r"step=(?P<step>[a-z_]+)",
        r"^(?P<node>message|greet|router|handlers|respond_agent|post_check|classify|validate|tickets|llm)\b",
    ]


THEME = Theme({"repr.node": "bold magenta", "repr.key": "dim cyan", "repr.step": "bold green"})
handler = RichHandler(console=Console(theme=THEME), highlighter=LogHighlighter(), show_path=False,
                      rich_tracebacks=True, markup=False, log_time_format="[%X]")
logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[handler], force=True)
for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(name).handlers = [handler]
    logging.getLogger(name).propagate = False
logging.getLogger("httpx").setLevel(logging.WARNING)


class SessionsOnly(Sampler):
    """Keeps a root span only when it starts inside using_session(). RAGent2 grades passages in plain worker threads, which inherit neither the session nor suppress_tracing, so their model calls would otherwise start traces of their own."""

    def should_sample(self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
        if attributes and attributes.get("session.id"):
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)
        return SamplingResult(Decision.DROP, None, trace_state)

    def get_description(self) -> str:
        return "SessionsOnly"


def start_tracing() -> None:
    s = get_settings()
    if not s.tracing:
        return
    os.environ.setdefault("PHOENIX_COLLECTOR_ENDPOINT", s.phoenix_collector_endpoint)
    if s.phoenix_api_key and s.phoenix_api_key.get_secret_value():
        os.environ.setdefault("PHOENIX_API_KEY", s.phoenix_api_key.get_secret_value())
    from phoenix.otel import register
    register(project_name=s.phoenix_project_name, auto_instrument=True, sampler=ParentBased(root=SessionsOnly()))
    logging.getLogger(__name__).info("tracing  project=%s endpoint=%s", s.phoenix_project_name,
                                     os.environ["PHOENIX_COLLECTOR_ENDPOINT"])


def warm_knowledge() -> None:
    s = get_settings()
    if s.kb_backend != "ragent2":
        return
    log = logging.getLogger(__name__)

    def load() -> None:
        from ole5_chat.knowledge import retriever
        try:
            retriever.warm(sorted(set(s.kb_tenants.values())))
            log.info("kb  ready tenants=%s", sorted(set(s.kb_tenants.values())))
        except Exception:
            log.exception("kb  warm-up failed")

    log.info("kb  loading models in the background")
    threading.Thread(target=load, daemon=True).start()


def warm_voice() -> None:
    """The fixed voice lines synthesized and saved to disk in the background, so the first ones play at once."""
    s = get_settings()
    if s.voice_enabled and s.groq_api_key:
        from ole5_chat.voice import fillers
        fillers.warm_in_background()


start_tracing()
warm_knowledge()
warm_voice()

from ole5_chat.api import app

# The speech detector in web/vendor: the browser refuses a module or worklet script not served as JavaScript, and
# Windows can map .mjs to text/plain.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/octet-stream", ".onnx")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
