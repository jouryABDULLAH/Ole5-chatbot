"""Deterministic scope gate used by Search."""

import re


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
    r"صلاحيات المستخدم|بيانات العميل|معلومات العميل|سجل المستخدم)"
)


def is_user_specific_request(text: str) -> bool:
    """Return true for customer data the chat cannot access."""

    return bool(_USER_SPECIFIC_RE.search(text) or _ARABIC_USER_SPECIFIC_RE.search(text))
