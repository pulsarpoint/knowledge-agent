"""The Emit event schedule target: each firing records one schedule.fired event that handlers route to agents. 0120
widens the target and event source checks."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.events import processor as event_processor, scheduler
from knowledge_admin.events.schedule_event import fire
from test_api import _client
from test_event_processor import a_handler, runs as handler_runs
from test_jira import setup
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0120_schedule_emit_event.up.sql', '0120_schedule_emit_event.down.sql'
EMIT = {'target_kind': 'event.emit', 'name': 'Morning summary', 'enabled': True, 'cron': '0 6 * * *', 'timezone': 'UTC'}


def error(response) -> str:
    return response.json()['error']['message']


def migrate(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def create(client, **changes) -> dict:
    response = client.post('/api/schedules', headers=HEADERS, json={**EMIT, **changes})
    assert response.status_code == 201, response.text
    return response.json()['schedule']


def listed(client, schedule: dict) -> dict:
    [row] = [s for s in client.get('/api/schedules', headers=HEADERS).json()['schedules'] if s['id'] == schedule['id']]
    return row


def schedule_events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute("SELECT * FROM ka.events WHERE source='schedule' ORDER BY id").fetchall()


def schedule_row(store, schedule_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.schedules WHERE id=%s', (schedule_id,)).fetchone()


def due(store, schedule_id):
    """Make the schedule due; returns the slot it is due for (the firing's identity)."""
    with store.connect() as conn:
        return conn.execute(
            "UPDATE ka.schedules SET next_run_at=now()-interval '1 minute' WHERE id=%s RETURNING next_run_at",
            (schedule_id,),
        ).fetchone()['next_run_at']


def successes(store, schedule_id) -> list:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT started_at FROM ka.schedule_runs WHERE schedule_id=%s AND status='succeeded' ORDER BY id",
            (schedule_id,),
        ).fetchall()
    return [r['started_at'].isoformat() for r in rows]


def test_a_firing_records_one_event_with_the_payload(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    fired_at = due(store, schedule['id']).isoformat()
    assert scheduler.run_once(store) is True
    [event] = schedule_events(store)
    saved = schedule_row(store, schedule['id'])
    assert (event['source'], str(event['source_id']), event['type'], event['subject'], event['title']) == (
        'schedule',
        schedule['id'],
        'schedule.fired',
        'Morning summary',
        'Morning summary',
    )
    assert event['payload'] == {
        'schedule_id': schedule['id'],
        'name': 'Morning summary',
        'fired_at': fired_at,
        'last_run_at': None,
        'trigger': 'scheduled',
    }
    assert event['dedupe_key'] == f'schedule:{schedule["id"]}:{fired_at}' and event['status'] == 'pending'
    assert event['occurred_at'].isoformat() == fired_at
    assert (saved['last_status'], saved['last_message']) == ('succeeded', f'Recorded event {event["id"]}.')


def test_run_now_is_manual_and_last_run_at_is_the_previous_successful_firing(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    due(store, schedule['id'])
    scheduler.run_once(store)
    [first_success] = successes(store, schedule['id'])
    requested = client.post(f'/api/schedules/{schedule["id"]}/run', headers=HEADERS)
    assert requested.status_code == 200
    slot = schedule_row(store, schedule['id'])['next_run_at'].isoformat()
    scheduler.run_once(store)
    [_, second] = schedule_events(store)
    assert second['payload']['trigger'] == 'manual'
    assert second['payload']['last_run_at'] == first_success
    assert second['payload']['fired_at'] == slot
    history = client.get(f'/api/schedules/{schedule["id"]}/runs', headers=HEADERS).json()['runs']
    assert [r['trigger'] for r in history] == ['manual', 'schedule']


def test_last_run_at_skips_a_failed_run_and_ignores_the_current_one(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    due(store, schedule['id'])
    scheduler.run_once(store)  # succeeds
    [success] = successes(store, schedule['id'])
    due(store, schedule['id'])
    scheduler.run_once(store, {'event.emit': lambda store, s: ('failed', 'down')})
    due(store, schedule['id'])
    scheduler.run_once(store)
    first, last = schedule_events(store)
    assert first['payload']['last_run_at'] is None
    assert last['payload']['last_run_at'] == success
    assert schedule_row(store, schedule['id'])['last_status'] == 'succeeded'


def test_a_firing_claimed_again_after_its_lease_ran_out_records_one_event(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    due(store, schedule['id'])
    stale = scheduler.claim(store)
    first = fire(store, stale)  # the worker dies before finish
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.schedules SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (schedule['id'],)
        )
    fresh = scheduler.claim(store)
    assert fresh is not None and fresh['last_run_at'] != stale['last_run_at']
    again = fire(store, fresh)
    [event] = schedule_events(store)
    assert first == again == ('succeeded', f'Recorded event {event["id"]}.')


def test_a_handler_bound_to_the_schedule_starts_one_run_and_ignores_other_schedules(tester):
    t = tester
    mine, other = create(t.client), create(t.client, name='Nightly check')
    handler = a_handler(
        t.store,
        name='Morning summary',
        type_pattern='schedule.fired',
        conditions=Jsonb([{'field': 'payload.schedule_id', 'op': 'equals', 'value': mine['id']}]),
        action='agent',
        emit=None,
        agent_id=t.agent,
        prompt='Summarise since {{ event.payload.last_run_at }}',
    )
    for schedule in (mine, other):
        due(t.store, schedule['id'])
        assert scheduler.run_once(t.store) is True
    while event_processor.run_once(t.store):
        pass
    by_schedule = {e['payload']['schedule_id']: e for e in schedule_events(t.store)}
    [run] = handler_runs(t.store, by_schedule[mine['id']]['id'])
    assert (run['handler_id'], run['status'], run['message']) == (
        handler['id'],
        'succeeded',
        'Started a run of Tester.',
    )
    assert handler_runs(t.store, by_schedule[other['id']]['id']) == []
    with t.store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_sessions WHERE purpose='event'").fetchone()['n'] == 1


def test_create_edit_and_delete_an_emit_schedule(store, tmp_path):
    client = _client(tmp_path, repository_store=store)  # no Jira settings needed
    schedule = create(client)
    assert schedule['target_id'] == schedule['id'] and schedule['target_kind'] == 'event.emit'
    assert schedule['name'] == 'Morning summary' and schedule['params'] == {} and schedule['next_run_at'] is not None
    second = create(client, name='Morning summary')  # each emit schedule is its own target
    assert second['id'] != schedule['id']
    saved = client.put(
        f'/api/schedules/{schedule["id"]}',
        headers=HEADERS,
        json={'cron': '0 7 * * *', 'timezone': 'UTC', 'enabled': False, 'revision': 1, 'name': '  Daily digest  '},
    )
    assert saved.status_code == 200, saved.text
    row = saved.json()['schedule']
    assert (row['name'], row['cron'], row['enabled'], row['target_id']) == (
        'Daily digest',
        '0 7 * * *',
        False,
        schedule['id'],
    )
    assert client.delete(f'/api/schedules/{schedule["id"]}', headers=HEADERS).status_code == 204
    assert [s['id'] for s in client.get('/api/schedules', headers=HEADERS).json()['schedules']] == [second['id']]


def test_emit_schedules_need_a_name_and_take_no_params(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for body in [
        {k: v for k, v in EMIT.items() if k != 'name'},
        {**EMIT, 'name': '   '},
        {**EMIT, 'params': {'jql': 'project = X'}},
    ]:
        assert client.post('/api/schedules', headers=HEADERS, json=body).status_code in (400, 422), body
    schedule = create(client)
    value = {'cron': '0 6 * * *', 'timezone': 'UTC', 'enabled': True, 'revision': 1}
    refused = client.put(f'/api/schedules/{schedule["id"]}', headers=HEADERS, json={**value, 'params': {'limit': 3}})
    assert refused.status_code == 400 and error(refused) == 'Emit event schedules take no settings.'
    for name in ('', '   '):
        blank = client.put(f'/api/schedules/{schedule["id"]}', headers=HEADERS, json={**value, 'name': name})
        assert blank.status_code == 400 and error(blank) == 'Enter a name.', name


def test_a_blank_emit_schedule_name_is_refused_with_enter_a_name(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for body in [{k: v for k, v in EMIT.items() if k != 'name'}, {**EMIT, 'name': ''}, {**EMIT, 'name': '   '}]:
        refused = client.post('/api/schedules', headers=HEADERS, json=body)
        assert refused.status_code == 400 and error(refused) == 'Enter a name.', body
    assert create(client, name='  Morning summary ')['name'] == 'Morning summary'


def test_jira_pull_schedules_keep_their_name(store, tmp_path):
    client, config = setup(store, tmp_path)
    body = {'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True}
    named = client.post('/api/schedules', headers=HEADERS, json={**body, 'name': 'Mine'})
    assert named.status_code == 400 and error(named) == 'The Jira pull schedule cannot be renamed.'
    schedule = client.post('/api/schedules', headers=HEADERS, json=body).json()['schedule']
    assert (schedule['name'], schedule['target_id']) == ('Jira auto pull', config['id'])
    assert 'handlers' not in listed(client, schedule)
    renamed = client.put(
        f'/api/schedules/{schedule["id"]}',
        headers=HEADERS,
        json={'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True, 'revision': 1, 'name': 'Mine'},
    )
    assert renamed.status_code == 400 and error(renamed) == 'The Jira pull schedule cannot be renamed.'


def test_the_list_shows_the_handlers_bound_to_each_emit_schedule(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule, other = create(client), create(client, name='Nightly check')
    assert listed(client, schedule)['handlers'] == []

    def bound(name, schedule_id=schedule['id'], **values):
        return a_handler(
            store,
            name=name,
            type_pattern=values.pop('type_pattern', 'schedule.fired'),
            conditions=Jsonb(
                [{'field': 'payload.schedule_id', 'op': 'equals', 'value': schedule_id}, *values.pop('more', [])]
            ),
            **values,
        )

    first = bound('Summary')
    assert listed(client, schedule)['handlers'] == [{'id': str(first['id']), 'name': 'Summary', 'enabled': True}]
    wildcard = bound(
        'Any schedule', type_pattern='schedule.*', more=[{'field': 'title', 'op': 'exists', 'value': None}]
    )
    disabled = bound('Paused', enabled=False)
    bound('Wrong type', type_pattern='jira.ticket.*')
    bound('Other schedule', schedule_id=other['id'])
    a_handler(store, name='No condition', type_pattern='schedule.fired')
    assert listed(client, schedule)['handlers'] == [
        {'id': str(wildcard['id']), 'name': 'Any schedule', 'enabled': True},
        {'id': str(disabled['id']), 'name': 'Paused', 'enabled': False},
        {'id': str(first['id']), 'name': 'Summary', 'enabled': True},
    ]
    assert [h['name'] for h in listed(client, other)['handlers']] == ['Other schedule']
    client.delete(f'/api/schedules/{schedule["id"]}', headers=HEADERS)
    with store.connect() as conn:  # deleting a schedule leaves its handlers
        assert conn.execute('SELECT count(*) AS n FROM ka.event_handlers').fetchone()['n'] == 6


def test_the_migration_allows_emit_schedules_and_schedule_events_and_down_removes_them(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        schedule_id = uuid4()
        conn.execute(
            """INSERT INTO ka.schedules(id,project_id,name,target_kind,target_id,cron)
            VALUES (%s,%s,'Morning','event.emit',%s,'0 6 * * *')""",
            (schedule_id, project, schedule_id),
        )
        conn.execute(
            """INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron)
            VALUES (%s,'Jira auto pull','jira.pull',%s,'0 * * * *')""",
            (project, uuid4()),
        )
        conn.execute(
            """INSERT INTO ka.events(project_id,source,source_id,type,subject,payload,dedupe_key,occurred_at)
            VALUES (%s,'schedule',%s,'schedule.fired','Morning','{}','k1',now())""",
            (project, schedule_id),
        )
        migrate(conn, DOWN)
        kinds = conn.execute('SELECT target_kind FROM ka.schedules').fetchall()
        assert [k['target_kind'] for k in kinds] == ['jira.pull']
        assert conn.execute("SELECT count(*) AS n FROM ka.events WHERE source='schedule'").fetchone()['n'] == 0
    for statement in [
        """INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron)
        VALUES ((SELECT id FROM ka.projects LIMIT 1),'x','event.emit',gen_random_uuid(),'0 6 * * *')""",
        """INSERT INTO ka.events(project_id,source,type,subject,payload,dedupe_key,occurred_at)
        VALUES ((SELECT id FROM ka.projects LIMIT 1),'schedule','schedule.fired','x','{}','k2',now())""",
    ]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            conn.execute(statement)
    with store.connect() as conn:
        migrate(conn, UP)
        conn.execute(
            """INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron)
            VALUES (%s,'Again','event.emit',gen_random_uuid(),'0 6 * * *')""",
            (project,),
        )
