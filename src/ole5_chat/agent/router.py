"""The router: one model call that picks which handler takes the customer's message, from the options of the conversation's phase."""

import logging
import re

from ole5_chat import llm
from ole5_chat.agent import prompt
from ole5_chat.agent.contracts import Reply, route_schema
from ole5_chat.agent.state import ChatState
from ole5_chat.agent.validate import SORRY

log = logging.getLogger(__name__)

_USER_SPECIFIC_RE = re.compile(
    r"\b(?:my|our|their|his|her)\s+(?:user\s+)?"
    r"(?:account|profile|email|phone|mobile|permissions?|role|personal data|personal information|records?)\b"
    r"|\b(?:what|tell me|show me|reveal)\s+(?:is\s+)?my password\b"
    r"|\b(?:account|profile|user|customer)\s+(?:details|information|data|record|records)\b"
    r"|\b(?:user|customer|employee|person)\b.{0,40}\b(?:account|profile|email|phone|mobile|permissions?|role|personal data|personal information|records?|details)\b"
    r"|\b(?:show|check|look up|tell me|change|update)\s+(?:my|the user's|their)\s+"
    r"(?:account|profile|email|phone|permissions?|role|personal data|personal information)\b",
    re.IGNORECASE,
)
_ARABIC_USER_SPECIFIC_RE = re.compile(
    r"(?:حسابي|حسابنا|بياناتي|معلوماتي|بريدي(?: الإلكتروني)?|رقم(?: هاتفي| جوالي)|"
    r"صلاحياتي|ملفي الشخصي|ما كلمة مروري|ما هي كلمة مروري|أخبرني بكلمة مروري|"
    r"حساب المستخدم|بيانات المستخدم|معلومات المستخدم|"
    r"صلاحيات المستخدم|بيانات العميل|معلومات العميل|سجل المستخدم)",
)


def is_user_specific_request(text: str) -> bool:
    """True for requests about customer/user data that the chat cannot access."""

    return bool(_USER_SPECIFIC_RE.search(text) or _ARABIC_USER_SPECIFIC_RE.search(text))


def router(state: ChatState) -> dict:
    phase = state["phase"]
    latest = next((message.text for message in reversed(state["messages"]) if message.type == "human"), "")
    if is_user_specific_request(latest):
        log.info("router  scope=user_specific next=ticket")
        return {"next": "ticket"}
    try:
        decision = llm.structured(prompt.router(state), state["messages"], route_schema(phase), node="router")
    except llm.ModelFailed as exc:
        # No guess at the route: the customer is asked to send the message again and the state stays as it was.
        log.error("ERROR router  phase=%s model call failed: %s", phase, exc)
        language = state.get("language") or state["identity"].language
        return {"next": None, "reply": Reply(say=SORRY[language], sources=[]),
                "corrections": state["corrections"] + [f"router failed: {exc}"]}
    log.info("router  phase=%s next=%s", phase, decision.next)
    return {"next": decision.next}
