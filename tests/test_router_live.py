"""router against the real model on fixed cases: prints what it chose for each, the accuracy and the median latency of one call. Runs only with RUN_LIVE=1."""

import logging
import os
import re
import statistics
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import PendingIssue
from ole5_chat.agent.router import router
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity

REAL = llm.structured

GREETING = "أهلاً بك، أنا مساعد دعم Ole5. أجيب عن أسئلتك حول النظام وأفتح لك تذكرة دعم عند الحاجة. كيف أقدر أساعدك اليوم؟"
OFFER = "I don't have an answer for that. Would you like me to open a ticket?"
ERROR = "What does the error message say?"
ANYTHING_ELSE = "Is there anything else you want to add?"
SUMMARY = "Your ticket summary is ready. Shall I submit it?"
ARCHIVE = "The archive button does nothing"
DENIED = "Access denied when opening a correspondence"
ANSWER = "To add a watermark, open the document settings and choose Watermark."

# phase, the issue being handled, the assistant's last message, the customer's message, the expected next.
CASES = [
    ("none", None, GREETING, "كيف أضيف علامة مائية؟", "question"),
    ("none", None, GREETING, "مرحبا", "talk"),
    ("none", None, GREETING, "open a ticket for me about the archive button", "question"),
    ("none", None, GREETING, "نبي ننقل بياناتنا من النظام القديم", "ticket"),
    ("none", None, GREETING, "ولا موظف عندنا يقدر يسجل دخول", "urgent"),
    ("none", None, GREETING, "وش صار على تذكرتي؟", "status"),
    ("none", None, GREETING, "what's the status of my tickets?", "status"),
    ("offer_ticket", ARCHIVE, OFFER, "ايه", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "yes please", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "لا شكراً", "decline"),
    ("offer_ticket", ARCHIVE, OFFER, "no, it's fine", "decline"),
    ("offer_ticket", ARCHIVE, OFFER, "طيب وكيف أغير كلمة المرور؟", "question"),
    ("collect", DENIED, ERROR, "تطلع رسالة Access denied", "details"),
    ("collect", DENIED, ERROR, "ما أعرف", "details"),
    ("collect", DENIED, ERROR, "[attached: screen.png]", "details"),
    ("collect", DENIED, ERROR, "هذا كل شيء", "done"),
    ("collect", DENIED, ANYTHING_ELSE, "لا", "done"),
    ("collect", DENIED, ERROR, "خلاص ألغ التذكرة", "cancel"),
    ("collect", DENIED, ERROR, "ايش يعني درجة السرية؟", "question"),
    ("confirm", DENIED, SUMMARY, "ارسلها", "yes"),
    ("confirm", DENIED, SUMMARY, "submit it", "yes"),
    ("confirm", DENIED, SUMMARY, "خل الأولوية عالية", "change"),
    ("confirm", DENIED, SUMMARY, "لا، ألغها", "cancel"),
    ("confirm", DENIED, SUMMARY, "كم ياخذ الرد عادة؟", "question"),
    ("none", None, GREETING, "النظام ما يفتح عند أحد في الوزارة من الصبح", "urgent"),
    ("offer_ticket", ARCHIVE, OFFER, "ايه بس قبل كذا كيف أرفق ملف؟", "question"),
    ("collect", DENIED, ERROR, "يظهر الخطأ لما أضغط حفظ، وهذا كل اللي عندي", "done"),
    ("collect", DENIED, ERROR, "بعد عندي مشكلة ثانية: نبي نرحل بيانات", "ticket"),
    ("confirm", DENIED, SUMMARY, "ممكن تغير العنوان وبعدها أرسلها", "change"),
    ("confirm", DENIED, SUMMARY, "تمام", "yes"),
]

# Messages that appear nowhere in the router prompt.
HELD_OUT = [
    ("offer_ticket", ARCHIVE, OFFER, "أبي أفتح تذكرة", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "go ahead", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "ok sure, افتحها", "accept"),
    ("offer_ticket", ARCHIVE, OFFER, "مو لازم، بشوف بنفسي", "decline"),
    ("offer_ticket", ARCHIVE, OFFER, "لا ما يحتاج thanks", "decline"),
    ("none", None, GREETING, "my ticket number is T-123, any update?", "status"),
    ("none", None, GREETING, "رفعت تذكرة أمس، وصلتكم؟", "status"),
    ("none", None, GREETING, "كل المستخدمين عندنا طالعين من النظام وما أحد يقدر يدخل", "urgent"),
    ("none", None, GREETING, "nobody in our ministry can log in since 8am, شغلنا واقف", "urgent"),
    ("none", None, GREETING, "أبي أرحّل الأرشيف القديم حقنا للنظام", "ticket"),
    ("none", None, GREETING, "how do I forward a correspondence? أبي الخطوات", "question"),
    ("none", None, GREETING, "شكراً على المساعدة", "talk"),
    ("none", None, GREETING, "I can't log in to my account", "question"),
    ("collect", DENIED, ERROR, "تطلع Access denied، بس هل هذا يعني ما عندي صلاحية؟", "details"),
    ("collect", DENIED, ERROR, "it says error 403 forbidden", "details"),
    ("collect", DENIED, ERROR, "مدري والله", "details"),
    ("collect", DENIED, ERROR, "ما أبي التذكرة خلاص، انحلت", "cancel"),
    ("collect", DENIED, ANYTHING_ELSE, "no that's all", "done"),
    ("collect", DENIED, ERROR, "That's all I have, I don't know anything else", "done"),
    ("confirm", DENIED, SUMMARY, "the title is wrong", "change"),
    ("confirm", DENIED, SUMMARY, "يب تمام أرسل", "yes"),
    ("confirm", DENIED, SUMMARY, "غيرت رأيي، لا ترسلها", "cancel"),
    ("confirm", DENIED, SUMMARY, "وش يعني الأولوية متوسطة؟", "question"),
    ("confirm", DENIED, "I don't have information on how long a reply takes. The summary is ready whenever you want "
                        "to send it.", "تمام", "yes"),
    ("confirm", DENIED, "I changed the priority to high. The updated summary is shown.", "تمام", "yes"),
    ("confirm", DENIED, "I changed the priority to high. Shall I submit it now?", "اوكي", "yes"),
    ("none", None, ANSWER, "وعلى الآيباد؟", "question"),
    ("none", None, ANSWER, "and can I change its color?", "question"),
    ("none", None, ANSWER, "ايه", "talk"),
    ("none", None, ANSWER, "ok thanks", "talk"),
    ("none", None, ANSWER, "ممكن تعيد الخطوات؟", "talk"),
    ("none", None, GREETING, "وش تقدر تسوي؟", "talk"),
    ("none", None, GREETING, "who are you?", "talk"),
    ("none", None, GREETING, "عندي استفسار", "talk"),
    ("none", None, GREETING, "something is wrong", "talk"),
    ("offer_ticket", ARCHIVE, OFFER, "انت روبوت؟", "talk"),
    ("collect", DENIED, ERROR, "وين ألقى رقم الخطأ؟", "question"),
    ("confirm", DENIED, SUMMARY, "how do I attach a file to a ticket?", "question"),
]


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the real model")
def test_router_live(monkeypatch, caplog):
    monkeypatch.setattr(llm, "structured", REAL)
    identity = Identity(customer_id="live", email="ole5@example.com", name="Sara Omar", organization="Test Org",
                        product="OLE5", language="ar")
    rows, latencies, calls = [], [], []
    for phase, issue, assistant, customer, expected in CASES + HELD_OUT:
        state = initial_state(identity)
        state["phase"] = phase
        state["pending"] = [PendingIssue(text=issue, source="no_answer")] if issue else []
        state["messages"] = [AIMessage(content=assistant), HumanMessage(content=customer)]
        caplog.clear()
        started = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger=llm.__name__):
            out = router(state)
        latencies.append(time.perf_counter() - started)
        slow = [int(m.group(1)) for r in caplog.records if (m := re.search(r"attempts=(\d+)", r.getMessage()))]
        calls.append((latencies[-1], slow[0] if slow else 1, phase, customer))
        rows.append((phase, customer, expected, out["next"], out.get("corrections", [])))
    print()
    for phase, customer, expected, got, corrections in rows:
        print(f"{'ok  ' if expected == got else 'MISS'} {phase:<12} {expected:<8} {got:<8} {customer} {corrections}")
    for name, part in (("examples", rows[:len(CASES)]), ("held out", rows[len(CASES):])):
        hits = sum(expected == got for _, _, expected, got, _ in part)
        print(f"{name}: accuracy {hits}/{len(part)} = {hits / len(part):.0%}")
    print(f"median latency {statistics.median(latencies):.2f}s\nslowest 5 calls:")
    for seconds, attempts, phase, customer in sorted(calls, reverse=True)[:5]:
        print(f"  {seconds:.2f}s attempts={attempts} {phase:<12} {customer}")
    assert not any(corrections for *_, corrections in rows), "a router call failed"
