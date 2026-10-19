"""Per-product chat settings from products/<code>/chat.yaml. Adding a product means adding a folder."""

from functools import lru_cache

import yaml
from pydantic import BaseModel, ConfigDict

from ole5_chat.config import get_settings


class ProductConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    greeting: dict[str, str]
    tone: str
    always_ticket: list[str] = []
    urgent_when: list[str] = []
    ask_before_ticket: dict[str, list[str]] = {}
    team_scopes: str | None = None

    def greet(self, language: str) -> str:
        return self.greeting.get(language) or self.greeting["en"]


@lru_cache(maxsize=16)
def load(code: str) -> ProductConfig:
    """
    Returns the settings for one product. Based on the product's chat.yaml.
    """
    
    folder = get_settings().products_dir / code.lower()
    data = yaml.safe_load((folder / "chat.yaml").read_text(encoding="utf-8"))
    scopes = folder / "team-scopes.md"
    return ProductConfig(
        code=code.upper(),
        team_scopes=scopes.read_text(encoding="utf-8") if scopes.exists() else None,
        **data,
    )
