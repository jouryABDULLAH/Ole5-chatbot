"""Exercise submission recovery without writing tickets to a real service."""
import json
from unittest.mock import Mock

from ole5_chat.agent.contracts import OperatorCompletion, PendingCustomerResponse
from ole5_chat.agent.nodes.ticket_operator import _apply_completion
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


def test_skip_detail_settles_the_item_and_readies_the_summary():
    state = detail_state()
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['skip_detail'].invoke({'item': 'page', 'value': 'unknown'}))
    assert result['success'] and result['summary_ready'] and result['missing_customer_details'] == []
    assert state['ticket_draft']['required']['page'] == 'unknown'
    assert not json.loads(bound['skip_detail'].invoke({'item': 'colour', 'value': 'unknown'}))['success']


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


def test_undelivered_revision_cannot_submit(monkeypatch):
    state = ready_state()
    state['draft_version'] = 2
    submit = Mock()
    monkeypatch.setattr(tools.ticket_submit, 'submit', submit)
    bound = {tool.name: tool for tool in tools.build_ticket_operator_tools(state)}
    result = json.loads(bound['submit_ticket'].invoke({'issue_id': 'issue-1', 'draft_version': 2}))
    assert not result['success']
    submit.assert_not_called()


def test_operator_receives_versions():
    prompt = ticket_operator_tool_prompt(ready_state())
    assert 'Current draft version: 1' in prompt
    assert 'Delivered summary version: 1' in prompt
