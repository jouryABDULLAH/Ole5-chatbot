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
    result = _apply_completion(state, OperatorCompletion(action='submitted', reason='Created',
        response_instruction='Report TEST-1'), report)
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
    result = _apply_completion(state, OperatorCompletion(action='submitted', reason='Created',
        response_instruction='Report TEST-1'), json.dumps([{'name': 'submit_ticket', 'content': success}]))
    record = result['created_tickets'][-1]
    assert record['ticket_number'] == 'TEST-1'
    assert (record['title'], record['summary'], record['queue'], record['type'], record['subtype'],
            record['priority']) == ('Translation', 'Translation inquiry', 'Ole5 Support', 'Inquiry',
                                    'General questions', 'Low')
    assert record['attachments'] == ['screen.png']
    assert result['attachments'] == []


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
