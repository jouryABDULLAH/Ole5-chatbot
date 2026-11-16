"""Keep required-detail display values and collected rows consistent with saved facts."""

from ole5_chat.support_core.state import TicketDraft

SKIPPED = {"unknown", "declined"}


def align_details(draft: TicketDraft, previous: TicketDraft | None = None,
                  edits: dict | None = None, labels: dict | None = None) -> TicketDraft:
    edits = edits or {}
    aliases = {label: key for key, languages in (labels or {}).items() for label in languages.values()}

    def canonical(mapping):
        return {aliases.get(key, key): value for key, value in mapping.items()}

    collected = canonical(draft.collected)
    customer = canonical(draft.collected_customer)
    old_customer = canonical(previous.collected_customer) if previous else {}
    display = {}
    for key, value in draft.required.items():
        if value is None or value in SKIPPED:
            collected.pop(key, None)
            customer.pop(key, None)
            continue
        shown = draft.required_customer.get(key) or customer.get(key) or value
        if previous and key not in edits and previous.required.get(key) == value:
            shown = previous.required_customer.get(key) or old_customer.get(key) or shown
        if isinstance(edits.get(key), str) and edits[key] not in SKIPPED:
            shown = edits[key]
        display[key] = shown
        collected[key] = value
        customer[key] = shown
    # Required details are represented once, using their current value, including after a clear or skip.
    unknown = [key for key in draft.unknown if aliases.get(key, key) not in draft.required]
    unknown += [key for key, value in draft.required.items() if value in SKIPPED]
    unknown_customer = [key for key in draft.unknown_customer if aliases.get(key, key) not in draft.required]
    return draft.model_copy(update={"required_customer": display, "collected": collected,
                                    "collected_customer": customer, "unknown": unknown,
                                    "unknown_customer": unknown_customer})
