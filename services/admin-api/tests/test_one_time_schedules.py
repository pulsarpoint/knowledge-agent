"""One-time Emit event schedules (0124): a schedule runs once at run_at instead of repeating on a cron timetable."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from knowledge_admin.events import scheduler
from knowledge_admin.events.schedules import PAST
from knowledge_admin.events.schedules_api import BOTH_OR_NEITHER, REPEATS
from test_api import _client
from test_jira import setup
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0124_one_time_schedules.up.sql', '0124_one_time_schedules.down.sql'


def later(**delta) -> datetime:
    return (datetime.now(UTC) + timedelta(**delta)).replace(microsecond=0)


def once(**changes) -> dict:
    return {
        'target_kind': 'event.emit',
        'name': 'Release check',
        'enabled': True,
        'run_at': later(hours=2).isoformat(),
        'timezone': 'UTC',
        **changes,
    }


def error(response) -> str:
    return response.json()['error']['message']


def create(client, **changes) -> dict:
    response = client.post('/api/schedules', headers=HEADERS, json=once(**changes))
    assert response.status_code == 201, response.text
    return response.json()['schedule']


def edit(client, schedule: dict, **changes):
    value = {'run_at': schedule['run_at'], 'timezone': schedule['timezone'], 'revision': schedule['revision']}
    return client.put(f'/api/schedules/{schedule["id"]}', headers=HEADERS, json={**value, **changes})


def listed(client, schedule: dict) -> dict:
    [row] = [s for s in client.get('/api/schedules', headers=HEADERS).json()['schedules'] if s['id'] == schedule['id']]
    return row


def row(store, schedule_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.schedules WHERE id=%s', (schedule_id,)).fetchone()


def due(store, schedule_id) -> None:
    """The time has come: run_at and next_run_at move into the past."""
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.schedules SET run_at=now()-interval '1 minute',next_run_at=now()-interval '1 minute' "
            'WHERE id=%s',
            (schedule_id,),
        )


def events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute("SELECT * FROM ka.events WHERE source='schedule' ORDER BY id").fetchall()


def history(store, schedule_id) -> list[str]:
    with store.connect() as conn:
        rows = conn.execute(
            'SELECT trigger FROM ka.schedule_runs WHERE schedule_id=%s ORDER BY id', (schedule_id,)
        ).fetchall()
    return [r['trigger'] for r in rows]


def test_a_one_time_schedule_is_created_for_its_time(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    run_at = later(hours=3)
    schedule = create(client, run_at=run_at.isoformat())
    assert schedule['cron'] is None and datetime.fromisoformat(schedule['run_at']) == run_at
    assert datetime.fromisoformat(schedule['next_run_at']) == run_at and schedule['enabled'] is True
    shown = listed(client, schedule)
    assert shown['run_at'] == schedule['run_at'] and shown['description'] == 'Once'
    paused = create(client, name='Paused one', enabled=False)
    assert paused['next_run_at'] is None


def test_a_time_without_an_offset_is_read_in_the_schedule_time_zone(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    local = (datetime.now(UTC) + timedelta(days=2)).replace(hour=9, minute=30, second=0, microsecond=0)
    schedule = create(client, run_at=local.strftime('%Y-%m-%dT%H:%M'), timezone='Asia/Tokyo')
    assert datetime.fromisoformat(schedule['run_at']) == local.replace(tzinfo=ZoneInfo('Asia/Tokyo'))


def test_bad_one_time_schedules_are_refused(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for body, message in [
        (once(run_at=later(minutes=-1).isoformat()), PAST),
        (once(cron='0 6 * * *'), BOTH_OR_NEITHER),
        ({k: v for k, v in once().items() if k != 'run_at'}, BOTH_OR_NEITHER),
        (once(target_kind='jira.pull', name=None), REPEATS),
    ]:
        response = client.post('/api/schedules', headers=HEADERS, json=body)
        assert response.status_code == 400 and error(response) == message, response.text
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []


def test_a_jira_pull_cannot_be_edited_into_a_one_time_schedule(store, tmp_path):
    client, _ = setup(store, tmp_path)
    pull = client.post(
        '/api/schedules',
        headers=HEADERS,
        json={'target_kind': 'jira.pull', 'enabled': True, 'cron': '*/15 * * * *', 'timezone': 'UTC'},
    ).json()['schedule']
    response = client.put(
        f'/api/schedules/{pull["id"]}',
        headers=HEADERS,
        json={'run_at': later(hours=1).isoformat(), 'timezone': 'UTC', 'enabled': True, 'revision': pull['revision']},
    )
    assert response.status_code == 400 and error(response) == REPEATS


def test_editing_checks_the_time_and_the_timetable(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    past = edit(client, schedule, run_at=later(minutes=-5).isoformat())
    assert past.status_code == 400 and error(past) == PAST
    both = edit(client, schedule, cron='0 6 * * *')
    assert both.status_code == 400 and error(both) == BOTH_OR_NEITHER
    repeating = edit(client, schedule, run_at=None, cron='0 6 * * *').json()['schedule']
    assert repeating['run_at'] is None and repeating['cron'] == '0 6 * * *' and repeating['description'] != 'Once'


def test_the_scheduler_fires_it_once_and_keeps_the_row_and_history(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    due(store, schedule['id'])
    assert scheduler.run_once(store) is True
    assert scheduler.run_once(store) is False
    assert len(events(store)) == 1
    saved = row(store, schedule['id'])
    assert saved['enabled'] is False and saved['next_run_at'] is None and saved['run_at'] is not None
    assert saved['last_status'] == 'succeeded' and history(store, schedule['id']) == ['schedule']
    shown = listed(client, schedule)
    assert shown['last_finished_at'] is not None and shown['last_status'] == 'succeeded'


def test_run_now_before_the_time_does_not_use_it_up(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    assert client.post(f'/api/schedules/{schedule["id"]}/run', headers=HEADERS).status_code == 200
    assert scheduler.run_once(store) is True
    saved = row(store, schedule['id'])
    assert saved['enabled'] is True and saved['next_run_at'] == saved['run_at']
    assert [e['payload']['trigger'] for e in events(store)] == ['manual']
    due(store, schedule['id'])
    assert scheduler.run_once(store) is True
    assert [e['payload']['trigger'] for e in events(store)] == ['manual', 'scheduled']
    assert row(store, schedule['id'])['enabled'] is False and history(store, schedule['id']) == ['manual', 'schedule']


def test_editing_a_finished_schedule_to_a_new_time_turns_it_back_on(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    due(store, schedule['id'])
    scheduler.run_once(store)
    finished = listed(client, schedule)
    run_at = later(days=1)
    again = edit(client, finished, run_at=run_at.isoformat()).json()['schedule']
    assert again['enabled'] is True and datetime.fromisoformat(again['next_run_at']) == run_at
    kept_off = edit(client, again, run_at=later(days=2).isoformat(), enabled=False).json()['schedule']
    assert kept_off['enabled'] is False and kept_off['next_run_at'] is None
    renamed = edit(client, kept_off, name='Still off').json()['schedule']
    assert renamed['enabled'] is False and renamed['next_run_at'] is None


def test_an_edit_during_the_run_to_a_new_time_is_not_used_up(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    due(store, schedule['id'])
    run_at = later(days=1)

    def moved_meanwhile(store, claimed):
        current = listed(client, schedule)
        assert edit(client, current, run_at=run_at.isoformat()).status_code == 200
        return 'succeeded', 'done'

    scheduler.run_once(store, {'event.emit': moved_meanwhile})
    saved = row(store, schedule['id'])
    assert saved['enabled'] is True and saved['next_run_at'] == run_at


def migrate(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_row(conn, project, **values) -> dict:
    row = {
        'project_id': project,
        'name': f'S {uuid4().hex[:6]}',
        'target_kind': 'event.emit',
        'target_id': uuid4(),
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.schedules({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def test_the_checks_and_the_migration_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a_row(conn, project, run_at=later(hours=1))
        repeating = a_row(conn, project, cron='0 6 * * *')
    for bad in [{}, {'cron': '0 6 * * *', 'run_at': later(hours=1)}, {'target_kind': 'jira.pull', 'run_at': later()}]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            a_row(conn, project, **bad)
    with store.connect() as conn:
        migrate(conn, DOWN)
        assert [r['id'] for r in conn.execute('SELECT id FROM ka.schedules').fetchall()] == [repeating['id']]
        assert (
            conn.execute(
                "SELECT is_nullable FROM information_schema.columns WHERE table_schema='ka' AND table_name='schedules' "
                "AND column_name='cron'"
            ).fetchone()['is_nullable']
            == 'NO'
        )
        migrate(conn, UP)
        a_row(conn, project, run_at=later(hours=1))
