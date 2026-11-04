"""The turn sheet gives Respond this turn's facts and stage; check the stages that failed or were missing before."""
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat.agent.contracts import PendingCustomerResponse
from ole5_chat.agent.prompts.respond import system_prompt, turn_note
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.turn_sheet import build_turn_sheet
from ole5_chat.identity import Identity


def state(**updates):
    current = initial_state(Identity(customer_id="t", email="t@example.com", name="T", organization="T",
                                     product="OLE5", language="ar"))
    current.update({"messages": [AIMessage(content="ملخص التذكرة جاهز. هل أرسله الآن؟")], **updates})
    return current


def operator(action, **facts):
    return {"action": action, "reason": "r", "issue_id": "issue-1", "issue": "زر الأرشفة لا يعمل",
            "requested_detail": None, "skipped": [], "cancelled_issue": None, "queued_issue": None,
            "next_issue": None, "ticket_result": {}, **facts}


def test_submit_button_is_submitted_with_the_ticket_number_and_the_button_named():
    # A button turn has no Router intent; the Operator's action still decides the stage.
    current = state(input_event="submit", intent=None, ticket_operator_result=operator(
        "submitted", ticket_result={"submission_succeeded": True, "ticket": {"ticket_number": "T-1"}}))
    sheet = build_turn_sheet(current, "ar")
    assert sheet["stage"] == "submitted" and sheet["button"] == "submit" and sheet["ticket_number"] == "T-1"
    note = turn_note(sheet)
    assert "pressed Submit" in note and "Ticket created, number: T-1" in note
    assert not sheet["remind_pending"]


def test_failed_submit_is_not_reported_as_created():
    current = state(intent="pending_response", ticket_operator_result=operator(
        "submitted", ticket_result={"submission_succeeded": False, "error": "API down"}))
    sheet = build_turn_sheet(current, "ar")
    assert sheet["stage"] == "submission_failed" and sheet["ticket_number"] is None


def test_cancel_then_next_issue_is_one_transition_sentence():
    current = state(intent="pending_response", ticket_operator_result=operator(
        "offer", issue="مشكلة زر الأرشفة", cancelled_issue="ترجمة نص المراسلة"))
    sheet = build_turn_sheet(current, "ar")
    assert sheet["stage"] == "offer"
    assert sheet["transition"] == ("Preparation for «ترجمة نص المراسلة» was stopped; the next issue «مشكلة زر "
                                   "الأرشفة» is now offered.")
    assert "Cancelled issue:" not in turn_note(sheet)


def test_side_question_reminds_of_the_pending_summary_and_shows_the_draft():
    current = state(intent="question", search_outcome="answered",
                    messages=[HumanMessage(content="هل يدعم النظام بيانات التسليم؟")],
                    current_references=[{"document": "Guide", "text": "نعم", "relation": "DIRECT"}],
                    pending_customer_response=PendingCustomerResponse(kind="summary", issue_id="issue-1"),
                    active_issue={"id": "issue-1", "description": "زر الأرشفة لا يعمل"},
                    ticket_draft={"title_customer": "زر الأرشفة لا يعمل", "queue": "Ole5 Support", "missing": []})
    sheet = build_turn_sheet(current, "ar")
    assert sheet["stage"] == "answered" and sheet["remind_pending"]
    assert sheet["draft"]["queue"] == "Ole5 Support"
    assert "is still waiting" in turn_note(sheet)


def test_queued_and_skipped_facts_reach_the_note():
    current = state(intent="question", search_outcome="no_answer", ticket_operator_result=operator(
        "request_detail", requested_detail="error text or screenshot",
        skipped=[{"item": "page", "value": "unknown"}], queued_issue="طباعة المراسلات"))
    sheet = build_turn_sheet(current, "ar")
    assert sheet["stage"] == "detail_requested" and sheet["skipped"] == "page (unknown)"
    note = turn_note(sheet)
    assert "Queued this turn: «طباعة المراسلات»" in note and "Skipped detail: page (unknown)" in note


def test_system_prompt_lists_created_tickets_without_raw_dicts():
    current = state(created_tickets=[{"ticket_number": "T-1", "title_customer": "زر الأرشفة", "created_at": "2026-11-04",
                                      "ticket_id": "secret-id"}])
    prompt = system_prompt(current, build_turn_sheet(current, "ar"))
    assert "- T-1: زر الأرشفة (created 2026-11-04)" in prompt and "secret-id" not in prompt
