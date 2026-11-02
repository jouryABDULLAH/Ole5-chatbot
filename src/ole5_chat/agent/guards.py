"""Response guardrails owned by the v2 graph."""

import re

from langchain_core.messages import HumanMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import Language
from ole5_chat.agent.prompts.guards import language_prompt

NAMES = {"ar": "Arabic", "en": "English"}


def language_of(text: str) -> str | None:
    try:
        found = llm.structured(language_prompt(), [HumanMessage(content=text)], Language, node="language").language
    except llm.ModelFailed:
        return None
    return None if found == "none" else found


def language_problem(text: str, language: str) -> str | None:
    found = language_of(text)
    if found is None or found == language:
        return None
    return f"The reply is in {NAMES[found]}. The customer wrote in {NAMES[language]}. Write the same reply in {NAMES[language]}."


def plain(text: str) -> str:
    text = re.sub(r"\*\*|__|`", "", text)
    return re.sub(r"(?m)^[ \t]*(#+|-)[ \t]+", "", text)

