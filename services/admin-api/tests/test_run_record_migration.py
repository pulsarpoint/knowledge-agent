"""Migration 0061 stores ordered, typed events for every agent run."""
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def insert(conn, project, **values):
    row = {'run_key': 'run-1', 'seq': 1, 'kind': 'start', 'agent_id': 'search', 'payload': Jsonb({}), **values}
    conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        VALUES (%s,%(run_key)s,%(seq)s,%(kind)s,%(agent_id)s,%(payload)s)'''.replace('%s', '%(project)s'),
        {**row, 'project': project})


def test_events_are_ordered_and_unique_per_run(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        insert(conn, project, seq=1, kind='start')
        insert(conn, project, seq=2, kind='tool_call')
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert(conn, project, seq=2, kind='message')


def test_kind_is_restricted(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        with pytest.raises(psycopg.errors.CheckViolation):
            insert(conn, project, kind='gossip')


def test_down_then_up(store):
    down = (MIGRATIONS / '0061_agent_run_events.down.sql').read_text()
    up = (MIGRATIONS / '0061_agent_run_events.up.sql').read_text()
    with store.connect() as conn:
        conn.execute(down, prepare=False)
        assert conn.execute("SELECT to_regclass('ka.agent_run_events') AS t").fetchone()['t'] is None
        conn.execute(up, prepare=False)
        assert conn.execute("SELECT to_regclass('ka.agent_run_events') AS t").fetchone()['t'] is not None
