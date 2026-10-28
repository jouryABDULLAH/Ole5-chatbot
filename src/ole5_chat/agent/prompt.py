"""Builds each LLM node's system prompt from prompts/chat/*.md and the conversation's own data: chat.yaml, the value lists and the search results."""

from functools import lru_cache

from ole5_chat import products, values
from ole5_chat.agent.contracts import OPTIONS, Classification, route_schema
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.tasks import RESPOND_TASK
from ole5_chat.agent.validate import NAMES, Language
from ole5_chat.config import ROOT, get_settings
from ole5_chat.llm import schema_text

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


PHASES = {
    "none": "No ticket is being offered or prepared.",
    "offer_ticket": "The assistant has just offered to open a ticket for the issue below and is waiting for an answer.",
    "collect": "A ticket is being prepared for the issue below; the assistant is asking the customer for details.",
    "confirm": "A ticket summary for the issue below is shown to the customer with a Submit button.",
}

MEANINGS = {
    "question": "asks about the product or reports a problem with it, including a follow-up to an earlier answer. A "
                "request to open a ticket that names a topic is a question too: the assistant searches first and "
                "offers a ticket only when it has no answer.",
    "talk": "nothing to look up: a greeting, thanks, a question about the assistant itself (who it is, what it can do), "
            "an acknowledgement with nothing open to agree to, a side remark, a request to repeat what the assistant "
            "just said, a question about why the assistant said or did something (\"why?\", \"what do you mean?\"), a "
            "request to open a ticket, or a report of a problem, that names no symptom and no feature, so there is "
            "nothing to search for yet (\"عندي مشكلة\", \"افتح لي تذكرة\", \"I need help\"). Once a message names "
            "anything, a screen, an error, something that doesn't work, it is a question, however short.",
        
    "ticket": "the message reports a topic from this list: {always_ticket}.",
    "urgent": "the message describes a situation from this list: {urgent_when}. Judge it from the impact described, "
              "not the tone.",
    "status": "the customer asks about their own tickets: status, whether one was received, its number.",
    "accept": "agrees to the ticket the assistant just offered.",
    "decline": "turns the offer down.",
    "details": "answers the assistant's questions about the problem, including \"I don't know\" about something the "
               "assistant asked, a file sent (a line \"[attached: name]\"), or more description. A refusal to give "
               "what was asked is an answer too (\"ما أبي أعطيك\", \"ما يهم\").",
    "done": "says there is nothing more to add, typically after the assistant asked whether there is anything else. "
            "A message that gives a last detail and says it is all they have is done. \"That's all I have\" and \"I "
            "don't know anything else\" are done.",
    "cancel": "drops the ticket being prepared or the summary shown.",
    "yes": "agrees to submit the summary shown.",
    "change": "asks to change something on the summary (priority, title, details).",
}

# What the assistant had said before the examples of each phase's own options.
AFTER = {
    "offer_ticket": "after the assistant asked \"would you like me to open a ticket?\"",
    "collect": "after the assistant asked \"what does the error message say?\"",
    "confirm": "while the summary is shown",
}

OLE5_EXAMPLES = {
    ("none", "question"): ['"كيف أضيف علامة مائية؟"', '"open a ticket for me about the archive button"',
                           '"وعلى الآيباد؟" (after an answer)'],
    ("none", "talk"): ['"مرحبا"', '"what can you do?"', '"تمام" (after an answer)', '"عندي مشكلة"', '"افتح لي تذكرة"'],
    ("none", "ticket"): ['"نبي ننقل بياناتنا من النظام القديم"'],
    ("none", "urgent"): ['"ولا موظف عندنا يقدر يسجل دخول"'],
    ("none", "status"): ['"وش صار على تذكرتي؟"', '"what\'s the status of my tickets?"'],
    ("offer_ticket", "accept"): ['"ايه"', '"yes please"'],
    ("offer_ticket", "decline"): ['"لا شكراً"', '"no, it\'s fine"', '"ألغها"', '"cancel it"'],
    ("offer_ticket", "question"): ['"طيب وكيف أغير كلمة المرور؟"'],
    ("offer_ticket", "talk"): ['"مين أنت؟"', '"ليش؟" (after the assistant said it can\'t do something)'],
    ("offer_ticket", "status"): ['"قبل كذا، التذكرة القديمة وش صار عليها؟"'],
    ("collect", "details"): ['"تطلع رسالة Access denied"', '"ما أعرف"', '"[attached: screen.png]"'],
    ("collect", "done"): ['"هذا كل شيء"', '"لا" (when the assistant asked whether there is anything else)',
                          '"التطبيق يقفل لحاله بعد التحديث، وهذا اللي أعرفه"', '"ما عندي شي ثاني، افتح التذكرة"'],
    ("collect", "cancel"): ['"خلاص ألغ التذكرة"'],
    ("collect", "question"): ['"ايش يعني درجة السرية؟"'],
    ("collect", "talk"): ['"ليش تحتاج هالمعلومة؟"'],
    ("collect", "status"): ['"التذكرة اللي فتحتها وش صار عليها؟"'],
    ("confirm", "yes"): ['"ارسلها"', '"submit it"'],
    ("confirm", "change"): ['"خل الأولوية عالية"'],
    ("confirm", "cancel"): ['"لا، ألغها"'],
    ("confirm", "question"): ['"كم ياخذ الرد عادة؟"'],
    ("confirm", "talk"): ['"لحظة، بشوف شي"'],
    ("confirm", "status"): ['"وصلتكم تذكرتي اللي قبل؟"'],
}

EXAMPLES = {
    "OLE5": OLE5_EXAMPLES,
    "RICH": {**OLE5_EXAMPLES,
             ("none", "ticket"): ['"أبغى استرداد مبلغ الرسائل اللي ما وصلت"', '"we need our IP whitelisted"'],
             ("none", "urgent"): ['"رسائل OTP ما توصل لأي عميل عندنا"', '"the API is down for us"']},
}


def _option(config: products.ProductConfig, phase: str, option: str) -> str:
    meaning = MEANINGS[option].format(always_ticket=_list(config.always_ticket), urgent_when=_list(config.urgent_when))
    examples = EXAMPLES[config.code]
    own = (phase, option) in examples and phase != "none"
    said = examples[(phase, option)] if own else examples[("none", option)]
    lead = f"Examples, {AFTER[phase]}" if own else "Examples"
    return f"{option}: {meaning}\n  {lead}: {', '.join(said)}"


def router(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    phase = state["phase"]
    pending = state.get("pending") or []
    last = next((m.text for m in reversed(state["messages"]) if m.type == "ai"), "none")
    return _fill("router", product_name=config.name, phase_description=PHASES[phase],
                 issue=pending[0].text if pending else "none", last_assistant=last,
                 options="\n\n".join(_option(config, phase, o) for o in OPTIONS[phase]),
                 schema=schema_text(route_schema(phase)))


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


def respond(state: ChatState, language: str) -> str:
    config = products.load(state["identity"].product)
    return _fill("respond", persona=_persona(config, language), language=NAMES[language],
                 task=state.get("task") or RESPOND_TASK, note=state.get("note") or "")


def search(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    return _fill("search", product_name=config.name, max_searches=str(get_settings().kb_max_searches))


def ground() -> str:
    from ole5_chat.agent.respond import Grounding
    return _fill("ground", schema=schema_text(Grounding))


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
    pending = state.get("pending") or []
    reason_rule = ("leave it empty: it is the catalog text of the chosen subtype and is filled in for you."
                   if catalog.has_reason_catalog else
                   "free text in English, up to 500 characters: a category, a dash, then a description.")
    scopes = f"## Team scopes\n\n{config.team_scopes}\n" if config.team_scopes else ""
    kinds = "\n".join(f"  {kind}: {', '.join(items)}" for kind, items in config.required.items())
    required = f"## Required for each kind of problem\n\n{kinds}\n" if kinds else ""
    return _fill("classify", product_name=config.name, reason_rule=reason_rule,
                 issue=pending[0].text if pending else "none", values=_values_block(catalog),
                 required_kinds=required, team_scopes=scopes, schema=schema_text(Classification))
