"""Router prompt builder and its reusable prompt sections."""

from ole5_chat import products
from ole5_chat.agent_v2.context import router_context
from ole5_chat.agent_v2.state import AgentState


ROUTER_ROUTES = {
    "question": "A product question or problem report needing the knowledge base. A named ordinary issue remains question even when the customer asks for a ticket: Search runs first.",
    "pending_response": "A reply to, or question about, the active pending offer, required detail, or submission summary. Use only when pending customer response is present.",
    "ticket_status": "A lookup of the customer's ticket data, or an explanation of an identified ticket status. Support-process questions such as hours or reply channel are other.",
    "escalation": "A configured always-ticket topic, configured urgent situation, a report that suggested steps did not help, or a bare ticket request with no recoverable subject.",
    "other": "A greeting, thanks, capability/About/company question, configured support-process question, off-topic message, evidence-grounded rephrasing, or unclear intent.",
}

ROUTER_EXAMPLES = {
    "question": (
        '"كيف أضيف علامة مائية؟"; '
        '"open a ticket for me about the archive button" → question, '
        'explicit_ticket_request=true; '
        '"وعلى الآيباد؟" after a product answer → question; '
        '"A letter sent to the CEO for signature was signed and sent back, '
        'but it does not appear in the system."'
    ),
    "pending_response": (
        'An error message after asking for error text; '
        'an attachment after asking for an attachment or screenshot; '
        '"ايه" after a ticket offer; '
        '"ارسلها" after a submission summary; '
        '"ما أعرف" after a required-detail question; '
        '"ما أبي أعطيك" after a required-detail question; '
        '"لماذا تحتاج اسم المتصفح؟"'
    ),
    "ticket_status": (
        '"وش صار على تذكرتي؟"; '
        '"What is the status of my ticket?"; '
        '"ما معنى غير متوفرة؟" after a ticket-status reply; '
        '"What happened to the ticket I opened earlier?"'
    ),
    "escalation": (
        '"نبي ننقل بياناتنا من النظام القديم"; '
        '"ولا موظف عندنا يقدر يسجل دخول"; '
        '"لا زال لا يعمل" after suggested steps; '
        '"افتح لي تذكرة" with no issue subject; '
        '"افتحوا تذكرة لنقل البيانات" → escalation, '
        'explicit_ticket_request=true'
    ),
    "other": (
        '"مرحبا"; "شكراً"; "What can you do?"; "Who developed Ole5?"; '
        '"وين توصلني الرسالة؟"; "كم ساعات الدعم؟"; '
        '"اشرحها ببساطة" after a product answer; '
        '"الأرشفة" with nothing pending; "عندي مشكلة" with no subject; '
        'an unrelated/off-topic question; '
        'an ambiguous reference such as "الثانية" when no clear referent exists'
    ),
}


def join_values(items: list[str]) -> str:
    return "; ".join(items) if items else "none"


def router_prompt(state: AgentState) -> str:
    config = products.load(state["identity"].product)
    context = router_context(state)
    language = state.get("language") or getattr(context.get("identity"), "language", "en")
    about = config.about.get(language) or config.about.get("ar") or "none"
    pending = context["pending_customer_response"]
    pending_text = pending.model_dump_json() if pending is not None else "none"
    route_lines = "\n".join(
        f"- {route}: {meaning}\n  Examples: {ROUTER_EXAMPLES[route]}"
        for route, meaning in ROUTER_ROUTES.items()
        if pending is not None or route != "pending_response"
    )
    return f"""You are the Router for {config.name}. Classify the latest customer message so code can send it to the component that owns the next operation.

Classify the latest customer message, not an older customer message. Use the full conversation and the separately labelled previous assistant message to resolve what the latest message refers to. Do not choose a ticket operation, mutate state, answer the customer, search the knowledge base, or call an API.

Product scope: {config.scope or 'none'}
About: {about}
Always-ticket topics: {join_values(config.always_ticket)}
Urgent situations: {join_values(config.urgent_when)}

Pending customer response: {pending_text}
Latest customer message: {context['latest_customer_message'] or 'none'}
Previous assistant message: {context['previous_assistant_message'] or 'none'}

Route definitions and examples:
{route_lines}

Rules:
- A pending response is valid only when pending customer response is present. If absent, do not return pending_response.
- A short value such as a browser name can be pending_response when the previous assistant asked for it; the same word with no pending question is other.
- A new product question during ticket preparation is question, not pending_response. A greeting, thanks, About/capability question, support-process question, rephrase, or unclear message is other and must not mutate ticket state.
- Set explicit_ticket_request=true only when the customer explicitly asks to prepare/open a ticket for the current named issue. Keep it false for policy-based escalation, urgency, status, acceptance, detail answers, rephrasing, and mere mentions of tickets. A named ordinary issue still routes to question so Search runs first.
- If the latest message has no safe interpretation, choose other. Do not invent a ticket subject or operation.

Return JSON only with decision, explicit_ticket_request, and reason. The reason should briefly explain the relationship between the latest message and the selected route. Respond in {language}."""
