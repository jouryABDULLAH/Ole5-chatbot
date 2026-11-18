"""Router receives compact worker context while Respond keeps the original facts."""

from copy import deepcopy
from importlib import import_module
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from ole5_chat.agent.context import router_context
from ole5_chat.agent.nodes.finalize import finalize_node
from ole5_chat.agent.nodes.router import _response_packet
from ole5_chat.agent.prompts.router import router_prompt
from ole5_chat.agent.prompts.router import ROUTER_EXAMPLES
from ole5_chat.agent.contracts import PendingCustomerResponse
from ole5_chat import products
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity


def state():
    return initial_state(Identity(customer_id="test", email="test@example.com",
                                 name="Test", organization="Test", product="OLE5", language="en"))


@pytest.mark.parametrize("outcome,expected", [("answered", "respond"), ("failed", "respond"),
                                             ("no_answer", "ticket_operator"), ("partial", "ticket_operator")])
def test_return_pass_routes_the_worker_outcome_without_a_model_call(model, outcome, expected):
    # The return pass is decided in code from the worker's recorded outcome (2026-11-18): no model call is made.
    from ole5_chat.agent.nodes.router import router_node
    current = state()
    current.update(last_worker="search", intent="question", search_outcome=outcome)
    result = router_node(current)
    assert result["next_target"] == expected
    assert current["intent"] == "question"
    assert model.calls == []


@pytest.mark.parametrize("has_pending", [False, True])
def test_intent_examples_are_rendered_and_prompt_sections_are_separate(has_pending):
    current = state()
    if has_pending:
        current["pending_customer_response"] = PendingCustomerResponse(kind="offer", issue_id="test")
    prompt = router_prompt(current)
    name = products.load("OLE5").name
    for intent, examples in ROUTER_EXAMPLES.items():
        if intent != "pending_response" or has_pending:
            assert examples.replace("{product}", name) in prompt
    assert "intent=question → next_target=search" in prompt
    assert "intent=other → next_target=respond" in prompt
    assert "database connection string" not in prompt
    assert "500 error" not in prompt
    assert "requires_more_info" not in prompt
    assert "+Return" not in prompt
    assert prompt.count("## CHOOSING THE NEXT TARGET") == 1
    lines = prompt.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("##"):
            assert lines[index - 1] == ""
            assert lines[index + 1] == ""


@pytest.mark.parametrize("worker", ["search", "ticket_operator", "ticket_enquiry"])
def test_router_omits_payloads_without_changing_response_facts(worker):
    current = state()
    current.update(
        messages=[HumanMessage(content="How does archiving work?")],
        last_worker=worker,
        search_result={"outcome": "answered", "scope": "product", "references": [
            {"text": "PRIVATE_CHUNK_SENTINEL", "document": "Guide", "relation": "DIRECT"}]},
        ticket_operator_result={"action": "submit", "ticket_result": {
            "submission_succeeded": False, "response_instruction": "PRIVATE_INSTRUCTION_SENTINEL"}},
        ticket_enquiry_result={"action": "lookup_status", "outcome": "found",
                               "tickets": [{"title": "PRIVATE_TICKET_SENTINEL"}],
                               "definitions": ["PRIVATE_DEFINITION_SENTINEL"]},
    )
    original = deepcopy(current)
    context = router_context(current)
    # The return pass no longer has a prompt (2026-11-18): only the context's compact summaries are checked here.
    for field in ("search_result", "ticket_operator_result", "ticket_enquiry_result"):
        assert field not in context
    assert context["search_summary"] == {"outcome": "answered", "reference_count": 1, "scope": "product"}
    assert context["ticket_operator_summary"]["ticket_created"] is False
    assert context["ticket_enquiry_summary"]["ticket_count"] == 1
    assert _response_packet(current)["search"] == original["search_result"]
    assert current == original


@pytest.mark.parametrize("worker", ["search", "ticket_operator", "ticket_enquiry"])
@pytest.mark.parametrize("failed", [False, True])
def test_latest_worker_replaces_previous_marker_and_finalize_clears_it(worker, failed):
    current = state()
    assert current["last_worker"] is None
    current["last_worker"] = "search" if worker != "search" else "ticket_operator"
    module = import_module(f"ole5_chat.agent.nodes.{worker}")
    result = {"search_outcome": "failed" if failed else "answered"} if worker == "search" else (
        {"manager_action": "submit", "ticket_result": {"submission_succeeded": not failed}}
        if worker == "ticket_operator" else
        {"ticket_enquiry_result": {"action": "lookup_failed" if failed else "lookup_status",
                                   "outcome": None if failed else "found"}})
    with patch.object(module, f"_{worker}_node", return_value=result):
        update = getattr(module, f"{worker}_node")(current)
    current.update(update)
    assert router_context(current)["last_worker"] == worker
    current.update(finalize_node(current))
    assert current["last_worker"] is None
    assert "Worker result:" not in router_prompt(current)
