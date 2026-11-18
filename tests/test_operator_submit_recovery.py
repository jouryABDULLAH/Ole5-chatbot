"""Exercise submission recovery without writing tickets to a real service."""
import json
from unittest.mock import Mock

import pytest

from ole5_chat.agent.contracts import OperatorCompletion, PendingCustomerResponse
from ole5_chat.agent.nodes.ticket_operator import _apply_completion, _completion_check, _completion_from_tools
from ole5_chat.agent.prompts.ticket_operator import ticket_operator_tool_prompt
from ole5_chat.agent.state import initial_state
from ole5_chat.agent.tools import ticket_operator as tools
from ole5_chat.identity import Identity
from ole5_chat.support_core.state import TicketDraft


def ready_state():
    state = initial_state(Identity(customer_id='test', email='ole5@example.com',
        name='Test', organization='Test', product='OLE5', language='ar'))
    state.update(input_event='message', active_issue={'id': 'issue-1', 'description': 'Translation question'},
        pending_customer_response=PendingCustomerResponse(kind='summary', issue_id='issue-1'),
        summary_shown=True, submission_requested=True, draft_version=1, summary_delivered_version=1,
        ticket_draft=TicketDraft(product='Ole5', title='Translation', summary='Translation inquiry',
            queue='Ole5 Support', type='Inquiry', subtype='General questions', reason='Feature question',
            priority='Low', classified=True).model_dump())
    return state


@pytest.mark.parametrize("reported", ["offer", "already_submitted", "clarify", "workflow_failed"])
def test_submission_receipt_wins_over_next_offer_or_old_ticket(monkeypatch, reported):
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    state = ready_state()
    state["pending_issues"] = [{"id": "issue-2", "description": "Slow archive search"}]
    monkeypatch.setattr(tools.ticket_submit, "submit", lambda *args: ({"ticket_number": "TEST-1"}, []))
    monkeypatch.setattr(tools.ticket_api, "ticket_client", lambda: object())
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    receipt = bound["submit_ticket"].invoke({"issue_id": "issue-1", "draft_version": 1})
    report = json.dumps([{"name": "submit_ticket", "content": receipt}])
    mistaken = OperatorCompletion(action=reported, reason="Next offer or old ticket")
    assert _completion_check(state, report)(mistaken)
    update = _apply_completion(state, mistaken, report)
    sheet = build_turn_sheet({**state, **update}, "en")
    assert sheet["stage"] == "submitted" and sheet["ticket_number"] == "TEST-1"
    assert sheet["issue"] == "Translation question"
    assert sheet["next_issue"] == "Slow archive search"
    assert update["pending_customer_response"].issue_id == "issue-2"


def test_cancelled_preparation_is_not_reported_as_an_older_submitted_ticket():
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    state = ready_state()
    state.update(active_issue={"id": "issue-2", "description": "Slow archive search"},
                 pending_customer_response=PendingCustomerResponse(kind="offer", issue_id="issue-2"))
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    cancelled = bound["manage_issues"].invoke({"operation": "cancel_preparation", "issue_id": "issue-2"})
    report = json.dumps([
        {"name": "check_submitted_tickets", "content": json.dumps({"success": True, "tickets": [
            {"ticket_number": "OLD-1", "title": "Mobile attachment issue"}]})},
        {"name": "manage_issues", "content": cancelled},
    ])
    mistaken = OperatorCompletion(action="already_submitted", existing_ticket="OLD-1", reason="Old mobile issue")
    assert _completion_check(state, report)(mistaken)
    update = _apply_completion(state, mistaken, report)
    sheet = build_turn_sheet({**state, **update}, "en")
    assert sheet["stage"] == "preparation_stopped"
    assert sheet["cancelled_issue"] == "Slow archive search"
    assert not update["pending_customer_response"]
    assert not update["ticket_operator_result"]["existing_ticket"]


@pytest.mark.parametrize("mode", ["edit", "failed", "initial", "unchanged"])
def test_confirmed_edits_reach_respond_only_after_existing_draft_is_saved(monkeypatch, mode):
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    from ole5_chat.agent.prompts.respond import turn_note

    state = ready_state()
    state["ticket_draft"]["required"] = {"error text or screenshot": "unknown"}
    before = TicketDraft(**state["ticket_draft"])
    after = before.model_copy(update={"required": {"error text or screenshot": "ERORORO"}})
    if mode == "initial":
        state["ticket_draft"] = None
    monkeypatch.setattr(tools.ticket_classifier, "classify", lambda _: (
        {"note": "classification failed"} if mode == "failed" else {"ticket_draft": after}))
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    fields = {} if mode == "unchanged" else {"required": after.required}
    payload = bound["save_ticket_draft"].invoke({"fields": fields, "issue_id": "issue-1"})
    saved = json.loads(payload)
    expected = ([{"field": "required.error text or screenshot", "before": "unknown", "after": "ERORORO"}]
                if mode == "edit" else [])
    assert saved.get("confirmed_changes", []) == expected
    completion = _completion_from_tools(state, json.dumps([{"name": "save_ticket_draft", "content": payload}]))
    update = _apply_completion(state, completion, json.dumps([{"name": "save_ticket_draft", "content": payload}]))
    sheet = build_turn_sheet({**state, **update}, "ar")
    assert sheet["confirmed_changes"] == expected
    assert ("Confirmed draft changes this turn:" in turn_note(sheet)) == bool(expected)


def test_saved_changes_exclude_other_issues_failures_and_reverted_edits():
    from ole5_chat.agent.nodes.ticket_operator import _saved_changes

    def save(before, after, issue="issue-1", success=True):
        return {"name": "save_ticket_draft", "result": {
            "success": success, "issue_id": issue,
            "confirmed_changes": [{"field": "title", "before": before, "after": after}],
        }}
    assert _saved_changes([save("A", "B"), save("B", "C", success=False),
                           save("X", "Y", issue="issue-2")], "issue-1") == [
        {"field": "title", "before": "A", "after": "B"}]
    assert _saved_changes([save("A", "B"), save("B", "A")], "issue-1") == []
    detail = save(None, "Inbox")
    detail["result"]["confirmed_changes"][0]["field"] = "required.page"
    skip = {"name": "skip_detail", "result": {"success": True, "issue_id": "issue-1", "item": "page"}}
    assert _saved_changes([detail, skip], "issue-1") == []


def test_wrong_version_empty_save_and_successful_retry(monkeypatch):
    state = ready_state()
    classifier = Mock(side_effect=AssertionError('An empty save must not reclassify'))
    submit = Mock(return_value=({'ticket_number': 'TEST-1'}, []))
    monkeypatch.setattr(tools.ticket_classifier, 'classify', classifier)
    monkeypatch.setattr(tools.ticket_submit, 'submit', submit)
    monkeypatch.setattr(tools.ticket_api, 'ticket_client', lambda: object())
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    failed = bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 0})
    assert json.loads(failed)['draft_version'] == 1
    submit.assert_not_called()
    saved = json.loads(bound['save_ticket_draft'].invoke({'fields': {}, 'issue_id': 'issue-1'}))
    assert saved['unchanged'] and state['summary_shown']
    assert state['draft_version'] == state['summary_delivered_version'] == 1
    success = bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 1})
    assert json.loads(success)['success']
    submit.assert_called_once()
    report = json.dumps([{'name': 'submit_ticket', 'content': failed},
                         {'name': 'submit_ticket', 'content': success}])
    result = _apply_completion(state, OperatorCompletion(action='submitted', reason='Created'), report)
    assert result['ticket_result']['submission_succeeded']
    assert result['pending_customer_response'] is None
    assert not result['summary_shown']
    assert not json.loads(bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 1}))['success']
    submit.assert_called_once()


def test_submitted_ticket_record_keeps_content_and_clears_attachments(monkeypatch):
    state = ready_state()
    state['attachments'] = [{'id': 'a1', 'file_name': 'screen.png', 'size': 10}]
    monkeypatch.setattr(tools.ticket_submit, 'submit', Mock(return_value=(
        {'ticket_id': 'id-1', 'ticket_number': 'TEST-1', 'created_at': '2026-11-03'}, [])))
    monkeypatch.setattr(tools.ticket_api, 'ticket_client', lambda: object())
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    success = bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 1})
    result = _apply_completion(state, OperatorCompletion(action='submitted', reason='Created'),
        json.dumps([{'name': 'submit_ticket', 'content': success}]))
    record = result['created_tickets'][-1]
    assert record['ticket_number'] == 'TEST-1'
    assert (record['title'], record['summary'], record['queue'], record['type'], record['subtype'],
            record['priority']) == ('Translation', 'Translation inquiry', 'Ole5 Support', 'Inquiry',
                                    'General questions', 'Low')
    assert record['attachments'] == ['screen.png']
    assert result['attachments'] == []


def detail_state():
    state = ready_state()
    state.update(pending_customer_response=PendingCustomerResponse(kind='detail', issue_id='issue-1', item='page'),
                 summary_shown=False, submission_requested=False, summary_delivered_version=None,
                 ticket_draft=TicketDraft(**{**state['ticket_draft'], 'required': {'page': None, 'browser': 'Chrome'},
                                             'missing': ['page']}).model_dump())
    return state


@pytest.mark.parametrize('earlier_ticket', [True, False])
def test_send_request_with_nothing_waiting_is_not_reported_as_a_failed_submission(earlier_ticket):
    from ole5_chat.agent import ticket_action_guard
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    state = ready_state()
    # The ticket was already submitted (or none was ever prepared): no summary waits for confirmation.
    state.update(summary_shown=False, submission_requested=False, pending_customer_response=None,
                 created_tickets=[{'ticket_number': 'T-1'}] if earlier_ticket else [])
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    refused = bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 1})
    assert json.loads(refused)['nothing_to_submit']
    report = json.dumps([{'type': 'tool', 'name': 'submit_ticket', 'content': refused}])
    update = _apply_completion(state, OperatorCompletion(action='submission_failed', reason='nothing waiting'), report)
    assert update['ticket_result'] == {'nothing_to_submit': True}
    state.update(update)
    sheet = build_turn_sheet(state, 'ar')
    assert sheet['stage'] == 'other' and 'nothing was submitted' in sheet['operator']
    reply = ticket_action_guard.fallback(state)
    assert ('التذكرة مرفوعة من قبل، ورقمها T-1' in reply) if earlier_ticket else ('ما عندي تذكرة جاهزة للإرسال' in reply)
    assert 'التذكرة ما انرفعت' not in reply


@pytest.mark.parametrize('description,expected', [
    ('Login using facial or fingerprint biometrics', 'ودك أرفع لك تذكرة دعم لهالطلب؟'),
    ('تسجيل الدخول بالبصمة', 'ودك أرفع لك تذكرة دعم بخصوص «تسجيل الدخول بالبصمة»؟'),
])
def test_fallback_never_quotes_a_request_name_in_the_other_language(description, expected):
    from ole5_chat.agent import ticket_action_guard
    state = ready_state()
    state.update(language='ar', search_outcome='no_answer', ticket_draft=None,
                 active_issue={'id': 'issue-1', 'description': description},
                 pending_customer_response=PendingCustomerResponse(kind='offer', issue_id='issue-1'))
    assert ticket_action_guard.fallback(state) == f'ما لقيت جواب لسؤالك. {expected}'


def test_skip_detail_settles_the_item_and_readies_the_summary():
    state = detail_state()
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['skip_detail'].invoke({'item': 'page', 'value': 'unknown'}))
    assert result['success'] and result['summary_ready'] and result['missing_customer_details'] == []
    assert state['ticket_draft']['required']['page'] == 'unknown'
    assert not json.loads(bound['skip_detail'].invoke({'item': 'colour', 'value': 'unknown'}))['success']


@pytest.mark.parametrize('urgent', [True, False])
def test_urgent_issue_summary_is_ready_without_customer_details(monkeypatch, urgent):
    state = detail_state()
    # Both details are genuinely unanswered; reclassification must not erase a saved browser.
    state['ticket_draft']['required']['browser'] = None
    state['active_issue'] = {**state['active_issue'], 'urgent': urgent}
    reclassified = TicketDraft(**{**state['ticket_draft'], 'required': {'page': None, 'browser': None},
                                  'missing': ['page', 'browser'], 'classified': True})
    monkeypatch.setattr(tools.ticket_classifier, 'classify', lambda _: {'ticket_draft': reclassified})
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['save_ticket_draft'].invoke({'fields': {'summary': 'Nobody can sign in'},
                                                           'issue_id': 'issue-1'}))
    assert result['summary_ready'] is urgent
    assert result['missing_customer_details'] == ['page', 'browser']  # still reported, only not asked


# Requirement changed 2026-11-17: Bug asks no details; Mobile App Issue asks only the phone's operating system.
@pytest.mark.parametrize('ticket_type,subtype,asked', [
    ('Mobile App Issue', 'Application crash', {'phone os': 'iOS 17'}),
    ('Bug', 'Software malfunction', {}),
    ('Inquiry', 'General questions', {}),
])
def test_required_details_come_from_the_ticket_type(model, ticket_type, subtype, asked):
    from ole5_chat.support_core.classify import classify
    from ole5_chat.support_core.contracts import Classification
    state = detail_state()
    # The model leaves out the bug's required list and keeps one value it heard; the code restores the list.
    model.add(Classification(
        queue='Ole5 Technical', type=ticket_type, subtype=subtype, reason='Issue - archive button',
        priority='Medium', title='Archive button fails', summary='The archive button fails.',
        title_customer='Archive button fails', summary_customer='The archive button fails.',
        confidence='high', queue_reason='Product behaviour', collected={},
        required={'phone os': 'iOS 17'} if ticket_type != 'Inquiry' else {'colour': None}))
    result = classify(tools._classification_state(state, TicketDraft(product='Ole5')))
    assert result['ticket_draft'].required == asked
    assert result['ticket_draft'].missing == [key for key, value in asked.items() if value is None]


def test_null_customer_wording_for_an_unanswered_detail_is_dropped_not_refused(model):
    from ole5_chat.support_core.classify import classify
    from ole5_chat.support_core.contracts import Classification
    # The model writes null for the detail not given, as it does in required; the classification still stands.
    model.add(Classification.model_validate({
        'queue': 'Ole5 Technical', 'type': 'Bug', 'subtype': 'Software malfunction',
        'reason': 'Issue - archive button', 'priority': 'Medium', 'title': 'Archive button fails',
        'summary': 'The archive button fails.', 'confidence': 'high', 'queue_reason': 'Product behaviour',
        'required': {'page': None, 'error text or screenshot': 'Access denied'},
        'required_customer': {'page': None, 'error text or screenshot': 'Access denied'}}))
    result = classify(tools._classification_state(detail_state(), TicketDraft(product='Ole5')))
    assert result['ticket_draft'].required_customer == {'error text or screenshot': 'Access denied'}


def test_reclassifying_keeps_a_skipped_detail(monkeypatch):
    state = detail_state()
    state['ticket_draft']['required']['page'] = 'declined'
    reclassified = TicketDraft(**{**state['ticket_draft'], 'required': {'page': None, 'browser': 'Chrome'},
                                  'missing': ['page'], 'classified': True})
    monkeypatch.setattr(tools.ticket_classifier, 'classify', lambda _: {'ticket_draft': reclassified})
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['save_ticket_draft'].invoke({'fields': {'summary': 'Changed'}, 'issue_id': 'issue-1'}))
    assert result['missing_customer_details'] == [] and result['summary_ready']
    assert state['ticket_draft']['required']['page'] == 'declined'


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('lost_error', [None, 'different invented error'])
def test_page_edit_preserves_saved_error_and_merges_classifier_input(monkeypatch, nested, lost_error):
    state = ready_state()
    state['ticket_draft']['required'] = {'page': 'Wall page', 'error text or screenshot': 'ERROR_load'}
    seen = []

    def classify(value):
        draft = value['ticket_draft']
        seen.append(draft)
        return {'ticket_draft': draft.model_copy(update={
            'required': {'page': 'Inbox page', 'error text or screenshot': lost_error}})}

    monkeypatch.setattr(tools.ticket_classifier, 'classify', classify)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    edits = {'page': 'صفحة الوارد'}
    saved = json.loads(bound['save_ticket_draft'].invoke({'fields': {'required': edits} if nested else edits}))
    assert seen[0].required == {'page': 'صفحة الوارد', 'error text or screenshot': 'ERROR_load'}
    assert saved['draft']['required'] == {'page': 'Inbox page', 'error text or screenshot': 'ERROR_load'}
    assert saved['summary_ready'] and saved['missing_customer_details'] == []
    assert saved['confirmed_changes'] == [{'field': 'required.page', 'before': 'Wall page', 'after': 'صفحة الوارد'}]


@pytest.mark.parametrize('new_value', ['ERROR_new', None])
def test_explicit_error_edit_or_clear_is_not_restored_from_old_draft(monkeypatch, new_value):
    state = ready_state()
    state['ticket_draft']['required'] = {'page': 'Inbox', 'error text or screenshot': 'ERROR_old'}
    def classify(value):
        return {'ticket_draft': value['ticket_draft'].model_copy(update={
            'required': {'page': None, 'error text or screenshot': None}})}
    monkeypatch.setattr(tools.ticket_classifier, 'classify', classify)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    saved = json.loads(bound['save_ticket_draft'].invoke({'fields': {
        'required': {'error text or screenshot': new_value}}}))
    assert saved['draft']['required'] == {'page': 'Inbox', 'error text or screenshot': new_value}
    assert saved['missing_customer_details'] == ([] if new_value else ['error text or screenshot'])


def test_type_change_does_not_restore_details_no_longer_required(monkeypatch):
    state = ready_state()
    state['ticket_draft']['required'] = {'page': 'Inbox', 'error text or screenshot': 'ERROR_old'}
    monkeypatch.setattr(tools.ticket_classifier, 'classify', lambda value: {
        'ticket_draft': value['ticket_draft'].model_copy(update={'required': {'username': None}})})
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    saved = json.loads(bound['save_ticket_draft'].invoke({'fields': {'summary': 'Password issue'}}))
    assert saved['draft']['required'] == {'username': None}
    assert saved['missing_customer_details'] == ['username']


@pytest.mark.parametrize('ready', [True, False])
def test_offer_cannot_replace_the_next_step_from_a_successful_draft_save(ready):
    state = ready_state()
    state['summary_shown'] = False  # saving a revision hides the old summary until it is presented again
    state['ticket_draft']['required'] = {'page': 'Inbox' if ready else None}
    state['ticket_draft']['missing'] = [] if ready else ['page']
    report = json.dumps([{'name': 'save_ticket_draft', 'content': json.dumps({
        'success': True, 'issue_id': 'issue-1', 'summary_ready': ready,
        'classification_succeeded': True, 'missing_customer_details': [] if ready else ['page'],
    })}])
    mistaken = OperatorCompletion(action='offer', reason='Ask whether to send', issue_id='issue-1')
    assert _completion_check(state, report)(mistaken)
    update = _apply_completion(state, mistaken, report)
    assert update['ticket_operator_result']['action'] == ('show_summary' if ready else 'request_detail')
    assert update['pending_customer_response'].kind == ('summary' if ready else 'detail')
    assert update['ticket_draft'] == state['ticket_draft']
    if ready:
        assert update['summary_shown']


def test_cancel_ends_the_issue_activates_the_next_and_is_not_offered_again():
    state = detail_state()
    state['pending_issues'] = [{'id': 'issue-2', 'description': 'Archive button', 'source': 'no_answer'}]
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    cancelled = bound['manage_issues'].invoke({'operation': 'cancel_preparation', 'issue_id': 'issue-1'})
    assert state['active_issue']['id'] == 'issue-2' and state['pending_issues'] == []
    report = json.dumps([{'name': 'manage_issues', 'content': cancelled}])
    result = _apply_completion(state, OperatorCompletion(action='offer', reason='Offer again', issue_id='issue-1'),
                               report)
    assert result['ticket_operator_result']['action'] == 'cancelled'
    assert result['ticket_operator_result']['issue'] == 'Translation question'
    assert result['pending_customer_response'] is None


def test_completion_check_names_only_real_details_and_offers_the_next_issue():
    from ole5_chat.agent.nodes.ticket_operator import _completion_check
    state = detail_state()
    check = _completion_check(state, "[]")
    assert check(OperatorCompletion(action='request_detail', reason='r', requested_detail='page')) is None
    assert 'not a missing item' in check(OperatorCompletion(action='request_detail', reason='r',
                                                            requested_detail='user role'))
    state['ticket_draft'] = None
    assert 'no draft exists' in _completion_check(state, "[]")(
        OperatorCompletion(action='request_detail', reason='r', requested_detail='user role'))
    report = json.dumps([{'name': 'manage_issues', 'content': json.dumps({
        'success': True, 'operation': 'cancel_preparation', 'active_issue': {'id': 'issue-2'}})}])
    after_cancel = _completion_check(detail_state(), report)
    assert 'return offer' in after_cancel(OperatorCompletion(action='cancelled', reason='r', issue_id='issue-1'))
    assert after_cancel(OperatorCompletion(action='offer', reason='r', issue_id='issue-2')) is None


def test_submit_activates_and_offers_the_next_queued_issue(monkeypatch):
    state = ready_state()
    state['pending_issues'] = [{'id': 'issue-2', 'description': 'Printing shows a blank page', 'source': 'no_answer'}]
    monkeypatch.setattr(tools.ticket_submit, 'submit', Mock(return_value=({'ticket_number': 'TEST-2'}, [])))
    monkeypatch.setattr(tools.ticket_api, 'ticket_client', lambda: object())
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    success = bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 1})
    assert state['active_issue']['id'] == 'issue-2' and state['pending_issues'] == []
    result = _apply_completion(state, OperatorCompletion(action='submitted', reason='Created'),
                               json.dumps([{'name': 'submit_ticket', 'content': success}]))
    assert result['ticket_result']['submission_succeeded']
    assert result['pending_customer_response'].kind == 'offer'
    assert result['pending_customer_response'].issue_id == 'issue-2'
    assert result['ticket_operator_result']['next_issue'] == 'Printing shows a blank page'


def test_new_issue_during_an_offer_is_queued_and_the_offer_stays():
    state = ready_state()
    offer = PendingCustomerResponse(kind='offer', issue_id='issue-1')
    state.update(pending_customer_response=offer, summary_shown=False, submission_requested=False)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    queued = bound['manage_issues'].invoke({'operation': 'register', 'description': 'Archive button fails'})
    result = _apply_completion(state, OperatorCompletion(action='offer', reason='Offer again', issue_id='issue-1'),
                               json.dumps([{'name': 'manage_issues', 'content': queued}]))
    assert result['ticket_operator_result']['action'] == 'queued'
    assert result['ticket_operator_result']['queued_issue'] == 'Archive button fails'
    assert result['pending_customer_response'] == offer
    assert [issue['description'] for issue in result['pending_issues']] == ['Archive button fails']


def test_two_new_issues_offer_the_first_and_queue_the_second():
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None,
                 summary_shown=False, submission_requested=False)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    first = bound['manage_issues'].invoke({'operation': 'register', 'description': 'Login fails'})
    second = bound['manage_issues'].invoke({'operation': 'register', 'description': 'Archive button fails'})
    result = _apply_completion(state, OperatorCompletion(action='offer', reason='Offer the first', issue_id='issue-1'),
                               json.dumps([{'name': 'manage_issues', 'content': first},
                                           {'name': 'manage_issues', 'content': second}]))
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['pending_customer_response'].kind == 'offer'


def test_already_submitted_problem_registers_nothing_and_names_the_ticket(monkeypatch):
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None,
                 summary_shown=False, submission_requested=False)
    monkeypatch.setattr('ole5_chat.agent.nodes.ticket_enquiry.sheets.tickets', lambda: [
        {'customer_email': 'ole5@example.com', 'ticket_number': 'T-1', 'title': 'Archive button not working'},
        {'customer_email': 'other@example.com', 'ticket_number': 'T-2', 'title': 'Login fails'}])
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    listed = bound['check_submitted_tickets'].invoke({})
    assert [t['ticket_number'] for t in json.loads(listed)['tickets']] == ['T-1']  # only this customer's tickets
    report = json.dumps([{'name': 'check_submitted_tickets', 'content': listed}])
    result = _apply_completion(state, OperatorCompletion(action='already_submitted', reason='Same problem as T-1',
                                                         existing_ticket='T-1'), report)
    assert result['ticket_operator_result']['action'] == 'already_submitted'
    assert result['ticket_operator_result']['existing_ticket'] == 'T-1'
    assert result['pending_customer_response'] is None and result['active_issue'] is None
    # A ticket check_submitted_tickets did not return is not reported as the customer's ticket. It was clarify;
    # naming a ticket the lookup never returned is an internal fault, not an unclear customer.
    unlisted = OperatorCompletion(action='already_submitted', reason='Same problem as T-2', existing_ticket='T-2')
    assert 'T-2' in _completion_check(state, report)(unlisted)  # the interpreter is told first
    result = _apply_completion(state, unlisted, report)
    assert result['ticket_operator_result']['action'] == 'workflow_failed'
    assert result['ticket_operator_result']['existing_ticket'] is None


def test_search_handoff_with_only_a_requirements_read_runs_the_agent_once_more(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from ole5_chat.agent.nodes import ticket_operator as node
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None, summary_shown=False,
                 submission_requested=False, last_worker='search', search_outcome='no_answer',
                 messages=[HumanMessage(content='وش بريدي المسجل')])
    monkeypatch.setattr('ole5_chat.agent.nodes.ticket_enquiry.sheets.tickets', lambda: [])
    runs = []

    class ScriptedAgent:
        def __init__(self, tools):
            self.tools = {tool.name: tool for tool in tools}

        def invoke(self, payload):
            runs.append(payload['messages'])
            done = []
            if len(runs) == 1:
                # Reading the requirements is not a workflow step: it must not stop the second run.
                call = {'name': 'get_ticket_requirements', 'args': {}, 'id': 'requirements', 'type': 'tool_call'}
                done = [AIMessage(content='', tool_calls=[call]), self.tools['get_ticket_requirements'].invoke(call)]
            else:
                call = {'name': 'manage_issues', 'args': {'operation': 'register', 'description': 'Registered email'},
                        'id': 'register', 'type': 'tool_call'}
                done = [AIMessage(content='', tool_calls=[call]), self.tools['manage_issues'].invoke(call)]
            return {'messages': [*payload['messages'], *done, AIMessage(content='handoff')]}

    # Nothing could match the request, so the first run is not interpreted at all: one completion is enough.
    completions = iter([OperatorCompletion(action='offer', reason='Registered', issue_id='issue-1')])
    monkeypatch.setattr(node, 'create_agent', lambda **kwargs: ScriptedAgent(kwargs['tools']))
    monkeypatch.setattr(node.llm, 'tool_client', lambda temperature: None)
    monkeypatch.setattr(node.llm, 'structured', lambda *args, **kwargs: next(completions))
    result = node.ticket_operator_node(state)
    assert len(runs) == 2
    assert runs[1][-1].type == 'human' and 'returned no submitted tickets' in runs[1][-1].content
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['active_issue']['description'] == 'Registered email'


def test_registration_then_clarify_does_not_run_the_agent_again(monkeypatch):
    sent, result = _search_handoff_run(monkeypatch, lambda: [], register=True, completion=OperatorCompletion(
        action='clarify', reason='Interpreter mistake'))
    assert sum(1 for message in sent if message.type == 'human') == 1  # one run: no system note was added
    assert result['active_issue']['description'] == 'Registered email'


def _search_handoff_run(monkeypatch, tickets, completion, register=False):
    """Run the Operator node on a Search no_answer handoff with a scripted agent; returns what the agent was sent
    and the node's result."""
    from langchain_core.messages import AIMessage, HumanMessage
    from ole5_chat.agent.nodes import ticket_operator as node
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None, summary_shown=False,
                 submission_requested=False, last_worker='search', search_outcome='no_answer',
                 messages=[HumanMessage(content='وش بريدي المسجل')])
    monkeypatch.setattr('ole5_chat.agent.nodes.ticket_enquiry.sheets.tickets', tickets)
    sent = []

    class ScriptedAgent:
        def __init__(self, tools):
            self.register = next(tool for tool in tools if tool.name == 'manage_issues')

        def invoke(self, payload):
            sent.extend(payload['messages'])
            done = []
            if register:
                call = {'name': 'manage_issues', 'args': {'operation': 'register', 'description': 'Registered email'},
                        'id': 'register', 'type': 'tool_call'}
                done = [AIMessage(content='', tool_calls=[call]), self.register.invoke(call)]
            return {'messages': [*payload['messages'], *done, AIMessage(content='handoff')]}

    monkeypatch.setattr(node, 'create_agent', lambda **kwargs: ScriptedAgent(kwargs['tools']))
    monkeypatch.setattr(node.llm, 'tool_client', lambda temperature: None)
    monkeypatch.setattr(node.llm, 'structured', lambda *args, **kwargs: completion)
    return sent, node.ticket_operator_node(state)


def test_search_handoff_lookup_runs_in_code_and_reaches_agent_and_report(monkeypatch):
    sent, result = _search_handoff_run(
        monkeypatch,
        lambda: [{'customer_email': 'ole5@example.com', 'ticket_number': 'T-1', 'title': 'Registered email'}],
        OperatorCompletion(action='already_submitted', reason='Same request as T-1', existing_ticket='T-1'))
    # The agent starts with the lookup already made: the call, then its result.
    assert sent[-2].tool_calls[0]['name'] == 'check_submitted_tickets'
    assert sent[-1].type == 'tool' and sent[-1].name == 'check_submitted_tickets'
    assert [t['ticket_number'] for t in json.loads(sent[-1].content)['tickets']] == ['T-1']
    # The report holds it too: already_submitted survives only for a ticket the lookup listed.
    assert result['ticket_operator_result']['action'] == 'already_submitted'
    assert result['ticket_operator_result']['existing_ticket'] == 'T-1'


def test_failed_search_handoff_lookup_still_registers_and_offers(monkeypatch):
    def unavailable():
        raise OSError('sheet unavailable')

    sent, result = _search_handoff_run(monkeypatch, unavailable, register=True, completion=OperatorCompletion(
        action='offer', reason='Registered without the duplicate comparison', issue_id='issue-1'))
    lookup = json.loads(sent[-1].content)
    assert lookup['success'] is False and 'tickets' not in lookup  # a failure, not an empty ticket list
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['active_issue']['description'] == 'Registered email'
    assert result['pending_customer_response'].kind == 'offer'


def test_search_handoff_agent_that_registers_nothing_is_run_again(monkeypatch):
    """Part 2: with no submitted ticket to match, an agent run that ends without registering is retried once."""
    from langchain_core.messages import AIMessage, HumanMessage
    from ole5_chat.agent.nodes import ticket_operator as node
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None, summary_shown=False,
                 submission_requested=False, last_worker='search', search_outcome='no_answer',
                 messages=[HumanMessage(content='وش بريدي المسجل')])
    monkeypatch.setattr('ole5_chat.agent.nodes.ticket_enquiry.sheets.tickets', lambda: [])
    runs = []

    class ScriptedAgent:
        def __init__(self, tools):
            self.register = next(tool for tool in tools if tool.name == 'manage_issues')

        def invoke(self, payload):
            runs.append(payload['messages'])
            done = []
            if len(runs) > 1:  # the first run makes no tool call; the retry registers
                call = {'name': 'manage_issues', 'args': {'operation': 'register', 'description': 'Registered email'},
                        'id': 'register', 'type': 'tool_call'}
                done = [AIMessage(content='', tool_calls=[call]), self.register.invoke(call)]
            return {'messages': [*payload['messages'], *done, AIMessage(content='No ticket is needed.')]}

    monkeypatch.setattr(node, 'create_agent', lambda **kwargs: ScriptedAgent(kwargs['tools']))
    monkeypatch.setattr(node.llm, 'tool_client', lambda temperature: None)
    monkeypatch.setattr(node.llm, 'structured', lambda *args, **kwargs: OperatorCompletion(
        action='offer', reason='Registered on the retry', issue_id='issue-1'))
    result = node.ticket_operator_node(state)
    assert len(runs) == 2
    assert result['active_issue']['description'] == 'Registered email'
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['pending_customer_response'].kind == 'offer'


def test_clarify_after_a_successful_register_offers_the_issue():
    """Part 3: a registered issue is never left active with nothing pending."""
    from ole5_chat.agent.nodes.ticket_operator import _completion_check
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None,
                 summary_shown=False, submission_requested=False)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    registered = bound['manage_issues'].invoke({'operation': 'register', 'description': 'Registered email'})
    report = json.dumps([{'name': 'manage_issues', 'content': registered}])
    clarify = OperatorCompletion(action='clarify', reason='Nothing to do')
    # The model is told that clarify does not fit a registration it made.
    assert _completion_check(state, report)(clarify) is not None
    # If it keeps clarify, the registered issue is offered anyway.
    result = _apply_completion(state, clarify, report)
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['pending_customer_response'].kind == 'offer'
    assert result['pending_customer_response'].issue_id == 'issue-1'


def test_search_handoff_that_never_registers_is_a_workflow_failure(monkeypatch):
    def unavailable():
        raise OSError('sheet unavailable')

    for tickets, said in ((lambda: [], 'returned no submitted tickets'), (unavailable, 'The lookup failed')):
        sent, result = _search_handoff_run(monkeypatch, tickets, completion=None)  # the interpreter is never needed
        notes = [m.content for m in sent if m.type == 'human' and 'Note from the system' in m.content]
        assert len(notes) == 1 and said in notes[0]  # one more run, told what the lookup showed
        assert result['ticket_operator_result']['action'] == 'workflow_failed'
        assert result['active_issue'] is None and result['pending_customer_response'] is None


def test_search_handoff_with_a_ticket_to_match_and_no_valid_match_is_run_again_then_fails(monkeypatch):
    sent, result = _search_handoff_run(
        monkeypatch,
        lambda: [{'customer_email': 'ole5@example.com', 'ticket_number': 'T-1', 'title': 'Printing'}],
        completion=OperatorCompletion(action='clarify', reason='No match named'))
    notes = [m.content for m in sent if m.type == 'human' and 'Note from the system' in m.content]
    assert len(notes) == 1 and 'identify the matching ticket or issue' in notes[0]
    assert result['ticket_operator_result']['action'] == 'workflow_failed'


def test_two_registrations_in_one_turn_offer_the_first_from_the_tool_results():
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None,
                 summary_shown=False, submission_requested=False)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    report = json.dumps([
        {'name': 'manage_issues', 'content': bound['manage_issues'].invoke(
            {'operation': 'register', 'description': 'Login fails'})},
        {'name': 'manage_issues', 'content': bound['manage_issues'].invoke(
            {'operation': 'register', 'description': 'Archive button fails'})}])
    derived = _completion_from_tools(state, report)
    assert (derived.action, derived.issue_id) == ('offer', 'issue-1')
    result = _apply_completion(state, OperatorCompletion(action='clarify', reason='Unsure'), report)
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['ticket_operator_result']['queued_issue'] == 'Archive button fails'  # the second is still noted
    # A draft saved for the first issue before the second was queued is not sent back to an offer.
    first, second = json.loads(report)
    saved = {'name': 'save_ticket_draft', 'content': json.dumps(
        {'success': True, 'operation': 'save_ticket_draft', 'summary_ready': True, 'missing_customer_details': []})}
    assert _completion_from_tools(state, json.dumps([first, saved, second])).action == 'show_summary'


def test_clarify_after_a_queued_registration_is_queued_and_keeps_the_waiting_summary():
    state = ready_state()  # issue-1 active, its summary waiting
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    report = json.dumps([{'name': 'manage_issues', 'content': bound['manage_issues'].invoke(
        {'operation': 'register', 'description': 'Printing fails'})}])
    clarify = OperatorCompletion(action='clarify', reason='Unsure')
    assert 'queued' in _completion_check(state, report)(clarify)
    result = _apply_completion(state, clarify, report)
    assert result['ticket_operator_result']['action'] == 'queued'
    assert result['pending_customer_response'].kind == 'summary' and result['summary_shown']


def test_clarify_after_a_saved_draft_follows_the_draft_result():
    state = detail_state()
    saved = {'success': True, 'operation': 'save_ticket_draft', 'summary_ready': False,
             'missing_customer_details': ['page']}
    report = json.dumps([{'name': 'save_ticket_draft', 'content': json.dumps(saved)}])
    result = _apply_completion(state, OperatorCompletion(action='clarify', reason='Unsure'), report)
    assert result['ticket_operator_result']['action'] == 'request_detail'
    assert result['ticket_operator_result']['requested_detail'] == 'page'
    ready = json.dumps([{'name': 'save_ticket_draft', 'content': json.dumps(
        {**saved, 'summary_ready': True, 'missing_customer_details': []})}])
    result = _apply_completion(state, OperatorCompletion(action='workflow_failed', reason='Unsure'), ready)
    assert result['ticket_operator_result']['action'] == 'show_summary'


def test_failed_preparation_is_a_workflow_failure_and_the_offer_still_waits():
    state = ready_state()
    state.update(pending_customer_response=PendingCustomerResponse(kind='offer', issue_id='issue-1'),
                 ticket_draft=None, summary_shown=False, submission_requested=False)
    report = json.dumps([{'name': 'save_ticket_draft', 'content': json.dumps(
        {'success': False, 'operation': 'save_ticket_draft', 'classification_succeeded': False, 'error': 'down'})}])
    failed = _completion_from_tools(state, report)
    assert failed.action == 'workflow_failed'
    assert _completion_check(state, report)(failed) is None
    result = _apply_completion(state, failed, report)
    assert result['ticket_operator_result']['action'] == 'workflow_failed'
    assert result['pending_customer_response'].kind == 'offer'  # the customer's yes can be given again
    assert result['active_issue']['id'] == 'issue-1'


def test_registration_then_failed_preparation_stays_a_failure_and_keeps_the_issue_with_its_offer():
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None,
                 summary_shown=False, submission_requested=False)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    report = json.dumps([
        {'name': 'manage_issues', 'content': bound['manage_issues'].invoke(
            {'operation': 'register', 'description': 'Change the email'})},
        {'name': 'save_ticket_draft', 'content': json.dumps(
            {'success': False, 'operation': 'save_ticket_draft', 'classification_succeeded': False, 'error': 'down'})}])
    assert _completion_from_tools(state, report).action == 'workflow_failed'  # the registration does not cover it
    check = _completion_check(state, report)
    assert 'draft step failed' in check(OperatorCompletion(action='offer', reason='Registered', issue_id='issue-1'))
    assert check(OperatorCompletion(action='workflow_failed', reason='Preparation failed')) is None
    result = _apply_completion(state, OperatorCompletion(action='offer', reason='Registered'), report)
    assert result['ticket_operator_result']['action'] == 'workflow_failed'
    assert result['active_issue']['description'] == 'Change the email'  # the registered issue is kept
    assert result['pending_customer_response'].kind == 'offer'  # and a yes can prepare it again


def test_draft_step_on_the_active_issue_is_not_hidden_by_an_issue_queued_after_it():
    state = ready_state()
    state.update(pending_customer_response=PendingCustomerResponse(kind='offer', issue_id='issue-1'),
                 ticket_draft=None, summary_shown=False, submission_requested=False)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    report = json.dumps([
        {'name': 'save_ticket_draft', 'content': json.dumps(
            {'success': True, 'operation': 'save_ticket_draft', 'summary_ready': False,
             'missing_customer_details': ['page']})},
        {'name': 'manage_issues', 'content': bound['manage_issues'].invoke(
            {'operation': 'register', 'description': 'Printing fails'})}])
    assert _completion_from_tools(state, report).action == 'request_detail'
    queued = OperatorCompletion(action='queued', reason='Printing noted')
    assert 'request_detail' in _completion_check(state, report)(queued)
    result = _apply_completion(state, queued, report)
    assert result['ticket_operator_result']['action'] == 'request_detail'
    assert result['pending_customer_response'].kind == 'detail'  # the accepted offer moved on to its detail
    assert result['ticket_operator_result']['queued_issue'] == 'Printing fails'  # the queued issue is still a fact


def test_a_match_to_an_existing_issue_must_name_an_issue_that_exists():
    state = ready_state()  # issue-1 active, its summary waiting
    state['pending_issues'] = [{'id': 'issue-2', 'description': 'Printing fails'}]
    check = _completion_check(state, '[]', {'queued', 'show_summary'})
    assert 'issue-2' in check(OperatorCompletion(action='queued', reason='Same', issue_id='does-not-exist'))
    assert 'issue-2' in check(OperatorCompletion(action='queued', reason='Same'))
    assert check(OperatorCompletion(action='queued', reason='Same', issue_id='issue-2')) is None
    assert 'issue-1' in check(OperatorCompletion(action='show_summary', reason='Same', issue_id='issue-9'))
    assert check(OperatorCompletion(action='show_summary', reason='Same', issue_id='issue-1')) is None


def test_interpreter_that_keeps_clarify_after_a_registration_is_overruled_by_the_tool_results(model, operator_model):
    from langchain_core.messages import HumanMessage
    from ole5_chat.agent.nodes.ticket_operator import ticket_operator_node
    state = ready_state()
    state.update(active_issue=None, pending_customer_response=None, ticket_draft=None, summary_shown=False,
                 submission_requested=False, messages=[HumanMessage(content='غيروا إيميلي')])
    operator_model.call('manage_issues', operation='register', description='Change the email')
    clarify = OperatorCompletion(action='clarify', reason='Unsure')
    model.add(clarify, clarify, clarify)  # refused every time; the last one is not kept
    result = ticket_operator_node(state)
    assert result['ticket_operator_result']['action'] == 'offer'
    assert result['pending_customer_response'].kind == 'offer'


def test_tool_agent_crash_before_any_tool_is_a_workflow_failure_and_keeps_the_pending_step(model, operator_model):
    from langchain_core.messages import HumanMessage
    from ole5_chat.agent.nodes.ticket_operator import ticket_operator_node
    state = detail_state()
    state['messages'] = [HumanMessage(content='صفحة الأرشيف')]
    operator_model.fail(RuntimeError('connection lost'))
    result = ticket_operator_node(state)
    assert result['ticket_operator_result']['action'] == 'workflow_failed'
    assert result['pending_customer_response'].kind == 'detail'
    assert result['pending_customer_response'].item == 'page'


def test_submit_button_with_no_submission_attempt_is_a_workflow_failure():
    state = ready_state()
    state['input_event'] = 'submit'
    check = _completion_check(state, '[]')
    assert 'submit_ticket was not called' in check(OperatorCompletion(action='show_summary', reason='Still waiting'))
    failed = OperatorCompletion(action='workflow_failed', reason='submit_ticket was not called')
    assert check(failed) is None
    result = _apply_completion(state, failed, '[]')
    assert result['pending_customer_response'].kind == 'summary' and result['summary_shown']


def test_workflow_failure_reaches_respond_as_its_own_stage_and_fallback():
    from ole5_chat.agent import ticket_action_guard
    from ole5_chat.agent.prompts.respond import STAGE_GUIDANCE
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    state = ready_state()
    state.update(pending_customer_response=PendingCustomerResponse(kind='offer', issue_id='issue-1'),
                 ticket_operator_result={'action': 'workflow_failed'}, language='en')
    sheet = build_turn_sheet(state, 'en')
    assert sheet['stage'] == 'workflow_failed' and sheet['pending_kind'] == 'offer'
    assert 'internal problem' in sheet['operator']
    assert 'do not ask them to clarify' in STAGE_GUIDANCE['workflow_failed']
    from ole5_chat.agent.prompts.respond import turn_note
    assert 'ticket offer waiting for a yes or no' in turn_note(sheet)  # the waiting step reaches Respond
    fallback = ticket_action_guard.fallback(state)
    assert fallback.startswith('Sorry, a problem on my side') and 'Would you like a support ticket' in fallback


def test_clarify_carries_its_question_to_respond_and_keeps_the_waiting_step():
    from ole5_chat.agent.prompts.respond import turn_note
    from ole5_chat.agent.turn_sheet import build_turn_sheet
    state = ready_state()  # issue-1 active, its summary waiting
    check = _completion_check(state, '[]')
    assert 'clarification_question' in check(OperatorCompletion(action='clarify', reason='Unreadable reply'))
    assert 'clarification_question' in check(
        OperatorCompletion(action='clarify', reason='Unreadable reply', clarification_question='  '))
    question = 'Do you want to change the translation ticket summary, or submit it as it is?'
    clarify = OperatorCompletion(action='clarify', reason='Unreadable reply', clarification_question=question)
    assert check(clarify) is None
    result = _apply_completion(state, clarify, '[]')
    assert result['ticket_operator_result']['clarification_question'] == question
    assert result['pending_customer_response'].kind == 'summary' and result['summary_shown']
    state.update(result)
    sheet = build_turn_sheet(state, 'en')
    assert sheet['stage'] == 'clarify' and sheet['clarification_question'] == question
    assert f'Question the ticket workflow needs answered: {question}' in turn_note(sheet)


def test_clarification_question_is_dropped_when_the_tool_results_replace_clarify():
    state = ready_state()
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    report = json.dumps([{'name': 'manage_issues', 'content': bound['manage_issues'].invoke(
        {'operation': 'register', 'description': 'Printing fails'})}])
    clarify = OperatorCompletion(action='clarify', reason='Unsure', clarification_question='Which one?')
    result = _apply_completion(state, clarify, report)
    assert result['ticket_operator_result']['action'] == 'queued'
    assert result['ticket_operator_result']['clarification_question'] is None
    offer = OperatorCompletion(action='show_summary', reason='Waiting', clarification_question='Which one?')
    assert _apply_completion(state, offer, '[]')['ticket_operator_result']['clarification_question'] is None


def test_cancel_with_nothing_active_is_not_a_success():
    state = detail_state()
    state['active_issue'] = None
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['manage_issues'].invoke({'operation': 'cancel_preparation'}))
    assert not result['success'] and 'nothing to cancel' in result['error']


def test_undelivered_revision_cannot_submit(monkeypatch):
    state = ready_state()
    state['draft_version'] = 2
    submit = Mock()
    monkeypatch.setattr(tools.ticket_submit, 'submit', submit)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 2}))
    assert not result['success']
    submit.assert_not_called()


def test_issue_registered_after_a_cancel_gets_a_new_id():
    state = initial_state(Identity(customer_id='test', email='ole5@example.com',
        name='Test', organization='Test', product='OLE5', language='ar'))
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    first = json.loads(bound['manage_issues'].invoke({'operation': 'register', 'description': 'Archive button'}))
    bound['manage_issues'].invoke({'operation': 'cancel_preparation', 'issue_id': first['issue']['id']})
    second = json.loads(bound['manage_issues'].invoke({'operation': 'register', 'description': 'Printing'}))
    assert first['issue']['id'] == 'issue-1'
    assert second['issue']['id'] == 'issue-2'
    assert state['issues_registered'] == 2


def test_classifier_reads_only_the_new_issues_part_of_the_chat():
    from langchain_core.messages import AIMessage, HumanMessage
    state = initial_state(Identity(customer_id='test', email='ole5@example.com',
        name='Test', organization='Test', product='OLE5', language='ar'))
    state['messages'] = [HumanMessage(content='زر الأرشفة ما يشتغل')]
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    bound['manage_issues'].invoke({'operation': 'register', 'description': 'Archive button'})
    state['messages'] = [*state['messages'], AIMessage(content='ما نص الخطأ؟'), HumanMessage(content='ERROR404'),
                         AIMessage(content='الملخص جاهز'), HumanMessage(content='لا الغه')]
    bound['manage_issues'].invoke({'operation': 'cancel_preparation', 'issue_id': 'issue-1'})
    state['messages'] = [*state['messages'], AIMessage(content='أوقفت التحضير.'),
                         HumanMessage(content='ولا موظف يقدر يسجل دخول')]
    second = json.loads(bound['manage_issues'].invoke({'operation': 'register', 'description': 'Nobody can sign in'}))
    assert second['issue']['messages_from'] == 4  # the cancel turn's message; the archive details stay out
    classified = tools._classification_state(state, TicketDraft(product='OLE5'))
    assert [m.content for m in classified['messages']] == ['لا الغه', 'أوقفت التحضير.', 'ولا موظف يقدر يسجل دخول']


def test_operator_receives_versions():
    prompt = ticket_operator_tool_prompt(ready_state())
    assert 'Current draft version: 1' in prompt
    assert 'Delivered summary version: 1' in prompt
