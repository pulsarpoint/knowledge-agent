"""Hand-back (0119): after the agent's reply is posted, the ticket is assigned back to whoever gave it to the agent —
the pure target, the column, the API, the notification, the reply worker and the whole flow against the Jira mock."""

import json
import urllib.request
from datetime import timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.events import jira_replies, processor as event_processor
from knowledge_admin.events.jira_replies import NOT_POSTED, comment_body
from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.jira.events import give_back_target, own_comment_ids, ticket
from knowledge_admin.sessions.text import notify_event_run
from test_event_handlers_api import EMIT, error
from test_event_processor import a_handler
from test_handler_assignee_target import ASSIGNEE, jira_mock  # noqa: F401  (fixture)
from test_jira import ISSUES, setup
from test_jira_replies import connection, note, pending
from test_jira_reply_notifications import notified, prepared
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
ANA = {'give_back_key': 'JIRAUSER10100', 'give_back_name': 'Ana Petrović', 'give_back_status': 'pending'}


# The pure target


def reassigned(from_id, from_name, to_id='ops-bot', **payload) -> dict:
    change = {'from': from_name, 'to': 'Ops Bot', 'from_id': from_id, 'to_id': to_id}
    return {'assignee_id': to_id, 'changes': {'assignee': change}, **payload}


def test_reassigned_to_the_bot_goes_back_to_the_previous_assignee():
    payload = reassigned('ana', 'Ana Petrović', reporter_id='marko', reporter='Marko Ilić')
    assert give_back_target(payload) == ('ana', 'Ana Petrović')


def test_created_assigned_goes_back_to_the_reporter():
    payload = {'assignee_id': 'ops-bot', 'changes': {}, 'reporter_id': 'ana', 'reporter': 'Ana Petrović'}
    assert give_back_target(payload) == ('ana', 'Ana Petrović')


def test_assigned_from_unassigned_goes_back_to_the_reporter():
    payload = reassigned(None, None, reporter_id='ana', reporter='Ana Petrović')
    assert give_back_target(payload) == ('ana', 'Ana Petrović')


def test_neither_gives_no_target():
    assert give_back_target({'assignee_id': 'ops-bot', 'changes': {}}) is None
    assert give_back_target({}) is None


def test_the_bot_itself_is_never_the_target():
    # A display-name change records an assignee change with the same key; a bot that reported the ticket.
    payload = reassigned('ops-bot', 'Ops Bot (old)', reporter_id='ops-bot', reporter='Ops Bot')
    assert give_back_target(payload) is None
    assert give_back_target({**payload, 'reporter_id': 'ana', 'reporter': 'Ana'}) == ('ana', 'Ana')


def test_a_name_falls_back_to_the_key():
    assert give_back_target({'assignee_id': 'ops-bot', 'reporter_id': 'ana', 'reporter': None}) == ('ana', 'ana')


def test_the_payload_carries_the_reporter_key():
    issue = {**ISSUES[0], 'fields': {**ISSUES[0]['fields'], 'reporter': {'key': 'ana', 'displayName': 'Ana'}}}
    payload = ticket('https://jira.test', issue)
    assert (payload['reporter'], payload['reporter_id']) == ('Ana', 'ana')
    assert (
        ticket('https://jira.test', {**issue, 'fields': {**issue['fields'], 'reporter': None}})['reporter_id'] is None
    )


# The column


def test_give_back_defaults_off_and_needs_reply_to_jira(store):
    assert a_handler(store)['give_back'] is False
    on = a_handler(
        store, name='A', action='agent', emit=None, agent_id='x', prompt='p', reply_to_jira=True, give_back=True
    )
    assert on['give_back'] is True
    with pytest.raises(psycopg.errors.CheckViolation):
        a_handler(store, name='B', action='agent', emit=None, agent_id='x', prompt='p', give_back=True)


def test_down_and_up_again(store):
    with store.connect() as conn:
        conn.execute((MIGRATIONS / '0119_jira_give_back.down.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0119_jira_give_back.up.sql').read_text(), prepare=False)
        assert (
            conn.execute(
                """SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='notifications' AND column_name LIKE 'give_back%%'"""
            ).fetchone()['n']
            == 6
        )


# The API

AGENT = {**EMIT, 'name': 'Answer on Jira', 'action': 'agent', 'emit': None, 'prompt': 'Triage {{ event.subject }}'}


def test_give_back_is_saved_and_returned(tester):
    client = tester.client
    draft = {**AGENT, 'agent_id': tester.agent, 'reply_to_jira': True, 'give_back': True}
    response = client.post('/api/event-handlers', headers=HEADERS, json=draft)
    assert response.status_code == 201, response.text
    handler = response.json()['handler']
    assert handler['give_back'] is True
    saved = client.put(
        f'/api/event-handlers/{handler["id"]}', headers=HEADERS, json={**draft, 'give_back': False, 'revision': 1}
    ).json()['handler']
    assert saved['give_back'] is False
    with tester.store.connect() as conn:
        audits = conn.execute(
            "SELECT details FROM ka.audit_events WHERE action LIKE 'event_handler.%%' ORDER BY created_at"
        ).fetchall()
    assert [row['details']['give_back'] for row in audits] == [True, False]


def test_give_back_defaults_off(tester):
    draft = {**AGENT, 'agent_id': tester.agent, 'reply_to_jira': True}
    assert (
        tester.client.post('/api/event-handlers', headers=HEADERS, json=draft).json()['handler']['give_back'] is False
    )


def test_give_back_is_refused_without_reply_to_jira(tester):
    draft = {**AGENT, 'agent_id': tester.agent, 'give_back': True}
    response = tester.client.post('/api/event-handlers', headers=HEADERS, json=draft)
    assert error(response, 400) == 'Turn on Reply in Jira to give the ticket back.'


# The notification


def prepared_with(sessions, give_back=True, payload=None):
    sid, event = prepared(sessions)
    with sessions.store.connect() as conn:
        handler = conn.execute('SELECT handler_id FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()['handler_id']
        conn.execute('UPDATE ka.event_handlers SET give_back=%s WHERE id=%s', (give_back, handler))
        conn.execute(
            'UPDATE ka.events SET payload=payload || %s WHERE id=%s',
            (
                Jsonb(payload or {'assignee_id': 'ops-bot', 'reporter_id': 'ana', 'reporter': 'Ana Petrović'}),
                event['id'],
            ),
        )
    return sid


def test_the_target_is_stored_when_the_handler_gives_back(sessions):
    row = notified(sessions, prepared_with(sessions))
    assert (row['jira_status'], row['give_back_key'], row['give_back_name'], row['give_back_status']) == (
        'pending',
        'ana',
        'Ana Petrović',
        'pending',
    )
    assert row['give_back_next_attempt_at'] is None  # due once the comment is posted


@pytest.mark.parametrize('give_back, payload', [(False, None), (True, {'assignee_id': 'ops-bot'})])
def test_no_target_is_stored(sessions, give_back, payload):
    row = notified(sessions, prepared_with(sessions, give_back, payload))
    assert (row['jira_status'], row['give_back_key'], row['give_back_status']) == ('pending', None, None)


def test_no_target_when_nothing_is_posted(sessions):
    row = notified(sessions, prepared_with(sessions), text='  ')
    assert (row['jira_status'], row['give_back_key'], row['give_back_status']) == (None, None, None)


# The reply worker


def jira(requests, comment=201, lookup=200, assign=204):
    """A Jira for issue 10001: answers the comment POST, the user lookup and the assignment with these statuses."""

    def handler(request):
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, dict(request.url.params), body))
        if request.method == 'POST':
            if comment != 201:
                return httpx.Response(comment)
            return httpx.Response(201, json={'id': '10500', 'created': '2026-10-15T10:00:01.000+0000'})
        if request.url.path.endswith('/user'):
            if lookup != 200:
                return httpx.Response(lookup)
            return httpx.Response(200, json={'key': 'JIRAUSER10100', 'name': 'ana.p', 'displayName': 'Ana Petrović'})
        if request.method == 'PUT':
            return httpx.Response(assign)
        return httpx.Response(200, json={'startAt': 0, 'total': 0, 'comments': []})

    return httpx.MockTransport(handler)


def methods(requests) -> list[str]:
    return [method for method, *_ in requests]


def due(store, notification_id) -> None:
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.notifications SET give_back_next_attempt_at=now()-interval '1 second'
            WHERE id=%s AND give_back_next_attempt_at IS NOT NULL""",
            (notification_id,),
        )


def test_the_comment_note():
    assert comment_body('Hi', 'Triage', 'Ana') == 'Hi\n\nAssigned back to Ana.\n\n— Triage (Knowledge Agent)'
    assert comment_body('Hi', 'Triage', None) == 'Hi\n\n— Triage (Knowledge Agent)'
    long = comment_body('x' * 40_000, 'Triage', 'Ana')
    assert len(long) <= jira_replies.MAX_COMMENT and long.endswith(
        '\n\nAssigned back to Ana.\n\n— Triage (Knowledge Agent)'
    )


def test_comment_then_assign_by_the_looked_up_login(store, tmp_path):
    setup(store, tmp_path)
    nid, requests = pending(store, **ANA), []
    assert jira_replies.run_once(store, jira(requests)) is True
    row = note(store, nid)
    assert (row['jira_status'], row['give_back_status'], row['give_back_attempts']) == ('posted', 'pending', 0)
    assert row['give_back_next_attempt_at'] is not None
    [(_, _, _, posted)] = requests
    assert posted['body'] == 'The answer\n\nAssigned back to Ana Petrović.\n\n— triage (Knowledge Agent)'
    requests.clear()
    assert jira_replies.run_once(store, jira(requests)) is True
    assert requests == [
        ('GET', '/jira/rest/api/2/user', {'key': 'JIRAUSER10100'}, None),
        ('PUT', '/jira/rest/api/2/issue/10001/assignee', {}, {'name': 'ana.p'}),
    ]
    row = note(store, nid)
    assert (row['give_back_status'], row['give_back_attempts'], row['give_back_error']) == ('done', 1, None)
    assert row['give_back_next_attempt_at'] is None and row['jira_attempts'] == 1
    assert jira_replies.run_once(store, jira(requests)) is False


def test_a_failed_comment_never_assigns(store, tmp_path):
    setup(store, tmp_path)
    nid, requests = pending(store, **ANA), []
    jira_replies.run_once(store, jira(requests, comment=403))
    row = note(store, nid)
    assert (row['jira_status'], row['give_back_status'], row['give_back_error']) == ('failed', 'failed', NOT_POSTED)
    assert NOT_POSTED == 'Not assigned back: the comment was not posted.'
    assert jira_replies.run_once(store, jira(requests)) is False
    assert methods(requests) == ['POST']


def test_a_comment_being_retried_does_not_assign(store, tmp_path):
    setup(store, tmp_path)
    nid, requests = pending(store, **ANA), []
    jira_replies.run_once(store, jira(requests, comment=500))
    row = note(store, nid)
    assert (row['jira_status'], row['give_back_status'], row['give_back_next_attempt_at']) == (
        'pending',
        'pending',
        None,
    )
    assert jira_replies.run_once(store, jira(requests)) is False  # the comment is not due yet; nothing to assign
    assert methods(requests) == ['POST']


@pytest.mark.parametrize('failing', [{'assign': 500}, {'lookup': 500}])
def test_a_failed_assignment_is_retried_without_a_second_comment(store, tmp_path, failing):
    setup(store, tmp_path)
    nid, requests = pending(store, **ANA), []
    jira_replies.run_once(store, jira(requests))
    for attempt, wait in ((1, timedelta(minutes=1)), (2, timedelta(minutes=5))):
        assert jira_replies.run_once(store, jira(requests, **failing)) is True
        row = note(store, nid)
        assert (row['give_back_status'], row['give_back_attempts']) == ('pending', attempt)
        assert row['give_back_error'].startswith('Jira could not complete the request')
        with store.connect() as conn:
            left = conn.execute(
                'SELECT give_back_next_attempt_at-now() AS wait FROM ka.notifications WHERE id=%s', (nid,)
            ).fetchone()['wait']
        assert wait - timedelta(seconds=5) < left <= wait
        assert jira_replies.run_once(store, jira(requests)) is False  # not due yet
        due(store, nid)
    jira_replies.run_once(store, jira(requests, **failing))
    row = note(store, nid)
    assert (row['give_back_status'], row['give_back_attempts'], row['give_back_next_attempt_at']) == ('failed', 3, None)
    assert (row['jira_status'], row['jira_comment_id']) == ('posted', '10500')
    assert methods(requests).count('POST') == 1


def test_a_retried_assignment_can_succeed(store, tmp_path):
    setup(store, tmp_path)
    nid, requests = pending(store, **ANA), []
    jira_replies.run_once(store, jira(requests))
    jira_replies.run_once(store, jira(requests, assign=500))
    due(store, nid)
    jira_replies.run_once(store, jira(requests))
    row = note(store, nid)
    assert (row['give_back_status'], row['give_back_attempts'], row['give_back_error']) == ('done', 2, None)
    assert methods(requests).count('POST') == 1


def test_a_refused_assignment_fails_at_once(store, tmp_path):
    setup(store, tmp_path)
    nid, requests = pending(store, **ANA), []
    jira_replies.run_once(store, jira(requests))
    jira_replies.run_once(store, jira(requests, assign=400))
    row = note(store, nid)
    assert (row['give_back_status'], row['give_back_attempts'], row['give_back_error']) == (
        'failed',
        1,
        'Jira refused the assignment',
    )


def test_without_a_target_nothing_is_assigned(store, tmp_path):
    setup(store, tmp_path)
    nid, requests = pending(store), []
    jira_replies.run_once(store, jira(requests))
    row = note(store, nid)
    assert (row['jira_status'], row['give_back_status'], row['give_back_next_attempt_at']) == ('posted', None, None)
    assert requests[0][3]['body'] == 'The answer\n\n— triage (Knowledge Agent)'
    assert jira_replies.run_once(store, jira(requests)) is False
    assert methods(requests) == ['POST']


def test_a_pending_reply_with_the_note_is_recognised_as_ours(store, tmp_path):
    setup(store, tmp_path)
    pending(store, **ANA)
    jira_row = connection(store)
    body = 'The answer\n\nAssigned back to Ana Petrović.\n\n— triage (Knowledge Agent)'
    new = [{'id': '1', 'author_id': 'mock-admin', 'body': body}, {'id': '2', 'author_id': 'mock-admin', 'body': 'x'}]
    with store.connect() as conn:
        assert own_comment_ids(conn, jira_row['project_id'], jira_row['id'], 'mock-admin', '10001', new) == {'1'}


def test_the_notification_api_shows_the_hand_back(store, tmp_path):
    client, _ = setup(store, tmp_path)
    nid = pending(store, **ANA, give_back_error='Jira could not complete the request')
    listed = client.get('/api/notifications', headers=HEADERS).json()['notifications'][0]
    assert listed['give_back_status'] == 'pending'
    detail = client.get(f'/api/notifications/{nid}', headers=HEADERS).json()['notification']
    assert (detail['give_back_name'], detail['give_back_error']) == (
        'Ana Petrović',
        'Jira could not complete the request',
    )


# The whole flow against the Jira mock


def test_the_ticket_goes_back_to_the_reporter_and_the_hand_back_starts_nothing(tester, jira_mock):
    t = tester
    base, control = jira_mock
    settings = {'base_url': f'{base}/jira', 'username': 'mock-admin', 'token': 'jira-mock-token'}
    assert t.client.put('/api/jira/settings', headers=HEADERS, json=settings).status_code == 200
    mapped = t.client.put(
        '/api/jira/user-agents/ops-bot', headers=HEADERS, json={'name': 'Ops Bot', 'agent_id': t.agent}
    )
    assert mapped.status_code == 200, mapped.text
    handler = {**ASSIGNEE, 'reply_to_jira': True, 'give_back': True}
    assert t.client.post('/api/event-handlers', headers=HEADERS, json=handler).status_code == 201
    control('/mock/tickets', {'summary': 'Help with the payment API', 'assignee': 'ops-bot', 'reporter': 'ana'})
    with t.store.connect() as conn:
        project = t.store.project(conn, 'shop')

    def state() -> dict:
        with urllib.request.urlopen(f'{base}/mock/state') as answer:
            return json.loads(answer.read())

    issue = state()['issues'][-1]
    key = issue['key']
    assert (issue['fields']['reporter']['key'], issue['fields']['assignee']['key']) == ('ana', 'ops-bot')

    def pull_and_process() -> list[dict]:
        with t.store.connect() as conn:
            last = conn.execute('SELECT coalesce(max(id),0) AS id FROM ka.events').fetchone()['id']
        jira_sync.pull_tickets(t.store, project, f'key = {key}', 5, None)
        while event_processor.run_once(t.store):
            pass
        with t.store.connect() as conn:
            return conn.execute(
                """SELECT r.* FROM ka.event_handler_runs r JOIN ka.events e ON e.id=r.event_id
                WHERE e.id>%s ORDER BY r.id""",
                (last,),
            ).fetchall()

    [run] = pull_and_process()
    assert run['status'] == 'succeeded'
    with t.store.connect() as conn:
        notify_event_run(conn, run['agent_session_id'], 1, 'waiting', 'Looked into it.')
        nid = conn.execute(
            'SELECT id FROM ka.notifications WHERE session_id=%s', (run['agent_session_id'],)
        ).fetchone()['id']
    assert (note(t.store, nid)['give_back_key'], note(t.store, nid)['give_back_name']) == ('ana', 'Ana Petrović')

    while jira_replies.run_once(t.store):  # the comment, then the assignment
        pass
    row = note(t.store, nid)
    assert (row['jira_status'], row['give_back_status']) == ('posted', 'done')
    after = state()
    [comment] = after['comments'][issue['id']]
    assert comment['body'] == 'Looked into it.\n\nAssigned back to Ana Petrović.\n\n— Tester (Knowledge Agent)'
    assert after['issues'][-1]['fields']['assignee']['key'] == 'ana'

    with t.store.connect() as conn:
        events = conn.execute('SELECT count(*) AS n FROM ka.events').fetchone()['n']
    assert pull_and_process() == []  # the hand-back's own update: Ana is not mapped, so no run
    with t.store.connect() as conn:
        latest = conn.execute('SELECT * FROM ka.events ORDER BY id DESC LIMIT 1').fetchone()
        assert conn.execute('SELECT count(*) AS n FROM ka.events').fetchone()['n'] == events + 1
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 1
    assert (latest['type'], latest['payload']['assignee_id'], latest['payload']['for_assignee']) == (
        'jira.ticket.updated',
        'ana',
        True,
    )
    assert pull_and_process() == []  # and nothing more after it
