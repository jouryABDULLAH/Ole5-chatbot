"""Build the structured ticket-classification prompt from the product catalog."""

from functools import lru_cache

from ole5_chat import products, values
from ole5_chat.config import ROOT
from ole5_chat.llm import schema_text
from ole5_chat.support_core.contracts import Classification
from ole5_chat.support_core.state import ChatState


@lru_cache(maxsize=4)
def _template() -> str:
    return (ROOT / "prompts" / "chat" / "classify.md").read_text(encoding="utf-8")


def _values_block(catalog: values.Catalog) -> str:
    lines = ["## Values", "", "### queue", ""]
    lines += [f"  {o.value}" + (f": {o.description}" if o.description else "") for o in catalog.queues]
    lines += ["", "### type, then subtype under it", ""]
    for ticket_type in catalog.types:
        lines.append(f"  {ticket_type.value}")
        for subtype in catalog.subtypes_of(ticket_type.value):
            reason = catalog.reason_for(subtype.value)
            lines.append(f"    - {subtype.value}" + (f": {reason}" if reason else ""))
    lines += ["", "### priority", ""]
    lines += [f"  {o.value}" for o in catalog.priorities]
    return "\n".join(lines) + "\n"


def classify(state: ChatState) -> str:
    """Render the prompt used by the shared ticket classifier."""
    identity = state["identity"]
    config = products.load(identity.product)
    catalog = values.catalog(identity.product)
    pending = state.get("pending") or []
    reason_rule = (
        "leave it empty: it is the catalog text of the chosen subtype and is filled in for you."
        if catalog.has_reason_catalog
        else "free text in English, up to 500 characters: a category, a dash, then a description."
    )
    scopes = f"## Team scopes\n\n{config.team_scopes}\n" if config.team_scopes else ""
    kinds = "\n".join(f"  {kind}: {', '.join(items)}" for kind, items in config.required.items())
    required = f"## Required for each kind of problem\n\n{kinds}\n" if kinds else ""
    text = _template()
    replacements = {
        "product_name": config.name,
        "reason_rule": reason_rule,
        "issue": pending[0].text if pending else "none",
        "language": {"ar": "Arabic", "en": "English"}.get(state.get("language") or identity.language, "Arabic"),
        "values": _values_block(catalog),
        "current_draft": state["ticket_draft"].model_dump_json() if state.get("ticket_draft") else "none",
        "other_issues": "\n".join(p.text for p in pending[1:]) or "none",
        "required_kinds": required,
        "team_scopes": scopes,
        "schema": schema_text(Classification),
    }
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text
