from ole5_chat.support_core.detail_display import align_details
from ole5_chat.support_core.state import TicketDraft
from ole5_chat.agent.tools.ticket_operator import _confirmed_changes


LABELS = {'page': {'ar': 'اسم الصفحة', 'en': 'the page name'}}


def test_saved_display_survives_unrelated_edit_and_partial_translations():
    old = TicketDraft(product='Ole5', required={'page': 'Wall page'},
                      required_customer={'page': 'صفحة الجدار'})
    new = TicketDraft(product='Ole5', required={'page': 'Wall page', 'error text or screenshot': 'ERROR_load'},
                      collected_customer={'page': 'Wall page'}, collected={'browser': 'Chrome'})
    aligned = align_details(new, old, {'error text or screenshot': 'ERROR_load'}, LABELS)
    assert aligned.required_customer == {'page': 'صفحة الجدار', 'error text or screenshot': 'ERROR_load'}
    assert aligned.collected_customer['page'] == 'صفحة الجدار'
    assert aligned.collected['browser'] == 'Chrome'
    assert _confirmed_changes(old, aligned) == [
        {'field': 'required.error text or screenshot', 'before': None, 'after': 'ERROR_load'}]


def test_explicit_page_edit_uses_customer_value_in_both_views_and_acknowledgement():
    old = TicketDraft(product='Ole5', required={'page': 'Wall page'}, required_customer={'page': 'صفحة الجدار'})
    new = TicketDraft(product='Ole5', required={'page': 'Inbox'},
                      required_customer={'page': 'Inbox'}, collected_customer={'اسم الصفحة': 'صفحة الجدار'})
    aligned = align_details(new, old, {'page': 'صفحة الوارد'}, LABELS)
    assert aligned.collected == {'page': 'Inbox'}
    assert aligned.collected_customer == aligned.required_customer == {'page': 'صفحة الوارد'}
    assert _confirmed_changes(old, aligned) == [
        {'field': 'required.page', 'before': 'صفحة الجدار', 'after': 'صفحة الوارد'}]


def test_cleared_and_skipped_values_do_not_remain_in_collected_rows():
    for value in (None, 'unknown', 'declined'):
        draft = TicketDraft(product='Ole5', required={'page': value}, required_customer={'page': 'صفحة الجدار'},
                            collected={'page': 'Wall page'}, collected_customer={'اسم الصفحة': 'صفحة الجدار'})
        aligned = align_details(draft, labels=LABELS)
        assert not aligned.required_customer and not aligned.collected and not aligned.collected_customer


def test_api_keeps_display_values_on_draft_and_submitted_card():
    from ole5_chat import api
    from ole5_chat.agent.state import initial_state
    from ole5_chat.identity import Identity
    state = initial_state(Identity(customer_id='t', email='t@example.com', name='T', organization='T',
                                   product='OLE5', language='ar'))
    draft = TicketDraft(product='Ole5', required={'page': 'Inbox', 'error text or screenshot': 'E403'},
                        required_customer={'page': 'صفحة الوارد'})
    state['ticket_draft'] = draft.model_dump()
    state['created_tickets'] = [{'ticket_number': 'T-1', 'required': draft.required,
                                'required_customer': draft.required_customer, 'message_index': 0}]
    view = api._view('test', state)
    assert view.detail_labels['page'] == 'اسم الصفحة'
    assert view.ticket_draft.required_customer == view.created_tickets[0].required_customer == {
        'page': 'صفحة الوارد', 'error text or screenshot': 'E403'}
