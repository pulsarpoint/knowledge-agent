"""0091: ka.events.source also allows 'manual'; the down step deletes manual events."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_events_migration import an_event

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_manual_event(conn, project=None, **values) -> dict:
    return an_event(conn, project, **{'source': 'manual', 'source_id': None, 'dedupe_key': f'manual:{uuid4()}', **values})


def test_manual_is_a_source(store):
    with store.connect() as conn:
        event = a_manual_event(conn)
    assert (event['source'], event['source_id'], event['status'], event['root_id'] == event['id']) == ('manual', None, 'pending', True)


def test_other_checks_still_hold_for_manual(store):
    with store.connect() as conn:
        root = a_manual_event(conn)
    for bad in [{'handler_id': uuid4()}, {'parent_id': root['id']}, {'depth': 1}, {'source': 'mail'}]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            a_manual_event(conn, project=root['project_id'], **bad)


def test_down_deletes_manual_events_and_up_again(store):
    with store.connect() as conn:
        manual = a_manual_event(conn)
        jira = an_event(conn, project=manual['project_id'])
        run(conn, '0091_manual_events.down.sql')
        assert [r['id'] for r in conn.execute('SELECT id FROM ka.events').fetchall()] == [jira['id']]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            a_manual_event(conn, project=jira['project_id'])
        run(conn, '0091_manual_events.up.sql')
        a_manual_event(conn, project=jira['project_id'])
