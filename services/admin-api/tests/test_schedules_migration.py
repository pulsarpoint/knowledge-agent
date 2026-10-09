"""0086: schedules (a cron expression in a time zone running one target) and their run history."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0086_schedules.up.sql', '0086_schedules.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_schedule(conn, target=None, **values) -> dict:
    project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES (%s,'Shop') RETURNING id", (f'shop-{uuid4().hex[:8]}',)).fetchone()['id']
    row = {'project_id': project, 'name': 'Jira auto pull', 'target_kind': 'jira.pull', 'target_id': target or uuid4(),
           'cron': '*/15 * * * *', **values}
    columns = ','.join(row)
    return conn.execute(f"INSERT INTO ka.schedules({columns}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                        tuple(row.values())).fetchone()


def test_defaults_and_checks(store):
    with store.connect() as conn:
        schedule = a_schedule(conn)
        assert schedule['timezone'] == 'UTC' and schedule['enabled'] is True and schedule['revision'] == 1
        assert schedule['run_requested'] is False and schedule['next_run_at'] is None and schedule['last_status'] is None
    for bad in [{'target_kind': 'agent.run'}, {'last_status': 'working'}, {'name': ' '}, {'revision': 0}]:
        with pytest.raises(psycopg.errors.IntegrityError), store.connect() as conn:
            a_schedule(conn, **bad)


def test_one_schedule_per_target(store):
    target = uuid4()
    with store.connect() as conn:
        first = a_schedule(conn, target)
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        conn.execute("""INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron)
            VALUES (%s,'Again','jira.pull',%s,'0 * * * *')""", (first['project_id'], target))


def test_runs_are_deleted_with_their_schedule_and_checked(store):
    with store.connect() as conn:
        schedule = a_schedule(conn)
        conn.execute("""INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status,message)
            VALUES (%s,'manual',now(),now(),'succeeded','Pulled 1 of 1 tickets.')""", (schedule['id'],))
        conn.execute('DELETE FROM ka.schedules WHERE id=%s', (schedule['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        schedule = a_schedule(conn)
        conn.execute("""INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status)
            VALUES (%s,'cron',now(),now(),'succeeded')""", (schedule['id'],))


def test_down_drops_both_tables_and_up_restores_them(store):
    exists = "SELECT to_regclass('ka.schedules') IS NOT NULL AND to_regclass('ka.schedule_runs') IS NOT NULL AS found"
    with store.connect() as conn:
        a_schedule(conn)
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.schedules') IS NULL AND to_regclass('ka.schedule_runs') IS NULL AS gone").fetchone()['gone']
        run(conn, UP)
        assert conn.execute(exists).fetchone()['found'] is True
