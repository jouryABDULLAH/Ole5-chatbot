"""Compare the fixed Manager action schema with the current phase-scoped router.

This is a standalone live evaluation. It does not call the graph and does not
modify application code. Run it explicitly, for example:

    python scripts/manager_interpreter_eval.py --runs 3

Review the expected labels first with:

    python scripts/manager_interpreter_eval.py --list-cases

The model calls are intentionally left to the person running the experiment.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, get_args

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict

# Make `python scripts/manager_interpreter_eval.py` work from a checkout even
# when the package has not been installed in editable mode.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ole5_chat import llm, products
from ole5_chat.agent import prompt as production_prompt
from ole5_chat.agent.router import router as current_router
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.contracts import PendingIssue
from ole5_chat.identity import Identity


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "manager-interpreter-eval.json"
DEFAULT_REPORT = ROOT / "evals" / "results" / "manager-interpreter-eval-report.md"
RUNNER = ROOT / "tests" / "test_router_live.py"

Action = Literal[
    "request_ticket", "always_ticket", "urgent", "solution_failed",
    "accept_offer", "decline_offer", "provide_details", "dont_know",
    "refuse_detail", "done", "change", "cancel", "submit", "status",
    "clarify",
]
Intent = Literal["question", "ticket", "other"]


class ManagerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    reason: str


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    reason: str


@dataclass(frozen=True)
class Case:
    case_id: str
    phase: str
    issue: str | None
    assistant: str
    customer: str
    expected_next: str | None
    expected_action: str | None
    expected_intent: str
    source: str


def _load_live_cases():
    spec = importlib.util.spec_from_file_location("router_live_cases", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.CASES, module.HELD_OUT


def _detail_action(customer: str) -> str:
    text = customer.casefold()
    if any(part in text for part in ("ما أعرف", "ما ادري", "ما أدري", "مدري", "don't know", "not sure")):
        return "dont_know"
    if any(part in text for part in ("ما أبي", "ما ابي", "ما أبغى", "won't say", "don't want to give")):
        return "refuse_detail"
    return "provide_details"


def _mapped_action(phase: str, customer: str, expected: str) -> str | None:
    if expected == "accept":
        return "accept_offer"
    if expected == "decline":
        return "decline_offer"
    if expected == "details":
        return _detail_action(customer)
    text = customer.casefold()
    if expected == "ticket" and any(part in text for part in ("لا زال", "العطل مستمر", "still", "doesn't work", "does not work")):
        return "solution_failed"
    return {
        "done": "done", "yes": "submit", "change": "change", "cancel": "cancel",
        "ticket": "always_ticket" if any(part in text for part in ("نقل بيانات", "ترحيل بيانات", "data migration")) else "request_ticket",
        "urgent": "urgent", "status": "status",
    }.get(expected)


def _intent(expected: str) -> str:
    return "ticket" if expected in {"accept", "decline", "details", "done", "yes", "change", "cancel", "ticket", "urgent", "status"} else {"question": "question", "talk": "other"}[expected]


def _base_cases() -> list[Case]:
    cases, held_out = _load_live_cases()
    selected = []
    for source, rows in (("CASES", cases), ("HELD_OUT", held_out)):
        for number, (phase, issue, assistant, customer, expected) in enumerate(rows, 1):
            selected.append(Case(f"{source.lower()}-{number}", phase, issue, assistant, customer,
                                 expected, _mapped_action(phase, customer, expected), _intent(expected), source))
    return selected


def _stress_cases() -> list[Case]:
    return [
        Case("stress-solution-failed", "none", None, "The steps above should solve it.", "لا زال لا يعمل",
             None, "solution_failed", "ticket", "STRESS"),
        Case("stress-dont-know", "collect", "Login fails", "ما اسم المتصفح الذي تستخدمه؟", "ما أدري",
             None, "dont_know", "ticket", "STRESS"),
        Case("stress-refuse-detail", "collect", "Login fails", "ما اسم المتصفح الذي تستخدمه؟", "ما أبي أعطيك",
             None, "refuse_detail", "ticket", "STRESS"),
        Case("stress-yes-detail", "collect", "Login fails", "هل تظهر رسالة خطأ؟", "ايه",
             None, "provide_details", "ticket", "STRESS"),
        Case("stress-yes-offer", "offer_ticket", "Archive button fails", "Would you like a ticket?", "ايه",
             "accept", "accept_offer", "ticket", "STRESS"),
        Case("stress-yes-summary", "confirm", "Archive button fails", "The summary is ready. Submit it?", "ايه",
             "yes", "submit", "ticket", "STRESS"),
        Case("stress-status-collect", "collect", "Login fails", "What browser do you use?", "وش صار على تذكرتي؟",
             "status", "status", "ticket", "STRESS"),
        Case("stress-clarify", "none", None, "Hello", "طيب", None, None, "other", "STRESS"),
        Case("stress-chrome-detail", "collect", "Login fails", "What browser do you use?", "Chrome",
             None, "provide_details", "ticket", "STRESS"),
        Case("stress-archive-detail", "collect", "Archive issue", "Which screen are you using?", "الأرشفة",
             None, "provide_details", "ticket", "STRESS"),
    ]


def _transcript(case: Case) -> list:
    return [AIMessage(content=case.assistant), HumanMessage(content=case.customer)]


def _pending_text(case: Case) -> str:
    return case.issue or "none"


def _pending_context(case: Case) -> str:
    kind = {"offer_ticket": "offer", "collect": "detail", "confirm": "summary"}.get(case.phase, "none")
    item = "the detail requested in the assistant's last message" if case.phase == "collect" else "none"
    return f"pending: {kind}, item: {item}, issue: {_pending_text(case)}"


def _examples(action: str, config) -> str:
    source = production_prompt.EXAMPLES.get(config.code, production_prompt.EXAMPLES["OLE5"])
    found: list[str] = []
    for (_, option), examples in source.items():
        mapped = {"accept": "accept_offer", "decline": "decline_offer", "details": "provide_details",
                  "yes": "submit", "urgent": "urgent", "status": "status"}.get(option, option)
        if mapped == action:
            found.extend(examples)
    if action == "provide_details":
        unknown_markers = ("ما أعرف", "ما ادري", "ما أدري", "مدري", "don't know", "not sure")
        found = [example for example in found if not any(marker in example.casefold() for marker in unknown_markers)]
    return ", ".join(found[:2]) or ACTION_EXAMPLES.get(action, "no example from the current router")


ACTION_MEANINGS = {
    "request_ticket": "the customer explicitly asks for a ticket about a named problem",
    "always_ticket": "the issue matches a configured always_ticket topic",
    "urgent": "the customer reports a configured urgent situation",
    "solution_failed": "the customer says the previous answer or steps did not solve the problem",
    "accept_offer": "accepts the ticket offer just shown",
    "decline_offer": "declines the ticket offer just shown",
    "provide_details": "provides the requested detail, including a yes/no answer or attachment",
    "dont_know": "says they do not know the requested detail",
    "refuse_detail": "refuses to provide the requested detail",
    "done": "says there is nothing more to add",
    "change": "asks to change the ticket summary",
    "cancel": "cancels the ticket preparation or summary",
    "submit": "agrees to submit the summary that was shown",
    "status": "asks about a ticket status or existing ticket",
    "clarify": "cannot be safely interpreted as another action",
}

ACTION_EXAMPLES = {
    "request_ticket": "open a ticket about this issue",
    "always_ticket": "نحتاج نقل بياناتنا من النظام القديم",
    "urgent": "nobody in our organization can log in",
    "solution_failed": "لا زال لا يعمل",
    "dont_know": "ما أدري",
    "refuse_detail": "ما أبي أعطيك هذه المعلومة",
    "clarify": "طيب",
}


def _fixed_manager_prompt(case: Case) -> str:
    config = products.load("OLE5")
    lines = []
    for action in get_args(Action):
        lines.append(f"{action}: {ACTION_MEANINGS[action]}. Example: {_examples(action, config)}")
    return (f"You are the ticket Manager for {config.name}. Interpret the customer's latest message; do not write a reply or call APIs.\n\n"
            f"{config.scope}\n\nAlways-ticket topics: {', '.join(config.always_ticket) or 'none'}\n"
            f"Urgent topics: {', '.join(config.urgent_when) or 'none'}\n\n"
            "The full conversation is provided in the messages after this instruction.\n\n"
            f"{_pending_context(case)}\n\n"
            "Choose the single action that fits best using the conversation and pending context. Keep all actions available in every case. "
            "Do not treat a pending response as proof that the customer answered it. If the message is ambiguous, choose clarify. "
            "Return JSON only matching the schema: {action, reason}.\n\nActions:\n" + "\n".join(lines))


def _intent_prompt(case: Case) -> str:
    config = products.load("OLE5")
    about_values = getattr(config, "about", {}) or {}
    about = about_values.get("ar") or about_values.get("en") or "none"
    return (f"You are the broad-intent Router for {config.name}. Choose only question, ticket, or other. Do not choose a ticket operation.\n\n"
            f"Scope: {config.scope}\nAbout: {about}\nAlways-ticket topics: {', '.join(config.always_ticket) or 'none'}\n"
            f"Urgent topics: {', '.join(config.urgent_when) or 'none'}\n\nThe full conversation is provided in the messages after this instruction.\n\n{_pending_context(case)}\n\n"
            "Intent meanings:\n"
            "question: names something in Ole5 and asks or reports about it.\n"
            "ticket: asks for a ticket, describes an urgent or always-ticket situation, says the previous answer or steps did not help, asks about ticket status, or replies to whatever customer response is pending.\n"
            "other: greetings, thanks, questions about the assistant or company, off-topic messages, garbled input, or a bare feature name when nothing is pending.\n\n"
            "Use pending context: a value such as Chrome can be a ticket detail answer, not a standalone question. "
            "Pick the one broad intent that best fits. Return JSON only matching {intent, reason}.")


def _state(case: Case):
    identity = Identity(customer_id="manager-eval", email="ole5@example.com", name="Sara Omar",
                        organization="Test Org", product="OLE5", language="ar")
    state = initial_state(identity)
    state["phase"] = case.phase
    state["pending"] = [PendingIssue(text=case.issue, source="no_answer")] if case.issue else []
    state["messages"] = _transcript(case)
    return state


def _run_call(kind: str, case: Case):
    started = time.perf_counter()
    try:
        if kind == "current_router":
            routed = current_router(_state(case))
            result = routed.get("next")
            error = "; ".join(routed.get("corrections", [])) or None
        elif kind == "manager":
            decision = llm.structured(_fixed_manager_prompt(case), _transcript(case), ManagerDecision, node="manager_eval")
            result = {"value": decision.action, "reason": decision.reason}
            error = None
        else:
            decision = llm.structured(_intent_prompt(case), _transcript(case), IntentDecision, node="router_eval")
            result = {"value": decision.intent, "reason": decision.reason}
            error = None
        if kind == "current_router":
            result = {"value": result, "reason": None}
        return {**result, "error": error, "seconds": time.perf_counter() - started}
    except Exception as exc:  # keep one model failure from hiding the rest of the experiment
        return {"value": None, "error": f"{type(exc).__name__}: {exc}", "seconds": time.perf_counter() - started}


def _guard_assessment(case: Case, returned: str | None) -> str:
    if returned == "submit" and case.phase != "confirm":
        return "guard should block: no summary shown"
    if returned == "accept_offer" and case.phase != "offer_ticket":
        return "guard should block: no ticket offer shown"
    if returned == "decline_offer" and case.phase != "offer_ticket":
        return "guard should block: no ticket offer shown"
    if returned == "change" and case.phase != "confirm":
        return "guard should block: no summary shown"
    if returned in {"provide_details", "dont_know", "refuse_detail", "done"} and case.phase != "collect":
        return "guard should block: no detail collection is active"
    if returned == "clarify":
        return "safe clarification"
    return "no matching existing phase guard identified; review manually"


def _report(payload: dict) -> str:
    rows = payload["rows"]
    manager_cases = [r for r in rows if r["expected_action"]]
    other_cases = [r for r in rows if not r["expected_action"] and r["expected_intent"] != "ticket"]
    current_ticket_cases = [r for r in manager_cases if r["expected_next"] is not None]
    current_other_cases = [r for r in other_cases if r["expected_next"] is not None]

    def rate(items, key, expected_key):
        passed = sum(all(t[key].get("value") == r[expected_key] and not t[key].get("error") for t in r["trials"]) for r in items)
        return f"{passed}/{len(items)}"

    def chain_rate(items):
        passed = sum(all(t["chain"].get("value") and not t["chain"].get("error") for t in r["trials"]) for r in items)
        return f"{passed}/{len(items)}"

    out = ["# Manager interpreter evaluation", "", "Generated by `scripts/manager_interpreter_eval.py`.", "",
           "A case passes only when all configured trials are correct. This report compares the current phase-scoped router, the new broad router, the fixed Manager, and their chain.", "",
           "## Pass rates", "", "| Case group | Current router | New Router (intent) | Manager (action) | New chain |", "|---|---:|---:|---:|---:|"]
    out.append(f"| Ticket-side cases | {rate(current_ticket_cases, 'current_router', 'expected_next')} | {rate(manager_cases, 'intent_router', 'expected_intent')} | {rate(manager_cases, 'manager', 'expected_action')} | {chain_rate(manager_cases)} |")
    out.append(f"| Question/other cases | {rate(current_other_cases, 'current_router', 'expected_next')} | {rate(other_cases, 'intent_router', 'expected_intent')} | n/a | n/a |")
    out += ["", "## Misses", "", "| Case | Expected | Returned | Pending context | Guard assessment |", "|---|---|---|---|---|"]
    for row in rows:
        case = Case(**row["case"])
        checks = []
        if row["expected_next"] is not None:
            checks.append(("current_router", "current router", "expected_next"))
        if row["expected_intent"]:
            checks.append(("intent_router", "new router", "expected_intent"))
        if row["expected_action"]:
            checks.append(("manager", "Manager", "expected_action"))
        for key, label, expected_key in checks:
            if any(t[key].get("value") != row[expected_key] or t[key].get("error") for t in row["trials"]):
                returned = "; ".join(str(t[key].get("value") or t[key].get("error")) for t in row["trials"])
                reasons = "; ".join(str(t[key].get("reason") or "-") for t in row["trials"])
                guard = "; ".join(_guard_assessment(case, t[key].get("value")) for t in row["trials"]) if key == "manager" else "n/a"
                out.append(f"| `{case.case_id}` ({label}) | `{row[expected_key]}` | `{returned}`<br>Reasons: {reasons} | `{_pending_context(case)}` | {guard} |")
        if row["expected_action"] and any(not t["chain"].get("value") or t["chain"].get("error") for t in row["trials"]):
            returned = "; ".join(str(t["chain"].get("value") or t["chain"].get("error")) for t in row["trials"])
            out.append(f"| `{case.case_id}` (new chain) | `router=ticket + manager={row['expected_action']}` | `{returned}` | `{_pending_context(case)}` | n/a |")
    out += ["", "## Notes", "", "- The three `ايه` cases are deliberately separated by pending context: detail, offer, and summary.",
            "- `Chrome` and `الأرشفة` test whether the new Router recognizes a pending detail answer as `ticket`.",
            "- Guard assessments are an evaluation aid and do not claim that the proposed Manager guards exist in today’s code.",
            "- Review model failures separately from semantic misses; a failed call is not a correct classification."]
    return "\n".join(out) + "\n"


def _print_case_map(cases: list[Case]) -> None:
    print("case_id | phase | customer | expected_next -> expected_action / expected_intent")
    print("--- | --- | --- | ---")
    for case in cases:
        print(f"{case.case_id} | {case.phase} | {case.customer} | "
              f"{case.expected_next or '-'} -> {case.expected_action or '-'} / {case.expected_intent}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--list-cases", action="store_true", help="print expected labels and exit without model calls")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")

    cases = _base_cases() + _stress_cases()
    if args.list_cases:
        _print_case_map(cases)
        return
    rows = []
    for case in cases:
        row = {"case": asdict(case), "expected_next": case.expected_next, "expected_action": case.expected_action,
               "expected_intent": case.expected_intent, "trials": []}
        for _ in range(args.runs):
            trial = {"current_router": _run_call("current_router", case),
                     "intent_router": _run_call("intent_router", case)}
            if case.expected_action:
                trial["manager"] = _run_call("manager", case)
                trial["chain"] = {"value": trial["intent_router"]["value"] == "ticket" and trial["manager"]["value"] == case.expected_action,
                                   "error": trial["intent_router"]["error"] or trial["manager"]["error"],
                                   "seconds": trial["intent_router"]["seconds"] + trial["manager"]["seconds"]}
            row["trials"].append(trial)
        rows.append(row)

    payload = {"runs": args.runs, "case_count": len(cases), "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_report(payload), encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"Wrote {args.report}")


if __name__ == "__main__":
    main()
