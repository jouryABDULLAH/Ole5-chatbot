"""Validate ticket classification values against the configured catalog."""

import re

from ole5_chat.support_core.contracts import Classification
from ole5_chat.values import Catalog

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
    """Normalize and validate model-selected ticket fields."""
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
              "summary": c.summary, "title_customer": c.title_customer, "summary_customer": c.summary_customer,
              "confidence": c.confidence, "queue_reason": c.queue_reason}
    return fields, problems
