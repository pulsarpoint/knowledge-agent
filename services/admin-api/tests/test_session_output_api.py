"""Reading a run's output: GET /api/sessions/{id}/output and /status, POST /api/sessions?wait= and the runs list filter
has_output."""

import threading
import time

from psycopg.types.json import Jsonb

from knowledge_admin.sessions import run_status, start as start_module
from run_helpers import session_row
from test_output_schemas_api import SCHEMA, created
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

ERRORS = {'errors': ["$.severity: 1 is not of type 'string'"], 'raw': '{"severity": 1}'}


# --- pure mapping -------------------------------------------------------------------------------------------------


def test_session_statuses_map_to_client_statuses():
    def status(value, schema=True, output=False):
        return run_status.client_status(value, has_schema=schema, has_output=output)

    assert [status(value) for value in ('starting', 'working', 'failed', 'stopped')] == [
        'queued',
        'running',
        'failed',
        'failed',
    ]
    assert status('waiting') == 'finished' and status('waiting', schema=False) == 'waiting'
    assert status('closed', output=True) == 'finished' and status('closed') == 'failed'
    assert status('closed', schema=False) == 'finished'


# --- endpoints ----------------------------------------------------------------------------------------------------


def start(tester, schema=True, **body):
    values = {'message': 'Triage DEMO-1.', 'agent_id': tester.agent, **body}
    if schema:
        values['output_schema_id'] = created(tester.client)['id']
    response = tester.client.post('/api/sessions', headers=HEADERS, json=values)
    assert response.status_code == 201, response.text
    return response.json()['id']


def update(tester, session_id, **columns):
    sets = ','.join(f'{name}=%s' for name in columns)
    values = [Jsonb(v) if isinstance(v, (dict, list)) else v for v in columns.values()]
    with tester.store.connect() as conn:
        conn.execute(f'UPDATE ka.agent_sessions SET {sets} WHERE id=%s', (*values, session_id))


def add_event(tester, session_id, turn, seq, kind, payload):
    with tester.store.connect() as conn:
        project = tester.store.project(conn, 'shop')
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload) VALUES (%s,%s,%s,%s,%s,%s)""",
            (project, f'cc:{session_id}:{turn}', seq, kind, tester.agent, Jsonb(payload)),
        )


def get(tester, session_id, path):
    return tester.client.get(f'/api/sessions/{session_id}/{path}', headers=HEADERS)


def test_output_of_a_finished_run(tester):
    session_id = start(tester)
    update(tester, session_id, status='working', turns=1)
    refused = get(tester, session_id, 'output')
    assert (refused.status_code, refused.json()['error']['message']) == (409, 'The run has not finished.')
    update(tester, session_id, status='waiting', output={'severity': 'high'})
    body = get(tester, session_id, 'output').json()
    schema = body['schema']
    assert (schema['name'], schema['version']) == ('Triage', 1) and schema['id']
    assert (body['output'], body['valid']) == ({'severity': 'high'}, True)
    assert 'errors' not in body and 'raw' not in body


def test_output_of_a_failed_run_has_its_errors_and_raw_answer(tester):
    session_id = start(tester)
    update(tester, session_id, status='failed', status_message='Output did not match the schema', output_error=ERRORS)
    body = get(tester, session_id, 'output').json()
    assert (body['output'], body['valid'], body['errors'], body['raw']) == (
        None,
        False,
        ERRORS['errors'],
        ERRORS['raw'],
    )


def test_output_of_a_run_that_failed_without_an_answer_says_why(tester):
    session_id = start(tester)
    update(tester, session_id, status='failed', status_message='The model is not reachable.')
    body = get(tester, session_id, 'output').json()
    assert (body['output'], body['valid'], body['error']) == (None, False, 'The model is not reachable.')
    update(tester, session_id, status='stopped', status_message='')
    assert get(tester, session_id, 'output').json()['error'] == 'Stopped.'


def test_the_output_is_ready_only_while_the_run_is_finished_or_failed(tester):
    session_id = start(tester)
    update(tester, session_id, status='working', turns=2, output={'severity': 'high'})  # a follow-up turn runs
    assert get(tester, session_id, 'status').json()['output_ready'] is False
    update(tester, session_id, status='failed', status_message='Crashed.')  # the follow-up failed, the output stays
    failed = get(tester, session_id, 'status').json()
    assert (failed['status'], failed['output_ready']) == ('failed', True)
    update(tester, session_id, status='failed', output=None)
    assert get(tester, session_id, 'status').json()['output_ready'] is False


def test_output_of_a_run_without_a_schema_is_not_found(tester):
    session_id = start(tester, schema=False)
    response = get(tester, session_id, 'output')
    assert (response.status_code, response.json()['error']['message']) == (404, run_status.NO_SCHEMA)
    missing = get(tester, '00000000-0000-0000-0000-000000000001', 'output')
    assert missing.status_code == 404


def test_status_reports_progress_and_the_step(tester):
    session_id = start(tester)
    queued = get(tester, session_id, 'status').json()
    assert queued['status'] == 'queued' and queued['output_ready'] is False
    assert queued['progress'] == {'turns': 0, 'tool_calls': 0, 'step': None}
    update(tester, session_id, status='working', turns=1)
    assert get(tester, session_id, 'status').json()['progress']['step'] == 'thinking'
    add_event(tester, session_id, 1, 1, 'tool_call', {'name': 'read_file', 'call_id': 'a'})
    add_event(tester, session_id, 1, 2, 'tool_result', {'name': 'read_file', 'call_id': 'a'})
    add_event(tester, session_id, 1, 3, 'tool_call', {'name': 'search_docs', 'call_id': 'b'})
    running = get(tester, session_id, 'status').json()
    assert running['status'] == 'running'
    assert running['progress'] == {'turns': 1, 'tool_calls': 2, 'step': 'search_docs'}
    update(tester, session_id, status='waiting', output={'severity': 'high'})
    done = get(tester, session_id, 'status').json()
    assert (done['status'], done['output_ready'], done['progress']['step']) == ('finished', True, None)
    assert done['started_at'] and done['last_activity_at'] and 'error' not in done


def test_status_of_a_failed_run_has_its_error(tester):
    session_id = start(tester)
    update(tester, session_id, status='failed', status_message='Output did not match the schema')
    body = get(tester, session_id, 'status').json()
    assert (body['status'], body['error'], body['output_ready']) == ('failed', 'Output did not match the schema', False)
    update(tester, session_id, status='stopped', status_message='')
    assert get(tester, session_id, 'status').json()['error'] == 'Stopped.'
    assert get(tester, '00000000-0000-0000-0000-000000000001', 'status').status_code == 404


# --- run and wait -------------------------------------------------------------------------------------------------


def body_for(tester):
    return {'message': 'Triage DEMO-1.', 'agent_id': tester.agent, 'output_schema_id': created(tester.client)['id']}


def finish_later(tester, delay, **columns):
    """After `delay` seconds, set columns on the newest session (the run that the waiting start made)."""

    def finish():
        time.sleep(delay)
        with tester.store.connect() as conn:
            row = conn.execute('SELECT id FROM ka.agent_sessions ORDER BY created_at DESC LIMIT 1').fetchone()
        update(tester, row['id'], **columns)

    thread = threading.Thread(target=finish)
    thread.start()
    return thread


def test_the_wait_is_bounded_to_1_to_120_seconds(tester):
    body = body_for(tester)
    for wait in (0, 121):
        response = tester.client.post(f'/api/sessions?wait={wait}', headers=HEADERS, json=body)
        assert response.status_code == 400


def test_a_start_that_waits_answers_with_the_output_when_the_run_finishes(tester):
    thread = finish_later(tester, 0.7, status='waiting', output={'severity': 'high'})
    response = tester.client.post('/api/sessions?wait=10', headers=HEADERS, json=body_for(tester))
    thread.join()
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['output']) == ('finished', {'severity': 'high'})
    assert body['id'] == str(session_row(tester.store, body['id'])['id'])


def test_a_start_that_waits_answers_with_the_failure(tester):
    thread = finish_later(
        tester, 0.7, status='failed', status_message='Output did not match the schema', output_error=ERRORS
    )
    response = tester.client.post('/api/sessions?wait=10', headers=HEADERS, json=body_for(tester))
    thread.join()
    body = response.json()
    assert response.status_code == 200
    assert (body['status'], body['output'], body['error']) == ('failed', None, 'Output did not match the schema')
    assert (body['errors'], body['raw']) == (ERRORS['errors'], ERRORS['raw'])


def test_a_start_that_waits_too_long_answers_202_and_status_follows(tester):
    response = tester.client.post('/api/sessions?wait=1', headers=HEADERS, json=body_for(tester))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body['status'] == 'running' and set(body) == {'id', 'status'}
    update(tester, body['id'], status='working', turns=1)
    assert get(tester, body['id'], 'status').json()['status'] == 'running'
    update(tester, body['id'], status='waiting', output={'severity': 'low'})
    ready = get(tester, body['id'], 'status').json()
    assert (ready['status'], ready['output_ready']) == ('finished', True)
    assert get(tester, body['id'], 'output').json()['output'] == {'severity': 'low'}


def ended_at_start(monkeypatch, **columns):
    """Make the start's own transaction end the run, so the wait's first look sees it closed."""
    original = start_module.start_run

    def start_run(conn, *args, **kwargs):
        session_id = original(conn, *args, **kwargs)
        sets = ','.join(f'{name}=%s' for name in columns)
        values = [Jsonb(v) if isinstance(v, (dict, list)) else v for v in columns.values()]
        conn.execute(f'UPDATE ka.agent_sessions SET {sets} WHERE id=%s', (*values, session_id))
        return session_id

    monkeypatch.setattr(start_module, 'start_run', start_run)


def test_a_wait_on_a_run_finished_at_start_answers_at_once(tester, monkeypatch):
    ended_at_start(monkeypatch, status='waiting', output={'severity': 'high'})
    began = time.monotonic()
    response = tester.client.post('/api/sessions?wait=10', headers=HEADERS, json=body_for(tester))
    assert time.monotonic() - began < 5
    assert response.status_code == 200, response.text
    assert (response.json()['status'], response.json()['output']) == ('finished', {'severity': 'high'})


def test_a_wait_on_a_run_failed_at_start_answers_with_the_failure(tester, monkeypatch):
    ended_at_start(monkeypatch, status='failed', status_message='No model.')
    response = tester.client.post('/api/sessions?wait=10', headers=HEADERS, json=body_for(tester))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['output'], body['error']) == ('failed', None, 'No model.')


def test_a_wait_on_a_vanished_run_answers_202_when_the_time_is_up(tester, monkeypatch):
    monkeypatch.setattr(run_status, 'run_row', lambda *args: None)
    response = tester.client.post('/api/sessions?wait=1', headers=HEADERS, json=body_for(tester))
    assert response.status_code == 202, response.text
    assert response.json()['status'] == 'running'


def test_a_start_without_wait_answers_at_once_as_before(tester):
    response = tester.client.post('/api/sessions', headers=HEADERS, json=body_for(tester))
    assert response.status_code == 201 and response.json()['status'] == 'starting'


# --- runs list ----------------------------------------------------------------------------------------------------


def test_the_runs_list_filters_by_output(tester):
    with_output, without = start(tester), start(tester, schema=False)
    update(tester, with_output, output={'severity': 'high'})
    for session_id in (with_output, without):
        add_event(tester, session_id, 1, 1, 'start', {'agent': 'A', 'kind': 'agent_session', 'title': 't'})

    def listed(query):
        runs = tester.client.get(f'/api/agent-runs?{query}', headers=HEADERS).json()['runs']
        return {run['run_key'].split(':')[1] for run in runs}

    assert listed('has_output=true') == {with_output}
    assert listed('has_output=false') == {without}
    assert listed('') == {with_output, without}
