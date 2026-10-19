"""Part 5: a matching Send-to-agent handler starts an ordinary agent run (purpose event) through start_run — the saved
agent with everything it has on, the rendered prompt as its first message, the handler's creator as actor — one run per
event and handler. 0103 adds the run's event and handler and the notification's run."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.events import processor as event_processor
from knowledge_admin.sessions.start import AGENT_NO_MODEL
from test_event_processor import a_handler, an_event, event, runs
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0103_event_runs.up.sql', '0103_event_runs.down.sql'
PROMPT = 'Triage {{ event.subject }} ({{ event.payload.status }})'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def sessions(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.agent_sessions ORDER BY created_at').fetchall()


def test_a_matching_handler_starts_one_ordinary_run(tester):
    t = tester
    handler = a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    first = an_event(t.store)
    assert event_processor.run_once(t.store) is True
    [handler_run] = runs(t.store, first['id'])
    [session] = sessions(t.store)
    assert (
        handler_run['status'],
        handler_run['message'],
        handler_run['agent_session_id'],
        handler_run['agent_task_id'],
    ) == ('succeeded', 'Started a run of Tester.', session['id'], None)
    assert (
        session['purpose'],
        session['agent_id'],
        session['actor_id'],
        session['event_id'],
        session['handler_id'],
        session['status'],
    ) == ('event', t.agent, handler['created_by'], first['id'], handler['id'], 'starting')
    detail = t.client.get(f'/api/sessions/{session["id"]}', headers=HEADERS).json()
    assert [(i['seq'], i['text']) for i in detail['inputs']] == [(1, 'Triage DEMO-1 (In Progress)')]
    assert detail['config']['id'] == t.agent and detail['purpose'] == 'event'
    assert detail['title'] == 'Tester · jira.ticket.updated DEMO-1'
    with t.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tasks').fetchone()['n'] == 0
        audit = conn.execute(
            """SELECT actor_id,details FROM ka.audit_events WHERE action='agent_run.created'
            AND resource_id=%s""",
            (str(session['id']),),
        ).fetchone()
    assert audit['actor_id'] == handler['created_by'] and audit['details']['agent_id'] == t.agent
    assert event(t.store, first['id'])['status'] == 'processed'
    listed = t.client.get(t.path, headers=HEADERS).json()['sessions']
    assert [(row['id'], row['purpose']) for row in listed] == [(str(session['id']), 'event')]


def test_processing_the_event_again_starts_nothing_new(tester):
    t = tester
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    first = an_event(t.store)
    event_processor.run_once(t.store)
    with t.store.connect() as conn:  # as if the event were picked up again
        conn.execute("UPDATE ka.events SET status='pending',processed_at=NULL WHERE id=%s", (first['id'],))
    event_processor.run_once(t.store)
    [session] = sessions(t.store)
    again = runs(t.store, first['id'])
    assert [(r['status'], r['message'], r['agent_session_id']) for r in again] == [
        ('succeeded', 'Started a run of Tester.', session['id'])
    ] * 2


@pytest.mark.parametrize(
    'change, message',
    [
        ('UPDATE ka.agents SET enabled=false WHERE id=%s', 'Agent is disabled.'),
        ('UPDATE ka.agents SET model_id=NULL WHERE id=%s', AGENT_NO_MODEL),
    ],
)
def test_an_unusable_agent_fails_the_handler_and_starts_nothing(tester, change, message):
    t = tester
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    with t.store.connect() as conn:
        conn.execute(change, (t.agent,))
    first = an_event(t.store)
    event_processor.run_once(t.store)
    [handler_run] = runs(t.store, first['id'])
    assert (handler_run['status'], handler_run['message'], handler_run['agent_session_id']) == ('failed', message, None)
    assert event(t.store, first['id'])['status'] == 'failed'
    assert sessions(t.store) == []


def test_a_missing_agent_fails_the_handler_and_starts_nothing(store):
    a_handler(store, action='agent', emit=None, agent_id='gone', prompt=PROMPT)
    first = an_event(store)
    event_processor.run_once(store)
    [handler_run] = runs(store, first['id'])
    assert (handler_run['status'], handler_run['message'], handler_run['agent_session_id']) == (
        'failed',
        'Agent not found.',
        None,
    )
    assert sessions(store) == []


def columns(conn, table) -> set[str]:
    return {
        row['column_name']
        for row in conn.execute(
            """SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s""",
            (table,),
        ).fetchall()
    }


def test_0103_columns_and_the_down_refusal(tester):
    t = tester
    with t.store.connect() as conn:
        assert {'event_id', 'handler_id'} <= columns(conn, 'agent_sessions')
        assert 'session_id' in columns(conn, 'notifications')
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    first = an_event(t.store)
    event_processor.run_once(t.store)
    with t.store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='Event runs exist; delete them before rollback'):
            run(conn, DOWN)
    with t.store.connect() as conn:
        conn.execute('DELETE FROM ka.agent_session_inputs')
        conn.execute('DELETE FROM ka.agent_sessions')
        conn.commit()
        run(conn, DOWN)
        assert not {'event_id', 'handler_id'} & columns(conn, 'agent_sessions')
        assert 'session_id' not in columns(conn, 'notifications')
        run(conn, UP)
        assert {'event_id', 'handler_id'} <= columns(conn, 'agent_sessions')
        assert 'session_id' in columns(conn, 'notifications')
    assert event(t.store, first['id'])['status'] == 'processed'


def test_an_event_or_handler_deleted_later_leaves_the_run(tester):
    t = tester
    handler = a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    an_event(t.store)
    event_processor.run_once(t.store)
    with t.store.connect() as conn:
        conn.execute('DELETE FROM ka.event_handler_runs WHERE handler_id=%s', (handler['id'],))
        conn.execute('DELETE FROM ka.event_handlers WHERE id=%s', (handler['id'],))
    [session] = sessions(t.store)
    assert (session['handler_id'], session['purpose']) == (None, 'event')


def test_an_event_run_starts_with_every_tool_even_without_servers(tester):
    t = tester  # ssh_run is offered, but no server is usable: its check refuses a call
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    first = an_event(t.store)
    event_processor.run_once(t.store)
    [handler_run] = runs(t.store, first['id'])
    [session] = sessions(t.store)
    assert (handler_run['status'], handler_run['agent_session_id']) == ('succeeded', session['id'])


def test_the_event_detail_and_notifications_carry_the_run(tester):
    t = tester
    handler = a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    first = an_event(t.store)
    event_processor.run_once(t.store)
    [session] = sessions(t.store)
    [handler_run] = t.client.get(f'/api/events/{first["id"]}', headers=HEADERS).json()['event']['runs']
    assert (handler_run['agent_session_id'], handler_run['agent_task']) == (str(session['id']), None)
    assert handler_run['run'] == {
        'id': str(session['id']),
        'status': 'starting',
        'agent_id': t.agent,
        'notification_id': None,
    }
    with t.store.connect() as conn:
        note = conn.execute(
            """INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,handler_id,agent_id,
            session_id) VALUES (%s,'agent.response','succeeded',%s,'Done.',%s,%s,%s,%s) RETURNING id""",
            (session['project_id'], session['title'], first['id'], handler['id'], t.agent, session['id']),
        ).fetchone()['id']
    [row] = t.client.get('/api/notifications', headers=HEADERS).json()['notifications']
    assert (row['id'], row['session_id'], row['agent_name']) == (note, str(session['id']), 'Tester')
    [handler_run] = t.client.get(f'/api/events/{first["id"]}', headers=HEADERS).json()['event']['runs']
    assert handler_run['run']['notification_id'] == note
    one = t.client.get(f'/api/notifications/{note}', headers=HEADERS).json()['notification']
    assert (one['session_id'], one['task']) == (str(session['id']), None)


def a_mapping(store, agent_id: str, key: str = 'ops-bot', name: str = 'Ops Bot'):
    """A Jira connection whose user `key` is mapped to the agent; returns the connection id."""
    with store.connect() as conn:
        connection = conn.execute(
            """INSERT INTO ka.jira_connections(project_id,name,base_url,username,jql)
            VALUES (%s,'primary','https://jira.test','mock-admin','project = DEMO') RETURNING id""",
            (store.project(conn, 'shop'),),
        ).fetchone()['id']
        conn.execute(
            """INSERT INTO ka.jira_user_agents(connection_id,jira_user_key,jira_user_name,agent_id)
            VALUES (%s,%s,%s,%s)""",
            (connection, key, name, agent_id),
        )
    return connection


def an_assigned_event(store, connection, assignee_id='ops-bot', assignee='Ops Bot') -> dict:
    payload = {'status': 'In Progress', 'assignee_id': assignee_id, 'assignee': assignee, 'for_assignee': True}
    return an_event(store, source_id=connection, payload=Jsonb(payload))


def test_an_assignee_handler_runs_the_mapped_agent(tester):
    t = tester
    connection = a_mapping(t.store, t.agent)
    handler = a_handler(t.store, action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    first = an_assigned_event(t.store, connection)
    event_processor.run_once(t.store)
    [handler_run] = runs(t.store, first['id'])
    [session] = sessions(t.store)
    assert (handler_run['status'], handler_run['message'], handler_run['agent_session_id']) == (
        'succeeded',
        'Started a run of Tester.',
        session['id'],
    )
    assert (session['agent_id'], session['handler_id'], session['actor_id']) == (
        t.agent,
        handler['id'],
        handler['created_by'],
    )


@pytest.mark.parametrize(
    'assignee_id, assignee, message',
    [
        ('marko', 'Marko Ilić', 'No agent is mapped to Jira user Marko Ilić (marko).'),
        (None, None, 'The ticket is not assigned.'),
    ],
)
def test_an_assignee_handler_without_an_agent_fails_and_starts_nothing(tester, assignee_id, assignee, message):
    t = tester
    connection = a_mapping(t.store, t.agent)
    a_handler(t.store, action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    first = an_assigned_event(t.store, connection, assignee_id, assignee)
    event_processor.run_once(t.store)
    [handler_run] = runs(t.store, first['id'])
    assert (handler_run['status'], handler_run['message'], handler_run['agent_session_id']) == ('failed', message, None)
    assert event(t.store, first['id'])['status'] == 'failed'
    assert sessions(t.store) == []


def test_a_disabled_mapped_agent_fails_with_the_disabled_text(tester):
    t = tester
    connection = a_mapping(t.store, t.agent)
    a_handler(t.store, action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (t.agent,))
    first = an_assigned_event(t.store, connection)
    event_processor.run_once(t.store)
    [handler_run] = runs(t.store, first['id'])
    assert (handler_run['status'], handler_run['message']) == ('failed', 'Agent is disabled.')
    assert sessions(t.store) == []


def test_a_mapping_of_another_connection_is_not_used(tester):
    t = tester
    a_mapping(t.store, t.agent)
    a_handler(t.store, action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    first = an_assigned_event(t.store, uuid4())
    event_processor.run_once(t.store)
    [handler_run] = runs(t.store, first['id'])
    assert handler_run['message'] == 'No agent is mapped to Jira user Ops Bot (ops-bot).'


def test_a_fixed_and_an_assignee_handler_fire_independently(tester):
    t = tester
    connection = a_mapping(t.store, t.agent)
    a_handler(t.store, name='A fixed', action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    a_handler(t.store, name='B assignee', action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    unmapped = an_assigned_event(t.store, connection, 'marko', 'Marko Ilić')
    event_processor.run_once(t.store)
    assert [(r['handler_name'], r['status']) for r in runs(t.store, unmapped['id'])] == [
        ('A fixed', 'succeeded'),
        ('B assignee', 'failed'),
    ]
    mapped = an_assigned_event(t.store, connection)
    event_processor.run_once(t.store)
    assert [(r['handler_name'], r['status']) for r in runs(t.store, mapped['id'])] == [
        ('A fixed', 'succeeded'),
        ('B assignee', 'succeeded'),
    ]
    assert len(sessions(t.store)) == 3


def test_deleting_the_agent_removes_its_mapping_and_the_handler_keeps_working_for_others(tester):
    t = tester
    connection = a_mapping(t.store, t.agent)
    with t.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.agents(id,name,enabled,system_prompt,model_id) SELECT 'other','Other',true,'',model_id
            FROM ka.agents WHERE id=%s""",
            (t.agent,),
        )
        conn.execute(
            """INSERT INTO ka.jira_user_agents(connection_id,jira_user_key,jira_user_name,agent_id)
            VALUES (%s,'ana','Ana Petrović','other')""",
            (connection,),
        )
        conn.execute('DELETE FROM ka.agents WHERE id=%s', (t.agent,))
    a_handler(t.store, action='agent', emit=None, agent_from='assignee', prompt=PROMPT)
    gone = an_assigned_event(t.store, connection)
    event_processor.run_once(t.store)
    assert runs(t.store, gone['id'])[0]['message'] == 'No agent is mapped to Jira user Ops Bot (ops-bot).'
    other = an_assigned_event(t.store, connection, 'ana', 'Ana Petrović')
    event_processor.run_once(t.store)
    assert runs(t.store, other['id'])[0]['message'] == 'Started a run of Other.'
