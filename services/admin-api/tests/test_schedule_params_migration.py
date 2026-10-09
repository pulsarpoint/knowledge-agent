"""0087: schedules.params holds target settings (an object, default {})."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_schedule(conn, **values) -> dict:
    project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES (%s,'Shop') RETURNING id", (f'shop-{uuid4().hex[:8]}',)).fetchone()['id']
    row = {'project_id': project, 'name': 'Jira auto pull', 'target_kind': 'jira.pull', 'target_id': uuid4(), 'cron': '*/15 * * * *', **values}
    return conn.execute(f"INSERT INTO ka.schedules({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *", tuple(row.values())).fetchone()


def test_params_default_to_an_empty_object_and_must_be_an_object(store):
    with store.connect() as conn:
        assert a_schedule(conn)['params'] == {}
        assert a_schedule(conn, params=Jsonb({'jql': 'project = DEMO', 'limit': 10}))['params'] == {'jql': 'project = DEMO', 'limit': 10}
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_schedule(conn, params=Jsonb(['not', 'an', 'object']))


def test_down_drops_the_column_and_up_restores_it(store):
    column = """SELECT count(*) AS n FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='schedules' AND column_name='params'"""
    with store.connect() as conn:
        a_schedule(conn, params=Jsonb({'jql': 'x'}))
        run(conn, '0087_schedule_params.down.sql')
        assert conn.execute(column).fetchone()['n'] == 0
        run(conn, '0087_schedule_params.up.sql')
        assert conn.execute(column).fetchone()['n'] == 1
