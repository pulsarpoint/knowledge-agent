"""0083: changes to a session are announced on ka_sessions — run events (one notice per statement and session, with
the newest id), session rows (update, delete; an update of only the lease or last activity is silent) and queued
messages (insert, update, delete); other run keys are silent and nothing is sent before commit. Down removes the
triggers; up restores them."""
import json
from pathlib import Path

import psycopg

from session_rows import a_model, add_ad_hoc_session

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0083_session_notices.up.sql', '0083_session_notices.down.sql'
LATER_UP, LATER_DOWN = '0084_remove_claude_code.up.sql', '0084_remove_claude_code.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def two_sessions(store):
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        model = a_model(conn, actor)
        return project, actor, [add_ad_hoc_session(conn, project, actor, model) for _ in range(2)]


def insert_events(conn, project, keys: list[str]) -> list[dict]:
    """One INSERT statement for all keys (seq 1, 2, … per key)."""
    seqs = [keys[:index + 1].count(key) for index, key in enumerate(keys)]
    return conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT %s,k,s,'message','ad-hoc','{}' FROM unnest(%s::text[],%s::int[]) AS t(k,s) RETURNING id,run_key''',
        (project, keys, seqs)).fetchall()


def notices(listener) -> list[dict]:
    return [json.loads(n.payload) for n in listener.notifies(timeout=0.5) if n.channel == 'ka_sessions']


def test_event_inserts_notify_once_per_statement_and_session_with_the_newest_id(store):
    project, _, (first, second) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        rows = insert_events(conn, project, [f'cc:{first}:1', f'cc:{second}:1', f'cc:{first}:1', f'cc:{first}:2',
                                             'run:other', f'cc:{second}:x', 'cc:not-a-session:1'])
        assert notices(listener) == []  # nothing before commit
        conn.commit()
        got = notices(listener)
        assert len(got) == 2
        assert {(n['s'], n['e']) for n in got} == {
            (str(first), max(r['id'] for r in rows if r['run_key'].startswith(f'cc:{first}:'))),
            (str(second), next(r['id'] for r in rows if r['run_key'] == f'cc:{second}:1'))}
        insert_events(conn, project, ['run:quiet', 'cc:also-quiet:1'])
        conn.commit()
        assert notices(listener) == []


def test_session_rows_and_queued_messages_notify_their_session(store):
    _, actor, (first, second) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (first,))
        conn.execute('UPDATE ka.agent_sessions SET input_tokens=5 WHERE id=%s', (first,))
        conn.commit()
        assert notices(listener) == [{'s': str(first)}]  # identical notices in one transaction are folded
        conn.execute("UPDATE ka.agent_sessions SET status='waiting' WHERE id=%s", (second,))
        conn.rollback()
        assert notices(listener) == []  # only committed changes are announced
        conn.execute("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Hi')",
                     (second, actor))
        conn.commit()
        assert notices(listener) == [{'s': str(second)}]
        conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE session_id=%s", (second,))
        conn.commit()
        assert notices(listener) == [{'s': str(second)}]
        conn.execute('DELETE FROM ka.agent_session_inputs WHERE session_id=%s', (second,))
        conn.commit()
        assert notices(listener) == [{'s': str(second)}]
        conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (first,))
        conn.commit()
        assert notices(listener) == [{'s': str(first)}]


def test_lease_only_session_updates_are_silent(store):
    _, _, (first, _) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        conn.execute("UPDATE ka.agent_sessions SET lease_expires_at=now()+interval '1 minute' WHERE id=%s", (first,))
        conn.commit()
        assert notices(listener) == []  # a lease renewal (updated_at moves too) is not news
        conn.execute('UPDATE ka.agent_sessions SET last_activity_at=now() WHERE id=%s', (first,))
        conn.commit()
        assert notices(listener) == []
        conn.execute('UPDATE ka.agent_sessions SET lease_expires_at=NULL,last_activity_at=now() WHERE id=%s', (first,))
        conn.commit()
        assert notices(listener) == []
        for change in ("status='working'", 'turns=1', 'output_tokens=7', "worker_id='w1'"):
            conn.execute(f"UPDATE ka.agent_sessions SET {change},lease_expires_at=now() WHERE id=%s", (first,))
            conn.commit()
            assert notices(listener) == [{'s': str(first)}], change


def test_down_removes_the_notices_and_up_restores_them(store):
    project, _, (first, _) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        run(conn, LATER_DOWN)  # 0084 renamed the table the down migration names
        run(conn, DOWN)
        conn.commit()
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working' WHERE id=%s", (first,))
        insert_events(conn, project, [f'cc:{first}:1'])
        conn.commit()
        assert notices(listener) == []
        run(conn, UP)
        run(conn, LATER_UP)
        conn.commit()
        conn.execute("UPDATE ka.agent_sessions SET status='waiting' WHERE id=%s", (first,))
        conn.commit()
        assert notices(listener) == [{'s': str(first)}]
