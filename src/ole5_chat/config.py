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
    kb_top_k: int = 15
    kb_max_rounds: int = 3
    kb_tool_repair_attempts: int = 2
    kb_embed_timeout: float = 120.0
    qdrant_timeout: float = 60.0
    kb_find_timeout: float = 260.0
    kb_find_retries: int = 1
    kb_max_searches: int = 4
    collect_rounds: int = 2

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

    # Voice channel (src/ole5_chat/voice). Speech goes to Groq with the same key as the model.
    voice_enabled: bool = True
    voice_stt_model: str = "whisper-large-v3-turbo"
    voice_short_clip_s: float = 1.8  # shorter clips (one word, with the detector's padding: 1.3-1.7 s): no hint, session language forced
    voice_tts_model_ar: str = "canopylabs/orpheus-arabic-saudi"
    voice_tts_voice_ar: str = "abdullah"  # male voices chosen by the user (D29)
    voice_tts_model_en: str = "canopylabs/orpheus-v1-english"
    voice_tts_voice_en: str = "troy"
    voice_model_timeout: float = 15.0
    voice_kb_max_searches: int = 1
    # A lighter RAGent2 profile for voice (D28): answerable questions came back in 20-28 s instead of 20-45 s.
    voice_kb_top_k: int = 8
    voice_kb_max_rounds: int = 2
    voice_kb_grading_concurrency: int = 8
    voice_kb_find_timeout: float = 60.0  # RAGent2's own limit: a search the voice turn gave up on stops soon after
    voice_search_timeout: float = 35.0  # past this the turn answers as for a question with no answer and offers a ticket
    voice_turn_deadline: float = 55.0
    voice_progress_every: float = 10.0  # a "still checking" line after this long without speech, twice at most
    voice_max_utterance_s: float = 60.0
    voice_cache_dir: Path = ROOT / ".voice_cache"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
