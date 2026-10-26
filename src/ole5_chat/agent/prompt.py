"""Builds each LLM node's system prompt from prompts/chat/*.md and the conversation's own data: chat.yaml, the value lists and the search results."""

from functools import lru_cache

from ole5_chat import products, values
from ole5_chat.agent.contracts import OPTIONS, Classification, route_schema
from ole5_chat.agent.state import ChatState
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
                "request to open a ticket about an ordinary topic is a question too: the assistant searches first and "
                "offers a ticket only when it has no answer.",
    "talk": "nothing to look up: a greeting, thanks, a question about the assistant itself (who it is, what it can do), "
            "an acknowledgement with nothing open to agree to, a side remark, a request to repeat what the assistant "
            "just said, a problem or need with nothing specific in it yet (\"عندي مشكلة\", \"I need help\").",
    "ticket": "the message reports a topic from this list: {always_ticket}.",
    "urgent": "the message describes a situation from this list: {urgent_when}. Judge it from the impact described, "
              "not the tone.",
    "status": "the customer asks about their own tickets: status, whether one was received, its number.",
    "accept": "agrees to the ticket the assistant just offered.",
    "decline": "turns the offer down.",
    "details": "answers the assistant's questions about the problem, including \"I don't know\" about something the "
               "assistant asked, a file sent (a line \"[attached: name]\"), or more description.",
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
    ("none", "talk"): ['"مرحبا"', '"what can you do?"', '"تمام" (after an answer)', '"عندي مشكلة"'],
    ("none", "ticket"): ['"نبي ننقل بياناتنا من النظام القديم"'],
    ("none", "urgent"): ['"ولا موظف عندنا يقدر يسجل دخول"'],
    ("none", "status"): ['"وش صار على تذكرتي؟"', '"what\'s the status of my tickets?"'],
    ("offer_ticket", "accept"): ['"ايه"', '"yes please"'],
    ("offer_ticket", "decline"): ['"لا شكراً"', '"no, it\'s fine"', '"ألغها"', '"cancel it"'],
    ("offer_ticket", "question"): ['"طيب وكيف أغير كلمة المرور؟"'],
    ("offer_ticket", "talk"): ['"مين أنت؟"'],
    ("collect", "details"): ['"تطلع رسالة Access denied"', '"ما أعرف"', '"[attached: screen.png]"'],
    ("collect", "done"): ['"هذا كل شيء"', '"لا" (when the assistant asked whether there is anything else)',
                          '"التطبيق يقفل لحاله بعد التحديث، وهذا اللي أعرفه"', '"ما عندي شي ثاني، افتح التذكرة"'],
    ("collect", "cancel"): ['"خلاص ألغ التذكرة"'],
    ("collect", "question"): ['"ايش يعني درجة السرية؟"'],
    ("collect", "talk"): ['"ليش تحتاج هالمعلومة؟"'],
    ("confirm", "yes"): ['"ارسلها"', '"submit it"'],
    ("confirm", "change"): ['"خل الأولوية عالية"'],
    ("confirm", "cancel"): ['"لا، ألغها"'],
    ("confirm", "question"): ['"كم ياخذ الرد عادة؟"'],
    ("confirm", "talk"): ['"لحظة، بشوف شي"'],
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
    text = _fill("router", product_name=config.name, phase_description=PHASES[phase],
                 issue=pending[0].text if pending else "none", last_assistant=last,
                 options="\n\n".join(_option(config, phase, o) for o in OPTIONS[phase]),
                 schema=schema_text(route_schema(phase)))
    if spoken(state):
        text = text.replace("Reply with JSON only", f"{VOICE_ROUTER}\n\nReply with JSON only", 1)
    return text


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


RESPOND_TASK = ("Answer the customer from the passages. If no passage answers the question, say you have no answer for "
                "this and ask whether they would like a ticket opened.")
TALK_TASK = ("This message needs no search. Reply to what the customer actually said, in one or two short lines. A "
             "greeting gets the persona's greeting and thanks gets its thanks line; anything else gets a plain reply to "
             "it, never a greeting or thanks line. If the customer says they have a problem but not what it is, ask what "
             "it is in one short open question, with no acknowledgement before it: \"يسعدني مساعدتك، ما المشكلة التي "
             "تواجهها؟\" If they say there is no problem or nothing to add, don't ask again: say you're here if they need "
             "anything. Don't state a product fact that isn't already in this chat, and don't offer a ticket.")


# What respond_agent is asked to do this turn, set by the handler. {issue} is pending[0].text, {new} the issue just queued.
TASKS = {
    "question": RESPOND_TASK,
    "talk": TALK_TASK,
    "chat_offer_ticket": "Answer the customer's message as usual. A ticket offer for this issue is still open: {issue}. "
                         "If you answered something else, end by asking whether they would still like that ticket "
                         "opened. Example: the customer asks how to reset their password while a ticket offer for "
                         "'dark mode' is open. Reply with the password steps, then: 'Would you still like me to open "
                         "the ticket about dark mode?'",
    "chat_ticket": "Answer the customer's message as usual. Then continue the current ticket: {current}",
    "offer": "This needs the support team: {issue}. Say in one line that you can't resolve it here, and ask whether "
             "they would like a ticket opened.",
    "queued": "The customer raised a new issue: {new}. It will be handled after the current ticket ({issue}). Say so in "
              "one line. Don't answer the new issue and don't offer a ticket for it now. Then continue the current "
              "ticket: {current}",
    "accept": "The customer agreed to a ticket for: {issue}. Ask what the support team will need to act on it: work "
              "out the likely cause and ask what would confirm or rule it out, a few things at a time, skipping what "
              "the customer already said. Ask about what the customer sees and did. Don't name a screen, template, "
              "setting or option unless a passage names it. Nothing has been created yet; "
              "the ticket is created only when they submit the summary. Don't say a ticket is opened, created or "
              "being opened.",
    "declined": "The customer said no to opening a ticket; none was opened. Acknowledge it in one line without saying "
                "a ticket was cancelled or closed, and say they can contact support at any time.",
    "urgent_ready": "This is urgent, so the ticket was prepared right away without the usual questions: {issue}. The "
                    "summary is shown with a Submit button. Nothing has been created yet. Say in one line that because "
                    "it is urgent the summary is ready now, and ask whether to submit the ticket for: {issue}. Don't "
                    "repeat the summary.",
    "details": "Ask what else the support team needs to act on: {issue}. A few things at a time, skipping what the "
               "customer already said. If the team has enough, ask instead whether there is anything else they'd like "
               "to add. Ask about what the customer sees and did. Don't name a screen, template, setting or option "
               "unless a passage names it. Nothing has been created yet; the ticket is "
               "created only when they submit the summary. Don't say a ticket is opened, created or being opened. "
               "Don't ask again a question you already asked; if the customer didn't answer it, they don't know.",
    "open_question": "Ask whether there is anything else they'd like to add about: {issue}. Nothing else.",
    "confirm": "The ticket summary is ready and shown with a Submit button. Nothing has been created yet; the ticket is "
               "created only when they submit. Say the summary is ready in one line and ask whether to submit the "
               "ticket for: {issue}. "
               "Don't say the ticket is created, opened or being opened. Don't repeat the summary.",
    "changed": "The ticket summary was updated as the customer asked and is shown again. Say what changed in one line "
               "and ask whether to submit the ticket for: {issue}.",
    "created": "The ticket {number} was created. Tell the customer its number and that the support team will reply by "
               "email. Nothing more.",
    "cancelled": "The customer cancelled the ticket. Acknowledge it in one line and say they can contact support at any "
                 "time.",
}
URGENT = "This sounds urgent. "
# Appended to queued when the new issue is urgent.
# How a reply goes back to the current ticket, by phase (the queued task, and chat in collect or confirm).
CURRENT = {
    "offer_ticket": "ask whether they would like a ticket for: {issue}.",
    "collect": "ask what else the support team needs for: {issue}.",
    "confirm": "the summary for {issue} is still shown; ask whether to submit it.",
}
QUEUED_URGENT = " Because the new issue is urgent, also say they can cancel the current one to start it now."


# The voice channel (VOICE_PLAN.md section 6). A voice turn keeps every rule above about what a reply says; these change
# how it is said, and the confirm step's wording, since the customer may not be looking at the screen.

VOICE_STYLE = """## This reply is spoken

The customer is talking to you by voice: your reply is read aloud by a speech engine and also shown on screen.
- Keep exactly the content the rules above put in the reply: the same steps, conditions and causes from DIRECT
  passages, nothing added and nothing dropped. Only the form changes.
- Short spoken sentences, in the order the passages give. Don't add ordering words such as "first" or "then"
  (أولاً، ثم) that the passages don't use, and don't turn different ways of doing something into steps.
- One step per line is fine, since a line break becomes a pause; no bullet or number markers.
- Write screen and menu names exactly as the passages do, arrows included: the speech engine reads an arrow as "then".
  No brackets or slashes outside names.
- At most two short questions, and a question goes last.
- Don't name the documents or sources; they are shown on screen."""

VOICE_ROUTER = ("The customer is speaking: their messages are speech-to-text transcripts and may have misheard or "
                "missing words. Judge what they meant. While the summary is shown, yes needs a clear yes or an "
                "instruction to send it (نعم، ايه، ايوه، اكيد، موافق، أرسلها، افتحها، yes, sure, submit, send, go "
                "ahead, confirm); an acknowledgement without one (\"تمام كذا\", \"اوكي زين\", \"ok, sounds good\") is "
                "talk, not yes and not question.")

PRIORITY_AR = {"Low": "منخفضة", "Medium": "متوسطة", "High": "عالية", "Critical": "حرجة"}
# The last words of every reply that asks to submit, said word for word. The closing question has no yes or send word
# in it, so the assistant's own echo of its last words cannot answer it.
VOICE_CLOSE = {
    "ar": "إن أردت تعديل شيء فأخبرني، ولإرسالها قل: نعم أرسلها. هل تريد إرسالها الآن؟",
    "en": "If anything needs changing, tell me; to send it, say: yes, submit. Would you like it submitted now?",
}
VOICE_SUBMIT_PHRASE = {"ar": "نعم أرسلها", "en": "yes, submit"}
VOICE_NUMBER = {"ar": "رقم تذكرتك هو {number}.", "en": "Your ticket number is {number}."}
VOICE_ARABIC = (" Speak formal Arabic: restate the summary in your own Arabic words, keeping product, screen and error "
                "names as written, address the customer directly, and don't quote the English.")
VOICE_READ_BACK = ("Read the summary back in two or three short spoken sentences, in your own words and without labels "
                   "such as \"title\" or \"details\": what the ticket is about ({about}) and its priority, "
                   "{priority}{urgent}.")
VOICE_END = " Then end with exactly these words: \"{close}\""
VOICE_TASKS = {
    "confirm": "The ticket summary is ready and shown on screen. Nothing has been created yet; the ticket is created only "
               "when they submit. " + VOICE_READ_BACK + VOICE_END + " Don't say the ticket is created, opened or being "
               "opened.",
    "urgent_ready": "This is urgent, so the ticket was prepared right away without the usual questions: {issue}. Nothing "
                    "has been created yet. Say that in one sentence. Then " + VOICE_READ_BACK[0].lower()
                    + VOICE_READ_BACK[1:] + VOICE_END,
    "changed": "The ticket summary now differs from before only in: {change}. Compare that with what the customer asked: "
               "say in one sentence what of their request is now on the summary, and if part of it is not there, say "
               "that part was not changed. Then "
               "read the rest of the summary back in one or two short sentences in your own words, without repeating "
               "the change: what the ticket is about ({about}) and its priority, {priority}{urgent}." + VOICE_END,
    "unchanged": "The customer asked to change the ticket summary, but nothing on it changed: it is still about {about}, "
                 "with priority {priority}{urgent}. If what they asked for is already on the summary, say it already "
                 "reads that way, and end with exactly these words: \"{close}\" Otherwise say in one sentence that the "
                 "change could not be made, and ask them to say exactly what to change; then don't ask them to submit.",
    "change_failed": "The change could not be made; the summary on screen is unchanged. Say so in one short sentence and "
                     "ask them to say the change again. Don't ask them to submit.",
    "submit_failed": "The ticket could not be created because of a problem on our side; the summary on screen is "
                     "unchanged. Say so in one sentence, and that they can say \"{phrase}\" to try again.",
    "submit_failed_again": "The ticket could not be created again. Apologise once, say it can't be created right now, and "
                           "suggest trying again later. Don't ask them to submit.",
    "created": "The ticket {number} was created. Say it was created in one sentence, then give the number in a sentence of "
               "its own written exactly \"{number_sentence}\", then say the support team will reply by email.",
    "reconfirm_line": "Nothing has been submitted. Reply with exactly this sentence and nothing else: \"{line}\"",
    "reconfirm": "Nothing has been submitted and nothing on the summary was changed: the customer's reply did not clearly "
                 "say to submit. If they asked to change something, say it has not been changed yet and ask them to say "
                 "the change on its own. If they asked a question, answer it from this conversation." + VOICE_END,
    "current_confirm": "the summary for {issue} is still shown." + VOICE_END,
}
VOICE_CREATED_ALONE = " Nothing more."
VOICE_CREATED_THEN = " Nothing more about this ticket. Then, separately:"
VOICE_FEW_QUESTIONS = " Ask about one or two things only, each as its own short question; keep the rest for your next reply."


def voice(state: ChatState) -> bool:
    return state.get("channel") == "voice"


def spoken(state: ChatState) -> bool:
    """A voice turn the customer spoke (a typed message in voice mode has no heard language)."""
    return voice(state) and state.get("heard_language") is not None


def voice_language(state: ChatState) -> str:
    """The language the reply will be in: what speech-to-text heard; for a typed message the letters it was typed in
    (as respond_agent will judge it); else the last reply's, else the customer's."""
    from ole5_chat.agent.validate import script_language
    typed = next((m.text for m in reversed(state["messages"]) if m.type == "human"), "") if state["messages"] else ""
    found = state.get("heard_language") or script_language(typed) or state.get("language") or state["identity"].language
    return found if found in VOICE_CLOSE else "en"


def respond(state: ChatState, language: str) -> str:
    config = products.load(state["identity"].product)
    text = _fill("respond", persona=_persona(config, language), language=NAMES[language],
                 task=state.get("task") or RESPOND_TASK, note=state.get("note") or "")
    return f"{text.rstrip()}\n\n{VOICE_STYLE}\n" if voice(state) else text


def search(state: ChatState) -> str:
    config = products.load(state["identity"].product)
    return _fill("search", product_name=config.name, max_searches=str(get_settings().kb_max_searches))


def ground() -> str:
    from ole5_chat.agent.respond_agent import Grounding
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
    return _fill("classify", product_name=config.name, reason_rule=reason_rule,
                 issue=pending[0].text if pending else "none", values=_values_block(catalog),
                 team_scopes=scopes, schema=schema_text(Classification))
