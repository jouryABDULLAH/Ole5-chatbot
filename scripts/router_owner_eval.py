"""Live Router/Manager interpretation evaluation; no graph, retrieval or ticket calls.

Offline review: python scripts/router_owner_eval.py --list-cases
Offline checks: python scripts/router_owner_eval.py --self-test
Live (run explicitly): python scripts/router_owner_eval.py --runs 3 --layer both

Results go to live_tests_results/. Existing files are never overwritten. Use
--output-dir for another run. --report-from regenerates a report from saved JSON
without model calls (choose a fresh output directory).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, create_model

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DEFAULT_DIR = ROOT / "live_tests_results"
Route = Literal["question", "pending_response", "ticket_status", "escalation", "other"]
RouteWithoutPending = Literal["question", "ticket_status", "escalation", "other"]
Pending = Literal["none", "offer", "detail", "summary"]
OFFER_COMMON = ("update_impact", "separate_issue", "clarify")
DETAIL_COMMON = ("cancel", "update_impact", "separate_issue")
SUMMARY_COMMON = ("cancel", "separate_issue", "clarify")
MANAGER_OPTIONS = {
    "none": ("register_issue", "urgent_issue", "failed_solution", "clarify"),
    "offer": ("accept", "decline") + OFFER_COMMON,
    "detail": ("provide", "skip_detail", "not_an_answer") + DETAIL_COMMON,
    "summary": ("submit", "edit_summary") + SUMMARY_COMMON,
}


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Route
    explicit_ticket_request: bool
    reason: str


class RouterDecisionWithoutPending(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: RouteWithoutPending
    explicit_ticket_request: bool
    reason: str


ROUTER_SCHEMAS = {
    "none": RouterDecisionWithoutPending,
    "offer": RouterDecision,
    "detail": RouterDecision,
    "summary": RouterDecision,
}


def manager_schema(kind: str) -> type[BaseModel]:
    return create_model(
        f"Manager_{kind}", __config__=ConfigDict(extra="forbid", strict=True),
        action=(Literal[MANAGER_OPTIONS[kind]], ...), reason=(str, ...),
    )


SCHEMAS = {kind: manager_schema(kind) for kind in MANAGER_OPTIONS}


@dataclass(frozen=True)
class Case:
    case_id: str
    history: tuple[tuple[str, str], ...]
    pending: Pending
    item: str | None
    active_issue: str | None
    draft: str | None
    created: tuple[str, ...]
    expect_route: Route
    expect_wants: bool
    expect_action: str | None
    group: str
    issue_type: str | None = None
    note: str = ""
    summary_shown: bool = False
    submission_requested: bool = False


# Synthetic conversation fixtures; these are not claims about actual product behavior.
GREETING = "أهلاً بك، أنا مساعد دعم Ole5. كيف أقدر أساعدك اليوم؟"
ANSWER = "لإضافة علامة مائية، اختر علامة مائية من إعدادات المراسلة ثم احفظ."
ANSWER2 = "لأرشفة المراسلة، افتحها واختر الأرشفة."
OFFER = "ما لقيت إجابة لهذا في التوثيق. تبيني أجهز لك تذكرة دعم؟"
ASK_BROWSER = "عشان أكمل التذكرة، ما اسم المتصفح الذي تستخدمه؟"
ASK_SCREEN = "في أي شاشة تظهر المشكلة؟"
ASK_ERROR = "ما نص رسالة الخطأ التي تظهر لك؟"
SUMMARY = "هذا ملخص التذكرة: العنوان: زر الأرشفة لا يعمل. الأولوية: متوسطة. أرسلها؟"
ERROR = "عذراً، حدث خطأ. أعد إرسال رسالتك."
CREATED = "تم إنشاء التذكرة رقم 123. سيصلك الرد على بريدك المسجل."
STATUS = "حالة التذكرة رقم 123: غير متوفرة."
ARCHIVE = "زر الأرشفة لا يعمل"
LOGIN = "فشل تسجيل الدخول"


def conversation(*texts: str) -> tuple[tuple[str, str], ...]:
    """Fixtures start with assistant, alternate, and end before the tested user reply."""
    return tuple(("assistant" if i % 2 == 0 else "customer", s) for i, s in enumerate(texts))


def build_cases() -> list[Case]:
    cases: list[Case] = []
    greeting = conversation(GREETING)
    answered = conversation(GREETING, "كيف أضيف علامة مائية؟", ANSWER)
    created_history = conversation(GREETING, ARCHIVE, SUMMARY, "أرسل ملخص التذكرة كما هو", CREATED)
    status_history = (*created_history, ("customer", "وش صار على تذكرتي؟"), ("assistant", STATUS))

    def add(cid, customer, route, wants=False, action=None, *, history=greeting,
            pending="none", item=None, issue=None, draft=None, created=(),
            group="nothing_pending", issue_type=None, note="", summary_shown=None,
            submission_requested=None):
        if summary_shown is None:
            summary_shown = pending == "summary" and history[-1][1] == SUMMARY
        if submission_requested is None:
            submission_requested = summary_shown
        cases.append(Case(cid, (*history, ("customer", customer)), pending, item,
                          issue, draft, tuple(created), route, wants, action, group, issue_type,
                          note, summary_shown, submission_requested))

    # Hand-labelled targets; no substring-derived gold labels.
    for cid, customer, route, wants, action in [
        ("n01", "كيف أضيف علامة مائية؟", "question", False, None),
        ("n02", "زر الأرشفة ما يشتغل", "question", False, None),
        ("n03", "ودي أفتح تذكرة عن زر الأرشفة", "question", True, None),
        ("n04", "open a ticket for me about the archive button", "question", True, None),
        ("n05", "ما أبي تذكرة، بس قولي كيف أأرشف", "question", False, None),
        ("n06", "هل أحتاج تذكرة عشان أأرشف؟", "question", False, None),
        ("n08", "ما أقدر أدخل النظام", "question", False, None),
        ("n09", "افتح لي تذكرة", "escalation", True, "clarify"),
        ("n10", "نبي ننقل بياناتنا من النظام القديم", "escalation", False, "register_issue"),
        ("n11", "أبي أرحّل الأرشيف القديم حقنا للنظام", "escalation", False, "register_issue"),
        ("n12", "ولا موظف عندنا يقدر يسجل دخول", "escalation", False, "urgent_issue"),
        ("n13", "ما أحد في الجهة يقدر يدخل النظام من الصباح", "escalation", False, "urgent_issue"),
        ("n22", "وش ساعات الدعم؟", "other", False, None),
        ("n23", "مرحبا", "other", False, None),
        ("n24", "what can you do?", "other", False, None),
        ("n25", "ايش اسم الشركة المسؤولة عن اوليفاي؟", "other", False, None),
        ("n26", "مين سواك؟", "other", False, None),
        ("n27", "وش أفضل مطعم في الرياض؟", "other", False, None),
        ("n30", "ايه", "other", False, None),
        ("n31", "عندي مشكلة", "other", False, None),
        ("n33", "الأرشفة", "other", False, None),
    ]:
        add(cid, customer, route, wants, action)
    add("n07", "وعلى الآيباد؟", "question", history=answered)
    add("n14", "جربت الخطوات ولا زال ما يشتغل", "escalation", action="failed_solution", history=answered)
    add("n15", "لا زال لا يعمل", "escalation", action="failed_solution", history=answered)
    for cid, customer in [("n16", "وش صار على تذكرتي؟"), ("n17", "وش بيصير بعدها؟"),
                          ("n18", "وين يوصل الرد؟"), ("n19", "كم ياخذ الرد عادة؟"),
                          ("n20", "كيف أعرف إذا ردوا علي؟")]:
        add(cid, customer, "ticket_status" if cid == "n16" else "other", history=created_history, created=("123",))
    add("n21", "ما معنى غير متوفرة؟", "ticket_status", history=status_history, created=("123",))
    add("n28", "تمام شكراً", "other", history=answered)
    add("n29", "اشرحها ببساطة", "other", history=answered)
    add("n32", "الثانية ما فهمتها", "other",
        history=(*answered, ("customer", "وكيف أأرشف؟"), ("assistant", ANSWER2)),
        note="Exactly two answers identify the second; rephrase, not unresolved reference.")
    add("n34", "الثانية", "other", note="No numbered items or resolvable referent; Respond asks a focused clarification.")

    offer_history = conversation(GREETING, "زر الأرشفة ما يشتغل", OFFER)
    for cid, customer, route, action in [
        ("o01", "ايه", "pending_response", "accept"),
        ("o02", "ايوه افتحها", "pending_response", "accept"),
        ("o03", "لا", "pending_response", "decline"),
        ("o04", "لا شكراً، بشوفها بنفسي", "pending_response", "decline"),
        ("o05", "خلاص ما أبي", "pending_response", "decline"),
        ("o06", "كيف أضيف علامة مائية؟", "question", None),
        ("o07", "وش حالة تذكرتي السابقة؟", "ticket_status", None),
        ("o08", "شكراً", "other", None),
        ("o09", "مين سواك؟", "other", None),
        ("o10", "وبعد، ولا موظف يقدر يدخل النظام الحين", "escalation", "separate_issue"),
    ]:
        add(cid, customer, route, action=action, history=offer_history, pending="offer",
            issue=ARCHIVE, group="offer", created=("122",) if cid == "o07" else (),
            note="New urgent issue differs from active archive issue; scoped action is separate_issue." if cid == "o10" else "")

    detail_history = conversation(GREETING, "ما أقدر أدخل النظام", OFFER, "ايه", ASK_BROWSER)
    for cid, customer, route, action in [
        ("d01", "Chrome", "pending_response", "provide"),
        ("d02", "أستخدم كروم", "pending_response", "provide"),
        ("d03", "ما أعرف", "pending_response", "skip_detail"),
        ("d04", "ما أبي أعطيك", "pending_response", "skip_detail"),
        ("d05", "ما عندي شي أضيفه", "pending_response", "skip_detail"),
        ("d06", "ليش تحتاج اسم المتصفح؟", "pending_response", "not_an_answer"),
        ("d07", "خلاص ضبط، ما أبي تذكرة", "pending_response", "cancel"),
        ("d08", "الحين ولا أحد في الجهة يقدر يدخل", "escalation", "update_impact"),
        ("d09", "وبعد زر الأرشفة ما يشتغل", "question", None),
        ("d10", "ايه", "pending_response", "not_an_answer"),
        ("d11", "كيف أصدر تقرير؟", "question", None),
        ("d12", 'هل "الوصول مرفوض" يعني ما عندي صلاحية؟', "question", None),
        ("d13", "وين يوصل الرد لما تنفتح التذكرة؟", "other", None),
        ("d14", "شكراً", "other", None),
        ("d15", "وش تقدر تسوي؟", "other", None),
    ]:
        add(cid, customer, route, action=action, pending="detail", item="browser name",
            issue=LOGIN, history=detail_history, group="detail", issue_type="password",
            draft="Login failure | browser not provided",
            note="Explicit cancellation is present; not testing implicit resolution." if cid == "d07" else "")
    for cid, customer in [("s01", "الأرشفة"), ("s02", "صفحة المراسلات الواردة")]:
        add(cid, customer, "pending_response", action="provide", pending="detail", item="page",
            issue="تظهر رسالة Access denied", draft="Access denied | page not provided", issue_type="bug",
            history=conversation(GREETING, "تطلع لي رسالة Access denied", OFFER, "ايه", ASK_SCREEN), group="screen")
    for cid, customer, action in [("e01", "تطلع رسالة Access denied", "provide"),
                                  ("e02", "[attached: screen.png]", "provide"),
                                  ("e03", "ما أذكر نص الرسالة", "skip_detail")]:
        add(cid, customer, "pending_response", action=action, pending="detail", item="error text or screenshot",
            issue=ARCHIVE, draft="Archive failure | error not provided", issue_type="bug",
            history=conversation(GREETING, ARCHIVE, OFFER, "ايه", ASK_ERROR), group="error_detail",
            note="Attachment marker only; no image reading or attachment validation is evaluated." if cid == "e02" else "")
    add("y01", "ايه", "pending_response", action="provide", pending="detail", item="whether an error appears",
        issue=LOGIN, draft="Login failure | error occurrence not provided", group="yes_no_detail",
        history=conversation(GREETING, LOGIN, OFFER, "ايه", "هل تظهر رسالة خطأ؟"))
    for cid, customer, route, action in [
        ("m01", "ايه", "pending_response", "submit"),
        ("m02", "تمام أرسلها", "pending_response", "submit"),
        ("m03", "اوكي", "pending_response", "submit"),
        ("m04", "خلّ الأولوية عالية", "pending_response", "edit_summary"),
        ("m05", "غيّر العنوان إلى: الأرشفة لا تحفظ", "pending_response", "edit_summary"),
        ("m06", "لا تبعثها", "pending_response", "cancel"),
        ("m07", "غيرت رأيي، ما أبي التذكرة", "pending_response", "cancel"),
        ("m08", "ايه", "pending_response", "clarify"),
        ("m09", "كيف أضيف علامة مائية؟", "question", None),
        ("m10", "وش بيصير بعد ما أرسلها؟", "other", None),
        ("m11", "شكراً", "other", None),
        ("m12", "ولا موظف يقدر يدخل الحين، هذي أهم", "escalation", "separate_issue"),
    ]:
        add(cid, customer, route, action=action, pending="summary", issue=ARCHIVE,
            draft="زر الأرشفة لا يعمل | متوسطة", group="summary", issue_type="bug",
            history=conversation(GREETING, ARCHIVE, OFFER, "ايه", ERROR if cid == "m08" else SUMMARY),
            note="Summary state exists but actual last output was an error; no consent inferred." if cid == "m08" else "")

    # Additional contrasting cases not copied verbatim into the prompts. Diagnostic,
    # not a claim of a blind held-out dataset; freeze before tuning prompts.
    for cid, customer, route, wants, action in [
        ("x01", "افتحوا لي تذكرة لنقل البيانات", "escalation", True, "register_issue"),
        ("x02", "كل موظفي الجهة عاجزين عن الدخول، جهز تذكرة", "escalation", True, "urgent_issue"),
        ("x03", "هل ممكن نفتح تذكرة لو واجهتنا مشكلة؟", "other", False, None),
        ("x04", "أريد تذكرة بشأن اختفاء المرفقات", "question", True, None),
        ("x05", "لا تفتح تذكرة، اشرح لي طريقة البحث", "question", False, None),
        ("x06", "هل عندكم دعم هاتفي؟", "other", False, None),
        ("x07", "السلام عليكم كيف يمكنك مساعدتي", "other", False, None),
    ]:
        add(cid, customer, route, wants, action, group="contrasts")
    add("x08", "هل تظهر نفس الخيارات على الجوال؟", "question", history=answered, group="contrasts")
    add("x09", "أحتاج شرحاً أبسط للخطوة التي قلتها", "other", history=answered, group="contrasts")
    # Paraphrased holdout cases. These are intentionally not copied into the
    # prompt rules or seed-example tables.
    heldout_answer = conversation(
        "Hello, I am the Ole5 support assistant.",
        "How do I export a report?",
        "Open Reports, choose Export, and select the required format.",
    )
    heldout_detail = conversation(
        "Hello, I am the Ole5 support assistant.",
        "The archive button is not working.",
        "Would you like me to prepare a support ticket?",
        "Yes, prepare it.",
        "Which browser are you using?",
    )
    heldout_summary = conversation(
        "Hello, I am the Ole5 support assistant.",
        "The archive button is not working.",
        "Would you like me to prepare a support ticket?",
        "Yes, prepare it.",
        "Here is the ticket summary. Shall I submit it?",
    )
    heldout_error_summary = conversation(
        "Hello, I am the Ole5 support assistant.",
        "The archive button is not working.",
        "Would you like me to prepare a support ticket?",
        "Yes, prepare it.",
        "Here is the ticket summary. Shall I submit it?",
        "I am still waiting for the summary.",
        "An error occurred while displaying the summary. Please try again.",
    )
    add("h01", "How can I export a report from the archive?", "question", history=greeting, group="heldout")
    add("h02", "Please open a ticket about the report export button.", "question", True, history=greeting, group="heldout")
    add("h03", "Could you explain that in simpler words?", "other", history=heldout_answer, group="heldout")
    add("h04", "Thanks", "other", history=heldout_summary, pending="summary", issue=ARCHIVE,
        draft="Archive failure", group="heldout")
    add("h05", "I use Firefox.", "pending_response", action="provide", pending="detail", item="browser name",
        issue=LOGIN, draft="Login failure", history=heldout_detail, group="heldout")
    add("h06", "I do not remember.", "pending_response", action="skip_detail", pending="detail", item="browser name",
        issue=LOGIN, draft="Login failure", history=heldout_detail, group="heldout")
    add("h07", "Why do you need to know my browser?", "pending_response",
        action="not_an_answer", pending="detail", item="browser name", issue=LOGIN,
        draft="Login failure", history=heldout_detail, group="heldout")
    add("h08", "Yes", "pending_response", action="clarify", pending="summary", issue=ARCHIVE,
        draft="Archive failure", history=heldout_error_summary, group="heldout")
    add("h09", "Yes", "other", history=greeting, group="heldout")
    add("h10", "When should I expect the support reply?", "other", history=greeting, group="heldout")
    add("h11", "The instructions still did not fix it.", "escalation", action="failed_solution",
        history=heldout_answer, group="heldout")
    add("h12", "A separate department-wide sign-in outage started.", "escalation", action="separate_issue",
        pending="detail", item="browser name", issue=ARCHIVE, draft="Archive failure",
        history=heldout_detail, group="heldout")
    add("h13", "طيب", "other", history=greeting, group="heldout")
    add("h14", "عندي مشكلة", "other", history=greeting, group="heldout")
    add("h15", "Yes", "other", history=greeting, group="heldout")
    add("h16", "اشرحها بطريقة أبسط", "other", history=heldout_answer, group="heldout")
    add("h17", "الثانية ما فهمتها", "other", history=(*heldout_answer,
        ("customer", "How do I archive a message?"), ("assistant", "Open Archive and choose the message.")), group="heldout")
    add("h18", "ألغها", "pending_response", action="decline", pending="offer", issue=ARCHIVE,
        history=offer_history, group="heldout")
    add("h19", "ما أدري", "pending_response", action="skip_detail", pending="detail", item="browser name",
        issue=LOGIN, draft="Login failure", history=heldout_detail, group="heldout")
    add("h20", "ليش تحتاج اسم المتصفح؟", "pending_response", action="not_an_answer", pending="detail",
        item="browser name", issue=LOGIN, draft="Login failure", history=heldout_detail, group="heldout")
    add("h21", "ولا موظف في الجهة يقدر يدخل", "escalation", action="update_impact", pending="detail",
        item="browser name", issue=LOGIN, draft="Login failure", history=heldout_detail, group="heldout")
    add("h22", "ولا موظف في الجهة يقدر يدخل", "escalation", action="separate_issue", pending="detail",
        item="browser name", issue=ARCHIVE, draft="Archive failure", history=heldout_detail, group="heldout")
    add("h23", "Raise the priority to high", "pending_response", action="edit_summary", pending="summary",
        issue=ARCHIVE, draft="Archive failure", history=heldout_summary, group="heldout")
    add("h24", "Where will the support reply arrive?", "other", history=greeting, group="heldout")
    add("h25", "متى يوصل رد الدعم؟", "other", history=greeting, group="heldout")
    add("h26", "Can you explain that more simply?", "other", history=heldout_answer, group="heldout")
    add("h27", "I don't remember the browser", "pending_response", action="skip_detail", pending="detail",
        item="browser name", issue=LOGIN, draft="Login failure", history=heldout_detail, group="heldout")
    add("h28", "Please change the title to Archive failure", "pending_response", action="edit_summary",
        pending="summary", issue=ARCHIVE, draft="Archive failure", history=heldout_summary, group="heldout")
    return cases


ROUTER_RULES = """Interpret the latest customer message in context and choose its workflow.
Classify what the customer said. Do not plan the next ticket step, select missing fields,
or decide that a ticket should now be created. Workflow code does that after routing.

ROUTES AND BOUNDARIES
question: a product operation, rule, symptom or new condition needing documentation.
The message must ask or report something; mentioning a product or screen alone is insufficient.
An explicit ticket request naming an ordinary issue ALSO uses question: our policy searches first.
The words 'ticket' or 'open' do not override this policy. Escalation exceptions below take priority.

escalation: a configured always-ticket topic, a configured urgent situation, a report that
previously suggested steps did not resolve the issue, or a bare ticket request with no recoverable subject.
It sends the issue to Manager; it does not mean submit a ticket. Failed steps normally lead to an offer.
A new ordinary symptom during preparation is question. A configured urgent situation is escalation
even during preparation; Manager determines whether it updates the same issue or introduces another.
One person's login failure is not organization-wide urgency.

pending_response: a reply to or question about the ticket interaction explicitly recorded in
pending_kind and waiting_for. Includes offer replies, requested details, refusals, summary edits,
cancellation and summary confirmation. It is not a synonym for 'follow-up message'.
If pending_kind is none, NEVER choose pending_response. A previous product answer does not create
a pending ticket interaction. If pending_kind exists, not every message belongs to it: independent
product questions, process questions and social messages keep their own routes.
A recognizable attempt to answer a pending question belongs here even if the value is invalid;
Manager validates that answer. Do not choose the next collection question yourself.

ticket_status: status, receipt, number or meaning of an existing ticket's status. These use ticket
data or approved status definitions, not the product documentation workflow. Support-process facts
such as hours, reply channel and what happens next are handled by Respond from configuration.

other: greetings, thanks, assistant capabilities, basic company/provider identity (name, website,
who developed Ole5), support-process questions answered from configuration, off-topic requests,
unclear intents, or rephrasing a clearly identified previous product answer.
These remain other even if they mention Ole5 or occur during ticket preparation. Missing About facts
do not turn a company question into question or escalation. A rephrase changes wording; a new
product fact/condition such as mobile compatibility is question. Explaining a ticket status is
ticket_status; explaining the currently requested detail is pending_response.

The Router does not have a separate clarify route. Unclear intents, missing subjects and unresolved
references use other and Respond asks one focused clarification. 'I don't know' to a requested
detail is pending_response. A bare feature name with no contextual request is other. The same name
answering a requested page is pending_response. A request to explain is not inherently unclear;
use its identified subject and context.

IMPORTANT CONTRASTS
- 'الأرشفة' with nothing pending and no contextual question -> other; Respond asks what the customer means.
  'الأرشفة' after 'Which screen?' -> pending_response.
  'زر الأرشفة لا يعمل' as a new ordinary issue -> question.
- 'open a ticket for me about the archive button' -> question, explicit_ticket_request=true.
  'افتح لي تذكرة' with no subject -> escalation, explicit_ticket_request=true.
- 'جربت الخطوات وما نفعت' after a proposed solution -> escalation.
  'اشرحها ببساطة' after a product answer -> other, even though it is a follow-up.
  'وعلى الآيباد؟' after a product answer -> question because it asks about another condition.
- 'مين سواك؟', 'وش تقدر تسوي؟', 'ايش اسم الشركة المسؤولة عن اوليفاي؟' -> other.
  Do not reinterpret assistant/company identity as a question about product operation.
- 'وش ساعات الدعم؟', 'هل عندكم دعم هاتفي؟', 'وين يوصل الرد؟',
  'وش بيصير بعدها؟' after creation -> other; Respond uses support configuration.
  'ما معنى غير متوفرة؟' after a status reply -> ticket_status.
- 'Chrome', an error text or an attachment after the matching detail question -> pending_response.
  Do not infer a fresh ticket request because the detail helps prepare a ticket.
- 'ايه' after an offer or summary -> pending_response. 'ايه' with no pending question -> other; Respond asks what the customer means.
  'ايه' after a browser-name question -> pending_response; Manager handles the invalid value.
- 'مرحبا', 'شكراً', 'تمام' after an answer with nothing pending -> other.

BOOLEAN: explicit_ticket_request
True only when the latest message explicitly requests a NEW ticket preparation for its issue,
including a bare request. False for policy-required escalation, hypothetical questions, mentions,
negations, answers to requested details, acceptance of an existing offer, and summary submission consent.
This records expressed request, not 'needs a ticket', 'is ready for a ticket' or 'wants support'.
- 'نبي ننقل بياناتنا من النظام القديم' -> escalation, false (policy only).
- 'افتحوا تذكرة لنقل البيانات' -> escalation, true (explicit preparation request).
- 'ولا موظف يقدر يسجل دخول' -> escalation, false (impact report).
- 'لا زال لا يعمل' after steps -> escalation, false (failed solution).
- 'ايه افتحها' answering our existing offer -> pending_response, false.
- 'أرسلها' answering summary confirmation -> pending_response, false.
- 'هل ممكن نفتح تذكرة لو واجهتنا مشكلة؟' -> other, false (hypothetical process question answered from configuration).
- 'ما أبي تذكرة، اشرح لي البحث' -> question, false (negated request).

Return decision, explicit_ticket_request and one short reason identifying the message's meaning
and relevant context. Do not use the reason to plan collection, priority or submission.
"""

def definitions_only(text: str) -> str:
    """Remove prompt-specific example blocks while retaining boundaries and rules."""
    lines: list[str] = []
    skip_contrasts = False
    skip_boolean_examples = False
    for line in text.splitlines():
        if line == "IMPORTANT CONTRASTS":
            skip_contrasts = True
            continue
        if line.startswith("BOOLEAN:"):
            skip_contrasts = False
            skip_boolean_examples = True
            lines.append(line)
            continue
        if line.startswith("Return decision,"):
            skip_boolean_examples = False
            lines.append(line)
            continue
        if skip_contrasts or (skip_boolean_examples and line.startswith("- ")):
            continue
        if "->" in line or "e.g." in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def manager_meaning(action: str, prompt_variant: str) -> str:
    meaning = MEANINGS[action]
    if prompt_variant == "definitions":
        meaning = meaning.split(", e.g.", 1)[0]
    return meaning


MEANINGS = {
    "register_issue": "A described issue qualifies for preparation, including always-ticket topics, without the more specific urgent_issue or failed_solution trigger. Excludes bare requests with no subject.",
    "urgent_issue": "A configured urgent situation when nothing is pending; code registers/prepares it.",
    "failed_solution": "Customer reports that previously suggested steps failed. Prefer this specific label over register_issue; retain the original issue from history. This does not authorize submission.",
    "accept": "Accept the pending offer; includes open it/yes in response to that offer.",
    "decline": "Decline the pending offer, including no thanks or no longer wanting that offer.",
    "provide": "Customer supplies the requested detail: value, valid yes/no answer or attachment marker. Other missing details do not change this label.",
    "skip_detail": "Customer does not know, remember or want to provide the current detail, or says there is nothing more to add. Record the item unavailable and advance to the next required item.",
    "not_an_answer": "Customer did not provide the requested detail. If they ask why it is needed, Respond explains briefly; otherwise Respond asks them to provide it if they have the information.",
    "submit": "Consent to submit the displayed summary when the actual latest assistant turn asked for submission.",
    "edit_summary": "Request/provide an edit to the draft summary; code reclassifies and revalidates it.",
    "cancel": "Explicitly cancel or stop preparation (at an offer, simple rejection is decline).",
    "update_impact": "New impact facts establish configured urgency for the SAME active issue. A request to raise priority alone is not this action.",
    "separate_issue": "A distinct issue from active preparation, including a different urgent issue; code preserves FIFO.",
    "clarify": "No safe interpretation from the allowed actions: missing subject, invalid detail reply or ambiguous consent. Last resort.",
}


MANAGER_RULES = """Interpret the CUSTOMER'S latest message, not the assistant's next task.
Code chooses missing fields and state transitions after your interpretation. Do not decide what
to ask next, judge overall completeness, or describe how to create a ticket in your reason.

1. When a customer supplies the pending detail, choose provide even if other required details
   remain. Page after a page question, error text after an error question, and an attachment marker
   after a screenshot request are provide. not_an_answer is used when the customer did not provide
   the requested detail.
2. 'ما أعرف', 'ما أبي أعطيك' and 'ما عندي شي أضيفه' all use skip_detail.
   Record the current item unavailable and advance to the next required item. Do not force the
   customer to answer it, and do not end collection automatically.
3. Yes/no is provide for a yes/no detail. 'ايه' after asking for a browser name cannot supply a
   browser; use not_an_answer. Why the browser is needed -> not_an_answer; Respond explains why or
   asks the customer to provide it if they have the information. Do not encourage skipping it.
4. At summary, agreement is submit only when submission_requested=true and the actual last
   assistant message asked for submission. If summary_shown or submission_requested is false,
   or the last message was an error or another question, use clarify even if an older summary
   appears in the conversation.
5. Resolve issue identity before classifying urgency. A new login outage during an archive-button
   ticket is separate_issue. Organization-wide impact for the active login issue is update_impact.
   'Make priority high' at a summary is edit_summary; code reclassifies the edited draft.
6. When nothing is pending, configured urgency uses urgent_issue. Otherwise, if the message says
   previously suggested steps failed, choose failed_solution rather than the broader register_issue.
   Both may lead to preparation/offer, but failed_solution links back to the already discussed issue.
   Do not invent a new issue called 'still broken'. This label alone never authorizes submission.
7. Bare 'open a ticket' with no recoverable subject -> clarify. An always-ticket topic with a clear
   subject -> register_issue. At an existing offer, acceptance is accept; rejection is decline.
8. Choose clarify only when no available action can safely describe the customer's message.
Return action and a short reason naming the customer's meaning and relevant issue/item, not a plan.
"""


def pending_text(c: Case) -> str:
    return {"none": "nothing", "offer": "acceptance or rejection of an offer to prepare a ticket",
            "detail": f"the detail: {c.item}", "summary": "submission confirmation of the summary shown"}[c.pending]


def prompts(c: Case, config: dict, prompt_variant: str = "full") -> dict[str, str]:
    state = {"active_issue": c.active_issue, "pending_kind": c.pending, "waiting_for": pending_text(c),
             "known_created_ticket_numbers": c.created,
             "summary_shown": c.summary_shown,
             "submission_requested": c.submission_requested,
             "last_assistant_message": c.history[-2][1] if len(c.history) >= 2 else None}
    router_config = {k: v for k, v in config.items() if k != "required"}
    router_schema = ROUTER_SCHEMAS[c.pending]
    pending_rule = ("pending_response is available because a pending interaction exists."
                    if c.pending != "none" else
                    "No pending interaction exists, so pending_response is not an available route in this schema.")
    router_rules = ROUTER_RULES if prompt_variant == "full" else definitions_only(ROUTER_RULES)
    manager_rules = MANAGER_RULES if prompt_variant == "full" else definitions_only(MANAGER_RULES)
    router = (f"You are the Router for {config['name']}.\n"
              f"Product rules: {json.dumps(router_config, ensure_ascii=False)}\n"
              f"Conversation state: {json.dumps(state, ensure_ascii=False)}\n{pending_rule}\n{router_rules}\n"
              f"JSON schema: {json.dumps(router_schema.model_json_schema(), ensure_ascii=False)}")
    manager_state = {**state, "draft": c.draft, "requested_item": c.item}
    manager = (f"You are the Ticket Manager interpreter for {config['name']}. Interpret only; execute nothing.\n"
               f"Always-ticket: {config['always_ticket']}\nUrgency: {config['urgent_when']}\n"
               f"State: {json.dumps(manager_state, ensure_ascii=False)}\nActions available now:\n" +
               "\n".join(f"{a}: {manager_meaning(a, prompt_variant)}" for a in MANAGER_OPTIONS[c.pending]) +
               "\n" + manager_rules + "\nJSON schema: " +
               json.dumps(SCHEMAS[c.pending].model_json_schema(), ensure_ascii=False))
    return {"router": router, "manager": manager}


def validate_cases(cases: list[Case]) -> None:
    ids = set()
    for c in cases:
        assert c.case_id not in ids, c.case_id
        ids.add(c.case_id)
        assert c.expect_route in get_args(Route)
        assert c.pending in MANAGER_OPTIONS
        assert c.history and c.history[-1][0] == "customer"
        assert all(role in {"customer", "assistant"} and text.strip() for role, text in c.history)
        assert all(c.history[i][0] != c.history[i-1][0] for i in range(1, len(c.history)))
        assert (c.item is not None) == (c.pending == "detail"), c.case_id
        if c.pending != "none":
            assert c.active_issue, c.case_id
        if c.pending in {"detail", "summary"}:
            assert c.draft, c.case_id
        if c.submission_requested:
            assert c.pending == "summary" and c.summary_shown, c.case_id
        if c.expect_action:
            assert c.expect_action in MANAGER_OPTIONS[c.pending], c.case_id
            assert c.expect_route in {"escalation", "pending_response"}, c.case_id
        if c.expect_route in {"escalation", "pending_response"}:
            assert c.expect_action, c.case_id


def list_cases(cases: list[Case]) -> None:
    for c in cases:
        print(f"\n{c.case_id} [{c.group}] pending={c.pending}; item={c.item}; issue={c.active_issue}; draft={c.draft}")
        for role, message in c.history:
            print(f"  {role}: {message}")
        print(f"  EXPECT: {c.expect_route}; explicit_ticket_request={c.expect_wants}; manager={c.expect_action or 'n/a'}")
        if c.note:
            print(f"  LABEL NOTE: {c.note}")
    print(f"\n{len(cases)} Router cases; {sum(c.expect_action is not None for c in cases)} Manager cases.")


def call_model(layer: str, c: Case, system: str) -> dict:
    # Lazy imports keep list-cases, report regeneration and offline checks network-free.
    from langchain_core.messages import AIMessage, HumanMessage
    from ole5_chat import llm
    started = time.perf_counter()
    try:
        messages = [(AIMessage if role == "assistant" else HumanMessage)(content=txt) for role, txt in c.history]
        result = llm.structured(system, messages, ROUTER_SCHEMAS[c.pending] if layer == "router" else SCHEMAS[c.pending],
                                node=f"owner_eval_{layer}")
        return {"output": result.model_dump(), "error": None,
                "latency_ms": round((time.perf_counter()-started)*1000, 2)}
    except Exception as exc:
        # SDK/network failures are failures too, never semantic successes.
        return {"output": None, "error": {"type": type(exc).__name__, "message": str(exc)[:1000]},
                "latency_ms": round((time.perf_counter()-started)*1000, 2)}


def request_flag(output: dict):
    """Read current outputs and historical saved outputs without changing old results."""
    return output.get("explicit_ticket_request", output.get("wants_ticket"))


def trial_correct(row: dict, trial: dict, layer: str, part="joint") -> bool:
    result = trial.get(layer)
    if not result or result["error"] or not result["output"]:
        return False
    out, c = result["output"], row["case"]
    if layer == "manager":
        return out["action"] == c["expect_action"]
    route_ok = out["decision"] == c["expect_route"]
    wants_ok = request_flag(out) == c["expect_wants"]
    return route_ok if part == "route" else wants_ok if part == "wants" else route_ok and (c["expect_route"] != "question" or wants_ok)


def case_correct(row: dict, layer: str, runs: int, part="joint") -> bool:
    return len(row["trials"]) == runs and all(trial_correct(row, t, layer, part) for t in row["trials"])


def cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def ratio(n, d) -> str:
    return f"{n}/{d} ({n/d:.1%})" if d else "n/a (0 eligible)"


def render_report(payload: dict) -> str:
    rows, runs = payload["rows"], payload["runs"]
    layers = ["router", "manager"] if payload["layer"] == "both" else [payload["layer"]]
    out = ["# Router owner interpretation evaluation", "",
           f"Status: **{payload['status']}**. Repetitions: {runs}. Cases recorded: {len(rows)}/{payload['case_count']}. "
           f"Prompt variant: `{payload.get('prompt_variant', 'full')}`. Case group: `{payload.get('case_group', 'all')}`.", "",
           "A case passes only when all configured repetitions are correct. Trials are production-wrapper calls; "
           "the wrapper can retry internally. Counts below separate trial metrics from all-repetitions case metrics.", "",
           "Scoring policy: question-only-request-flag. The explicit preparation flag is scored only on "
           "cases whose EXPECTED route is question, regardless of the predicted route. Other routes ignore "
           "the flag for correctness. Routes and call errors are still scored on every eligible case. "
           "Historical results rendered here are rescored under this policy; this is not evidence of a model improvement.", "",
           "This evaluates interpretation only. No Search, API, guards, extraction payloads or state transitions run. "
           "Manager is measured independently even if Router misses. Cases overlap prompt examples; this is a "
           "diagnostic regression suite, not a blind generalization benchmark or a new baseline comparison.", "",
           "## Case pass rates", "", "| Layer / group | All repetitions correct |", "|---|---:|"]
    for layer in layers:
        eligible = [r for r in rows if layer == "router" or r["case"]["expect_action"]]
        parts = ["joint", "route", "wants"] if layer == "router" else ["joint"]
        for part in parts:
            scored = [r for r in eligible if r["case"]["expect_route"] == "question"] if part == "wants" else eligible
            out.append(f"| {layer} overall ({part}) | {ratio(sum(case_correct(r, layer, runs, part) for r in scored), len(scored))} |")
        field = "expect_route" if layer == "router" else "expect_action"
        for value in sorted({r["case"][field] for r in eligible}):
            group = [r for r in eligible if r["case"][field] == value]
            out.append(f"| {layer}: {value} | {ratio(sum(case_correct(r, layer, runs) for r in group), len(group))} |")
        if layer == "manager":
            for kind in MANAGER_OPTIONS:
                group = [r for r in eligible if r["case"]["pending"] == kind]
                out.append(f"| manager pending={kind} | {ratio(sum(case_correct(r, layer, runs) for r in group), len(group))} |")
    if payload["layer"] == "both":
        eligible = [r for r in rows if r["case"]["expect_action"]]
        n = sum(case_correct(r, "router", runs) and case_correct(r, "manager", runs) for r in eligible)
        out += ["", f"Interpretation chain (same Manager-eligible cases; route + action, flag only if expected question): **{ratio(n, len(eligible))}**.",
                "This is a paired correctness score, not execution of the graph or a recovery mechanism."]
    out += ["", "## Case-group pass rates", "", "| Layer / case group | All repetitions correct |", "|---|---:|"]
    for layer in layers:
        eligible = [r for r in rows if (layer == "router" or r["case"]["expect_action"])]
        for group_name in sorted({r["case"]["group"] for r in eligible}):
            group = [r for r in eligible if r["case"]["group"] == group_name]
            out.append(f"| {layer}: {group_name} | {ratio(sum(case_correct(r, layer, runs) for r in group), len(group))} |")
    out += ["", "## Clarification and other overuse (trial rates)", "",
            "| Layer | Clarify / all trials | Unexpected clarify / non-clarify targets | Correct clarify / clarify targets | Other / non-other targets |",
            "|---|---:|---:|---:|---:|"]
    for layer in layers:
        pairs = [(r, t[layer]) for r in rows for t in r["trials"] if layer in t]
        label = "decision" if layer == "router" else "action"
        expected = "expect_route" if layer == "router" else "expect_action"
        pred = lambda v: (v["output"] or {}).get(label) if not v["error"] else None
        non = [(r,v) for r,v in pairs if r["case"][expected] != "clarify"]
        yes = [(r,v) for r,v in pairs if r["case"][expected] == "clarify"]
        non_other = [(r,v) for r,v in pairs if r["case"][expected] != "other"]
        out.append(f"| {layer} | {ratio(sum(pred(v)=='clarify' for _,v in pairs), len(pairs))} | "
                   f"{ratio(sum(pred(v)=='clarify' for _,v in non), len(non))} | "
                   f"{ratio(sum(pred(v)=='clarify' for _,v in yes), len(yes))} | "
                   f"{ratio(sum(pred(v)=='other' for _,v in non_other), len(non_other)) if layer=='router' else 'n/a'} |")
    out += ["", "## Explicit preparation request flag errors", "",
            "Current field: `explicit_ticket_request`; historical field: `wants_ticket`. Expected meanings are unchanged.",
            "", "| Case / trial | Type | Expected | Returned | Reason |", "|---|---|---|---|---|"]
    router_pairs = [(r,i,t["router"]) for r in rows if r["case"]["expect_route"] == "question"
                    for i,t in enumerate(r["trials"],1) if "router" in t]
    for r,i,v in router_pairs:
        if v["output"] and not v["error"] and request_flag(v["output"]) != r["case"]["expect_wants"]:
            kind = "false positive" if request_flag(v["output"]) else "false negative"
            out.append(f"| {r['case']['case_id']} / {i} | {kind} | {r['case']['expect_wants']} | {request_flag(v['output'])} | {cell(v['output']['reason'])} |")
    for expected, label in [(False, "False positives"), (True, "False negatives")]:
        subset = [v for r,_,v in router_pairs if r["case"]["expect_wants"] == expected]
        misses = sum(bool(v["output"]) and not v["error"] and request_flag(v["output"]) != expected for v in subset)
        out.append(f"\n{label} / eligible trials: {ratio(misses, len(subset))}. Call errors are listed separately and fail joint accuracy.")
    out += ["", "## Misses", "", "| Case / layer | Customer | Expected | Outputs and reasons per trial | Pending context |", "|---|---|---|---|---|"]
    for r in rows:
        c = r["case"]
        for layer in layers:
            if layer == "manager" and not c["expect_action"]:
                continue
            if case_correct(r, layer, runs):
                continue
            expected = (f"{c['expect_route']}, explicit request={c['expect_wants'] if c['expect_route'] == 'question' else 'not scored'}"
                        if layer == "router" else c["expect_action"])
            returned = [json.dumps(t.get(layer, {"error": "not run"}), ensure_ascii=False) for t in r["trials"]]
            out.append(f"| {c['case_id']} / {layer} | {cell(c['history'][-1][1])} | {expected} | {cell(chr(10).join(returned))} | {cell(c['pending'])}; {cell(c['item'])}; {cell(c['active_issue'])} |")
    out += ["", "## Call errors", "", "| Case / trial / layer | Error |", "|---|---|"]
    for r in rows:
        for i,t in enumerate(r["trials"],1):
            for layer,v in t.items():
                if v["error"]:
                    out.append(f"| {r['case']['case_id']} / {i} / {layer} | {cell(json.dumps(v['error'], ensure_ascii=False))} |")
    out += ["", "## Latency", "", "| Layer | Calls | Median ms | P90 ms (nearest rank) |", "|---|---:|---:|---:|"]
    for layer in layers:
        times = sorted(t[layer]["latency_ms"] for r in rows for t in r["trials"] if layer in t)
        if times:
            out.append(f"| {layer} | {len(times)} | {statistics.median(times):.1f} | {times[math.ceil(.9*len(times))-1]:.1f} |")
    out += ["", "Latency includes wrapper retries and failed calls. No statistical independence is assumed across repetitions.", "",
            "The ايه contrast spans offer, yes/no detail, browser-name detail, summary, and no pending interaction. "
            "Inspect these together. Unknown/done collection transitions and explicit-request skip-to-summary are code behavior, "
            "not established by an interpreter action score."]
    return "\n".join(out) + "\n"


def self_test(cases: list[Case]) -> None:
    """Exercise reporting on pass/miss/error and all layer selections without live calls."""
    from copy import deepcopy
    config = {"name": "Test", "scope": "Test", "about": {}, "always_ticket": [], "urgent_when": [], "required": {}}
    for c in cases:
        for variant in ("full", "definitions"):
            built = prompts(c, config, variant)
            assert "JSON schema:" in built["manager"] and "JSON schema:" in built["router"]
        router_schema = ROUTER_SCHEMAS[c.pending].model_json_schema()
        route_values = router_schema["properties"]["decision"]["enum"]
        assert ("pending_response" in route_values) == (c.pending != "none")
        if c.expect_action:
            SCHEMAS[c.pending].model_validate({"action": c.expect_action, "reason": "fixture"})
    sample = [next(c for c in cases if c.case_id == id_) for id_ in ("n03", "o01", "n30")]
    for layer in ("router", "manager", "both"):
        rows = []
        for c in sample:
            trials = []
            for _ in range(3):
                t = {}
                if layer != "manager":
                    t["router"] = {"output": {"decision": c.expect_route, "explicit_ticket_request": c.expect_wants, "reason": "a | b\nc"}, "error": None, "latency_ms": 1}
                if layer != "router" and c.expect_action:
                    t["manager"] = {"output": {"action": c.expect_action, "reason": "fixture"}, "error": None, "latency_ms": 2}
                trials.append(t)
            rows.append({"case": asdict(c), "trials": trials})
        payload = {"rows": rows, "runs": 3, "layer": layer, "status": "complete", "case_count": 3}
        report = render_report(json.loads(json.dumps(payload)))
        assert "## Misses" in report
        if layer != "manager":
            assert "router overall (joint) | 3/3 (100.0%)" in report
            assert "router overall (wants) | 1/1 (100.0%)" in report
            ignored_flag = deepcopy(payload)
            ignored_flag["rows"][1]["trials"][0]["router"]["output"]["explicit_ticket_request"] = True
            assert case_correct(ignored_flag["rows"][1], "router", 3)
            assert "false positive" not in render_report(ignored_flag)
            bad_question_flag = deepcopy(payload)
            bad_question_flag["rows"][0]["trials"][0]["router"]["output"]["explicit_ticket_request"] = False
            assert not case_correct(bad_question_flag["rows"][0], "router", 3)
            assert case_correct(bad_question_flag["rows"][0], "router", 3, "route")
        if layer == "both":
            assert "**1/1 (100.0%)**" in report
        mutated = deepcopy(payload)
        if layer != "manager":
            mutated["rows"][0]["trials"][0]["router"]["output"]["decision"] = "other"
            assert not case_correct(mutated["rows"][0], "router", 3)
            mutated["rows"][0]["trials"][1]["router"]["output"]["explicit_ticket_request"] = False
        else:
            mutated["rows"][1]["trials"][0]["manager"]["error"] = {"type": "ModelFailed", "message": "offline synthetic error"}
            assert not case_correct(mutated["rows"][1], "manager", 3)
        assert "## Call errors" in render_report(mutated)
        if layer != "manager":
            assert "false negative" in render_report(mutated)
    assert "0 eligible" in render_report({"rows": [], "runs": 3, "layer": "both", "status": "interrupted", "case_count": len(cases)})
    assert request_flag({"wants_ticket": False}) is False
    assert request_flag({"explicit_ticket_request": True}) is True
    print(f"Offline checks passed: {len(cases)} cases, scoped schemas, prompts, pass/miss/error reports, and layer selections. No model calls.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--layer", choices=("router", "manager", "both"), default="both")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--show-prompts", metavar="CASE_ID", help="Print exact prompts and messages for review; no model calls")
    parser.add_argument("--prompt-variant", choices=("full", "definitions"), default="full",
                        help="Use the complete prompt or definitions without example blocks")
    parser.add_argument("--case-group", choices=("all", "core", "heldout"), default="all",
                        help="Run all cases, the original cases, or only paraphrased holdouts")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--report-from", type=Path, help="Render an existing JSON run without model calls")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    all_cases = build_cases()
    cases = all_cases if args.case_group == "all" else [
        c for c in all_cases if (c.group == "heldout") == (args.case_group == "heldout")
    ]
    validate_cases(cases)
    if args.list_cases:
        list_cases(cases)
        return
    if args.self_test:
        self_test(all_cases)
        return
    if args.show_prompts:
        from ole5_chat import products
        case = next((c for c in cases if c.case_id == args.show_prompts), None)
        if case is None:
            parser.error(f"Unknown case: {args.show_prompts}")
        config = products.load("OLE5").model_dump(include={"name", "scope", "about", "always_ticket", "urgent_when",
                                                           "support_hours", "support_channel", "reply_channel", "required"})
        print(json.dumps({"prompts": prompts(case, config, args.prompt_variant), "messages": case.history}, ensure_ascii=False, indent=2))
        return
    json_path = args.output_dir / "router-owner-eval.json"
    report_path = args.output_dir / "router-owner-eval-report.md"
    for path in ([report_path] if args.report_from else [json_path, report_path]):
        if path.exists():
            parser.error(f"Refusing to overwrite {path}; choose a fresh --output-dir")
    if args.report_from:
        payload = json.loads(args.report_from.read_text(encoding="utf-8"))
        report = render_report(payload)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as f:
            f.write(report)
        print(f"Wrote {report_path}; no model calls.")
        return
    from ole5_chat import products, llm
    from ole5_chat.config import get_settings
    config_obj = products.load("OLE5")
    config = config_obj.model_dump(include={"name", "scope", "about", "always_ticket", "urgent_when",
                                           "support_hours", "support_channel", "reply_channel", "required"})
    settings = get_settings()
    if not settings.groq_api_key:
        parser.error("GROQ_API_KEY is not set; no calls made")
    payload = {"schema_version": 4, "prompt_revision": "boundary-clarification-v4-prompt-variants-and-state-fixtures",
               "scoring_policy": "question-only-request-flag", "started_at": datetime.now(timezone.utc).isoformat(),
               "status": "running", "runs": args.runs, "layer": args.layer,
               "prompt_variant": args.prompt_variant, "case_group": args.case_group,
               "case_count": sum(bool(c.expect_action) for c in cases) if args.layer == "manager" else len(cases),
               "model": settings.groq_model, "wrapper": {"max_attempts": llm.RETRIES+1, "temperatures": [0, .3]},
               "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "product_config": config, "schemas": {"router": {k: s.model_json_schema() for k, s in ROUTER_SCHEMAS.items()},
                    "manager": {k: s.model_json_schema() for k,s in SCHEMAS.items()}}, "rows": []}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects existing results. Checkpoint only this run's file.
    with json_path.open("x+", encoding="utf-8") as saved:
        def checkpoint():
            saved.seek(0)
            json.dump(payload, saved, ensure_ascii=False, indent=2)
            saved.truncate()
            saved.flush()
        checkpoint()
        try:
            for c in cases:
                if args.layer == "manager" and not c.expect_action:
                    continue
                system = prompts(c, config, args.prompt_variant)
                row = {"case": asdict(c), "prompts": system, "trials": []}
                payload["rows"].append(row)
                for trial_number in range(1, args.runs+1):
                    trial = {}
                    row["trials"].append(trial)
                    for layer in ("router", "manager"):
                        if args.layer not in ("both", layer) or (layer == "manager" and not c.expect_action):
                            continue
                        print(f"{c.case_id} trial {trial_number}/{args.runs}: {layer}", flush=True)
                        trial[layer] = call_model(layer, c, system[layer])
                        checkpoint()
            payload["status"] = "complete"
        except KeyboardInterrupt:
            payload["status"] = "interrupted"
            print("Interrupted; completed calls are preserved.")
        finally:
            payload["finished_at"] = datetime.now(timezone.utc).isoformat()
            checkpoint()
    with report_path.open("x", encoding="utf-8") as f:
        f.write(render_report(payload))
    print(f"Wrote {json_path}\nWrote {report_path}")


if __name__ == "__main__":
    main()

