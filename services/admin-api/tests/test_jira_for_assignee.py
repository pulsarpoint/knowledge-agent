"""`for_assignee`: an event is the assignee's work when the ticket is assigned and it is new, reassigned, or a person
commented. Our own replies (the loop protection's `ours`) never count."""

import pytest
from knowledge_admin.jira.events import changes, for_assignee, ours

ASSIGNED = {'assignee': 'Ops Bot', 'assignee_id': 'ops-bot', 'status': 'Open', 'comments': []}
UNASSIGNED = {**ASSIGNED, 'assignee': None, 'assignee_id': None}
REPLY = 'A\n\n— Agent (Knowledge Agent)'


def commented(ticket: dict, *comments: tuple[str, str, str]) -> dict:
    added = [{'id': id_, 'author_id': author, 'body': body} for id_, author, body in comments]
    return {**ticket, 'comments': ticket['comments'] + added}


def decide(before: dict | None, after: dict, own: set[str] = frozenset()) -> bool:
    return for_assignee(after, {} if before is None else changes(before, after), before is None, own)


def test_created_assigned_is_for_the_assignee():
    assert decide(None, ASSIGNED)


def test_created_unassigned_is_not():
    assert not decide(None, UNASSIGNED)


def test_reassigned_is_for_the_new_assignee():
    after = {**ASSIGNED, 'assignee': 'Docs Bot', 'assignee_id': 'docs-bot'}
    assert changes(ASSIGNED, after)['assignee'] == {
        'from': 'Ops Bot',
        'to': 'Docs Bot',
        'from_id': 'ops-bot',
        'to_id': 'docs-bot',
    }
    assert decide(ASSIGNED, after)
    assert decide(UNASSIGNED, ASSIGNED)


def test_now_unassigned_is_not():
    assert not decide(ASSIGNED, UNASSIGNED)


def test_a_new_display_name_alone_is_not_a_reassignment():
    after = {**ASSIGNED, 'assignee': 'Operations Bot'}
    assert changes(ASSIGNED, after)['assignee']['from_id'] == 'ops-bot'
    assert not decide(ASSIGNED, after)


def test_a_person_comment_on_an_assigned_ticket_is_for_the_assignee():
    assert decide(ASSIGNED, commented(ASSIGNED, ('501', 'ana', 'Can you look?')))
    assert not decide(UNASSIGNED, commented(UNASSIGNED, ('501', 'ana', 'Can you look?')))


def test_our_own_reply_is_not():
    assert not decide(ASSIGNED, commented(ASSIGNED, ('501', 'mock-admin', REPLY)), own={'501'})


def test_a_person_commenting_with_our_reply_is_for_the_assignee():
    after = commented(ASSIGNED, ('501', 'mock-admin', REPLY), ('502', 'ana', 'Why?'))
    assert decide(ASSIGNED, after, own={'501'})


def test_a_status_only_change_is_not():
    assert not decide(ASSIGNED, {**ASSIGNED, 'status': 'Done'})


def test_reassigned_plus_our_reply_is_for_the_new_assignee():
    after = commented({**ASSIGNED, 'assignee': 'Docs Bot', 'assignee_id': 'docs-bot'}, ('501', 'mock-admin', REPLY))
    assert decide(ASSIGNED, after, own={'501'})


@pytest.mark.parametrize(
    ('comment', 'expected'),
    [
        ({'id': '501', 'author_id': 'ana', 'body': 'Hi'}, True),  # a posted reply's id
        ({'id': '502', 'author_id': 'Mock-Admin', 'body': REPLY}, True),  # pending: the connection user, any case
        ({'id': '502', 'author_id': 'ana', 'body': REPLY}, False),  # pending text by someone else
        ({'id': '502', 'author_id': 'mock-admin', 'body': 'Other'}, False),  # the connection user, other text
        ({'id': '502', 'author_id': None, 'body': REPLY}, False),
    ],
)
def test_ours_is_a_posted_id_or_a_pending_reply_by_the_connection_user(comment, expected):
    assert ours(comment, posted={'501'}, pending={REPLY}, username='mock-admin') is expected
