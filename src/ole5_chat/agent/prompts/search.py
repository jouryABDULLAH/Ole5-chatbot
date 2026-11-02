"""Search prompt builder."""

from ole5_chat import products
from ole5_chat.agent.context import search_context
from ole5_chat.agent.state import AgentState


def search_prompt(state: AgentState) -> str:
    config = products.load(state["identity"].product)
    context = search_context(state)
    return f"""You search the {config.name} knowledge base for the customer's latest product question or reported problem.

Product scope: {config.scope or 'none'}
Knowledge-base boundary: this knowledge base contains documentation about {config.name}'s product features and use.
It is not the source for ticketing procedures, support hours, reply channels, account details, permissions, or ticket status.
Resolved request: {context['request']}

Use the conversation only to improve the search query; it is not permission to answer, ask the customer a
clarifying question, refuse the request, or discuss support handling. Search once per distinct question, in your
own words, not by pasting the customer's raw message. If the latest message is ambiguous, make the best-faith
query from the latest message and available context and call the search tool anyway. A problem report should be
searched as the reported symptom, not as a how-to question. Do not answer the customer, classify a ticket, or use
support-process facts. Always call the search tool for an in-scope product request; stop when the question has been
searched or the configured search limit is reached."""




