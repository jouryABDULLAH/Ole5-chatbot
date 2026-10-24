"""Builds each LLM node's system prompt from prompts/chat/*.md and the conversation's own data: chat.yaml, the value lists and the search results."""

import json
import re
from functools import lru_cache

from ole5_chat import products, values
from ole5_chat.agent.contracts import Classification, Reply, RouterDecision
from ole5_chat.agent.state import ChatState, TicketDraft
from ole5_chat.agent.validate import NAMES, Language
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


def ticket_status(draft: TicketDraft | None, files: list[dict] = (), urgent: bool = False) -> str:
    names = ", ".join(f["file_name"] for f in files)
    attached = f"\nFiles the customer attached for the ticket: {names}" if names else ""
    if draft is None:
        urgency = "\nUrgent: yes: this message describes an urgent problem" if urgent else ""
        return "No ticket is being prepared." + urgency + attached
    ready = "yes, shown to the customer with a Submit button" if draft.classified else "no"
    urgency = "yes: the usual questions are skipped and the ticket is prepared right away" if urgent else "no"
    return (f"A ticket is being prepared.\nUrgent: {urgency}\nCollected: {_json(draft.collected)}"
            f"\nStill missing: {_json(draft.missing)}\nThe customer does not know: {_json(draft.unknown)}"
            f"\nSummary ready: {ready}{attached}")


def router(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    last = next((m.text for m in reversed(state["messages"]) if m.type == "ai"), "none")
    return _fill("router", product_name=config.name, always_ticket=_list(config.always_ticket),
                 urgent_when=_list(config.urgent_when), ask_before_ticket=_json(config.ask_before_ticket),
                 last_assistant=last, ticket=ticket_status(state.get("ticket_draft"), state.get("attachments") or []),
                 schema=schema_text(RouterDecision))


def language() -> str:
    return _fill("language", schema=schema_text(Language))


def _persona(config: products.ProductConfig, language: str) -> str:
    p = config.persona
    who = f"{p.name}, " if p.name else ""
    lines = [f"You are {who}{p.identity}. You introduced yourself at the start of this chat with: "
             f"\"{p.introduction.get(language) or p.introduction['en']}\"", "", "Greetings:"]
    lines += [f"- {g}" for g in p.greetings]
    lines += ["", "Questions and problems:"] + [f"- {q}" for q in p.questions]
    lines += ["", "Voice:"] + [f"- {v}" for v in p.voice]
    return "\n".join(lines)


GREETING = re.compile(
    r"^\W*(السلام عليكم|سلام عليكم|وعليكم السلام|مرحبا|مرحباً|أهلا|أهلاً|اهلا|هلا|يا هلا|صباح الخير|مساء الخير|"
    r"hello|hi|hey|good (?:morning|afternoon|evening)|greetings)\b", re.IGNORECASE)


def greeting_in(text: str) -> str | None:
    """The greeting a message opens with, or None."""
    m = GREETING.match(text.strip())
    return m.group(1) if m else None


def _outline(state: ChatState) -> str:
    """The lines this reply must contain, in order, decided from the state. respond words them in the persona's voice."""
    step, draft = state["step"], state.get("ticket_draft")
    urgent = _urgent(state)
    lines: list[str] = []
    greeting = greeting_in(state["messages"][-1].text)
    if greeting and step != "greet":
        lines.append(f'Reply to the customer\'s greeting ("{greeting}") first, in its own form.')
    if state.get("note"):
        lines.append(state["note"])
    if step == "greet":
        lines.append("Greet them back, or say who you are and what you can do, and invite their question. No product facts.")
    elif step == "answer":
        lines.append("Answer from the passages below only, and cite the documents you used. If they do not answer what "
                     "was asked, say you have no answer, offer a ticket, and cite nothing.")
    elif step == "offer_ticket":
        if urgent:
            lines.append("The problem is urgent: say so and ask whether to open an urgent ticket right away. "
                         "Do not say you have no answer.")
        else:
            lines.append("Let them know this is not something you can answer here, and ask whether they want a "
                         "ticket opened. State nothing about the product.")
    elif step == "collect":
        if draft is None or not draft.collected:
            lines.append("Ask them to describe the problem, nothing else.")
        else:
            lines.append(f"Ask for one or two of: {', '.join(draft.missing) or 'nothing'}. Do not ask for "
                         f"{', '.join(draft.collected) or 'nothing'} (already given) or "
                         f"{', '.join(draft.unknown) or 'nothing'} (they do not know).")
    elif step == "confirm":
        if urgent:
            lines.append("Tell them that because the problem is urgent the ticket was prepared right away, "
                         "without the usual questions.")
        lines.append("Say the ticket summary is ready and they can press Submit. Ask for nothing.")
    elif step == "ask":
        lines.append("Reply to what they said. You may state only two kinds of things: what this conversation "
                     "contains (their earlier messages, your earlier replies) and that you can help with the product "
                     "or open a ticket. If they ask about something the conversation does not contain, say so. If "
                     "they did not understand, say your last reply again more simply. If they declined or cancelled "
                     "a ticket, say they can contact support at any time. Never add how the product works, guess "
                     "what they meant, or promise anything.")
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


def _urgent(state: ChatState) -> bool:
    draft, decision = state.get("ticket_draft"), state.get("decision")
    return bool((draft and draft.urgent) or (decision and decision.urgent))


def _message_kind(state: ChatState) -> str:
    intent, step = state["decision"].intent, state["step"]
    if intent == "ticket_reply":
        return "a reply about the ticket (details, I don't know, yes, a change request)"
    if intent == "question" and step == "answer":
        return "a question the passages below answer"
    if intent == "question":
        return "a question the knowledge base could not answer"
    return "a greeting, thanks, or something else"


def respond(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    retrieval = state.get("last_retrieval")
    return _fill(
        "respond",
        product_name=config.name,
        persona=_persona(config, state["decision"].language),
        language=NAMES[state["decision"].language],
        ask_before_ticket=_json(config.ask_before_ticket),
        step=state["step"],
        message_kind=_message_kind(state),
        outline=_outline(state),
        ticket=ticket_status(state.get("ticket_draft"), state.get("attachments") or [],
                             urgent=_urgent(state)),
        evidence=Retrieval.from_json(retrieval).render() if retrieval and state["step"] == "answer" else NOT_SEARCHED,
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
