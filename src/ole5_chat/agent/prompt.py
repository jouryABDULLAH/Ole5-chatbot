"""Builds each LLM node's system prompt from prompts/chat/*.md and the conversation's own data: chat.yaml, the value lists and the search results."""

import json
from functools import lru_cache

from ole5_chat import products, values
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.config import ROOT
from ole5_chat.knowledge.contracts import Retrieval
from ole5_chat.llm import schema_text

NOT_SEARCHED = "KNOWLEDGE BASE\n  No search was run for this message."


@lru_cache(maxsize=8)
def _template(name: str) -> str:
    return (ROOT / "prompts" / "chat" / f"{name}.md").read_text(encoding="utf-8")


def _fill(name: str, **parts: str) -> str:
    text = _template(name)
    for key, value in parts.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _list(items: list[str]) -> str:
    return "; ".join(items) if items else "none"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def ticket_status(draft: TicketDraft | None, files: list[dict] = ()) -> str:
    names = ", ".join(f["file_name"] for f in files)
    attached = f"\nFiles the customer attached for the ticket: {names}" if names else ""
    if draft is None:
        return "No ticket is being prepared." + attached
    ready = "yes, shown to the customer with a Submit button" if draft.classified else "no"
    return (f"A ticket is being prepared.\nCollected: {_json(draft.collected)}\nStill missing: {_json(draft.missing)}"
            f"\nThe customer does not know: {_json(draft.unknown)}\nSummary ready: {ready}{attached}")


def router(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    last = next((m.text for m in reversed(state["messages"]) if m.type == "ai"), "none")
    return _fill("router", product_name=config.name, always_ticket=_list(config.always_ticket),
                 urgent_when=_list(config.urgent_when), ask_before_ticket=_json(config.ask_before_ticket),
                 last_assistant=last, ticket=ticket_status(state.get("ticket_draft"), state.get("attachments") or []),
                 schema=schema_text(RouterDecision))


def respond(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    retrieval = state.get("last_retrieval")
    return _fill(
        "respond",
        product_name=config.name,
        tone=config.tone,
        ask_before_ticket=_json(config.ask_before_ticket),
        step=state["step"],
        ticket=ticket_status(state.get("ticket_draft"), state.get("attachments") or []),
        evidence=Retrieval.from_json(retrieval).render() if retrieval else NOT_SEARCHED,
        schema=schema_text(Reply),
    )


def _values_block(catalog: values.Catalog) -> str:
    lines = ["## Values", "", "### queue", ""]
    lines += [f"  {o.value}" + (f": {o.description}" if o.description else "") for o in catalog.queues]
    lines += ["", "### type, then subtype under it", ""]
    for t in catalog.types:
        lines.append(f"  {t.value}")
        for s in catalog.subtypes_of(t.value):
            reason = catalog.reason_for(s.value)
            lines.append(f"    - {s.value}" + (f": {reason}" if reason else ""))
    lines += ["", "### priority", ""]
    lines += [f"  {o.value}" for o in catalog.priorities]
    return "\n".join(lines) + "\n"


def classify(state: ChatState) -> str:
    who = state["identity"]
    config = products.load(who.product)
    catalog = values.catalog(who.product)
    draft = state.get("ticket_draft")
    reason_rule = ("leave it empty: it is the catalog text of the chosen subtype and is filled in for you."
                   if catalog.has_reason_catalog else
                   "free text in English, up to 500 characters: a category, a dash, then a description.")
    scopes = f"## Team scopes\n\n{config.team_scopes}\n" if config.team_scopes else ""
    return _fill("classify", product_name=config.name, reason_rule=reason_rule,
                 collected=_json(draft.collected if draft else {}), values=_values_block(catalog),
                 team_scopes=scopes, schema=schema_text(Classification))
