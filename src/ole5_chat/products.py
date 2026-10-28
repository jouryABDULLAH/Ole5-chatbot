"""Per-product chat settings from products/<code>/chat.yaml: the persona (who the assistant is, how it introduces itself, greets, answers and sounds), the never-answered and urgent topics, and what to collect per kind of problem. Adding a product means adding a folder."""

from functools import lru_cache

import yaml
from pydantic import BaseModel, ConfigDict

from ole5_chat.config import get_settings


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    identity: str
    introduction: dict[str, str]
    greetings: list[str] = []
    questions: list[str] = []
    voice: list[str] = []


class ProductConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    persona: Persona
    always_ticket: list[str] = []
    urgent_when: list[str] = []
    # Per kind of problem, the information only the customer can give and the team cannot act without.
    required: dict[str, list[str]] = {}
    team_scopes: str | None = None

    def greet(self, language: str) -> str:
        return self.persona.introduction.get(language) or self.persona.introduction["en"]


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
