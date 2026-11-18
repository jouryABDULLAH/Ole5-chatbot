"""Generate the temporary customer-facing reply. Validate checks it."""

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ole5_chat import llm
from ole5_chat.agent.prompts.respond import system_prompt, turn_note
from ole5_chat.agent.turn_sheet import TurnSheet, build_turn_sheet

from ole5_chat.agent.state import AgentState
from ole5_chat.agent.guards import model_language, plain, script_language

log = logging.getLogger(__name__)


def _expected_language(state: AgentState) -> str:
    """The language of the customer's latest message. Its letters decide when they are all Arabic or all Latin and
    agree with the conversation's language (or there is none yet). The model reads the message only when it is mixed,
    has no letters, or its letters differ from the conversation's language: a bare value such as a username or an
    error code names no language and keeps the conversation's."""
    latest = next((str(message.content) for message in reversed(state.get("messages", []))
                   if message.type == "human"), "")
    stored = state.get("language")
    script = script_language(latest)
    if script and (not stored or script == stored):
        return script
    return model_language(latest) or stored or state["identity"].language or "ar"


def _messages(state: AgentState, sheet: TurnSheet) -> list:
    """The chat, then this turn's facts as the last message (the turn sheet)."""
    return [*state.get("messages", []), HumanMessage(content=turn_note(sheet))]


def respond_node(state: AgentState) -> dict:
    language = _expected_language(state)
    sheet = build_turn_sheet(state, language) # turns state into facts and one stage
    log.info("respond  intent=%s search=%s stage=%s button=%s", state.get("intent"),
             state.get("search_outcome"), sheet["stage"], sheet["button"])
    try:
        reply = llm.invoke(
            # Never above 0.3 (user's limit).
            [SystemMessage(content=system_prompt(state, sheet)), *_messages(state, sheet)], temperature=0.1
        ).text.strip()
    except llm.ModelFailed as exc:
        # An empty reply: Validate sends the fixed reply built from the workflow facts, so a failed rewrite never
        # ships a generic error with the answer's sources.
        log.error("ERROR respond model call failed; Validate sends the fixed reply: %s", exc)
        reply = ""
    cleaned = plain(reply)
    if cleaned != reply:
        log.info("respond markdown=stripped")
    reply = cleaned
    log.info("respond  reply=%r", reply)
    return {"reply": reply, "language": language}
