"""router_v2 against the real model on fixed cases: prints what it chose for each, the accuracy and the median latency of one call. Runs only with RUN_LIVE=1."""

import os
import statistics
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import PendingIssue
from ole5_chat.agent.router_v2 import router_v2
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity

REAL = llm.structured

GREETING = "أهلاً بك، أنا مساعد دعم Ole5. أجيب عن أسئلتك حول النظام وأفتح لك تذكرة دعم عند الحاجة. كيف أقدر أساعدك اليوم؟"
OFFER = "I don't have an answer for that. Would you like me to open a ticket?"
ERROR = "What does the error message say?"
ANYTHING_ELSE = "Is there anything else you want to add?"
SUMMARY = "Your ticket summary is ready. Press Submit to send it."
ARCHIVE = "The archive button does nothing"
DENIED = "Access denied when opening a correspondence"

# phase, the issue being handled, the assistant's last message, the customer's message, the expected next.
CASES = [
    ("none", None, GREETING, "كيف أضيف علامة مائية؟", "chat"),
    ("none", None, GREETING, "مرحبا", "chat"),
    ("none", None, GREETING, "open a ticket for me about the archive button", "chat"),
    ("none", None, GREETING, "نبي ننقل بياناتنا من النظام القديم", "ticket"),
    ("none", None, GREETING, "ولا موظف عندنا يقدر يسجل دخول", "urgent"),
    ("none", None, GREETING, "وش صار على تذكرتي؟", "status"),
    ("none", None, GREETING, "what's the status of my tickets?", "status"),
    ("offer_ticket", ARCHIVE, OFFER, "ايه", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "yes please", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "لا شكراً", "decline"),
    ("offer_ticket", ARCHIVE, OFFER, "no, it's fine", "decline"),
    ("offer_ticket", ARCHIVE, OFFER, "طيب وكيف أغير كلمة المرور؟", "chat"),
    ("collect", DENIED, ERROR, "تطلع رسالة Access denied", "details"),
    ("collect", DENIED, ERROR, "ما أعرف", "details"),
    ("collect", DENIED, ERROR, "[attached: screen.png]", "details"),
    ("collect", DENIED, ERROR, "هذا كل شيء", "done"),
    ("collect", DENIED, ANYTHING_ELSE, "لا", "done"),
    ("collect", DENIED, ERROR, "خلاص ألغ التذكرة", "cancel"),
    ("collect", DENIED, ERROR, "ايش يعني درجة السرية؟", "chat"),
    ("confirm", DENIED, SUMMARY, "ارسلها", "yes"),
    ("confirm", DENIED, SUMMARY, "submit it", "yes"),
    ("confirm", DENIED, SUMMARY, "خل الأولوية عالية", "change"),
    ("confirm", DENIED, SUMMARY, "لا، ألغها", "cancel"),
    ("confirm", DENIED, SUMMARY, "كم ياخذ الرد عادة؟", "chat"),
    ("none", None, GREETING, "النظام ما يفتح عند أحد في الوزارة من الصبح", "urgent"),
    ("offer_ticket", ARCHIVE, OFFER, "ايه بس قبل كذا كيف أرفق ملف؟", "chat"),
    ("collect", DENIED, ERROR, "يظهر الخطأ لما أضغط حفظ، وهذا كل اللي عندي", "details"),
    ("collect", DENIED, ERROR, "بعد عندي مشكلة ثانية: نبي نرحل بيانات", "ticket"),
    ("confirm", DENIED, SUMMARY, "ممكن تغير العنوان وبعدها أرسلها", "change"),
    ("confirm", DENIED, SUMMARY, "تمام", "yes"),
]


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the real model")
def test_router_v2_live(monkeypatch):
    monkeypatch.setattr(llm, "structured", REAL)
    identity = Identity(customer_id="live", email="ole5@example.com", name="Sara Omar", organization="Test Org",
                        product="OLE5", language="ar")
    rows, latencies = [], []
    for phase, issue, assistant, customer, expected in CASES:
        state = initial_state(identity)
        state["chat_phase"] = phase
        state["pending"] = [PendingIssue(text=issue, source="no_answer")] if issue else []
        state["messages"] = [AIMessage(content=assistant), HumanMessage(content=customer)]
        started = time.perf_counter()
        out = router_v2(state)
        latencies.append(time.perf_counter() - started)
        rows.append((phase, customer, expected, out["next"], out.get("corrections", [])))
    hits = sum(expected == got for _, _, expected, got, _ in rows)
    print()
    for phase, customer, expected, got, corrections in rows:
        print(f"{'ok  ' if expected == got else 'MISS'} {phase:<12} {expected:<8} {got:<8} {customer} {corrections}")
    print(f"accuracy {hits}/{len(rows)} = {hits / len(rows):.0%}, median latency {statistics.median(latencies):.2f}s")
    assert not any(corrections for *_, corrections in rows), "a router call failed"
