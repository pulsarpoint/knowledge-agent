"""The scheduled Jira pull records an event per new or changed ticket; repeats and manual fetches record none."""

import copy
from uuid import uuid4

import httpx
import pytest
from knowledge_admin.jira.events import changes
from knowledge_admin.jira.sync import auto_pull
from test_api import _client
from test_jira import ISSUES, setup
from test_repositories import HEADERS


def a_schedule(store, **params) -> dict:
    with store.connect() as conn:
        connection = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
    return {
        'id': uuid4(),
        'project_id': connection['project_id'],
        'target_kind': 'jira.pull',
        'target_id': connection['id'],
        'params': {'jql': 'project = DEMO', 'limit': 3, **params},
    }


def jira(issues):
    def handler(request):
        if request.url.path.endswith('/myself'):
            return httpx.Response(200, json={'name': 'mock-admin', 'displayName': 'Mock', 'active': True})
        start, size = int(request.url.params['startAt']), int(request.url.params['maxResults'])
        return httpx.Response(
            200, json={'startAt': start, 'total': len(issues), 'issues': copy.deepcopy(issues[start : start + size])}
        )

    return httpx.MockTransport(handler)


def events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT type,subject,title,payload,occurred_at FROM ka.events ORDER BY id').fetchall()


def test_first_pull_records_every_ticket_and_a_repeat_records_nothing(store, tmp_path):
    setup(store, tmp_path)
    issues = ISSUES[:3]
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == (
        'succeeded',
        'Pulled 3 of 3 tickets · 3 new events.',
    )
    recorded = events(store)
    assert [e['type'] for e in recorded] == ['jira.ticket.created'] * 3
    first = recorded[0]
    assert first['subject'] == issues[0]['key'] and first['title'] == issues[0]['fields']['summary']
    assert (
        first['payload']['url'] == f'https://jira.internal/jira/browse/{issues[0]["key"]}'
        and first['payload']['changes'] == {}
    )
    assert first['payload']['status'] == issues[0]['fields']['status']['name']
    assignee = issues[0]['fields']['assignee']
    assert (first['payload']['assignee'], first['payload']['assignee_id']) == (assignee['displayName'], assignee['key'])
    assert first['payload']['for_assignee'] is True
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == (
        'succeeded',
        'Pulled 3 of 3 tickets · 0 new events.',
    )
    assert len(events(store)) == 3


def test_a_changed_ticket_records_what_changed_even_after_a_manual_fetch(store, tmp_path):
    client, _ = setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    auto_pull(store, a_schedule(store), transport=jira(issues))
    changed = issues[0]['fields']
    changed['status'] = {'id': '3', 'name': 'Done'}
    changed['labels'] = ['demo', 'agent']
    changed['updated'] = '2026-10-09T10:00:00.000+0000'
    # The manual fetch updates the tickets table first; the scheduled pull must still notice the change.
    manual = _client(tmp_path, repository_store=store, jira_transport=jira(issues))
    assert (
        manual.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 2, 'jql': 'project = DEMO'}).status_code
        == 200
    )
    assert len(events(store)) == 2  # the manual fetch records nothing
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == (
        'succeeded',
        'Pulled 2 of 2 tickets · 1 new event.',
    )
    update = events(store)[-1]
    assert update['type'] == 'jira.ticket.updated' and update['subject'] == issues[0]['key']
    before = ISSUES[0]['fields']
    assert update['payload']['changes'] == {
        'status': {'from': before['status']['name'], 'to': 'Done'},
        'labels': {'added': ['agent'], 'removed': [l for l in before['labels'] if l not in ('demo', 'agent')]},
    }


def test_the_scheduled_pull_keeps_the_manual_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    manual = _client(tmp_path, repository_store=store, jira_transport=jira(copy.deepcopy(ISSUES)))
    assert (
        manual.post(
            '/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 5, 'jql': 'project = MANUAL'}
        ).status_code
        == 200
    )
    before = manual.get('/api/jira/tickets', headers=HEADERS).json()['connection']
    with store.connect() as conn:
        revision = conn.execute('SELECT fetch_revision FROM ka.jira_connections').fetchone()['fetch_revision']
    assert (
        auto_pull(store, a_schedule(store, jql='project = AUTO', limit=7), transport=jira(copy.deepcopy(ISSUES)))[0]
        == 'succeeded'
    )
    after = manual.get('/api/jira/tickets', headers=HEADERS).json()['connection']
    assert after['jql'] == 'project = MANUAL' and after['last_fetch_limit'] == 5
    assert (
        after['last_fetch_total'] == len(ISSUES)
        and after['last_successful_sync_at'] >= before['last_successful_sync_at']
    )
    with store.connect() as conn:
        assert (
            conn.execute('SELECT fetch_revision FROM ka.jira_connections').fetchone()['fetch_revision'] == revision + 1
        )


def test_a_clashing_pull_records_no_events(store, tmp_path):
    setup(store, tmp_path)
    inner = jira(ISSUES[:2])

    def clashing(request):
        if request.url.path.endswith('/search'):
            with store.connect() as conn:
                conn.execute('UPDATE ka.jira_connections SET fetch_revision=fetch_revision+1')
        return inner.handle_request(request)

    assert auto_pull(store, a_schedule(store), transport=httpx.MockTransport(clashing))[0] == 'skipped'
    assert events(store) == []


def test_changes():
    before = {
        'summary': 'A',
        'status': 'Open',
        'issue_type': 'Task',
        'priority': 'High',
        'assignee': None,
        'labels': ['x', 'y'],
        'description': 'd',
    }
    after = {**before, 'summary': 'B', 'assignee': 'Ana', 'labels': ['y', 'z'], 'description': 'e'}
    assert changes(before, after) == {
        'summary': {'from': 'A', 'to': 'B'},
        'assignee': {'from': None, 'to': 'Ana', 'from_id': None, 'to_id': None},
        'labels': {'added': ['z'], 'removed': ['x']},
        'description': {'changed': True},
    }
    assert changes(before, dict(before)) == {}


def test_hand_made_jira_events_never_change_what_a_pull_records(store, tmp_path):
    client, _ = setup(store, tmp_path)
    issues = ISSUES[:1]
    key = issues[0]['key']
    made = client.post(
        '/api/events',
        headers=HEADERS,
        json={
            'type': 'jira.ticket.updated',
            'subject': key,
            'source': 'jira',
            'payload': {'issue_id': issues[0]['id'], 'summary': 'Made up', 'status': 'Done'},
        },
    )
    assert made.status_code == 201, made.text
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == (
        'succeeded',
        'Pulled 1 of 1 tickets · 1 new event.',
    )
    assert [e['type'] for e in events(store)] == ['jira.ticket.updated', 'jira.ticket.created']


def posted_comment(store, issue, comment_id: str) -> None:
    with store.connect() as conn:
        jira = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
        conn.execute(
            """INSERT INTO ka.notifications(project_id,kind,status,title,body,jira_status,jira_connection_id,
            jira_issue_id,jira_issue_key,jira_comment_id,jira_comment_created)
            VALUES (%s,'agent.response','succeeded','T','A','posted',%s,%s,%s,%s,now())""",
            (jira['project_id'], jira['id'], issue['id'], issue['key'], comment_id),
        )


def pending_reply(store, issue) -> None:
    """A reply being posted: its answer is 'A' and it has no agent, so the comment text is OURS."""
    with store.connect() as conn:
        jira = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
        conn.execute(
            """INSERT INTO ka.notifications(project_id,kind,status,title,body,jira_status,jira_connection_id,
            jira_issue_id,jira_issue_key,jira_next_attempt_at)
            VALUES (%s,'agent.response','succeeded','T','A','pending',%s,%s,%s,now())""",
            (jira['project_id'], jira['id'], issue['id'], issue['key']),
        )


OURS = 'A\n\n— Agent (Knowledge Agent)'


def commented(issue, *comments: tuple[str, str, str]) -> None:
    """Add (id, author name, body) comments; the ticket's updated time moves to the last one, as in Jira."""
    rows = issue['fields'].setdefault('comment', {'startAt': 0, 'maxResults': 0, 'total': 0, 'comments': []})
    for comment_id, author, body in comments:
        created = f'2026-10-15T10:00:{len(rows["comments"]):02d}.000+0000'
        rows['comments'].append(
            {
                'id': comment_id,
                'author': {'name': author, 'displayName': author.title()},
                'body': body,
                'created': created,
                'updated': created,
            }
        )
        issue['fields']['updated'] = created
    rows['total'] = rows['maxResults'] = len(rows['comments'])


def pulled_twice(store, tmp_path, change) -> tuple[str, list[dict]]:
    """Pull two tickets, apply `change(store, issues)`, pull again: the second pull's summary and all events."""
    setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    commented(issues[0], ('500', 'jira-user', 'An old comment'))
    auto_pull(store, a_schedule(store), transport=jira(issues))
    change(store, issues)
    return auto_pull(store, a_schedule(store), transport=jira(issues))[1], events(store)


def test_events_carry_the_comments_and_which_are_new(store, tmp_path):
    def change(_, issues):
        commented(issues[0], ('501', 'jira-user', 'Can someone look?'))

    summary, recorded = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')
    created, updated = recorded[0]['payload'], recorded[-1]['payload']
    assert created['comments'] == [
        {
            'id': '500',
            'author': 'Jira-User',
            'author_id': 'jira-user',
            'created': '2026-10-15T10:00:00.000+0000',
            'body': 'An old comment',
        }
    ]
    assert (created['comment_count'], updated['comment_count']) == (1, 2)
    assert updated['changes'] == {'comments': {'added': ['501']}}
    assert recorded[1]['payload']['comments'] == []  # a ticket with no comments


def test_an_update_that_is_only_our_posted_comment_records_no_event(store, tmp_path):
    def change(store, issues):
        commented(issues[0], ('501', 'mock-admin', OURS))
        posted_comment(store, issues[0], '501')

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('0 new events.')


def test_a_person_commenting_in_the_same_second_as_us_is_recorded(store, tmp_path):
    def change(store, issues):
        commented(issues[0], ('501', 'mock-admin', OURS), ('502', 'jira-user', 'Thanks, but why?'))
        issues[0]['fields']['comment']['comments'][-1]['created'] = '2026-10-15T10:00:01.000+0000'
        posted_comment(store, issues[0], '501')

    summary, recorded = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')
    assert recorded[-1]['payload']['changes'] == {'comments': {'added': ['501', '502']}}


def test_a_field_change_with_our_comment_is_recorded(store, tmp_path):
    def change(store, issues):
        commented(issues[0], ('501', 'mock-admin', OURS))
        issues[0]['fields']['status'] = {'id': '3', 'name': 'Done'}
        posted_comment(store, issues[0], '501')

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')


def test_our_comment_id_on_another_ticket_does_not_hide_an_update(store, tmp_path):
    def change(store, issues):
        commented(issues[0], ('501', 'jira-user', 'A question'))
        posted_comment(store, issues[1], '501')

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')


def test_our_comment_still_being_posted_is_matched_by_author_and_text(store, tmp_path):
    def change(store, issues):
        commented(issues[0], ('501', 'Mock-Admin', OURS))  # the connection user, any case
        pending_reply(store, issues[0])

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('0 new events.')


@pytest.mark.parametrize(
    ('author', 'body'), [('jira-user', OURS), ('mock-admin', 'Something else'), ('jira-user', 'A question')]
)
def test_while_our_reply_is_being_posted_other_comments_are_recorded(store, tmp_path, author, body):
    def change(store, issues):
        commented(issues[0], ('501', author, body))
        pending_reply(store, issues[0])

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')


def test_an_update_without_new_comments_is_recorded(store, tmp_path):
    def change(_, issues):
        issues[0]['fields']['updated'] = '2026-10-15T11:00:00.000+0000'  # e.g. a field we do not compare

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')


def test_an_event_recorded_before_comments_were_carried_does_not_hide_our_comment(store, tmp_path):
    def change(store, issues):
        with store.connect() as conn:
            conn.execute("UPDATE ka.events SET payload = payload - 'comments'")
        commented(issues[0], ('501', 'mock-admin', OURS))
        posted_comment(store, issues[0], '501')

    summary, _ = pulled_twice(store, tmp_path, change)
    assert summary.endswith('1 new event.')


def test_our_reply_with_a_field_change_is_not_for_the_assignee_but_a_persons_comment_is(store, tmp_path):
    def change(store, issues):
        commented(issues[0], ('501', 'mock-admin', OURS))
        issues[0]['fields']['status'] = {'id': '3', 'name': 'Done'}
        posted_comment(store, issues[0], '501')
        commented(issues[1], ('601', 'ana', 'Can you look?'))

    _, recorded = pulled_twice(store, tmp_path, change)
    by_key = {event['subject']: event['payload'] for event in recorded[2:]}
    assert by_key['DEMO-1']['for_assignee'] is False
    assert by_key['DEMO-2']['for_assignee'] is True


def test_a_reassignment_carries_both_keys(store, tmp_path):
    def change(_, issues):
        issues[0]['fields']['assignee'] = {'key': 'ops-bot', 'name': 'ops-bot', 'displayName': 'Ops Bot'}
        issues[0]['fields']['updated'] = '2026-10-15T11:00:00.000+0000'

    _, recorded = pulled_twice(store, tmp_path, change)
    payload = recorded[-1]['payload']
    assert payload['changes']['assignee'] == {
        'from': 'Mock Administrator',
        'to': 'Ops Bot',
        'from_id': 'mock-admin',
        'to_id': 'ops-bot',
    }
    assert (payload['assignee_id'], payload['for_assignee']) == ('ops-bot', True)
