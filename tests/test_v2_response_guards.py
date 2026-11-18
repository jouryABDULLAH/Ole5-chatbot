"""Exercise response guards through the compiled graph with scripted model output."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ole5_chat import llm
from ole5_chat.agent.contracts import Grounding, SentenceVerdict
from ole5_chat.support_core.state import TicketDraft
from ole5_chat.agent.contracts import Language
from ole5_chat.agent import graph
from ole5_chat.agent.contracts import (OperatorCompletion, PendingCustomerResponse, RouterDecision,
                                      RouterDecisionWithoutPending, manager_schema)
from ole5_chat.agent.nodes import respond, validate
from ole5_chat.agent.state import initial_state
from ole5_chat.identity import Identity


def verdicts(count, unsupported=None):
    """A scripted grounding result for a reply of count sentences; unsupported maps a sentence number to its problem."""
    unsupported = unsupported or {}
    return Grounding(verdicts=[SentenceVerdict(
        number=number, verdict="unsupported" if number in unsupported else "not_product_fact",
        problem=unsupported.get(number, "")) for number in range(1, count + 1)])


def test_reply_is_split_into_the_sentences_the_check_numbers():
    from ole5_chat.agent.guards import sentences
    assert sentences("Hello! Do this:\n1. Open Archive.\n- Press Save؟ Done.") == [
        "Hello!", "Do this:", "1. Open Archive.", "- Press Save؟", "Done."]


def conversation(text, language="ar"):
    state = initial_state(Identity(customer_id="test", email="ole5@example.com", name="Test",
                                   organization="Test", product="OLE5", language=language))
    state.update(language=language, input_event="message",
                 messages=[AIMessage(content="ما اسم المستخدم؟"), HumanMessage(content=text)])
    return state


@pytest.mark.parametrize("next_field", [None, "browser name"])
def test_username_remains_arabic_when_operator_advances(model, operator_model, next_field):
    state = conversation("ahmad123")
    state.update(active_issue={"id": "issue-1", "description": "Cannot sign in"},
                 pending_customer_response=PendingCustomerResponse(kind="detail", issue_id="issue-1", item="username"),
                 ticket_draft=TicketDraft(product="OLE5", required={"username": None}).model_dump())
    required = {"username": "ahmad123"}
    if next_field:
        required[next_field] = None
    # A summary needs every classification field, as save_ticket_draft checks before it reports summary_ready.
    draft = TicketDraft(product="OLE5", required=required, classified=True, queue="Ole5 Support", type="Bug",
                        subtype="Other", reason="Sign-in fails", priority="Medium", title="Cannot sign in",
                        summary="The customer cannot sign in.")
    operator_model.call("save_ticket_draft", fields={"username": "ahmad123"}, issue_id="issue-1")
    completion = (OperatorCompletion(action="request_detail", reason="Username saved", requested_detail=next_field)
                  if next_field else OperatorCompletion(action="show_summary", reason="Username saved"))
    # A bare value names no language; the reply keeps the conversation's.
    model.add(RouterDecision(intent="pending_response", next_target="ticket_operator", explicit_ticket_request=False, reason="Requested username"),
              completion,
              Language(language="none"))
    with patch("ole5_chat.support_core.classify.classify", return_value={"ticket_draft": draft}),          patch.object(llm, "invoke", return_value=AIMessage(content="تم تسجيل اسم المستخدم.")) as writer:
        result = graph.build().invoke(state)
    assert result["language"] == "ar"
    assert result["pending_customer_response"].kind == ("detail" if next_field else "summary")
    assert "in Arabic" in writer.call_args_list[0].args[0][-1].content  # the first reply, before any rewrite
    # The customer's bare value needs the model; the all-Arabic reply is settled by its letters (2026-11-18).
    assert sum(schema is Language for schema, _ in model.calls) == 1


@pytest.mark.parametrize("text,route,action", [
    ("I do not know why this happens", "pending_response", "provide"),
    ("Please reply in English", "pending_response", "provide"),
    ("How do I reset my password", "question", "provide"),
    ("Hello", "other", "provide"),
    ("E403", "other", "provide"),
    ("E403", "pending_response", "not_an_answer"),
])
def test_conversational_messages_use_language_detector(text, route, action):
    state = conversation(text)
    state.update(intent=route, manager_action=action,
                 answered_detail=PendingCustomerResponse(kind="detail", issue_id="issue-1", item="error text"))
    # The conversation is Arabic and these letters are Latin (or absent), so the model reads them (2026-11-18).
    with patch.object(respond, "model_language", return_value="en") as detector:
        assert respond._expected_language(state) == "en"
    detector.assert_called_once_with(text)


@pytest.mark.parametrize("item,text", [("browser name", "Google Chrome"), ("error text", '"Access denied"'),
                                      ("error code", "E403"), ("filename", "screen.png")])
def test_literal_values_preserve_language(item, text):
    # The language model, not a regex, decides that a bare value names no language ("none" -> None).
    state = conversation(text)
    state.update(intent="pending_response", manager_action="provide",
                 answered_detail=PendingCustomerResponse(kind="detail", issue_id="issue-1", item=item))
    with patch.object(respond, "model_language", return_value=None) as detector:
        assert respond._expected_language(state) == "ar"
    detector.assert_called_once_with(text)


@pytest.mark.parametrize("failure", ["rewritten", "check_error", "rewrite_error", "still_unsupported"])
def test_grounding_rewrites_once_checks_the_rewrite_and_withholds_what_stays_unsupported(model, failure, caplog):
    state = conversation("How do I archive a record?", "en")
    model.add(RouterDecisionWithoutPending(intent="question", next_target="search", explicit_ticket_request=False, reason="Product question"))
    model.add(RouterDecisionWithoutPending(intent="question", next_target="respond", explicit_ticket_request=False, reason="Search returned the product result."))
    first = "Select Archive. There is an invented limit of 5 files."
    model.add(verdicts(2, {2: "No passage gives a limit."}))
    rewritten = "Select Archive."
    if failure == "still_unsupported":  # both rewrites (two since 2026-11-17) bring in a new unsupported instruction
        rewritten = "Select Archive. Then open Settings."
        model.add(verdicts(2, {2: "No passage gives this step."}), verdicts(2, {2: "No passage gives this step."}))
    rewrite = llm.ModelFailed("rewrite failed") if failure == "rewrite_error" else AIMessage(content=rewritten)

    def structured(system, messages, schema, node=None, check=None):
        if failure == "check_error" and schema is Grounding:
            raise llm.ModelFailed("offline")
        return model(system, messages, schema, node, check)

    retrieval = {"search_outcome": "answered", "current_references": [
        {"document": "Archive guide", "text": "Select Archive.", "relation": "DIRECT"}
    ]}
    # Since 2026-11-17 the grounding check runs in Validate and its rewrite is the shared Validate -> Respond repair.
    with patch.object(validate, "GROUNDING_ENFORCE", True), \
         patch.object(llm, "structured", structured), \
         patch.object(graph, "search_node", return_value=retrieval), \
         patch.object(llm, "invoke", side_effect=[AIMessage(content=first), rewrite, rewrite]) as writer:
        result = graph.build().invoke(state)
    message = result["messages"][-1]
    # The rewrite is checked again. When it is still unsupported, or a check or the rewrite fails, the answer is
    # withheld: the fixed reply is sent, never the unverified one.
    withheld = "Sorry, I couldn't prepare a reliable answer to your question. Please send it again."
    assert message.content == ("Select Archive." if failure == "rewritten" else withheld)
    if failure == "rewritten":
        assert message.additional_kwargs["sources"] == ["Archive guide"]
    else:  # a withheld answer carries no sources and is not kept as the latest supported answer
        assert message.additional_kwargs["sources"] == [] and result["last_answer_evidence"] is None
    if failure == "check_error":
        assert writer.call_count == 1 and "grounding check failed" in caplog.text
    else:
        assert sum(schema is Grounding for schema, _ in model.calls) == {
            "rewrite_error": 1, "rewritten": 2, "still_unsupported": 3}[failure]
        note = writer.call_args_list[1].args[0][-1].content
        assert "«There is an invented limit of 5 files.»: No passage gives a limit." in note
        assert "Keep every other sentence as it was" in note


def respond_then_validate(state):
    """Respond writes, Validate checks; the state carries both results, as in the graph."""
    state.update(respond.respond_node(state))
    state.update(validate.validate_node(state))
    return state


def test_grounding_verdicts_that_never_cover_the_reply_send_the_fixed_reply_not_the_unchecked_one(model):
    state = conversation("What is my registered email?", "en")
    state.update(intent="question", search_outcome="no_answer", current_references=[])
    invented = "Open Settings, then My Profile, to see your email."
    # Both attempts judge a sentence number the reply does not have and leave its one sentence without a verdict.
    wrong = Grounding(verdicts=[SentenceVerdict(number=7, verdict="unsupported", problem="No passage gives it.")])
    model.add(wrong, wrong)
    with patch.object(llm, "invoke", return_value=AIMessage(content=invented)) as writer:
        result = respond_then_validate(state)
    assert result["reply"] == "I could not find an answer to your question."
    assert result["validation_outcome"] == "fallback" and result["sources"] == []
    assert writer.call_count == 1  # no rewrite was attempted from an unusable check
    assert sum(schema is Grounding for schema, _ in model.calls) == 2  # the check and its correction retry


def test_grounding_runs_with_zero_passages_and_invented_steps_do_not_reach_the_customer(model):
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    state = conversation("What is my registered email?", "en")
    state.update(intent="question", search_outcome="no_answer", current_references=[],
                 active_issue={"id": "issue-1", "description": "Registered email"},
                 pending_customer_response=PendingCustomerResponse(kind="offer", issue_id="issue-1"),
                 ticket_operator_result={"action": "offer", "issue": "Registered email"})
    invented = "Open Settings, then My Profile, to see your email."
    model.add(*[verdicts(1, {1: "No passage gives these steps."})] * 3)
    with patch.object(llm, "invoke", return_value=AIMessage(content=invented)):
        # Two shared rewrites (since 2026-11-17), each checked again; the third rejection sends the fixed reply.
        assert respond_then_validate(state)["validation_outcome"] == "repair"
        assert respond_then_validate(state)["validation_outcome"] == "repair"
        reply = respond_then_validate(state)["reply"]
    assert reply == ("I could not find an answer to your question. "
                     "Would you like a support ticket for «Registered email»?")
    checked = validate._grounding_input(state, build_turn_sheet(state, "en"), invented, [])
    assert "Passages the reply's author saw (it need not use them all):\nnone" in checked
    assert "Account access: you cannot inspect customer account records" in checked
    # The reply goes in as numbered sentences, with the workflow facts and none of Respond's instructions.
    assert f"<reply>\n[1] {invented}\n</reply>" in checked
    assert "Current request (named by the problem the customer reported): «Registered email»" in checked
    assert "Guidance:" not in checked and "Write only the customer-facing reply" not in checked
    state["ticket_enquiry_result"] = {"action": "lookup_status", "tickets": [{"ticket_number": "T-1001"}]}
    assert "Ticket records the reply may also state:\nLookup result this turn:" in validate._grounding_input(
        state, build_turn_sheet(state, "en"), invented, [])
    assert "T-1001" in validate._grounding_input(state, build_turn_sheet(state, "en"), invented, [])


@pytest.mark.parametrize("reply", [
    "Hello! How can I help you?",
    "I cannot view your account details directly.",
    "I could not find an answer to that. I can open a ticket with the support team about it; would you like that?",
])
def test_zero_passage_reply_the_check_accepts_is_sent_as_written(model, reply):
    state = conversation("Hello", "en")
    state.update(intent="other", current_references=[])
    with patch.object(llm, "invoke", return_value=AIMessage(content=reply)) as writer:
        result = respond_then_validate(state)
    assert result["reply"] == reply and result["validation_outcome"] == "pass"
    assert writer.call_count == 1 and sum(schema is Grounding for schema, _ in model.calls) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_courtesy_only_skips_grounding_and_ticket_checks_unless_a_step_is_pending(model, pending):
    # Since 2026-11-18: the Router marks courtesy alone; with nothing pending, only the language check runs.
    from ole5_chat.agent.contracts import TicketActionCheck
    state = conversation("شكراً")
    state.update(intent="other", current_references=[], courtesy_only=True)
    if pending:  # a pending step brings a reminder about the ticket, so both checks still run
        state.update(active_issue={"id": "issue-1", "description": "ما أقدر أرفق ملفات"},
                     pending_customer_response=PendingCustomerResponse(kind="summary", issue_id="issue-1"))
    with patch.object(llm, "invoke", return_value=AIMessage(content="العفو، وإذا احتجت شي ثاني أنا موجود.")):
        result = respond_then_validate(state)
    assert result["validation_outcome"] == "pass"
    checked = {schema for schema, _ in model.calls}
    assert (Grounding in checked and TicketActionCheck in checked) is pending





