"""Compare system-context and appended-note handoffs using the real Operator model.

Run: venv/Scripts/python.exe scripts/operator_handoff_probe.py
Only register_issue handlers are exercised; no ticket submission APIs are called.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langchain_groq import ChatGroq

from ole5_chat import llm
from ole5_chat.agent.contracts import PendingCustomerResponse, manager_schema
from ole5_chat.agent.nodes.ticket_operator import _apply_none
from ole5_chat.agent.prompts.ticket_operator import manager_prompt
from ole5_chat.agent.state import initial_state
from ole5_chat.config import get_settings
from ole5_chat.identity import Identity


RULES = """
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
"""


def cases():
    identity = Identity(customer_id="operator-probe", email="test@example.com",
                        name="Test", organization="Test", product="OLE5", language="ar")

    def make(name, text, expected, *, outcome="no_answer", worker="search",
             kind=None, previous=None):
        state = initial_state(identity)
        messages = ([AIMessage(content=previous)] if previous else []) + [HumanMessage(content=text)]
        state.update(messages=messages, language="ar", input_event="message",
                     current_request=text, intent="question", last_worker=worker,
                     search_outcome=outcome, search_scope="product", explicit_ticket_request=False,
                     current_references=[], route_reason="Search returned no supported complete answer; hand off to Ticket Operator.")
        if kind:
            state.update(active_issue={"id": "issue-1", "description": "ترجمة نص المراسلة", "source": "no_answer"},
                         pending_customer_response=PendingCustomerResponse(
                             kind=kind, issue_id="issue-1", item="browser" if kind == "detail" else None),
                         summary_shown=kind == "summary", submission_requested=kind == "summary")
        if worker is None:
            state["route_reason"] = "Customer requested a ticket without an identifiable subject."
        return name, state, expected

    return [
        make("translation_question", "هل تتم ترجمة نص المراسلة تلقائياً إلى الإنجليزية؟", "register_issue"),
        make("translation_request", "أريد ترجمة نص المراسلة", "register_issue"),
        make("translation_followup", "أريد ترجمة نص المراسلة", "register_issue",
             previous="عذراً، لا تتوفر معلومات في قاعدة المعرفة حول ترجمة نص المراسلة تلقائياً. ما هو الموضوع أو المشكلة التي تحتاج المساعدة فيها؟"),
        make("other_feature", "هل يمكن تحويل نص المراسلة إلى تسجيل صوتي؟", "register_issue"),
        make("partial_answer", "كيف أحفظ المسودة وأترجم نصها إلى الإنجليزية؟", "register_issue", outcome="partial"),
        make("unclear_subject", "افتح لي تذكرة", "clarify", worker=None),
        make("unclear_after_search", "أحتاج مساعدة في ذلك", "clarify"),
        make("accept_offer", "نعم", "accept", kind="offer", previous="هل ترغب في تجهيز تذكرة عن ترجمة نص المراسلة؟"),
        make("decline_offer", "لا شكراً", "decline", kind="offer", previous="هل ترغب في تجهيز تذكرة عن ترجمة نص المراسلة؟"),
        make("provide_detail", "Chrome", "provide", kind="detail", previous="ما المتصفح الذي تستخدمه؟"),
        make("skip_detail", "لا أعرف", "skip_detail", kind="detail", previous="ما المتصفح الذي تستخدمه؟"),
        make("summary_consent", "نعم أرسلها", "submit", kind="summary", previous="ملخص التذكرة: طلب ترجمة المراسلة. هل توافق على إرسالها؟"),
        make("stale_search_new_turn", "افتح لي تذكرة", "clarify", worker=None,
             previous="انتهينا من السؤال السابق. ما الموضوع الجديد الذي تريد المساعدة فيه؟"),
    ]


def inputs(state, variant):
    pending = state.get("pending_customer_response")
    context = pending.kind if pending else "none"
    summary = {"last_worker": state.get("last_worker"), "search_outcome": state.get("search_outcome"),
               "search_scope": state.get("search_scope"), "reference_count": len(state.get("current_references", [])),
               "router_handoff_reason": state.get("route_reason")}
    handoff = "Internal handoff state (not a customer message):\n" + json.dumps(summary, ensure_ascii=False)
    system = manager_prompt(state, context) + "\n" + RULES
    messages = list(state["messages"])
    if variant == "system":
        system += "\n" + handoff
    else:
        messages.append(HumanMessage(content=handoff))
    return system, messages, manager_schema(context)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--case", help="Run only the named case")
    parser.add_argument("--output", default="docs/operator_handoff_results.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    settings.tracing = False

    def client(temperature):
        return ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key.get_secret_value(),
                        temperature=temperature, timeout=25, max_retries=0)

    def run(job):
        repeat, variant, case = job
        name, state, expected = deepcopy(case)
        system, messages, schema = inputs(state, variant)
        start = time.perf_counter()
        row = {"case": name, "variant": variant, "run": repeat, "expected": expected}
        try:
            decision = llm.structured(system, messages, schema, node="operator_probe_" + variant)
            row.update(action=decision.action, reason=decision.reason, passed=decision.action == expected)
            if name in {"translation_question", "translation_request", "other_feature", "partial_answer"}:
                if decision.action == "register_issue":
                    update = _apply_none(state, decision.action)
                    row["handler_passed"] = (update["pending_customer_response"].kind == "offer"
                        and "did not provide a supported answer" in update["ticket_result"]["response_instruction"])
                    row["response_instruction"] = update["ticket_result"]["response_instruction"]
                else:
                    row["handler_passed"] = False
                row["passed"] = row["passed"] and row["handler_passed"]
        except Exception as exc:
            row.update(passed=False, error=type(exc).__name__)
        row["seconds"] = round(time.perf_counter() - start, 3)
        print(json.dumps(row, ensure_ascii=True), flush=True)
        return row

    jobs = []
    for repeat in range(1, args.runs + 1):
        for case in cases():
            if args.case and case[0] != args.case:
                continue
            for variant in (("system", "note") if repeat % 2 else ("note", "system")):
                jobs.append((repeat, variant, case))
    with patch.object(llm, "tool_client", client), ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run, jobs))
    summary = {}
    for variant in ("system", "note"):
        subset = [row for row in rows if row["variant"] == variant]
        summary[variant] = {"passed": sum(row["passed"] for row in subset), "total": len(subset),
                            "median_seconds": round(statistics.median(row["seconds"] for row in subset), 3)}
    Path(args.output).write_text(json.dumps({"model": settings.groq_model, "rules": RULES,
                                            "summary": summary, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
