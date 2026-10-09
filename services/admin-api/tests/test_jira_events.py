"""The scheduled Jira pull records an event per new or changed ticket; repeats and manual fetches record none."""
import copy
from uuid import uuid4

import httpx

from knowledge_admin.jira_events import changes
from knowledge_admin.jira_sync import auto_pull
from test_api import _client
from test_jira import ISSUES, setup
from test_repositories import HEADERS


def a_schedule(store, **params) -> dict:
    with store.connect() as conn:
        connection = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
    return {'id': uuid4(), 'project_id': connection['project_id'], 'target_kind': 'jira.pull',
            'target_id': connection['id'], 'params': {'jql': 'project = DEMO', 'limit': 3, **params}}


def jira(issues):
    def handler(request):
        if request.url.path.endswith('/myself'):
            return httpx.Response(200, json={'name': 'mock-admin', 'displayName': 'Mock', 'active': True})
        start, size = int(request.url.params['startAt']), int(request.url.params['maxResults'])
        return httpx.Response(200, json={'startAt': start, 'total': len(issues), 'issues': copy.deepcopy(issues[start:start + size])})
    return httpx.MockTransport(handler)


def events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT type,subject,title,payload,occurred_at FROM ka.events ORDER BY id').fetchall()


def test_first_pull_records_every_ticket_and_a_repeat_records_nothing(store, tmp_path):
    setup(store, tmp_path)
    issues = ISSUES[:3]
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == ('succeeded', 'Pulled 3 of 3 tickets · 3 new events.')
    recorded = events(store)
    assert [e['type'] for e in recorded] == ['jira.ticket.created'] * 3
    first = recorded[0]
    assert first['subject'] == issues[0]['key'] and first['title'] == issues[0]['fields']['summary']
    assert first['payload']['url'] == f"https://jira.internal/jira/browse/{issues[0]['key']}" and first['payload']['changes'] == {}
    assert first['payload']['status'] == issues[0]['fields']['status']['name']
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == ('succeeded', 'Pulled 3 of 3 tickets · 0 new events.')
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
    assert manual.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 2, 'jql': 'project = DEMO'}).status_code == 200
    assert len(events(store)) == 2  # the manual fetch records nothing
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == ('succeeded', 'Pulled 2 of 2 tickets · 1 new event.')
    update = events(store)[-1]
    assert update['type'] == 'jira.ticket.updated' and update['subject'] == issues[0]['key']
    before = ISSUES[0]['fields']
    assert update['payload']['changes'] == {'status': {'from': before['status']['name'], 'to': 'Done'},
                                            'labels': {'added': ['agent'], 'removed': [l for l in before['labels'] if l not in ('demo', 'agent')]}}


def test_the_scheduled_pull_keeps_the_manual_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    manual = _client(tmp_path, repository_store=store, jira_transport=jira(copy.deepcopy(ISSUES)))
    assert manual.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 5, 'jql': 'project = MANUAL'}).status_code == 200
    before = manual.get('/api/jira/tickets', headers=HEADERS).json()['connection']
    with store.connect() as conn:
        revision = conn.execute('SELECT fetch_revision FROM ka.jira_connections').fetchone()['fetch_revision']
    assert auto_pull(store, a_schedule(store, jql='project = AUTO', limit=7), transport=jira(copy.deepcopy(ISSUES)))[0] == 'succeeded'
    after = manual.get('/api/jira/tickets', headers=HEADERS).json()['connection']
    assert after['jql'] == 'project = MANUAL' and after['last_fetch_limit'] == 5
    assert after['last_fetch_total'] == len(ISSUES) and after['last_successful_sync_at'] >= before['last_successful_sync_at']
    with store.connect() as conn:
        assert conn.execute('SELECT fetch_revision FROM ka.jira_connections').fetchone()['fetch_revision'] == revision + 1


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
    before = {'summary': 'A', 'status': 'Open', 'issue_type': 'Task', 'priority': 'High', 'assignee': None, 'labels': ['x', 'y'], 'description': 'd'}
    after = {**before, 'summary': 'B', 'assignee': 'Ana', 'labels': ['y', 'z'], 'description': 'e'}
    assert changes(before, after) == {'summary': {'from': 'A', 'to': 'B'}, 'assignee': {'from': None, 'to': 'Ana'},
                                      'labels': {'added': ['z'], 'removed': ['x']}, 'description': {'changed': True}}
    assert changes(before, dict(before)) == {}
