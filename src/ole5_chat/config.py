"""Settings, read from the environment and .env. The only place configuration is defined."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", env_file=ROOT / ".env", env_file_encoding="utf-8")

    groq_api_key: SecretStr | None = None
    groq_api_base: str = "https://api.groq.com"
    groq_model: str = "openai/gpt-oss-120b"
    # groq_model: str = "minimaxai/minimax-m2.7"

    qdrant_url: str = "https://ds.t2.sa:443/ole5_agent/qdrant"
    
    qdrant_api_key: SecretStr | None = None
    qdrant_login_url: str = "https://ds.t2.sa/ole5_agent/api/login"
    qdrant_login_email: str | None = None
    qdrant_login_password: SecretStr | None = None
    qdrant_session_cookie: str = "ole5_session"
    kb_backend: Literal["stub", "ragent2"] = "stub"
    kb_retrieval_mode: Literal["agentic", "direct"] = "agentic"
    kb_tenants: dict[str, str] = {"OLE5": "ole5"}
    embedding_service_url: str = "https://ds.t2.sa/text_embed"
    kb_candidate_pool: int = 30
    kb_top_k: int = 15
    kb_max_rounds: int = 3
    kb_tool_repair_attempts: int = 2
    kb_embed_timeout: float = 120.0
    qdrant_timeout: float = 60.0
    kb_find_timeout: float = 260.0
    kb_find_retries: int = 1
    kb_max_searches: int = 2 #4
    collect_rounds: int = 3  # questions asked in collect at most; then the summary is shown with what is missing

    sheets_dir: Path = ROOT / "sheets"
    products_dir: Path = ROOT / "products"

    kb_export_dir: Path = ROOT / "kb_export"
    attachments_dir: Path = ROOT / "uploads"
    public_base_url: str = "http://localhost:8000"
    attachment_max_mb: float = 10.0
    attachment_max_files: int = 5

    tracing: bool = False
    phoenix_project_name: str = "ole5-chat"
    phoenix_collector_endpoint: str = "http://localhost:6006"
    phoenix_api_key: SecretStr | None = None

    ticket_api_url: str | None = None
    ticket_api_user: str | None = None
    ticket_api_password: SecretStr | None = None


_SEARCH_DEFAULTS: tuple[str, int] | None = None


def search_settings() -> tuple[str, int]:
    """Return the effective retrieval mode and result count."""
    global _SEARCH_DEFAULTS
    settings = get_settings()
    if _SEARCH_DEFAULTS is None:
        _SEARCH_DEFAULTS = (settings.kb_retrieval_mode, settings.kb_top_k)
    return settings.kb_retrieval_mode, settings.kb_top_k


def set_search_settings(mode: Literal["agentic", "direct"] | None = None, top_k: int | None = None) -> tuple[str, int]:
    """Apply a process-local UI override, or restore the values from settings when both are omitted."""
    global _SEARCH_DEFAULTS
    settings = get_settings()
    if _SEARCH_DEFAULTS is None:
        _SEARCH_DEFAULTS = (settings.kb_retrieval_mode, settings.kb_top_k)
    if mode is None and top_k is None:
        selected_mode, selected_top_k = _SEARCH_DEFAULTS
    else:
        selected_mode = mode or settings.kb_retrieval_mode
        selected_top_k = top_k if top_k is not None else (10 if selected_mode == "agentic" else 5)
    settings.kb_retrieval_mode = selected_mode
    settings.kb_top_k = selected_top_k
    return search_settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
