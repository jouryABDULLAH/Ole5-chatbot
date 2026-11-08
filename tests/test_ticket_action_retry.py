"""Conversation simulations for the bounded Validate -> Respond correction."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import Grounding, RouterDecisionWithoutPending, TicketActionCheck, TicketViolation
from ole5_chat.agent.nodes import respond
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.ticket_action_guard import fallback
from ole5_chat.agent import ticket_action_guard
from ole5_chat.identity import Identity


@pytest.mark.parametrize("rejected_twice", [False, True])
@pytest.mark.parametrize("route", ["question", "other"])
def test_ticket_lie_gets_only_one_response_retry(model, rejected_twice, route):
    state = initial_state(Identity(customer_id="test", email="ole5@example.com", name="Test",
                                   organization="Test", product="OLE5", language="ar"))
    state["messages"] = [AIMessage(content="تم فتح تذكرة."), HumanMessage(content=(
        "افتح لي تذكرة عن عدم إمكانية حذف المراسلات" if route == "question" else "هل أنت متأكد؟"))]
    target = "search" if route == "question" else "respond"
    model.add(RouterDecisionWithoutPending(intent=route, next_target=target,
                                           explicit_ticket_request=route == "question", reason="Test"))
    if route == "question":
        model.add(RouterDecisionWithoutPending(intent="question", next_target="respond",
                                              explicit_ticket_request=True, reason="Search completed."))
    created = TicketViolation(sentence="تم إنشاء التذكرة بنجاح.", problem="No actual ticket was created.",
                              fact="ticket_result is empty and created_tickets has no ticket.")
    model.add(TicketActionCheck(violations=[created]),
              TicketActionCheck(violations=[created] if rejected_twice else []))
    model.add(Grounding(unsupported=[]), Grounding(unsupported=[]))
    bad = "تم إنشاء التذكرة بنجاح."
    good = "حذف المراسلات يتطلب الصلاحية الموثقة. إذا استمرت المشكلة، أخبرني وسأساعدك ضمن نطاقي."
    evidence = {"search_outcome": "answered", "current_references": [
        {"document": "Guide", "text": "حذف المراسلات يتطلب الصلاحية الموثقة.", "relation": "DIRECT"}]}
    with patch.object(respond, "GROUNDING_ENFORCE", False), \
         patch.object(graph, "search_node", return_value=evidence) as search, \
         patch.object(llm, "invoke", side_effect=[AIMessage(content=bad), AIMessage(content=bad if rejected_twice else good)]) as writer, \
         patch("ole5_chat.tickets.submit.submit") as submit:
        result = graph.build().invoke(state)
    assert writer.call_count == 2
    assert search.call_count == (1 if route == "question" else 0)
    submit.assert_not_called()
    note = writer.call_args.args[0][-1].content  # the turn note of the repair
    assert "- «تم إنشاء التذكرة بنجاح.»: No actual ticket was created. Fact: ticket_result is empty" in note
    assert "Keep every other sentence as it was" in note
    assert result["created_tickets"] == []
    assert result["validation_retries"] == 0
    assert result["validation_note"] is None
    assert len(result["messages"]) == 3  # rejected candidate never entered history
    final = result["messages"][-1]
    assert final.content == (fallback({**state, "language": "ar"}) if rejected_twice else good)
    if rejected_twice:
        assert final.additional_kwargs["sources"] == []
        assert result["last_answer_evidence"] is None


IDENTITY = Identity(customer_id="test", email="ole5@example.com", name="Test", organization="Test",
                    product="OLE5", language="ar")


def test_fallback_preserves_actual_creation():
    state = {"identity": IDENTITY, "language": "en", "ticket_result": {
        "submission_succeeded": True, "ticket": {"ticket_number": "T-123"}}}
    assert fallback(state).startswith("Ticket T-123 was created. The support team's reply arrives")


def test_submit_intent_is_not_creation():
    state = {"identity": IDENTITY, "language": "en", "manager_action": "submit", "ticket_result": {
        "submission_succeeded": False}}
    assert "was not created" in fallback(state) and "T-" not in fallback(state)


def test_fallback_says_what_happened_then_asks_what_the_current_request_waits_on():
    from ole5_chat.agent.contracts import PendingCustomerResponse
    state = {"identity": IDENTITY, "language": "ar",
             "ticket_operator_result": {"cancelled_issue": "الترجمة", "queued_issue": None},
             "active_issue": {"id": "issue-2", "description": "زر الأرشفة لا يعمل"},
             "pending_customer_response": PendingCustomerResponse(kind="offer", issue_id="issue-2")}
    assert fallback(state) == ("أوقفت تحضير الطلب «الترجمة» ولم يُرسل شيء. "
                               "هل ترغب في فتح تذكرة دعم للطلب «زر الأرشفة لا يعمل»؟")
    state.update(ticket_operator_result={"queued_issue": "الطباعة"},
                 pending_customer_response=PendingCustomerResponse(kind="detail", issue_id="issue-2", item="page"))
    assert fallback(state) == ("سجلت الطلب «الطباعة» وسأتابعه بعد الطلب الحالي. "
                               "لإكمال الطلب «زر الأرشفة لا يعمل» أحتاج: اسم الصفحة.")


def test_fallback_with_nothing_to_say_asks_to_send_again():
    assert "إعادة إرسال" in fallback({"identity": IDENTITY, "language": "ar"})


def test_checker_receives_authoritative_records_and_a_failed_check_lets_the_reply_pass(model):
    identity = Identity(customer_id="test", email="ole5@example.com", name="Test",
                        organization="Test", product="OLE5", language="en")
    state = initial_state(identity)
    state.update(manager_action="submit", ticket_result={"submission_succeeded": False},
                 created_tickets=[{"ticket_number": "OLD-1", "title": "Unrelated issue"}])
    failed = TicketViolation(sentence="Ticket created", problem="Submission failed.",
                             fact="ticket_result.submission_succeeded is false.")
    with patch.object(llm, "structured", return_value=TicketActionCheck(violations=[failed])) as checker:
        assert ticket_action_guard.violations(state, "Ticket created") == [failed]
    payload = checker.call_args.args[1][0].content
    assert '"submission_succeeded": false' in payload
    assert '"OLD-1"' in payload
    with patch.object(llm, "structured", side_effect=llm.ModelFailed("offline")):
        assert ticket_action_guard.violations(state, "Ticket created") == []
