"""A Send-to-agent handler can target the agent mapped to the Jira ticket's assignee (0118): the pure choice, the
stored column, the API validation, Preview, the processor's outcome and the whole flow against the Jira mock."""

import runpy
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.events import jira_replies, processor as event_processor
from knowledge_admin.events.targets import NOT_ASSIGNED, pick_agent
from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.sessions.text import notify_event_run
from test_event_handlers_api import EMIT, error
from test_event_processor import a_handler, an_event
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
SERVER = Path(__file__).resolve().parents[3] / 'deploy/jira-mock/server.py'
PROMPT = 'Work on {{ event.subject }}'
ASSIGNEE = {
    **EMIT,
    'name': 'Assignee',
    'conditions': [{'field': 'payload.for_assignee', 'op': 'equals', 'value': 'true'}],
    'action': 'agent',
    'emit': None,
    'agent_from': 'assignee',
    'agent_id': None,
    'prompt': PROMPT,
}
ROWS = [
    {'jira_user_key': 'ops-bot', 'agent_id': 'ops-agent', 'enabled': True},
    {'jira_user_key': 'docs-bot', 'agent_id': 'docs-agent', 'enabled': False},
]


def jira_event(assignee_id, assignee=None) -> dict:
    return {'source': 'jira', 'payload': {'assignee_id': assignee_id, 'assignee': assignee}}


# The pure choice


def test_the_mapped_agent_is_chosen():
    handler = {'agent_from': 'assignee', 'agent_id': None}
    assert pick_agent(handler, jira_event('ops-bot', 'Ops Bot'), ROWS) == ('ops-agent', None)


def test_an_unmapped_assignee_is_refused_with_name_and_key():
    handler = {'agent_from': 'assignee', 'agent_id': None}
    assert pick_agent(handler, jira_event('marko', 'Marko Ilić'), ROWS) == (
        None,
        'No agent is mapped to Jira user Marko Ilić (marko).',
    )


@pytest.mark.parametrize('payload', [{'assignee_id': None, 'assignee': None}, {}])
def test_an_unassigned_ticket_is_refused(payload):
    handler = {'agent_from': 'assignee', 'agent_id': None}
    assert pick_agent(handler, {'source': 'jira', 'payload': payload}, ROWS) == (None, NOT_ASSIGNED)
    assert NOT_ASSIGNED == 'The ticket is not assigned.'


def test_a_disabled_mapped_agent_is_refused_with_the_processor_text():
    handler = {'agent_from': 'assignee', 'agent_id': None}
    assert pick_agent(handler, jira_event('docs-bot', 'Docs Bot'), ROWS) == (None, event_processor.DISABLED)


def test_a_fixed_handler_keeps_its_agent():
    handler = {'agent_from': 'fixed', 'agent_id': 'tester'}
    assert pick_agent(handler, jira_event('ops-bot', 'Ops Bot'), ROWS) == ('tester', None)
    assert pick_agent(handler, {'source': 'manual', 'payload': {}}, []) == ('tester', None)


# The column


def test_the_column_defaults_to_fixed_and_agent_id_is_null_only_for_assignee(store):
    assert a_handler(store)['agent_from'] == 'fixed'
    assignee = a_handler(store, name='A', action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    assert (assignee['agent_from'], assignee['agent_id']) == ('assignee', None)
    bad = [
        {'action': 'agent', 'emit': None, 'prompt': PROMPT},  # fixed without an agent
        {'action': 'agent', 'emit': None, 'prompt': PROMPT, 'agent_from': 'assignee', 'agent_id': 'x'},
        {'agent_from': 'assignee'},  # emit
        {'action': 'agent', 'emit': None, 'prompt': PROMPT, 'agent_from': 'reporter'},
    ]
    for number, values in enumerate(bad):
        with pytest.raises(psycopg.errors.CheckViolation):
            a_handler(store, name=f'B{number}', **values)


def test_down_and_up_again(store):
    a_handler(store, name='A', action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    with store.connect() as conn:
        conn.execute((MIGRATIONS / '0118_handler_agent_from.down.sql').read_text(), prepare=False)
        assert conn.execute('SELECT count(*) AS n FROM ka.event_handlers').fetchone()['n'] == 0
        conn.execute((MIGRATIONS / '0118_handler_agent_from.up.sql').read_text(), prepare=False)
        assert (
            conn.execute(
                """SELECT column_default FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='event_handlers' AND column_name='agent_from'"""
            )
            .fetchone()['column_default']
            .startswith("'fixed'")
        )


# The API


def test_create_and_edit_an_assignee_handler(tester):
    t = tester
    response = t.client.post('/api/event-handlers', headers=HEADERS, json={**ASSIGNEE, 'agent_id': t.agent})
    assert response.status_code == 201, response.text
    handler = response.json()['handler']
    assert (handler['agent_from'], handler['agent_id'], handler['agent_name']) == ('assignee', None, None)
    fixed = {**ASSIGNEE, 'agent_from': 'fixed', 'agent_id': t.agent, 'revision': 1}
    saved = t.client.put(f'/api/event-handlers/{handler["id"]}', headers=HEADERS, json=fixed).json()['handler']
    assert (saved['agent_from'], saved['agent_id']) == ('fixed', t.agent)
    with t.store.connect() as conn:
        details = conn.execute("SELECT details FROM ka.audit_events WHERE action='event_handler.created'").fetchone()[
            'details'
        ]
    assert details['agent_from'] == 'assignee'


@pytest.mark.parametrize(
    'changes, message',
    [
        (
            {'action': 'emit', 'emit': EMIT['emit'], 'prompt': None},
            'Only Send to agent handlers can target the assignee.',
        ),
        ({'type_pattern': 'triage.*'}, 'Agent mapped to the assignee works only with Jira events, e.g. jira.ticket.*.'),
        (
            {'type_pattern': 'jira.sprint.*'},
            'Agent mapped to the assignee works only with Jira events, e.g. jira.ticket.*.',
        ),
    ],
)
def test_validation(tester, changes, message):
    response = tester.client.post('/api/event-handlers', headers=HEADERS, json={**ASSIGNEE, **changes})
    assert error(response, 400) == message


@pytest.mark.parametrize('pattern', ['*', 'jira.*', 'jira.ticket.created', '*.ticket.updated'])
def test_patterns_that_can_match_jira_events_are_accepted(tester, pattern):
    response = tester.client.post('/api/event-handlers', headers=HEADERS, json={**ASSIGNEE, 'type_pattern': pattern})
    assert response.status_code == 201, response.text


def test_preview_explains_the_target(tester):
    t = tester
    with t.store.connect() as conn:
        connection = conn.execute(
            """INSERT INTO ka.jira_connections(project_id,name,base_url,username,jql)
            VALUES (%s,'primary','https://jira.test','mock-admin','project = DEMO') RETURNING id""",
            (t.store.project(conn, 'shop'),),
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.jira_user_agents(connection_id,jira_user_key,jira_user_name,agent_id)
            VALUES (%s,'ops-bot','Ops Bot',%s)""",
            (connection, t.agent),
        )

    def preview(handler, assignee_id, assignee=None):
        payload = {'assignee_id': assignee_id, 'assignee': assignee, 'for_assignee': True}
        created = an_event(t.store, source_id=connection, payload=Jsonb(payload))
        response = t.client.post(
            '/api/event-handlers/preview', headers=HEADERS, json={'handler': handler, 'event_id': created['id']}
        )
        assert response.status_code == 200, response.text
        return response.json()

    mapped = preview(ASSIGNEE, 'ops-bot', 'Ops Bot')
    assert (mapped['matches'], mapped['target'], mapped['output']) == (
        True,
        {'agent_id': t.agent, 'agent_name': 'Tester'},
        {'prompt': 'Work on DEMO-1'},
    )
    assert preview(ASSIGNEE, 'marko', 'Marko Ilić')['target'] == {
        'error': 'No agent is mapped to Jira user Marko Ilić (marko).'
    }
    assert preview(ASSIGNEE, None)['target'] == {'error': 'The ticket is not assigned.'}
    fixed = {**ASSIGNEE, 'agent_from': 'fixed', 'agent_id': t.agent}
    assert preview(fixed, None)['target'] == {'agent_id': t.agent, 'agent_name': 'Tester'}
    assert preview({**fixed, 'agent_id': 'gone'}, None)['target'] == {'error': 'Agent not found.'}
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (t.agent,))
    assert preview(ASSIGNEE, 'ops-bot', 'Ops Bot')['target'] == {'error': 'Agent is disabled.'}
    assert 'target' not in preview(EMIT, 'ops-bot')


# The whole flow against the Jira mock


@pytest.fixture
def jira_mock(tmp_path, monkeypatch):
    monkeypatch.setenv('JIRA_MOCK_STATE', str(tmp_path / 'jira-mock-state.json'))
    module = runpy.run_path(str(SERVER))
    server = ThreadingHTTPServer(('127.0.0.1', 0), module['Handler'])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def control(path, form):
        request = urllib.request.Request(base + path, data=urllib.parse.urlencode(form).encode())
        try:
            opener.open(request)
        except urllib.error.HTTPError as answer:
            assert answer.code == 303, answer.code

    try:
        yield base, control
    finally:
        server.shutdown()
        server.server_close()
        module['reset']()


def test_the_assignee_agent_answers_a_reassigned_ticket_and_a_person_comment_but_not_our_reply(tester, jira_mock):
    t = tester
    base, control = jira_mock
    settings = {'base_url': f'{base}/jira', 'username': 'mock-admin', 'token': 'jira-mock-token'}
    assert t.client.put('/api/jira/settings', headers=HEADERS, json=settings).status_code == 200
    mapped = t.client.put(
        '/api/jira/user-agents/ops-bot', headers=HEADERS, json={'name': 'Ops Bot', 'agent_id': t.agent}
    )
    assert mapped.status_code == 200, mapped.text
    handler = t.client.post('/api/event-handlers', headers=HEADERS, json={**ASSIGNEE, 'reply_to_jira': True})
    assert handler.status_code == 201, handler.text
    with t.store.connect() as conn:
        project = t.store.project(conn, 'shop')

    def pull_and_process() -> list[dict]:
        """Pull DEMO-20, process the new events; the handler runs they recorded."""
        with t.store.connect() as conn:
            last = conn.execute('SELECT coalesce(max(id),0) AS id FROM ka.events').fetchone()['id']
        jira_sync.pull_tickets(t.store, project, 'key = DEMO-20', 5, None, events=True)
        while event_processor.run_once(t.store):
            pass
        with t.store.connect() as conn:
            return conn.execute(
                """SELECT r.* FROM ka.event_handler_runs r JOIN ka.events e ON e.id=r.event_id
                WHERE e.id>%s ORDER BY r.id""",
                (last,),
            ).fetchall()

    def edit(assignee):
        form = {'summary': 'Ticket 20', 'description': '', 'status': 'Open', 'assignee': assignee}
        control('/mock/DEMO-20/edit', form)

    assert pull_and_process() == []  # first seen, unassigned: not for the assignee

    edit('ops-bot')
    [first] = pull_and_process()
    assert (first['status'], first['message']) == ('succeeded', 'Started a run of Tester.')
    with t.store.connect() as conn:
        session = conn.execute('SELECT * FROM ka.agent_sessions WHERE id=%s', (first['agent_session_id'],)).fetchone()
        assert session['agent_id'] == t.agent and session['purpose'] == 'event'
        notify_event_run(conn, session['id'], 1, 'waiting', 'On it.')
        note = conn.execute('SELECT * FROM ka.notifications WHERE session_id=%s', (session['id'],)).fetchone()
    assert (note['jira_status'], note['agent_id'], note['jira_issue_key']) == ('pending', t.agent, 'DEMO-20')

    assert jira_replies.run_once(t.store) is True  # our reply is posted as mock-admin
    with t.store.connect() as conn:
        assert (
            conn.execute('SELECT jira_status FROM ka.notifications WHERE id=%s', (note['id'],)).fetchone()[
                'jira_status'
            ]
            == 'posted'
        )
    assert pull_and_process() == []  # our own reply starts nothing

    control('/mock/DEMO-20/comment', {'body': 'Any news?', 'author': 'ana'})
    [second] = pull_and_process()
    assert second['status'] == 'succeeded' and second['agent_session_id'] != first['agent_session_id']

    edit('marko')  # reassigned away to an unmapped user: no run for the old agent
    [third] = pull_and_process()
    assert (third['status'], third['message'], third['agent_session_id']) == (
        'failed',
        'No agent is mapped to Jira user Marko Ilić (marko).',
        None,
    )
    edit('')  # unassigned: not for the assignee
    assert pull_and_process() == []
    with t.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 2
