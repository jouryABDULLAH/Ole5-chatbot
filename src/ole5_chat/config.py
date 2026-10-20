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
    kb_tenants: dict[str, str] = {"OLE5": "ole5"}
    embedding_service_url: str = "https://ds.t2.sa/text_embed"
    kb_candidate_pool: int = 30
    kb_top_k: int = 10
    kb_max_rounds: int = 3
    kb_tool_repair_attempts: int = 2
    kb_embed_timeout: float = 60.0
    kb_find_timeout: float = 120.0

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
