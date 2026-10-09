"""The event processor: each pending event goes once through the enabled handlers of its project; emitted events join
the chain and are processed on a later pass; failures are recorded and nothing is retried."""
import threading
from uuid import uuid4

from psycopg.types.json import Jsonb

from knowledge_admin import event_processor
from test_test_sessions import tester  # noqa: F401  (fixture)


def an_event(store, project=None, **values) -> dict:
    with store.connect() as conn:
        project = project or store.project(conn, 'shop')
        row = {'project_id': project, 'source': 'jira', 'source_id': uuid4(), 'type': 'jira.ticket.updated',
               'subject': 'DEMO-1', 'title': 'Fix login', 'payload': Jsonb({'status': 'In Progress', 'labels': ['agent']}),
               'dedupe_key': f'k{uuid4()}', 'occurred_at': '2026-10-09T10:00:00Z', **values}
        return conn.execute(f"INSERT INTO ka.events({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                            tuple(row.values())).fetchone()


def a_handler(store, name='Triage', **values) -> dict:
    with store.connect() as conn:
        row = {'project_id': store.project(conn, 'shop'), 'name': name, 'type_pattern': 'jira.ticket.*',
               'conditions': Jsonb([]), 'action': 'emit', 'emit': Jsonb({'type': 'triage.requested'}),
               'created_by': store.actor(conn, 'admin-id', 'admin@example.test'), **values}
        return conn.execute(f"INSERT INTO ka.event_handlers({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                            tuple(row.values())).fetchone()


def event(store, event_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.events WHERE id=%s', (event_id,)).fetchone()


def runs(store, event_id) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.event_handler_runs WHERE event_id=%s ORDER BY id', (event_id,)).fetchall()


def test_an_event_without_a_handler_is_processed(store):
    first = an_event(store)
    assert event_processor.run_once(store) is True
    assert event_processor.run_once(store) is False  # never picked up again
    assert (event(store, first['id'])['status'], runs(store, first['id'])) == ('processed', [])
    assert event(store, first['id'])['processed_at'] is not None


def test_emit_creates_a_pending_child_in_the_chain(store):
    handler = a_handler(store, conditions=Jsonb([{'field': 'payload.labels', 'op': 'contains', 'value': 'agent'}]),
                        emit=Jsonb({'type': 'triage.requested', 'title': 'Triage {{ event.subject }}',
                                    'payload': '{"from": {{ event.payload.status | tojson }}}'}))
    parent = an_event(store)
    assert event_processor.run_once(store) is True
    [run] = runs(store, parent['id'])
    child = event(store, run['emitted_event_id'])
    assert (run['status'], run['message'], run['handler_name'], run['handler_id']) == (
        'succeeded', 'Emitted triage.requested.', 'Triage', handler['id'])
    assert (child['source'], child['source_id'], child['type'], child['subject'], child['title'], child['payload']) == (
        'handler', handler['id'], 'triage.requested', 'DEMO-1', 'Triage DEMO-1', {'from': 'In Progress'})
    assert (child['parent_id'], child['root_id'], child['depth'], child['handler_id'], child['status']) == (
        parent['id'], parent['root_id'], 1, handler['id'], 'pending')
    assert child['dedupe_key'] == f"handler:{handler['id']}:{parent['id']}"
    assert event(store, parent['id'])['status'] == 'processed'
    assert event_processor.run_once(store) is True  # the child: no handler matches triage.* → processed
    assert event(store, child['id'])['status'] == 'processed'


def test_disabled_and_other_projects_handlers_do_not_run(store):
    a_handler(store, enabled=False)
    with store.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-x','Other') RETURNING id").fetchone()['id']
    first = an_event(store, project=other)
    event_processor.run_once(store)
    assert (event(store, first['id'])['status'], runs(store, first['id'])) == ('processed', [])


def test_one_failing_handler_fails_the_event_but_the_others_run(store):
    a_handler(store, name='A broken', emit=Jsonb({'type': '{{ event.payload.nope }}'}))
    a_handler(store, name='B fine')
    first = an_event(store)
    event_processor.run_once(store)
    broken, fine = runs(store, first['id'])
    assert (broken['status'], fine['status']) == ('failed', 'succeeded')
    assert broken['message'].startswith('Template error:') and broken['emitted_event_id'] is None
    assert event(store, first['id'])['status'] == 'failed'
    assert event_processor.run_once(store) is True   # B's child
    assert event_processor.run_once(store) is False  # the failed event is not retried


def test_a_self_matching_handler_stops_at_depth_five(store):
    a_handler(store, type_pattern='*', emit=Jsonb({'type': 'loop.again'}))
    first = an_event(store)
    assert event_processor.tick(store) == 6  # depths 0..5; the one at depth 5 cannot emit
    with store.connect() as conn:
        chain = conn.execute('SELECT id,depth,status FROM ka.events WHERE root_id=%s ORDER BY depth', (first['id'],)).fetchall()
    assert [(row['depth'], row['status']) for row in chain] == [(0, 'processed'), (1, 'processed'), (2, 'processed'),
                                                                 (3, 'processed'), (4, 'processed'), (5, 'failed')]
    [last_run] = runs(store, chain[-1]['id'])
    assert last_run['message'] == event_processor.TOO_DEEP
    assert event_processor.tick(store) == 0


def test_a_claimed_event_is_not_processed_by_another_worker(store):
    an_event(store)
    with store.connect() as holder:
        assert holder.execute(event_processor.CLAIM).fetchone() is not None  # held until this block ends
        assert event_processor.run_once(store) is False


def test_send_to_agent_starts_an_event_session(tester):
    t = tester
    handler = a_handler(t.store, action='agent', emit=None, agent_id=t.agent,
                        prompt='Triage {{ event.subject }} ({{ event.payload.status }})')
    first = an_event(t.store)
    assert event_processor.run_once(t.store) is True
    [run] = runs(t.store, first['id'])
    assert (run['status'], run['message']) == ('succeeded', 'Started a session with Tester.')
    with t.store.connect() as conn:
        session = conn.execute('SELECT purpose,title,actor_id,agent_id FROM ka.agent_sessions WHERE id=%s',
                               (run['agent_session_id'],)).fetchone()
        text = conn.execute('SELECT text FROM ka.agent_session_inputs WHERE session_id=%s', (run['agent_session_id'],)).fetchone()['text']
    assert (session['purpose'], session['title'], session['actor_id'], session['agent_id']) == (
        'event', 'jira.ticket.updated DEMO-1', handler['created_by'], t.agent)
    assert text == 'Triage DEMO-1 (In Progress)'
    assert event(t.store, first['id'])['status'] == 'processed'


def test_a_disabled_agent_fails_the_run(tester):
    t = tester
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt='Triage {{ event.subject }}')
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (t.agent,))
    first = an_event(t.store)
    event_processor.run_once(t.store)
    [run] = runs(t.store, first['id'])
    assert (run['status'], run['message'], run['agent_session_id']) == ('failed', 'Agent is disabled.', None)
    assert event(t.store, first['id'])['status'] == 'failed'


def test_a_missing_agent_fails_the_run(store):
    a_handler(store, action='agent', emit=None, agent_id='gone', prompt='Triage {{ event.subject }}')
    first = an_event(store)
    event_processor.run_once(store)
    [run] = runs(store, first['id'])
    assert (run['status'], run['message']) == ('failed', event_processor.NO_AGENT)


def test_the_loop_survives_errors_and_stops(store, monkeypatch):
    calls = []

    def flaky(_store):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('boom')
        stopped.set()
        return 0

    stopped = threading.Event()
    monkeypatch.setattr(event_processor, 'POLL_SECONDS', 0.01)
    monkeypatch.setattr(event_processor, 'tick', flaky)
    event_processor.loop(store, stopped)
    assert len(calls) == 2


def test_a_handler_whose_matching_raises_fails_its_run_but_not_the_queue(store):
    bad = a_handler(store, name='A bad', conditions=Jsonb([{'field': 'subject', 'op': 'matches', 'value': '('}]))
    good = a_handler(store, name='B good')
    events = [an_event(store), an_event(store)]
    event_processor.tick(store)
    for each in events:
        assert event(store, each['id'])['status'] == 'failed'
        bad_run, good_run = runs(store, each['id'])
        assert (bad_run['handler_id'], bad_run['status'], bad_run['message']) == (bad['id'], 'failed', event_processor.UNEXPECTED)
        assert (good_run['handler_id'], good_run['status']) == (good['id'], 'succeeded')


def test_a_database_error_in_an_action_fails_only_that_run(store, monkeypatch):
    def broken(conn, _store, _handler, _event):
        conn.execute('SELECT 1/0')

    monkeypatch.setitem(event_processor.ACTIONS, 'emit', lambda conn, s, h, e: (
        broken(conn, s, h, e) if h['name'] == 'A broken' else event_processor.emit(conn, s, h, e)))
    a_handler(store, name='A broken')
    a_handler(store, name='B fine')
    first = an_event(store)
    event_processor.run_once(store)
    broken_run, fine_run = runs(store, first['id'])
    assert (broken_run['status'], broken_run['message']) == ('failed', event_processor.UNEXPECTED)
    assert fine_run['status'] == 'succeeded' and fine_run['emitted_event_id'] is not None
    assert event(store, first['id'])['status'] == 'failed'
