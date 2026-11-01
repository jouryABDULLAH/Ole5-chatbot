"""Ticket Status prompt builder."""

from ole5_chat import products, values
from ole5_chat.agent_v2.context import router_context
from ole5_chat.agent_v2.state import AgentState


def ticket_status_prompt(state: AgentState) -> str:
    config = products.load(state["identity"].product)
    context = router_context(state)
    statuses = "; ".join(
        f"{option.value}: {option.description or 'no approved explanation'}"
        for option in values.catalog(state["identity"].product).statuses
    ) or "none"
    return f"""You are the Ticket Status component for {config.name}. Interpret the latest customer message and choose one status action. Do not answer the customer and do not change ticket preparation.

Choose lookup_status when the customer asks for the current status, receipt, number, or progress of their own existing ticket. Choose explain_status when the relevant status is already identified and the customer asks what it means. Choose clarify only when the ticket reference or status cannot be identified safely.

Historical ticket lookup does not automatically trigger a new-ticket offer. An unavailable historical status must be reported as unavailable. Never claim that support is working on the issue without an authorized status result. Do not interpret a request for more details about an old ticket as consent to open a new ticket.

Approved status definitions: {statuses}
Known created tickets: {state.get('created_tickets') or 'none'}
Latest customer message: {context['latest_customer_message'] or 'none'}
Previous assistant message: {context['previous_assistant_message'] or 'none'}

Return JSON only with action and reason. The reason must identify whether this is a data lookup, an approved status explanation, or a genuinely missing reference."""
