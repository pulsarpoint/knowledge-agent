"""Handlers bind to an emit schedule by its name (the schedule.fired event's subject): names are unique per project
(0121) and renaming a schedule rewrites its handlers' subject condition in the same transaction."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.events.matching import type_matches
from knowledge_admin.events.schedules_api import bound
from test_api import _client
from test_event_processor import a_handler
from test_jira import setup
from test_repositories import HEADERS
from test_schedule_emit_event import EMIT, create, error, listed

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0121_schedule_names.up.sql', '0121_schedule_names.down.sql'
TAKEN = 'A schedule with this name already exists.'
RESERVED = 'This name is reserved for the Jira pull schedule.'


def subject(name: str) -> dict:
    return {'field': 'subject', 'op': 'equals', 'value': name}


def by_id(schedule_id) -> dict:
    return {'field': 'payload.schedule_id', 'op': 'equals', 'value': str(schedule_id)}


def handler(store, name: str, conditions: list, type_pattern='schedule.fired') -> dict:
    return a_handler(store, name=name, type_pattern=type_pattern, conditions=Jsonb(conditions))


def handler_row(store, handler_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.event_handlers WHERE id=%s', (handler_id,)).fetchone()


def edit(client, schedule: dict, **changes):
    value = {'cron': schedule['cron'], 'timezone': 'UTC', 'enabled': True, 'revision': schedule['revision'], **changes}
    return client.put(f'/api/schedules/{schedule["id"]}', headers=HEADERS, json=value)


def migrate(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_bound_takes_the_schedule_fired_type_and_the_subject_condition():
    def h(pattern, *conditions):
        return {'type_pattern': pattern, 'conditions': list(conditions)}

    assert bound(h('schedule.fired', subject('Morning')), 'Morning')
    assert bound(h('schedule.*', {'field': 'title', 'op': 'exists', 'value': None}, subject('Morning')), 'Morning')
    assert bound(h('*', subject('Morning')), 'Morning')
    assert not bound(h('schedule.fired', subject('Nightly')), 'Morning')
    assert not bound(h('jira.ticket.*', subject('Morning')), 'Morning')
    assert not bound(h('schedule.fired', {'field': 'subject', 'op': 'contains', 'value': 'Morning'}), 'Morning')
    assert not bound(h('schedule.fired', by_id(uuid4())), 'Morning')


def test_names_are_unique_on_create_and_rename(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    morning, nightly = create(client), create(client, name='Nightly check')
    taken = client.post('/api/schedules', headers=HEADERS, json={**EMIT, 'name': ' Morning summary '})
    assert taken.status_code == 409 and error(taken) == TAKEN
    renamed = edit(client, nightly, name='Morning summary')
    assert renamed.status_code == 409 and error(renamed) == TAKEN
    assert edit(client, morning, name='Morning summary').status_code == 200  # its own name is fine
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        conn.execute("UPDATE ka.schedules SET name='Morning summary' WHERE id=%s", (nightly['id'],))


def test_an_emit_schedule_cannot_take_the_jira_pull_name(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for name in ('Jira auto pull', ' jira AUTO pull '):
        refused = client.post('/api/schedules', headers=HEADERS, json={**EMIT, 'name': name})
        assert refused.status_code == 400 and error(refused) == RESERVED, name
    schedule = create(client)
    renamed = edit(client, schedule, name='JIRA AUTO PULL')
    assert renamed.status_code == 400 and error(renamed) == RESERVED
    pull = client.post(
        '/api/schedules',
        headers=HEADERS,
        json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True},
    )
    assert pull.status_code == 201 and pull.json()['schedule']['name'] == 'Jira auto pull'


def test_the_migration_pattern_list_is_every_pattern_that_takes_schedule_fired():
    sql = (MIGRATIONS / UP).read_text()
    listed = set(sql.split('h.type_pattern IN (')[1].split(')')[0].replace("'", '').replace(' ', '').split(','))
    candidates = ['*', '*.*', '*.*.*', 'schedule.*', '*.fired', 'schedule.fired', 'schedule.fired.*', 'schedule.*.*']
    candidates += ['*.fired.*', '*.*.fired', 'jira.*', 'jira.ticket.*', 'schedule.ran', '*.ran', 'schedule', 'fired']
    assert {p for p in candidates if type_matches(p, 'schedule.fired')} == listed
    assert listed == {'*', '*.*', 'schedule.*', '*.fired', 'schedule.fired'}


def test_renaming_rewrites_the_bound_handlers_and_leaves_the_others(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    create(client, name='Nightly check')
    extra = {'field': 'payload.trigger', 'op': 'equals', 'value': 'scheduled'}
    first = handler(store, 'Summary', [extra, subject('Morning summary')])
    wildcard = handler(store, 'Any schedule', [subject('Morning summary')], type_pattern='schedule.*')
    other = handler(store, 'Nightly', [subject('Morning summary')], type_pattern='jira.ticket.*')
    nightly = handler(store, 'Other schedule', [subject('Nightly check')])
    saved = edit(client, schedule, name='Daily digest')
    assert saved.status_code == 200, saved.text
    assert saved.json()['handlers_updated'] == 2
    assert handler_row(store, first['id'])['conditions'] == [extra, subject('Daily digest')]
    assert handler_row(store, first['id'])['revision'] == first['revision'] + 1
    assert handler_row(store, wildcard['id'])['conditions'] == [subject('Daily digest')]
    assert handler_row(store, other['id'])['conditions'] == [subject('Morning summary')]
    assert handler_row(store, nightly['id'])['conditions'] == [subject('Nightly check')]
    assert [h['name'] for h in listed(client, schedule)['handlers']] == ['Any schedule', 'Summary']
    timing = edit(client, saved.json()['schedule'], cron='0 7 * * *')  # no rename, nothing rewritten
    assert timing.status_code == 200 and timing.json()['handlers_updated'] == 0


def test_a_refused_rename_rewrites_no_handler(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    schedule = create(client)
    create(client, name='Nightly check')
    first = handler(store, 'Summary', [subject('Morning summary')])
    assert edit(client, schedule, name='Nightly check').status_code == 409
    assert handler_row(store, first['id'])['conditions'] == [subject('Morning summary')]


def test_events_filter_by_exact_subject(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for name in ('Morning', 'Morning summary', 'Morning'):
            conn.execute(
                """INSERT INTO ka.events(project_id,source,type,subject,payload,dedupe_key,occurred_at)
                VALUES (%s,'schedule','schedule.fired',%s,'{}',%s,now())""",
                (project, name, f'k{uuid4()}'),
            )
    found = client.get('/api/events', headers=HEADERS, params={'source': 'schedule', 'subject': 'Morning'}).json()
    assert [e['subject'] for e in found['events']] == ['Morning', 'Morning']


def test_facets_list_the_emit_schedules_by_name(store, tmp_path):
    client, _ = setup(store, tmp_path)
    create(client, name='Nightly check')
    create(client)
    client.post(
        '/api/schedules',
        headers=HEADERS,
        json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True},
    )
    assert client.get('/api/events/facets', headers=HEADERS).json()['schedules'] == [
        'Morning summary',
        'Nightly check',
    ]


def a_schedule(conn, project, name: str, created: str, kind='event.emit') -> object:
    schedule_id = uuid4()
    conn.execute(
        """INSERT INTO ka.schedules(id,project_id,name,target_kind,target_id,cron,created_at)
        VALUES (%s,%s,%s,%s,%s,'0 6 * * *',%s)""",
        (schedule_id, project, name, kind, schedule_id, created),
    )
    return schedule_id


def conditions(conn, handler_id) -> list:
    return conn.execute('SELECT conditions FROM ka.event_handlers WHERE id=%s', (handler_id,)).fetchone()['conditions']


def test_the_migration_renames_duplicates_and_rebinds_handlers_by_name_and_down_reverts(store):
    with store.connect() as conn:
        migrate(conn, DOWN)
        project = store.project(conn, 'shop')
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()[
            'id'
        ]
        oldest = a_schedule(conn, project, 'Morning', '2026-10-01')
        second = a_schedule(conn, project, 'Morning', '2026-10-02')
        third = a_schedule(conn, project, 'Morning', '2026-10-03')
        elsewhere = a_schedule(conn, other, 'Morning', '2026-10-04')
    extra = {'field': 'payload.trigger', 'op': 'equals', 'value': 'scheduled'}
    to_second = handler(store, 'Second', [extra, by_id(second)])
    to_oldest = handler(store, 'Oldest', [by_id(oldest)], type_pattern='*.fired')
    wrong_type = handler(store, 'Wrong type', [by_id(oldest)], type_pattern='jira.ticket.*')
    gone = handler(store, 'Gone', [by_id(uuid4())])
    with store.connect() as conn:
        migrate(conn, UP)
        names = {r['id']: r['name'] for r in conn.execute('SELECT id,name FROM ka.schedules').fetchall()}
        assert [names[oldest], names[second], names[third], names[elsewhere]] == [
            'Morning',
            'Morning (2)',
            'Morning (3)',
            'Morning',
        ]
        assert conditions(conn, to_second['id']) == [extra, subject('Morning (2)')]
        assert conditions(conn, to_oldest['id']) == [subject('Morning')]
        assert conditions(conn, wrong_type['id']) == [by_id(oldest)]
        assert conditions(conn, gone['id'])[0]['field'] == 'payload.schedule_id'
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        a_schedule(conn, project, 'Morning', '2026-10-05')
    with store.connect() as conn:
        migrate(conn, DOWN)
        assert conditions(conn, to_second['id']) == [extra, by_id(second)]
        assert conditions(conn, to_oldest['id']) == [by_id(oldest)]
        assert conditions(conn, wrong_type['id']) == [by_id(oldest)]
        fourth = a_schedule(conn, project, 'Morning', '2026-10-05')  # names may repeat again
        migrate(conn, UP)  # and up renames that one past the names already taken
        assert conn.execute('SELECT name FROM ka.schedules WHERE id=%s', (fourth,)).fetchone()['name'] == 'Morning (4)'
