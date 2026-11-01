"""Search prompt builder."""

from ole5_chat import products
from ole5_chat.agent_v2.context import search_context
from ole5_chat.agent_v2.state import AgentState


def search_prompt(state: AgentState) -> str:
    config = products.load(state["identity"].product)
    context = search_context(state)
    return f"""You search the {config.name} knowledge base for the customer's latest product question or reported problem.

Product scope: {config.scope or 'none'}
Resolved request: {context['request']}

Use the conversation to resolve follow-ups. Search once per distinct question, in your own words, not by pasting the customer's raw message. A problem report should be searched as the reported symptom, not as a how-to question. Do not answer the customer, classify a ticket, or use support-process facts. Call the search tool when evidence is needed; stop when the question has been searched or the configured search limit is reached."""

