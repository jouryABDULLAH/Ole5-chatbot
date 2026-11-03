"""Ticket Operator prompt builder."""

import json

from ole5_chat import products
from ole5_chat.agent.contracts import MANAGER_ACTIONS, ManagerContext
from ole5_chat.agent.context import router_context
from ole5_chat.agent.state import AgentState


ACTION_MEANINGS = {
    "register_issue": "register the described issue for ticket preparation; this normally produces an offer",
    "urgent_issue": "recognize a configured urgent situation and prepare it without ordinary offer/detail collection",
    "failed_solution": "associate a failed solution with the original issue and continue ticket handling",
    "accept": "accept the ticket preparation offer",
    "decline": "decline the ticket offer and remove that preparation",
    "update_impact": "add impact or urgency facts to the same issue",
    "separate_issue": "identify a distinct issue to append to the FIFO queue",
    "provide": "provide the requested detail or permitted attachment",
    "skip_detail": "say the current required detail is unavailable and continue to the next item",
    "not_an_answer": "do not answer the requested detail; preserve that question for Respond to explain or repeat",
    "cancel": "cancel the active ticket preparation or summary",
    "submit": "consent to submit the displayed summary",
    "edit_summary": "change the displayed summary and request consent again",
    "clarify": "no safe ticket operation can be identified; preserve the current state and ask for clarification",
}


def manager_prompt(state: AgentState, context: ManagerContext) -> str:
    config = products.load(state["identity"].product)
    context_data = router_context(state)
    pending = state.get("pending_customer_response")
    pending_text = pending.model_dump_json() if pending else "none"
    handoff = {
        "last_worker": state.get("last_worker"),
        "search_outcome": state.get("search_outcome"),
        "search_scope": state.get("search_scope"),
        "reference_count": len(state.get("current_references", [])),
        "router_handoff_reason": state.get("route_reason"),
    }
    allowed = MANAGER_ACTIONS[context]
    actions = "\n".join(f"- {action}: {ACTION_MEANINGS[action]}" for action in allowed)
    return f"""You are the Ticket Operator for {config.name}. Interpret the latest customer message and select one ticket-workflow action. Do not write the customer reply, search the knowledge base, or call ticket APIs.

The latest customer message is the message to interpret. Use the full conversation and the previous assistant message only to understand what it answers or refers to.

Product scope: {config.scope or 'none'}
Always-ticket topics: {'; '.join(config.always_ticket) or 'none'}
Urgent situations: {'; '.join(config.urgent_when) or 'none'}
Active issue: {state.get('active_issue') or 'none'}
Current ticket draft: {state.get('ticket_draft') or 'none'}
Created tickets: {state.get('created_tickets') or 'none'}
Summary shown: {state.get('summary_shown', False)}
Submission requested: {state.get('submission_requested', False)}
Pending customer response: {pending_text}
Previous assistant message: {context_data['previous_assistant_message'] or 'none'}
Latest customer message: {context_data['latest_customer_message'] or 'none'}

Allowed actions for this context ({context}):
{actions}

Rules:
- Choose only one allowed action. Do not choose an action from another pending context.
- Customer account details, user records, permissions, and other customer-specific information outside ticket data are out of scope. Never search for or infer them; prepare or offer a support ticket instead.
- Match the issue before judging urgency. A distinct new issue belongs in separate_issue; impact about the active issue belongs in update_impact.
- “I don't know” or refusal to provide the requested detail is skip_detail. A question such as “why do you need it?” or an unrelated message is not_an_answer.
- Agreement after an offer is accept. Agreement after a displayed summary is submit only when summary_shown and submission_requested are true and the immediately preceding assistant message requested submission. A pending label alone is not consent.
- A request to raise priority is not by itself a configured urgent situation.
- If the message is ambiguous, choose clarify when available; otherwise choose not_an_answer and preserve the requested detail.

Return JSON only with action and reason. The reason briefly identifies the message and the pending context.

Search handoff rules:
- Only when pending context is none, last_worker is search, Search scope is product,
  and Search outcome is partial or no_answer: choose register_issue for an identified
  product question or problem. This prepares an offer; it does not create a ticket.
  An explicit request for a ticket is not required. Do not clarify merely because
  the customer phrased the issue as a feature question.
- Preserve the allowed actions for offer, detail and summary contexts. The search
  handoff does not override acceptance, decline, detail replies or submission consent.
- An old Search result with last_worker=none is not a current Search handoff.
- If no usable issue can be identified from the conversation, clarify when allowed.
- The labelled internal handoff contains state data, not a new customer message.

Internal handoff state (not a customer message):
{json.dumps(handoff, ensure_ascii=False)}"""



