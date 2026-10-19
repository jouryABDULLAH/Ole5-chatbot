"""The rules the model does not get to bend: no answer without a DIRECT passage, never-answered topics, and every ticket value from the lists."""

import re

from ole5_chat.agent.contracts import Classification
from ole5_chat.values import Catalog

NO_ANSWER = {
    "en": "I don't have an answer for this. Would you like me to open a ticket?",
    "ar": "ليس لدي إجابة على هذا. هل تريد أن أفتح لك تذكرة؟",
}
SORRY = {
    "en": "Sorry, something went wrong on our side. Please try again.",
    "ar": "عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى.",
}
REASON_MAX = 500


def match(options: list[str], value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    candidates = [value.strip(), re.split(r"\s+[—–-]\s+", value.strip(), maxsplit=1)[0].strip()]
    for candidate in candidates:
        for option in options:
            if candidate.casefold() == option.casefold():
                return option
    return None


def check_ticket(c: Classification, catalog: Catalog) -> tuple[dict, list[str]]:
    problems: list[str] = []

    def pick(field: str, options: list[str], value: str | None) -> str | None:
        found = match(options, value)
        if found is None:
            problems.append(f"{field}: {value!r} is not on the list")
        return found

    queue = pick("queue", [o.value for o in catalog.queues], c.queue)
    type_ = pick("type", [o.value for o in catalog.types], c.type)
    subtype = pick("subtype", [o.value for o in catalog.subtypes_of(type_)], c.subtype) if type_ else None
    if type_ is None and c.subtype:
        problems.append(f"subtype: {c.subtype!r} cannot be checked without a valid type")
    priority = pick("priority", [o.value for o in catalog.priorities], c.priority)

    if catalog.has_reason_catalog:
        reason = catalog.reason_for(subtype) if subtype else None
    else:
        reason = (c.reason or "").strip() or None
        if reason is None:
            problems.append("reason: empty")
        elif len(reason) > REASON_MAX:
            problems.append(f"reason: longer than {REASON_MAX} characters, cut")
            reason = reason[:REASON_MAX]

    urgent_reason = (c.urgent_reason or "").strip() or None
    if c.urgent and urgent_reason is None:
        problems.append("urgent_reason: missing for an urgent ticket")

    fields = {"queue": queue, "type": type_, "subtype": subtype, "reason": reason, "priority": priority,
              "urgent": c.urgent, "urgent_reason": urgent_reason if c.urgent else None, "title": c.title,
              "summary": c.summary, "confidence": c.confidence, "queue_reason": c.queue_reason}
    return fields, problems
