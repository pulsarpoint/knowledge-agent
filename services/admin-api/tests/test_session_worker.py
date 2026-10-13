"""The session machinery the worker shares: a turn's commit runs in the transaction that records it, only for a
successful turn this worker still owns."""
from uuid import UUID

from knowledge_admin.knowledge_agents import session_worker as shared
from test_generations import sessions, start  # noqa: F401  (fixture)
from session_rows import a_model, add_ad_hoc_session
from test_repositories import HEADERS


def test_finish_turn_commits_with_a_successful_turn_only(sessions):
    store = sessions.store
    sid = UUID(start(sessions, model_id=sessions.claude, environment_ids=[], service_ids=[])['id'])
    [row] = shared.claim(store, 'worker-1', [], 4)
    runner = shared.SessionRunner(store, row)
    committed = []

    def commit(conn):  # sees the turn's own update: one transaction
        committed.append(conn.execute('SELECT status,draft FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone())

    runner.finish_turn(1, 'failed', 'Broken.', draft='# Lost', usage=None, commit=commit)
    runner.finish_turn(2, 'stopped', shared.STOPPED, draft='# Lost', usage=None, commit=commit)
    assert committed == []
    runner.finish_turn(3, 'waiting', '', draft='# Kept', commit=commit,
                       usage={'input_tokens': 3, 'output_tokens': 2, 'cached_tokens': 1})
    assert committed == [{'status': 'waiting', 'draft': '# Kept'}]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET worker_id='other' WHERE id=%s", (sid,))
    runner.finish_turn(4, 'waiting', '', draft='# Not mine', usage=None, commit=commit)
    assert len(committed) == 1  # a runner that lost the session writes nothing
    with store.connect() as conn:
        final = conn.execute('SELECT draft,draft_turn,input_tokens,cached_tokens FROM ka.agent_sessions WHERE id=%s',
                             (sid,)).fetchone()
    assert final == {'draft': '# Kept', 'draft_turn': 3, 'input_tokens': 3, 'cached_tokens': 1}


def test_comments_reach_only_the_running_turn_of_a_session_this_worker_holds(sessions):
    """Delivery inside a running turn (native sessions): queued messages go to that turn once, under the lease."""
    store = sessions.store
    sid = UUID(start(sessions, model_id=sessions.claude, environment_ids=[], service_ids=[])['id'])
    [row] = shared.claim(store, 'worker-1', [], 4)
    runner = shared.SessionRunner(store, row)
    assert runner.next_turn()[0] == 1  # the first message starts turn 1
    assert runner.queued_comments(1) == []
    url = f'/api/sessions/{sid}/messages'
    for text in ('Also cover retries.', 'And errors.'):
        assert sessions.client.post(url, headers=HEADERS, json={'text': text}).status_code == 202
    assert runner.queued_comments(2) == []  # not the running turn
    assert runner.queued_comments(1) == ['Also cover retries.', 'And errors.']
    assert runner.queued_comments(1) == []  # once
    with store.connect() as conn:
        delivered = conn.execute("SELECT turn FROM ka.agent_session_inputs WHERE session_id=%s AND status='delivered'",
                                 (sid,)).fetchall()
        assert [item['turn'] for item in delivered] == [1, 1, 1]
        conn.execute("UPDATE ka.agent_sessions SET worker_id='other' WHERE id=%s", (sid,))
    assert sessions.client.post(url, headers=HEADERS, json={'text': 'Lost?'}).status_code == 202
    assert runner.queued_comments(1) == []  # a runner that lost the session takes nothing


def test_an_interrupted_ad_hoc_turn_is_recorded_under_the_ad_hoc_agent(sessions):
    """A session left working by a lost worker fails; its error and end events name the agent 'ad-hoc' (no agents row)."""
    store = sessions.store
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_ad_hoc_session(conn, project, actor, a_model(conn, actor))
        conn.execute("UPDATE ka.agent_sessions SET status='working',turns=1 WHERE id=%s", (sid,))
    [row] = shared.claim(store, 'native-1', [], 4)
    assert (row['id'], row['agent_id']) == (sid, None)
    with store.connect() as conn:
        assert conn.execute('SELECT status FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()['status'] == 'failed'
        events = conn.execute('SELECT kind,agent_id FROM ka.agent_run_events WHERE run_key=%s ORDER BY seq',
                              (shared.run_key(sid, 1),)).fetchall()
    assert [(e['kind'], e['agent_id']) for e in events] == [('error', 'ad-hoc'), ('end', 'ad-hoc')]
