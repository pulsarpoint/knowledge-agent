"""Part 5: a matching Send-to-agent handler starts an ordinary agent run (purpose event) through start_run — the saved
agent with everything it has on, the rendered prompt as its first message, the handler's creator as actor — one run per
event and handler. 0103 adds the run's event and handler and the notification's run."""
from pathlib import Path

import psycopg
import pytest

from knowledge_admin import event_processor
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
    assert (handler_run['status'], handler_run['message'], handler_run['agent_session_id'], handler_run['agent_task_id']) == (
        'succeeded', 'Started a run of Tester.', session['id'], None)
    assert (session['purpose'], session['agent_id'], session['actor_id'], session['event_id'], session['handler_id'],
            session['status']) == ('event', t.agent, handler['created_by'], first['id'], handler['id'], 'starting')
    detail = t.client.get(f"/api/sessions/{session['id']}", headers=HEADERS).json()
    assert [(i['seq'], i['text']) for i in detail['inputs']] == [(1, 'Triage DEMO-1 (In Progress)')]
    assert detail['config']['id'] == t.agent and detail['purpose'] == 'event'
    with t.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tasks').fetchone()['n'] == 0
        audit = conn.execute("""SELECT actor_id,details FROM ka.audit_events WHERE action='agent_run.created'
            AND resource_id=%s""", (str(session['id']),)).fetchone()
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
        ('succeeded', 'Started a run of Tester.', session['id'])] * 2


@pytest.mark.parametrize('change, message', [
    ('UPDATE ka.agents SET enabled=false WHERE id=%s', 'Agent is disabled.'),
    ("UPDATE ka.agents SET resources='per_run' WHERE id=%s",
     'This agent gets its resources per run; handlers can only send to agents with fixed resources.'),
    ('UPDATE ka.agents SET model_id=NULL WHERE id=%s', AGENT_NO_MODEL)])
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
        'failed', 'Agent not found.', None)
    assert sessions(store) == []


def columns(conn, table) -> set[str]:
    return {row['column_name'] for row in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s""", (table,)).fetchall()}


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
