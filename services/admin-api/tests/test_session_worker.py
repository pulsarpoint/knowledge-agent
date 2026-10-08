"""The session machinery both workers share: each claims only its agent type's sessions; a turn's commit runs in the
transaction that records it, only for a successful turn this worker still owns."""
from uuid import UUID

from psycopg.types.json import Jsonb

from knowledge_admin import claude_code_worker
from knowledge_admin.knowledge_agents import session_worker as shared
from test_claude_code_sessions import sessions, claude_code_session  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_each_worker_claims_only_sessions_of_its_agent_type(sessions):
    store = sessions.store
    claude, native = UUID(claude_code_session(sessions)['id']), UUID(claude_code_session(sessions)['id'])
    with store.connect() as conn:  # the second one as a native session (Task 3 creates them through the API)
        conn.execute("UPDATE ka.agent_sessions_cc SET runtime='openai',agent_config=%s WHERE id=%s",
                     (Jsonb({'id': sessions.agent}), native))
    assert [row['id'] for row in claude_code_worker.claim(store, 'cc-1', [], 4)] == [claude]
    [row] = shared.claim(store, 'native-1', [], 4, 'openai')
    assert (row['id'], row['worker_id'], row['runtime'], row['agent_name']) == (native, 'native-1', 'openai', 'Technical writer')
    assert shared.claim(store, 'native-2', [], 4, 'openai') == []  # leased by native-1
    assert shared.claim(store, 'cc-2', [], 4, 'claude_code') == []  # leased by cc-1
    assert shared.run_key(native, 3) == f'cc:{native}:3'


def test_finish_turn_commits_with_a_successful_turn_only(sessions):
    store = sessions.store
    sid = UUID(claude_code_session(sessions)['id'])
    [row] = claude_code_worker.claim(store, 'worker-1', [], 4)
    runner = shared.SessionRunner(store, row)
    committed = []

    def commit(conn):  # sees the turn's own update: one transaction
        committed.append(conn.execute('SELECT status,draft FROM ka.agent_sessions_cc WHERE id=%s', (sid,)).fetchone())

    runner.finish_turn(1, 'failed', 'Broken.', draft='# Lost', usage=None, commit=commit)
    runner.finish_turn(2, 'stopped', shared.STOPPED, draft='# Lost', usage=None, commit=commit)
    assert committed == []
    runner.finish_turn(3, 'waiting', '', draft='# Kept', commit=commit,
                       usage={'input_tokens': 3, 'output_tokens': 2, 'cached_tokens': 1, 'cost_usd': 0.0})
    assert committed == [{'status': 'waiting', 'draft': '# Kept'}]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET worker_id='other' WHERE id=%s", (sid,))
    runner.finish_turn(4, 'waiting', '', draft='# Not mine', usage=None, commit=commit)
    assert len(committed) == 1  # a runner that lost the session writes nothing
    with store.connect() as conn:
        final = conn.execute('SELECT draft,draft_turn,input_tokens,cached_tokens FROM ka.agent_sessions_cc WHERE id=%s',
                             (sid,)).fetchone()
    assert final == {'draft': '# Kept', 'draft_turn': 3, 'input_tokens': 3, 'cached_tokens': 1}


def test_claude_code_worker_keeps_its_names():
    for name in ('STOPPED', 'INTERRUPTED', 'MAX_TOOL_OUTPUT', 'MAX_PREVIEW_CHARS', 'TOOL_TRUNCATED', 'clean_text',
                 'LEASE_SECONDS', 'IDLE_RELEASE_SECONDS'):
        assert getattr(claude_code_worker, name) is getattr(shared, name), name
    assert issubclass(claude_code_worker.SessionRunner, shared.SessionRunner)


def test_comments_reach_only_the_running_turn_of_a_session_this_worker_holds(sessions):
    """Delivery inside a running turn (native sessions): queued messages go to that turn once, under the lease."""
    store = sessions.store
    sid = UUID(claude_code_session(sessions)['id'])
    [row] = claude_code_worker.claim(store, 'worker-1', [], 4)
    runner = shared.SessionRunner(store, row)
    assert runner.next_turn()[0] == 1  # the first message starts turn 1
    assert runner.queued_comments(1) == []
    url = f'/api/claude-code/sessions/{sid}/messages'
    for text in ('Also cover retries.', 'And errors.'):
        assert sessions.client.post(url, headers=HEADERS, json={'text': text}).status_code == 202
    assert runner.queued_comments(2) == []  # not the running turn
    assert runner.queued_comments(1) == ['Also cover retries.', 'And errors.']
    assert runner.queued_comments(1) == []  # once
    with store.connect() as conn:
        delivered = conn.execute("SELECT turn FROM ka.agent_session_inputs WHERE session_id=%s AND status='delivered'",
                                 (sid,)).fetchall()
        assert [item['turn'] for item in delivered] == [1, 1, 1]
        conn.execute("UPDATE ka.agent_sessions_cc SET worker_id='other' WHERE id=%s", (sid,))
    assert sessions.client.post(url, headers=HEADERS, json={'text': 'Lost?'}).status_code == 202
    assert runner.queued_comments(1) == []  # a runner that lost the session takes nothing
