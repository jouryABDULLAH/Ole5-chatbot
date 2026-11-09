"""Prompt builders for semantic response guardrails."""

from ole5_chat import llm
from ole5_chat.agent.contracts import Grounding, Language


def language_prompt() -> str:
    return f'''You read one text and say which language it is written in.

- "ar" when the text is written in Arabic.
- "en" when the text is written in English.
- "none" when it has no words: only numbers, symbols, or an empty text.
- "none" when it is only a value: a name, username, email, code, number, file or browser name, or a quoted error
  text ("ahmad123", "E403", "Google Chrome", "Access denied").

Judge by the language of the sentences, not by names in them: an Arabic sentence that contains English product, screen or button names stays Arabic. A short message in one language is that language, however short.

Reply with JSON only, matching this schema:
{llm.schema_text(Language)}'''


def grounding_prompt() -> str:
    return f'''You check one customer-facing reply for made-up product facts: anything it states about the product that
the supplied passages and company facts do not say.

Judge only the sentences that state product facts in answer to the customer: steps, screens, fields, buttons, names,
values, limits, permissions, causes and fixes. Do not judge:
- greetings, courtesy, and restating or naming the customer's question or request;
- saying a solution or an answer could not be found, or that part of the question is not covered: «لم أجد…»,
  «لا توجد معلومات…» are never unsupported, unless the passages do give what the sentence says is missing;
- reminders, offers, detail questions, ticket numbers, statuses and records from the ticket workflow, and other
  ticket-workflow sentences; they are checked separately;
- company and support facts that match the company facts given;
- sentences that state this turn's ticket workflow facts: a pending step, a request by its name, a reminder that a
  request still waits.

A sentence is supported when a passage states it with the same meaning, in any wording or language. DIRECT and
INFERENTIAL passages both count. Passages the reply does not use are fine; it need not use them all.
A sentence is unsupported when:
- no passage states it;
- it changes a name, value, step or order that a passage gives;
- it applies a passage's fact to a different screen, symptom or situation;
- it adds a cause, fix or condition the passages do not give;
- it says something is impossible, unsupported or limited when no passage says so.

Report each unsupported sentence once. The reply's author rewrites only the sentences you report and keeps the rest.

Return JSON only, matching this schema:
{llm.schema_text(Grounding)}'''

