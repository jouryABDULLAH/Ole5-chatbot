"""Router receives compact worker context while Respond keeps the original facts."""

from copy import deepcopy
from importlib import import_module
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from ole5_chat.agent.context import respond_context, router_context
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
    assert "LEFT below are the customer's classified intent" in prompt
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


def test_router_omits_payloads_without_changing_response_facts():
    current = state()
    current.update(
        messages=[HumanMessage(content="How does archiving work?")],
        last_worker="ticket_operator",
        search_result={"outcome": "answered", "scope": "product", "references": [
            {"text": "PRIVATE_CHUNK_SENTINEL", "document": "Guide", "relation": "DIRECT"}]},
        ticket_operator_result={"action": "submit", "ticket_result": {
            "submission_succeeded": False, "response_instruction": "PRIVATE_INSTRUCTION_SENTINEL"}},
        ticket_enquiry_result={"action": "lookup_status", "lookup_succeeded": True,
                               "tickets": [{"title": "PRIVATE_TICKET_SENTINEL"}],
                               "definitions": ["PRIVATE_DEFINITION_SENTINEL"]},
    )
    original = deepcopy(current)
    context = router_context(current)
    prompt = router_prompt(current)
    for field in ("search_result", "ticket_operator_result", "ticket_enquiry_result"):
        assert field not in context
    for marker in ("PRIVATE_CHUNK_SENTINEL", "PRIVATE_INSTRUCTION_SENTINEL",
                   "PRIVATE_TICKET_SENTINEL", "PRIVATE_DEFINITION_SENTINEL"):
        assert marker not in prompt
    assert "Search outcome: answered" in prompt
    assert "Reference count: 1" in prompt
    assert "Search scope: product" in prompt
    assert "last_worker: ticket_operator" in prompt
    assert context["ticket_operator_summary"]["ticket_created"] is False
    assert context["ticket_enquiry_summary"]["ticket_count"] == 1
    assert _response_packet(current)["search"] == original["search_result"]
    assert respond_context(current)["ticket_operator_result"] == original["ticket_operator_result"]
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
        {"ticket_enquiry_result": {"action": "lookup_status", "lookup_succeeded": not failed}})
    with patch.object(module, f"_{worker}_node", return_value=result):
        update = getattr(module, f"{worker}_node")(current)
    current.update(update)
    assert router_context(current)["last_worker"] == worker
    assert f"last_worker: {worker}" in router_prompt(current)
    current.update(finalize_node(current))
    assert current["last_worker"] is None
    assert "last_worker: none" in router_prompt(current)
