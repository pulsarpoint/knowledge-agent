"""0088: ka.events — one row per thing that happened at a source; dedupe_key is unique per project."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def an_event(conn, project=None, **values) -> dict:
    project = project or conn.execute("INSERT INTO ka.projects(slug,name) VALUES (%s,'Shop') RETURNING id", (f'shop-{uuid4().hex[:8]}',)).fetchone()['id']
    row = {'project_id': project, 'source': 'jira', 'source_id': uuid4(), 'type': 'jira.ticket.created', 'subject': 'DEMO-1',
           'title': 'Check latency', 'payload': Jsonb({'issue_id': '10001'}), 'dedupe_key': f'jira:x:10001:{uuid4()}',
           'occurred_at': '2026-10-09T10:00:00Z', **values}
    return conn.execute(f"INSERT INTO ka.events({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *", tuple(row.values())).fetchone()


def test_checks(store):
    with store.connect() as conn:
        assert an_event(conn)['created_at'] is not None
    for bad in [{'source': 'mail'}, {'type': 'Created'}, {'type': 'jira'}, {'subject': ' '}, {'payload': Jsonb([1])}]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            an_event(conn, **bad)


def test_dedupe_key_is_unique_per_project(store):
    with store.connect() as conn:
        first = an_event(conn, dedupe_key='same')
        an_event(conn, dedupe_key='same')  # another project
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        an_event(conn, project=first['project_id'], dedupe_key='same')


def test_down_and_up(store):
    with store.connect() as conn:
        an_event(conn)
        conn.execute((MIGRATIONS / '0090_agent_tasks_notifications.down.sql').read_text(), prepare=False)  # 0090 references events
        conn.execute((MIGRATIONS / '0089_event_handlers.down.sql').read_text(), prepare=False)  # 0089 builds on 0088
        conn.execute((MIGRATIONS / '0088_events.down.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events') IS NULL AS gone").fetchone()['gone']
        conn.execute((MIGRATIONS / '0088_events.up.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events_jira_issues') IS NOT NULL AS found").fetchone()['found']
        conn.execute((MIGRATIONS / '0089_event_handlers.up.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0090_agent_tasks_notifications.up.sql').read_text(), prepare=False)
