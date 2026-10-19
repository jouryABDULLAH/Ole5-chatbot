"""The process to run: the chat API, plus the web/ folder served as static files for local testing. The page only talks to the API over HTTP. Logs go to the terminal through rich; traces go to Phoenix when TRACING is on; RAGent2 loads its models in the background when KB_BACKEND is ragent2."""

import logging
import os
import threading

from fastapi.staticfiles import StaticFiles
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
        r"^(?P<node>greet|router|search|respond|classify|validate|llm)\b",
    ]


THEME = Theme({"repr.node": "bold magenta", "repr.key": "dim cyan", "repr.step": "bold green"})
handler = RichHandler(console=Console(theme=THEME), highlighter=LogHighlighter(), show_path=False,
                      rich_tracebacks=True, markup=False, log_time_format="[%X]")
logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[handler], force=True)
for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(name).handlers = [handler]
    logging.getLogger(name).propagate = False
logging.getLogger("httpx").setLevel(logging.WARNING)


def start_tracing() -> None:
    s = get_settings()
    if not s.tracing:
        return
    os.environ.setdefault("PHOENIX_COLLECTOR_ENDPOINT", s.phoenix_collector_endpoint)
    if s.phoenix_api_key and s.phoenix_api_key.get_secret_value():
        os.environ.setdefault("PHOENIX_API_KEY", s.phoenix_api_key.get_secret_value())
    from phoenix.otel import register
    register(project_name=s.phoenix_project_name, auto_instrument=True)
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


start_tracing()
warm_knowledge()

from ole5_chat.api import app

app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
