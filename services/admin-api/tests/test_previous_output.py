"""Include the previous output: a handler run records its handler and the schedule at its event chain's root; with the
handler's include_previous_output on, the last valid output of the same handler for the same schedule goes into the
new run's task (never its instructions)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from knowledge_admin.events import previous_output, processor as event_processor
from test_event_processor import a_handler, an_event
from test_output_schemas_api import created
from test_repositories import HEADERS
from test_schedule_emit_event import create as a_schedule
from test_session_output_api import add_event, update
from test_test_sessions import tester  # noqa: F401  (fixture)

COMPARE = 'Compare with it: say what is new, what changed and what is resolved.'


def checker(tester, include=True, type_pattern='schedule.fired') -> dict:
    schema = created(tester.client, name='Health')
    return a_handler(
        tester.store,
        name='Checker',
        type_pattern=type_pattern,
        action='agent',
        emit=None,
        agent_id=tester.agent,
        prompt='Check the server.',
        output_schema_id=schema['id'],
        include_previous_output=include,
    )


def fire(tester, schedule_id=None, **values) -> UUID:
    """One event through the processor; returns the run it started."""
    if schedule_id is not None:
        values = {'source': 'schedule', 'source_id': schedule_id, 'type': 'schedule.fired', 'subject': 'Nightly'}
    event = an_event(tester.store, **values)
    assert event_processor.run_once(tester.store) is True
    with tester.store.connect() as conn:
        return conn.execute('SELECT id FROM ka.agent_sessions WHERE event_id=%s', (event['id'],)).fetchone()['id']


def session(tester, session_id) -> dict:
    with tester.store.connect() as conn:
        return conn.execute(
            """SELECT s.*,i.text AS task FROM ka.agent_sessions s
            JOIN ka.agent_session_inputs i ON i.session_id=s.id AND i.seq=1 WHERE s.id=%s""",
            (session_id,),
        ).fetchone()


def finished(tester, session_id, output) -> None:
    update(tester, session_id, status='waiting', output=output)


def test_the_block_is_compact_json_with_the_run_and_its_schema():
    row = {
        'id': UUID('12345678-aaaa-bbbb-cccc-000000000000'),
        'last_activity_at': datetime(2026, 10, 24, 6, 5, 59, tzinfo=UTC),
        'output': {'server': 'app-1', 'findings': []},
        'schema_name': 'Health',
        'output_schema_version': 2,
    }
    assert previous_output.block(row) == (
        '# Previous run\n'
        'Finished 2026-10-24 06:05 UTC (run 12345678), output schema "Health" v2:\n'
        '{"server":"app-1","findings":[]}\n' + COMPARE
    )


def test_the_json_is_capped_at_16_kb():
    row = {
        'id': uuid4(),
        'last_activity_at': datetime(2026, 10, 24, tzinfo=UTC),
        'output': {'text': 'x' * 20000},
        'schema_name': 'Health',
        'output_schema_version': 1,
    }
    text = previous_output.block(row).splitlines()[2]
    assert text.endswith(' (truncated)')
    assert len(text.removesuffix(' (truncated)').encode()) == 16 * 1024


def test_two_schedules_on_one_handler_keep_separate_previous_outputs_and_the_first_run_adds_nothing(tester):
    handler = checker(tester)
    nightly, hourly = uuid4(), uuid4()
    first = fire(tester, nightly)
    row = session(tester, first)
    assert (row['handler_id'], row['schedule_id'], row['task']) == (handler['id'], nightly, 'Check the server.')
    assert 'previous_output_from' not in row['agent_config']
    finished(tester, first, {'severity': 'nightly'})
    other = fire(tester, hourly)
    assert session(tester, other)['task'] == 'Check the server.'
    finished(tester, other, {'severity': 'hourly'})
    second = fire(tester, nightly)
    row = session(tester, second)
    assert row['task'].startswith('Check the server.\n\n# Previous run\nFinished ')
    assert f'(run {str(first)[:8]}), output schema "Health" v1:\n{{"severity":"nightly"}}\n{COMPARE}' in row['task']
    assert row['agent_config']['previous_output_from'] == str(first)
    assert 'Previous run' not in row['prompt_snapshot']['system_prompt']
    assert 'Previous run' not in row['agent_config']['system_prompt']['content']


def test_an_invalid_previous_output_is_skipped_for_the_last_valid_one(tester):
    checker(tester)
    nightly = uuid4()
    valid = fire(tester, nightly)
    finished(tester, valid, {'severity': 'low'})
    invalid = fire(tester, nightly)
    update(tester, invalid, status='failed', output_error={'errors': ['bad'], 'raw': '{}'})
    row = session(tester, fire(tester, nightly))
    assert row['agent_config']['previous_output_from'] == str(valid)
    assert '{"severity":"low"}' in row['task']


def test_a_run_outside_a_schedule_uses_the_handler_only_key(tester):
    checker(tester, type_pattern='*')
    scheduled = fire(tester, uuid4())
    finished(tester, scheduled, {'severity': 'scheduled'})
    first = fire(tester)  # a Jira event
    assert session(tester, first)['schedule_id'] is None
    assert session(tester, first)['task'] == 'Check the server.'
    finished(tester, first, {'severity': 'jira'})
    row = session(tester, fire(tester))
    assert row['agent_config']['previous_output_from'] == str(first)
    assert '{"severity":"jira"}' in row['task']


def test_a_run_from_an_emitted_event_takes_the_schedule_from_the_chain_root(tester):
    checker(tester, type_pattern='health.requested')
    root = an_event(tester.store, source='schedule', source_id=uuid4(), type='schedule.fired', subject='Nightly')
    emitter = a_handler(tester.store, name='Emitter')
    event_processor.run_once(tester.store)
    run_id = fire(
        tester,
        source='handler',
        source_id=emitter['id'],
        handler_id=emitter['id'],
        type='health.requested',
        parent_id=root['id'],
        root_id=root['id'],
        depth=1,
    )
    assert session(tester, run_id)['schedule_id'] == root['source_id']


def test_without_the_switch_nothing_is_added(tester):
    checker(tester, include=False)
    nightly = uuid4()
    finished(tester, fire(tester, nightly), {'severity': 'low'})
    row = session(tester, fire(tester, nightly))
    assert row['task'] == 'Check the server.' and 'previous_output_from' not in row['agent_config']


def test_the_handler_api_saves_the_switch(tester):
    body = {
        'name': 'Checker',
        'type_pattern': 'schedule.fired',
        'action': 'agent',
        'agent_id': tester.agent,
        'prompt': 'Check',
    }
    response = tester.client.post('/api/event-handlers', headers=HEADERS, json=body)
    assert response.json()['handler']['include_previous_output'] is False
    handler = response.json()['handler']
    changed = tester.client.put(
        f'/api/event-handlers/{handler["id"]}',
        headers=HEADERS,
        json={**body, 'include_previous_output': True, 'revision': handler['revision']},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()['handler']['include_previous_output'] is True
    listed = tester.client.get('/api/event-handlers', headers=HEADERS).json()['handlers']
    assert listed[0]['include_previous_output'] is True
    emit = {'name': 'Emit', 'type_pattern': '*', 'action': 'emit', 'emit': {'type': 'a.b'}}
    refused = tester.client.post('/api/event-handlers', headers=HEADERS, json={**emit, 'include_previous_output': True})
    assert refused.json()['error']['message'] == 'Only Send to agent handlers include the previous output.'


def test_the_run_page_names_the_schedule_the_handler_and_the_previous_run(tester):
    schedule = a_schedule(tester.client, name='Nightly check')
    handler = checker(tester)
    first = fire(tester, UUID(schedule['id']))
    finished(tester, first, {'severity': 'low'})
    second = fire(tester, UUID(schedule['id']))
    detail = tester.client.get(f'/api/sessions/{second}', headers=HEADERS).json()
    assert detail['started_by'] == {
        'schedule_id': schedule['id'],
        'schedule_name': 'Nightly check',
        'handler_id': str(handler['id']),
        'handler_name': 'Checker',
    }
    assert detail['previous_output']['session_id'] == str(first)
    assert detail['previous_output']['finished_at']
    with tester.store.connect() as conn:
        conn.execute('DELETE FROM ka.event_handlers WHERE id=%s', (handler['id'],))
        conn.execute('DELETE FROM ka.schedules WHERE id=%s', (schedule['id'],))
    gone = tester.client.get(f'/api/sessions/{second}', headers=HEADERS).json()['started_by']
    assert (gone['schedule_name'], gone['handler_name'], gone['handler_id']) == (None, None, str(handler['id']))
    plain = tester.client.post('/api/sessions', headers=HEADERS, json={'message': 'Hi', 'agent_id': tester.agent})
    detail = tester.client.get(f'/api/sessions/{plain.json()["id"]}', headers=HEADERS).json()
    assert detail['started_by'] is None and detail['previous_output'] is None


def test_the_runs_list_filters_by_schedule(tester):
    checker(tester)
    nightly, hourly = uuid4(), uuid4()
    runs = {fire(tester, nightly), fire(tester, hourly)}
    for run_id in runs:
        add_event(tester, run_id, 1, 1, 'start', {'agent': 'A', 'kind': 'event_session', 'title': 't'})

    def listed(query):
        found = tester.client.get(f'/api/agent-runs?{query}', headers=HEADERS).json()['runs']
        return {UUID(run['run_key'].split(':')[1]) for run in found}

    nightly_run = session_for(tester, nightly)
    assert listed(f'schedule_id={nightly}') == {nightly_run}
    assert listed('') == runs
    assert listed(f'schedule_id={uuid4()}') == set()


def session_for(tester, schedule_id) -> UUID:
    with tester.store.connect() as conn:
        return conn.execute('SELECT id FROM ka.agent_sessions WHERE schedule_id=%s', (schedule_id,)).fetchone()['id']


def test_the_time_is_the_last_activity_not_a_later_update(tester):
    schedule = a_schedule(tester.client, name='Nightly check')
    checker(tester)
    first = fire(tester, UUID(schedule['id']))
    update(tester, first, status='waiting', output={'severity': 'low'}, last_activity_at='2026-10-23T06:05:00Z')
    update(tester, first, status='closed', closed_at=datetime(2026, 10, 24, 6, 0, tzinfo=UTC))  # bumps updated_at
    with tester.store.connect() as conn:
        row = conn.execute('SELECT updated_at,last_activity_at FROM ka.agent_sessions WHERE id=%s', (first,)).fetchone()
    assert row['updated_at'] > row['last_activity_at']
    second = fire(tester, UUID(schedule['id']))
    assert f'Finished 2026-10-23 06:05 UTC (run {str(first)[:8]})' in session(tester, second)['task']
    detail = tester.client.get(f'/api/sessions/{second}', headers=HEADERS).json()
    assert datetime.fromisoformat(detail['previous_output']['finished_at']) == datetime(2026, 10, 23, 6, 5, tzinfo=UTC)
