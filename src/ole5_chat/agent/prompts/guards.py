"""Prompt builders for semantic response guardrails."""

from ole5_chat import llm
from ole5_chat.agent.contracts import Grounding, Language


def language_prompt() -> str:
    return f'''You read one text and say which language it is written in.

- "ar" when the text is written in Arabic.
- "en" when the text is written in English.
- "none" when it has no words: only numbers, symbols, or an empty text.

Judge by the language of the sentences, not by names in them: an Arabic sentence that contains English product, screen or button names stays Arabic. A short message in one language is that language, however short.

Reply with JSON only, matching this schema:
{llm.schema_text(Language)}'''


def grounding_prompt() -> str:
    return f'''Check whether the customer-facing reply is supported by the supplied passages.
Check product facts only. Greetings, courtesy, ordinary clarification questions and conditional
follow-up invitations do not need citations. Ticket-operation claims are validated separately
against actual workflow/API results, not product passages.
Return every unsupported fact, name, field, value, limit or step in `unsupported`. A fact applied to a different screen, symptom or situation is unsupported. A product restriction is unsupported unless the passages state it explicitly. Return an empty list when the reply is fully supported.

Return JSON only, matching this schema:
{llm.schema_text(Grounding)}'''

