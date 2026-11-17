"""Ticket Enquiry: the result comes from the recorded tool results and the interpreter's selection. A lookup that
could not be completed is lookup_failed, never an empty or missing ticket."""

from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from ole5_chat.agent.contracts import TicketEnquiryCompletion
from ole5_chat.agent.nodes import ticket_enquiry
from ole5_chat.agent.nodes.ticket_enquiry import build_ticket_tools, ticket_enquiry_node
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.turn_sheet import build_turn_sheet
from ole5_chat.identity import Identity
from ole5_chat.tickets.client import TicketApiError

EMAIL = "test@example.com"


def _row(number, created, email=EMAIL):
    return {"ticket_id": f"id-{number}", "ticket_number": number, "title": f"Title {number}", "product": "OLE5",
            "created_at": created, "customer_email": email}


# One dated with a time zone, one without (an older row), one with no date, and another customer's.
ROWS = [_row("T-1", "2026-11-03T10:00:00+00:00"), _row("T-2", "2026-10-01"), _row("T-3", None),
        _row("T-4", "2026-09-01", email="other@example.com")]


def _state(text="What is the status of my tickets?"):
    state = initial_state(Identity(customer_id="test", email=EMAIL, name="Test", organization="Test",
                                   product="OLE5", language="en"))
    state["messages"] = [HumanMessage(content=text)]
    state["intent"] = "ticket_enquiry"
    return state


def _selected(*numbers, action="lookup_status"):
    return TicketEnquiryCompletion(action=action, reason="test", selected_ticket_numbers=list(numbers))


def _interpreted(model) -> bool:
    return any(schema is TicketEnquiryCompletion for schema, _ in model.calls)


@pytest.fixture
def rows():
    with patch.object(ticket_enquiry.sheets, "tickets", return_value=ROWS):
        yield


@pytest.fixture
def api():
    with patch.object(ticket_enquiry, "ticket_client") as client:
        client.return_value.get.return_value = {"state": "open", "queue": "Support"}
        yield client.return_value


def _run(text="What is the status of my tickets?"):
    return ticket_enquiry_node(_state(text))["ticket_enquiry_result"]


@pytest.mark.parametrize("order,limit,numbers", [
    ("oldest_first", 1, ["T-2"]),
    ("newest_first", 1, ["T-1"]),
    ("oldest_first", None, ["T-2", "T-1"]),
])
def test_lookup_basis_reaches_respond_and_followup(order, limit, numbers):
    from ole5_chat.agent.nodes.finalize import finalize_node
    from ole5_chat.agent.prompts.respond import _ticket_records

    state = _state("Show my oldest ticket")
    sink = [{"name": "list_tickets", "args": {"order": order, "limit": limit},
             "result": {"outcome": "success", "tickets": [{"ticket_number": n} for n in numbers]}}]
    result = ticket_enquiry._result(state, _selected(numbers[0]), sink, None)
    basis = [{"tool": "list_tickets", "arguments": {"order": order, "limit": limit},
              "returned_ticket_numbers": numbers}]
    assert result["lookup_basis"] == basis
    state.update(ticket_enquiry_result=result, reply="Ticket details")
    assert str(basis) in _ticket_records(state)
    finalized = finalize_node(state)
    assert finalized["last_lookup"]["lookup_basis"] == basis
    state.update(finalized)
    assert str(basis) in _ticket_records(state)


def test_failed_or_unrelated_lookup_does_not_supply_selection_basis():
    sink = [
        {"name": "list_tickets", "args": {"order": "oldest_first", "limit": 1}, "error": "failed"},
        {"name": "find_tickets_by_title", "args": {"text": "archive"},
         "result": {"outcome": "success", "tickets": [{"ticket_number": "T-2"}]}},
        {"name": "get_ticket", "args": {"ticket_number": "T-1"},
         "result": {"outcome": "found", "ticket": {"ticket_number": "T-1"}, "status_available": True}},
    ]
    result = ticket_enquiry._result(_state(), _selected("T-1"), sink, "list failed")
    assert result["lookup_basis"] == []
    assert result["outcome"] == "partial"


def test_tool_agent_failing_before_any_result_is_lookup_failed(model, operator_model, rows, api):
    operator_model.fail(RuntimeError("model unavailable"))
    result = _run()
    assert result["action"] == "lookup_failed"
    assert result["tickets"] == [] and result["outcome"] is None
    assert not _interpreted(model)  # nothing was retrieved, so nothing is interpreted


def test_unreadable_sheet_for_the_oldest_ticket_is_lookup_failed_not_no_tickets(model, operator_model, api):
    operator_model.call("list_tickets", order="oldest_first", limit=1)
    with patch.object(ticket_enquiry.sheets, "tickets", side_effect=OSError("sheet locked")):
        result = _run("What is my oldest ticket?")
    assert result["action"] == "lookup_failed"
    assert "list_tickets failed" in result["error"]
    assert result["outcome"] != "empty"


def test_malformed_tool_output_is_lookup_failed(model, operator_model, rows, api):
    operator_model.call("list_tickets")
    with patch.object(ticket_enquiry, "_matches", return_value="not json"):
        result = _run()
    assert result["action"] == "lookup_failed"
    assert "list_tickets failed" in result["error"]


@pytest.mark.parametrize("payload", ['{}', '{"outcome": "success", "tickets": ["T-1"]}',
                                     '{"outcome": "empty", "tickets": [{"ticket_number": "T-1"}]}', '[]'])
def test_a_malformed_list_result_is_lookup_failed_never_empty(model, operator_model, rows, api, payload):
    operator_model.call("list_tickets")
    with patch.object(ticket_enquiry, "_matches", return_value=payload):
        result = _run("Show my tickets")
    assert result["action"] == "lookup_failed" and "malformed result" in result["error"]
    assert not _interpreted(model)


def test_a_failure_after_results_is_never_a_clarification(model, operator_model, rows, api):
    operator_model.call("list_tickets")
    operator_model.call("get_ticket", ticket_number="T-1")
    api.get.side_effect = RuntimeError("connection reset")  # not caught by get_ticket: ends the tool agent
    model.add(TicketEnquiryCompletion(action="clarify", reason="test", clarification_question="Which ticket?"))
    result = _run()
    assert result["action"] == "lookup_failed"
    assert result["failed_lookups"] == [{"ticket_number": "T-1", "reason": "failed"}]


def test_a_failed_lookup_is_kept_even_when_it_was_not_selected(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    operator_model.call("get_ticket", ticket_number="T-9")
    model.add(_selected("T-1"))  # the interpreter forgot T-9
    result = _run("Check T-1 and T-9")
    assert result["outcome"] == "partial"
    assert result["failed_lookups"] == [{"ticket_number": "T-9", "reason": "not_found"}]


def test_clarify_after_the_ticket_was_found_is_sent_back_once(model, operator_model, rows, api):
    operator_model.call("list_tickets", order="oldest_first", limit=1)
    operator_model.call("get_ticket", ticket_number="T-2")
    model.add(TicketEnquiryCompletion(action="clarify", reason="no number given", clarification_question="Which?"),
              _selected("T-2"))
    result = _run("وش اقدم تذكرة عندي؟")
    assert "get_ticket found ['T-2']" in model.problems[0]
    assert result["outcome"] == "found" and [t["ticket_number"] for t in result["tickets"]] == ["T-2"]


def test_clarify_kept_after_being_sent_back_stands(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    clarify = TicketEnquiryCompletion(action="clarify", reason="test", clarification_question="Which ticket?")
    model.add(clarify, clarify)
    result = _run("What about that one?")
    assert len(model.problems) == 1
    assert result["action"] == "clarify" and result["clarification_question"] == "Which ticket?"


def test_explaining_the_status_of_a_ticket_shown_earlier(model, operator_model, rows, api):
    # The log of 12 Nov 16:22: the ticket was shown the turn before, so no tool runs and its number is not in this
    # turn's results; the explanation is still given.
    model.add(_selected("T-MOCK-20261110103732-0001", action="explain_status"))
    result = _run("كيف يعني حالتها جديدة قريتوها يعني ولا لا")
    assert model.problems == []
    assert result["action"] == "explain_status" and result["outcome"] is None
    # Since 2026-11-17 a status listed in chat.yaml status_meanings also carries what it means for the customer.
    new = next(d for d in result["definitions"] if d["value"] == "New")
    assert new["description"] == "open" and "support team has received the ticket" in new["meaning"]


def test_an_explanation_after_the_lookup_stopped_keeps_the_failure_visible(model, operator_model, rows, api):
    operator_model.call("list_tickets")
    operator_model.fail(RuntimeError("model unavailable"))
    model.add(_selected(action="explain_status"))
    result = _run("What does New mean?")
    assert result["action"] == "explain_status" and result["outcome"] == "partial"
    assert result["error"] and "definitions" in result


def test_clarify_with_an_unsupported_number_stays_clarify(model, operator_model, rows, api):
    operator_model.call("list_tickets")
    model.add(TicketEnquiryCompletion(action="clarify", reason="test", selected_ticket_numbers=["T-77"],
                                      clarification_question="Which ticket?"))
    result = _run("What about that one?")
    assert result["action"] == "clarify" and result["tickets"] == []


def test_one_ticket_found_and_one_failing_reports_the_failure_as_temporary(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    operator_model.call("get_ticket", ticket_number="T-2")
    api.get.side_effect = [{"state": "open"}, RuntimeError("connection reset")]
    model.add(_selected("T-1"))
    result = _run("Check T-1 and T-2")
    assert result["outcome"] == "partial"
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1"]
    assert result["failed_lookups"] == [{"ticket_number": "T-2", "reason": "failed"}]


def test_limit_must_be_positive(rows, api):
    list_tickets = {t.name: t for t in build_ticket_tools(_state(), [])}["list_tickets"]
    for limit in (0, -1):
        with pytest.raises(Exception):
            list_tickets.invoke({"limit": limit})


def test_a_ticket_from_this_chat_carries_its_title_in_the_customers_language(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    model.add(_selected("T-1"))
    state = _state()
    state["created_tickets"] = [{"ticket_number": "T-1", "title_customer": "زر الأرشفة لا يعمل"}]
    result = ticket_enquiry_node(state)["ticket_enquiry_result"]
    assert result["tickets"][0]["title_customer"] == "زر الأرشفة لا يعمل"
    assert result["tickets"][0]["title"] == "Title T-1"


def test_creation_dates_are_plain_dates(rows, api):
    sink = []
    list_tickets = {t.name: t for t in build_ticket_tools(_state(), sink)}["list_tickets"]
    list_tickets.invoke({})
    assert [t["created_at"] for t in sink[0]["result"]["tickets"]] == ["2026-11-03", "2026-10-01", ""]


def test_status_api_failure_keeps_the_ticket_without_its_status(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    api.get.side_effect = TicketApiError("TicketGet: HTTP 500")
    model.add(_selected("T-1"))
    result = _run()
    assert result["action"] == "lookup_status" and result["outcome"] == "partial"
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1"]
    assert result["tickets"][0]["status_available"] is False


def test_one_ticket_found_and_one_not_found_keeps_both(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    operator_model.call("get_ticket", ticket_number="T-9")
    model.add(_selected("T-1", "T-9"))
    result = _run("What is the status of T-1 and T-9?")
    assert result["outcome"] == "partial"
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1"]
    assert result["failed_lookups"] == [{"ticket_number": "T-9", "reason": "not_found"}]


def test_every_ticket_not_found_is_not_found(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-9")
    model.add(_selected("T-9"))
    result = _run("What is the status of T-9?")
    assert result["action"] == "lookup_status" and result["outcome"] == "not_found"
    assert result["failed_lookups"] == [{"ticket_number": "T-9", "reason": "not_found"}]


def test_another_customers_ticket_is_not_found(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-4")
    model.add(_selected("T-4"))
    result = _run("What is the status of T-4?")
    assert result["outcome"] == "not_found" and result["tickets"] == []
    api.get.assert_not_called()


def test_no_tickets_is_empty_only_after_a_successful_list(model, operator_model, api):
    operator_model.call("list_tickets")
    model.add(_selected())
    with patch.object(ticket_enquiry.sheets, "tickets", return_value=[]):
        result = _run("Show my tickets")
    assert result["action"] == "lookup_status" and result["outcome"] == "empty"


def test_a_title_search_with_no_match_is_not_found_not_empty(model, operator_model, rows, api):
    operator_model.call("find_tickets_by_title", text="printing")
    model.add(_selected())
    result = _run("What happened to my printing ticket?")
    assert result["outcome"] == "not_found"


def test_clarify_keeps_the_question_for_respond(model, operator_model, rows, api):
    model.add(TicketEnquiryCompletion(action="clarify", reason="test", clarification_question="Which ticket?"))
    result = _run("What about that one?")
    assert result["action"] == "clarify" and result["clarification_question"] == "Which ticket?"


def test_interpreter_failure_is_lookup_failed(model, operator_model, rows, api):
    operator_model.call("list_tickets")
    result = _run("Show my tickets")  # nothing scripted: the interpreter fails
    assert result["action"] == "lookup_failed"
    assert result["tickets"] == []


def test_an_invented_ticket_number_is_sent_back_to_the_interpreter(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    model.add(_selected("T-77"), _selected("T-1"))
    result = _run()
    assert "T-77" in model.problems[0]
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1"]


def test_an_invented_ticket_number_that_stays_is_lookup_failed(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    model.add(_selected("T-77"))
    assert _run()["action"] == "lookup_failed"


def test_an_empty_selection_after_tickets_were_listed_is_sent_back(model, operator_model, rows, api):
    operator_model.call("list_tickets")
    model.add(_selected(), _selected("T-1", "T-2", "T-3"))
    result = _run("Show my tickets")
    assert "empty" in model.problems[0]
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1", "T-2", "T-3"]
    assert result["outcome"] == "found"


def test_only_the_selected_tickets_are_returned(model, operator_model, rows, api):
    operator_model.call("list_tickets", order="newest_first")
    model.add(_selected("T-1"))
    result = _run("What is my latest ticket?")
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1"]


def test_a_tool_agent_failure_after_results_keeps_them(model, operator_model, rows, api):
    operator_model.call("get_ticket", ticket_number="T-1")
    operator_model.fail(RuntimeError("model unavailable"))
    model.add(_selected("T-1"))
    result = _run()
    assert result["outcome"] == "partial" and "tool agent failed" in result["error"]
    assert [t["ticket_number"] for t in result["tickets"]] == ["T-1"]
    assert any("stopped with an error" in system for schema, system in model.calls
               if schema is TicketEnquiryCompletion)


def test_the_round_limit_stops_the_agent_and_keeps_the_results(model, operator_model, rows, api):
    for _ in range(12):
        operator_model.call("get_ticket", ticket_number="T-1")
    model.add(_selected("T-1"))
    result = _run()
    assert result["outcome"] == "partial" and result["error"]
    assert api.get.call_count < 12


def test_a_lookup_with_no_tool_call_is_lookup_failed(model, operator_model, rows, api):
    model.add(_selected())
    assert _run()["action"] == "lookup_failed"


def test_explaining_a_status_needs_no_ticket(model, operator_model, rows, api):
    model.add(_selected(action="explain_status"))
    result = _run("What does 'pending' mean?")
    assert result["action"] == "explain_status" and result["outcome"] is None
    assert "definitions" in result


def test_dates_sort_as_dates_with_undated_tickets_last(rows, api):
    sink = []
    tools = {t.name: t for t in build_ticket_tools(_state(), sink)}
    numbers = lambda result: [call["result"]["tickets"] for call in result][-1]  # noqa: E731
    tools["list_tickets"].invoke({"order": "oldest_first"})
    assert [t["ticket_number"] for t in numbers(sink)] == ["T-2", "T-1", "T-3"]
    tools["list_tickets"].invoke({"order": "newest_first"})
    assert [t["ticket_number"] for t in numbers(sink)] == ["T-1", "T-2", "T-3"]
    tools["list_tickets"].invoke({"order": "oldest_first", "limit": 1})
    assert [t["ticket_number"] for t in numbers(sink)] == ["T-2"]


def test_ticket_numbers_match_without_spaces_or_case(rows, api):
    sink = []
    get_ticket = {t.name: t for t in build_ticket_tools(_state(), sink)}["get_ticket"]
    get_ticket.invoke({"ticket_number": " t-1 "})
    assert sink[0]["result"]["outcome"] == "found"


def test_finalize_keeps_the_last_lookup_for_follow_ups():
    from ole5_chat.agent.nodes.finalize import finalize_node
    from ole5_chat.agent.prompts.respond import _ticket_records
    state = _state()
    ticket = {"ticket_number": "T-1", "title": "Title T-1", "created_at": "2026-11-03", "state": "open"}
    state.update(ticket_enquiry_result={"action": "lookup_status", "outcome": "found", "tickets": [ticket],
                                        "failed_lookups": []}, validation_outcome="pass", reply="T-1")
    state.update(finalize_node(state))
    assert state["last_lookup"] == {"tickets": [ticket], "failed_lookups": [], "lookup_basis": []}
    assert state["ticket_enquiry_result"] is None
    # The next turn has no lookup of its own: Respond still has the record of what was shown.
    assert "Shown to the customer in an earlier lookup" in _ticket_records(state) and "T-1" in _ticket_records(state)


def test_a_failed_lookup_does_not_replace_the_last_one():
    from ole5_chat.agent.nodes.finalize import finalize_node
    state = _state()
    state["last_lookup"] = {"tickets": [{"ticket_number": "T-1"}], "failed_lookups": []}
    state.update(ticket_enquiry_result={"action": "lookup_failed", "outcome": None, "tickets": [],
                                        "failed_lookups": [], "error": "x"}, validation_outcome="pass", reply="x")
    state.update(finalize_node(state))
    assert state["last_lookup"] == {"tickets": [{"ticket_number": "T-1"}], "failed_lookups": []}


def test_lookup_failed_has_its_own_respond_stage():
    state = _state()
    state["ticket_enquiry_result"] = {"action": "lookup_failed", "outcome": None, "tickets": [],
                                      "failed_lookups": [], "error": "x", "clarification_question": None}
    assert build_turn_sheet(state, "en")["stage"] == "ticket_lookup_failed"
    state["ticket_enquiry_result"] = {**state["ticket_enquiry_result"], "action": "lookup_status", "outcome": "empty"}
    assert build_turn_sheet(state, "en")["stage"] == "ticket_status"
