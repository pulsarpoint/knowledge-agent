"""Runs started with an output schema: the start pins the schema version ({id, name, version, schema}); the runner gives
the model a JsonSchemaOutput (strict when the model supports structured output, else with the schema in the
instructions); the worker validates the final answer, retries once with the errors, then stores the output and emits
run.output, or fails the run with the errors and the raw answer kept. Chats (the ad-hoc resource and document runs)
take no schema."""

import asyncio
import json

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.agents.runner import JsonSchemaOutput, instructions_and_tools, schema_output
from knowledge_admin.events import processor as event_processor
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.output_schemas import runs as output_runs
from knowledge_admin.sessions import run_record
from native_fakes import FakeAgent, llm, message
from run_helpers import events, session_row, wait_for
from test_event_processor import a_handler, an_event
from test_output_schemas_api import CHANGED, SCHEMA, created, saved
from test_repositories import HEADERS
from test_test_session_worker import claimed
from test_test_sessions import tester  # noqa: F401  (fixture)

VALID = '{"severity": "high"}'
PINNED = {'id': 'schema-1', 'name': 'Triage', 'version': 1, 'schema': SCHEMA}


# --- pure rules ---------------------------------------------------------------------------------------------------


def test_a_valid_answer_is_the_output():
    found = output_runs.verdict(SCHEMA, VALID, retried=False)
    assert (found.kind, found.output, found.errors) == ('valid', {'severity': 'high'}, [])


def test_an_invalid_answer_is_retried_once_with_its_errors_then_fails():
    first = output_runs.verdict(SCHEMA, '{"severity": 1}', retried=False)
    assert first.kind == 'retry'
    assert first.errors == ["$.severity: 1 is not of type 'string'"]
    assert first.message == (
        "Your final answer does not match the output schema: $.severity: 1 is not of type 'string'. "
        'Answer again with JSON only.'
    )
    second = output_runs.verdict(SCHEMA, '{"severity": 1}', retried=True)
    assert (second.kind, second.message, second.errors) == (
        'failed',
        'Output did not match the schema',
        ["$.severity: 1 is not of type 'string'"],
    )


def test_an_answer_that_is_not_json_is_an_error():
    found = output_runs.verdict(SCHEMA, 'High, I think.', retried=False)
    assert found.kind == 'retry' and found.errors[0].startswith('$: the answer is not JSON')


def test_an_answer_in_one_code_fence_is_read_as_its_json():
    for raw in ('```json\n{"severity": "high"}\n```', '```\n{"severity": "high"}\n```'):
        assert output_runs.verdict(SCHEMA, raw, retried=False).output == {'severity': 'high'}


def test_each_error_in_the_retry_message_is_capped():
    schema = {**SCHEMA, 'properties': {'severity': {'type': 'string', 'enum': ['a']}}}
    found = output_runs.verdict(schema, json.dumps({'severity': 'y' * 5000}), retried=False)
    assert len(found.errors[0]) <= output_runs.MAX_ERROR_CHARS + 1 and found.errors[0].endswith('…')


def test_an_output_over_256_kb_fails_without_a_retry():
    raw = json.dumps({'severity': 'x' * output_runs.MAX_OUTPUT_BYTES})
    found = output_runs.verdict(SCHEMA, raw, retried=False)
    assert (found.kind, found.message) == ('failed', 'Output is larger than 256 KB.')


def test_structured_output_is_on_for_openai_style_models_by_default():
    assert output_runs.supports_structured_output({'provider': 'openai'}) is True
    assert output_runs.supports_structured_output({'provider': 'anthropic'}) is False
    assert output_runs.supports_structured_output({'provider': 'bedrock', 'capabilities': {}}) is False
    off = {'provider': 'openai', 'capabilities': {'supports_structured_output': False}}
    on = {'provider': 'anthropic', 'capabilities': {'supports_structured_output': True}}
    assert (output_runs.supports_structured_output(off), output_runs.supports_structured_output(on)) == (False, True)


def test_the_output_type_is_the_pinned_schema_strict_only_with_structured_output():
    strict = schema_output({'provider': 'openai'}, PINNED)
    loose = schema_output({'provider': 'anthropic'}, PINNED)
    assert isinstance(strict, JsonSchemaOutput) and schema_output({'provider': 'openai'}, None) is None
    assert (strict.is_strict_json_schema(), loose.is_strict_json_schema()) == (True, False)
    assert (strict.is_plain_text(), strict.name(), strict.json_schema()) == (False, 'Triage', SCHEMA)
    # validate_json never raises: the worker validates the answer, so a mismatch is retried, not a crash
    assert strict.validate_json('{"severity": 1}') == '{"severity": 1}'
    assert strict.validate_json('not json') == 'not json'


def config_for(provider: str) -> dict:
    return {
        'id': 'a1',
        'name': 'Agent',
        'enabled': True,
        'model': {'provider': provider, 'enabled': True},
        'system_prompt': {'content': 'You triage.'},
        'instructions': '',
        'access': {'resources': []},
    }


def test_only_a_model_without_structured_output_gets_the_schema_in_its_instructions():
    block = 'Finish with JSON only, matching this JSON Schema: ' + json.dumps(SCHEMA)
    assert output_runs.fallback_instructions(SCHEMA) == block
    without, *_ = instructions_and_tools(config_for('anthropic'), subagents=True, output_schema=PINNED)
    with_, *_ = instructions_and_tools(config_for('openai'), subagents=True, output_schema=PINNED)
    plain, *_ = instructions_and_tools(config_for('anthropic'), subagents=True)
    assert without.endswith('\n\n' + block)
    assert block not in with_ and block not in plain


# --- start --------------------------------------------------------------------------------------------------------


def start(client, **body):
    return client.post('/api/sessions', headers=HEADERS, json={'message': 'Triage DEMO-1.', **body})


def test_a_saved_agent_run_pins_the_schema_version(tester):
    schema = created(tester.client)
    response = start(tester.client, agent_id=tester.agent, output_schema_id=schema['id'])
    assert response.status_code == 201, response.text
    pinned = {'id': schema['id'], 'name': 'Triage', 'version': 1, 'schema': SCHEMA}
    assert response.json()['config']['output_schema'] == pinned
    row = session_row(tester.store, response.json()['id'])
    assert (str(row['output_schema_id']), row['output_schema_version'], row['output']) == (schema['id'], 1, None)
    debug = tester.client.get(f'/api/sessions/{row["id"]}/debug', headers=HEADERS).json()
    assert debug['output_schema'] == pinned  # the Debug tab shows the pinned schema
    plain = start(tester.client, agent_id=tester.agent).json()
    assert 'output_schema' not in plain['config']
    assert tester.client.get(f'/api/sessions/{plain["id"]}/debug', headers=HEADERS).json()['output_schema'] is None


def test_a_start_may_name_a_version_and_run_agent_chats_take_a_schema(tester):
    schema = created(tester.client)
    assert saved(tester.client, schema, schema=CHANGED).status_code == 200
    older = start(tester.client, agent_id=tester.agent, output_schema_id=schema['id'], output_schema_version=1)
    assert older.json()['config']['output_schema']['schema'] == SCHEMA
    current = start(tester.client, agent_id=tester.agent, output_schema_id=schema['id'], purpose='chat')
    assert current.status_code == 201, current.text  # Run agent (purpose chat) takes one
    assert current.json()['config']['output_schema']['version'] == 2


def test_chats_and_unknown_schemas_are_refused(tester):
    schema = created(tester.client)
    chat = start(
        tester.client,
        started_from={'document_id': schema['id']},
        model_id=tester.model,
        system_prompt='Revise it.',
        output_schema_id=schema['id'],
    )
    assert chat.status_code == 400
    assert chat.json()['error']['message'].endswith('Chats do not take an output schema.')
    missing = start(tester.client, agent_id=tester.agent, output_schema_id='00000000-0000-0000-0000-000000000000')
    assert (missing.status_code, missing.json()['error']['message']) == (404, 'Output schema not found.')
    version = start(tester.client, agent_id=tester.agent, output_schema_id=schema['id'], output_schema_version=9)
    assert (version.status_code, version.json()['error']['message']) == (404, 'Output schema version not found.')
    alone = start(tester.client, agent_id=tester.agent, output_schema_version=1)
    assert alone.status_code == 400


# --- worker -------------------------------------------------------------------------------------------------------


def run(tester, session_id, fake) -> dict:
    """Run the session's first turn with the scripted fake agent and return the session row after it."""
    store = tester.store

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        done = await wait_for(store, session_id, lambda s: s['status'] in ('waiting', 'failed') and s['turns'] == 1)
        await asyncio.to_thread(tester.client.post, f'/api/sessions/{session_id}/close', headers=HEADERS)
        await asyncio.wait_for(runner, 5)
        return done

    return asyncio.run(scenario())


def answer(text: str) -> dict:
    return {'steps': [llm(message(text))], 'answer': text}


def output_events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute("SELECT * FROM ka.events WHERE type='run.output' ORDER BY id").fetchall()


@pytest.fixture
def worker(tester, monkeypatch):
    monkeypatch.setattr(run_record, '_store', tester.store)
    return tester


def test_the_same_agent_with_and_without_a_schema(worker):
    """Review Focus 1."""
    schema = created(worker.client)
    with_schema = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    fake = FakeAgent([answer(VALID)])
    done = run(worker, with_schema, fake)
    assert (done['status'], done['status_message'], done['output']) == ('waiting', '', {'severity': 'high'})
    assert fake.calls[0]['output_schema']['version'] == 1
    [event] = output_events(worker.store)
    assert (event['source'], event['type'], str(event['source_id'])) == ('run', 'run.output', with_schema)
    assert event['payload'] == {
        'run_id': with_schema,
        'agent_id': worker.agent,
        'agent_name': 'Tester',
        'schema_id': schema['id'],
        'schema_name': 'Triage',
        'schema_version': 1,
        'output': {'severity': 'high'},
        'started_from': None,
        'handler_id': None,
        'event_id': None,
    }
    without = start(worker.client, agent_id=worker.agent).json()['id']
    plain = FakeAgent([answer('High, I think.')])
    done = run(worker, without, plain)
    assert (done['status'], done['output'], done['output_schema_id']) == ('waiting', None, None)
    assert plain.calls[0]['output_schema'] is None
    assert len(output_events(worker.store)) == 1


def test_a_run_validates_against_the_version_it_pinned(worker):
    """Review Focus 2: the schema changes (a new required field) after the start."""
    schema = created(worker.client)
    session_id = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    assert saved(worker.client, schema, schema=CHANGED).status_code == 200
    done = run(worker, session_id, FakeAgent([answer(VALID)]))
    assert (done['status'], done['output'], done['output_schema_version']) == ('waiting', {'severity': 'high'}, 1)


def test_an_invalid_answer_is_retried_once_and_then_the_run_fails(worker):
    """Review Focus 3."""
    schema = created(worker.client)
    session_id = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    fake = FakeAgent([answer('{"severity": 1}'), answer('{"severity": 2}')])
    done = run(worker, session_id, fake)
    assert (done['status'], done['status_message'], done['output']) == (
        'failed',
        'Output did not match the schema',
        None,
    )
    assert fake.calls[1]['task'] == (
        "Your final answer does not match the output schema: $.severity: 1 is not of type 'string'. "
        'Answer again with JSON only.'
    )
    [error] = [e['payload'] for e in events(worker.store, session_id) if e['kind'] == 'error']
    assert error == {
        'type': 'OutputInvalid',
        'message': 'Output did not match the schema',
        'errors': ["$.severity: 2 is not of type 'string'"],
        'raw': '{"severity": 2}',
    }
    assert done['output_error'] == {'errors': ["$.severity: 2 is not of type 'string'"], 'raw': '{"severity": 2}'}
    assert output_events(worker.store) == []


def test_a_corrected_answer_after_the_retry_is_the_output(worker):
    schema = created(worker.client)
    session_id = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    fake = FakeAgent([answer('Here: high'), answer(VALID)])
    done = run(worker, session_id, fake)
    assert (done['status'], done['output'], done['output_error']) == ('waiting', {'severity': 'high'}, None)
    retry = [
        e['payload'] for e in events(worker.store, session_id) if e['kind'] == 'input' and e['payload'].get('retry')
    ]
    assert [item['task'] for item in retry] == [fake.calls[1]['task']]
    assert len(output_events(worker.store)) == 1


def test_an_output_over_the_limit_fails_the_run(worker):
    schema = created(worker.client)
    session_id = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    huge = json.dumps({'severity': 'x' * output_runs.MAX_OUTPUT_BYTES})
    fake = FakeAgent([answer(huge)])
    done = run(worker, session_id, fake)
    assert (done['status'], done['status_message'], len(fake.calls)) == ('failed', 'Output is larger than 256 KB.', 1)


# --- handlers -----------------------------------------------------------------------------------------------------


def test_a_handler_starts_its_run_with_its_schema_current_or_pinned(tester):
    schema = created(tester.client)
    assert saved(tester.client, schema, schema=CHANGED).status_code == 200
    a_handler(
        tester.store,
        name='Follows',
        action='agent',
        emit=None,
        agent_id=tester.agent,
        prompt='Triage',
        output_schema_id=schema['id'],
    )
    a_handler(
        tester.store,
        name='Pinned',
        action='agent',
        emit=None,
        agent_id=tester.agent,
        prompt='Triage',
        output_schema_id=schema['id'],
        output_schema_version=1,
    )
    an_event(tester.store)
    assert event_processor.run_once(tester.store) is True
    with tester.store.connect() as conn:
        rows = conn.execute(
            """SELECT h.name,s.output_schema_version,s.agent_config->'output_schema'->>'version' AS pinned
            FROM ka.agent_sessions s JOIN ka.event_handlers h ON h.id=s.handler_id ORDER BY h.name"""
        ).fetchall()
    assert [(r['name'], r['output_schema_version'], r['pinned']) for r in rows] == [
        ('Follows', 2, '2'),
        ('Pinned', 1, '1'),
    ]


def handler_body(tester, **values) -> dict:
    return {
        'name': 'Triage',
        'type_pattern': 'jira.ticket.*',
        'action': 'agent',
        'agent_id': tester.agent,
        'prompt': 'Triage {{ event.subject }}',
        **values,
    }


def test_the_handler_api_saves_and_checks_the_schema(tester):
    schema = created(tester.client)
    body = handler_body(tester, output_schema_id=schema['id'], output_schema_version=1)
    response = tester.client.post('/api/event-handlers', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    handler = response.json()['handler']
    assert (handler['output_schema_id'], handler['output_schema_version']) == (schema['id'], 1)
    changed = tester.client.put(
        f'/api/event-handlers/{handler["id"]}',
        headers=HEADERS,
        json={**body, 'output_schema_version': None, 'revision': handler['revision']},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()['handler']['output_schema_version'] is None
    for values, text in (
        ({'output_schema_id': '00000000-0000-0000-0000-000000000000'}, 'Output schema not found.'),
        ({'output_schema_id': schema['id'], 'output_schema_version': 7}, 'Output schema version not found.'),
        ({'output_schema_version': 1}, 'Choose the output schema of this version.'),
    ):
        refused = tester.client.post('/api/event-handlers', headers=HEADERS, json=handler_body(tester, **values))
        assert (refused.status_code, refused.json()['error']['message']) == (400, text)
    emit = {
        'name': 'Emit',
        'type_pattern': 'jira.ticket.*',
        'action': 'emit',
        'emit': {'type': 'a.b'},
        'output_schema_id': schema['id'],
    }
    refused = tester.client.post('/api/event-handlers', headers=HEADERS, json=emit)
    assert refused.json()['error']['message'] == 'Only Send to agent handlers take an output schema.'


# --- model settings -----------------------------------------------------------------------------------------------


def model_body(**values) -> dict:
    return {
        'name': 'Model',
        'provider': 'openai',
        'model': 'gpt-5',
        'api_format': 'responses',
        'base_url': 'http://model.test/v1',
        **values,
    }


def test_models_carry_the_structured_output_capability(tester):
    openai = tester.client.post('/api/llm/models', headers=HEADERS, json=model_body(name='A')).json()
    assert openai['capabilities'] == {'supports_structured_output': True}
    claude = tester.client.post(
        '/api/llm/models',
        headers=HEADERS,
        json=model_body(name='B', provider='anthropic', api_format='native', api_key='k', base_url='http://c.test'),
    ).json()
    assert claude['capabilities'] == {'supports_structured_output': False}
    off = model_body(name='A', capabilities={'supports_structured_output': False}, revision=openai['revision'])
    updated = tester.client.put(f'/api/llm/models/{openai["id"]}', headers=HEADERS, json=off).json()
    assert updated['capabilities'] == {'supports_structured_output': False}
    kept = tester.client.put(
        f'/api/llm/models/{openai["id"]}', headers=HEADERS, json=model_body(name='A', revision=updated['revision'])
    ).json()
    assert kept['capabilities'] == {'supports_structured_output': False}  # an edit without it keeps it
    moved = model_body(name='A', provider='anthropic', api_format='native', api_key='k', base_url='http://c.test')
    moved = tester.client.put(
        f'/api/llm/models/{openai["id"]}', headers=HEADERS, json={**moved, 'revision': kept['revision']}
    ).json()
    assert moved['capabilities'] == {'supports_structured_output': False}  # a new provider: its default again
    back = model_body(name='A', revision=moved['revision'], api_key='k')
    back = tester.client.put(f'/api/llm/models/{openai["id"]}', headers=HEADERS, json=back).json()
    assert back['capabilities'] == {'supports_structured_output': True}
    pinned = start(tester.client, agent_id=tester.agent).json()['config']['model']
    assert pinned['capabilities'] == {'supports_structured_output': True}


def test_every_turn_of_a_run_with_a_schema_is_validated(worker):
    """A later turn (a message or a wake-up after the first answer) ends with its own validated output and event."""
    schema = created(worker.client)
    session_id = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    store = worker.store
    fake = FakeAgent([answer(VALID), answer('{"severity": "low"}')])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, session_id, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        url = f'/api/sessions/{session_id}'
        await asyncio.to_thread(worker.client.post, url + '/messages', headers=HEADERS, json={'text': 'Again.'})
        done = await wait_for(store, session_id, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await asyncio.to_thread(worker.client.post, url + '/close', headers=HEADERS)
        await asyncio.wait_for(runner, 5)
        return done

    done = asyncio.run(scenario())
    assert done['output'] == {'severity': 'low'}
    assert [e['payload']['output'] for e in output_events(store)] == [{'severity': 'high'}, {'severity': 'low'}]
    assert fake.calls[1]['output_schema']['id'] == schema['id']


# --- run.output in the events chain -------------------------------------------------------------------------------


def lineage(event: dict) -> tuple:
    return (event['depth'], event['parent_id'], event['root_id'])


def test_a_run_started_outside_events_records_depth_zero(worker):
    schema = created(worker.client)
    session_id = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    assert 'event' not in session_row(worker.store, session_id)['agent_config']
    run(worker, session_id, FakeAgent([answer(VALID)]))
    [event] = output_events(worker.store)
    assert lineage(event) == (0, None, event['id'])  # a root of its own chain
    assert 'chain' not in event['payload']


def test_a_handler_run_records_its_event_and_its_output_joins_that_chain(worker):
    schema = created(worker.client)
    a_handler(
        worker.store, action='agent', emit=None, agent_id=worker.agent, prompt='Triage', output_schema_id=schema['id']
    )
    root = an_event(worker.store)
    event_processor.run_once(worker.store)
    with worker.store.connect() as conn:
        session = conn.execute('SELECT id,agent_config FROM ka.agent_sessions').fetchone()
    assert session['agent_config']['event'] == {'id': root['id'], 'root_id': root['id'], 'depth': 0}
    run(worker, session['id'], FakeAgent([answer(VALID)]))
    [event] = output_events(worker.store)
    assert lineage(event) == (1, root['id'], root['id'])
    assert event['payload']['event_id'] == root['id']


def test_a_handler_with_a_schema_is_skipped_on_an_event_at_the_deepest_level(tester):
    schema = created(tester.client)
    common = {
        'action': 'agent',
        'emit': None,
        'agent_id': tester.agent,
        'prompt': 'Again',
        'type_pattern': 'run.output',
    }
    a_handler(tester.store, name='A with schema', output_schema_id=schema['id'], **common)
    a_handler(tester.store, name='B without schema', **common)  # its run emits no run.output, so it cannot loop
    deepest = an_event(tester.store, source='run', type='run.output', subject='Tester', depth=event_processor.MAX_DEPTH)
    assert event_processor.run_once(tester.store) is True
    found = [(r['handler_name'], r['status'], r['message']) for r in runs_of(tester.store, deepest['id'])]
    assert found == [
        ('A with schema', 'skipped', 'Stopped: the event chain is 5 levels deep; its run.output would be deeper.'),
        ('B without schema', 'succeeded', 'Started a run of Tester.'),
    ]
    assert event_row(tester.store, deepest['id'])['status'] == 'processed'  # a stopped loop is not a failed event


def runs_of(store, event_id) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.event_handler_runs WHERE event_id=%s ORDER BY id', (event_id,)).fetchall()


def event_row(store, event_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.events WHERE id=%s', (event_id,)).fetchone()


def loop_until_quiet(worker, first_session: str) -> list[str]:
    """Run a session, process every pending event, run the session a handler started, and so on until nothing is
    left; returns the sessions in the order they ran."""
    ran, pending = [], [first_session]
    while pending:
        session_id = pending.pop()
        run(worker, session_id, FakeAgent([answer(VALID)]))
        ran.append(session_id)
        assert len(ran) <= 20, 'the loop did not stop'
        while event_processor.run_once(worker.store):
            pass
        with worker.store.connect() as conn:
            pending = [str(r['id']) for r in conn.execute("SELECT id FROM ka.agent_sessions WHERE status<>'closed'")]
    return ran


def test_a_run_output_loop_through_an_emit_handler_stops_at_the_deepest_level(worker):
    schema = created(worker.client)
    a_handler(worker.store, name='Emit', type_pattern='run.output', emit=Jsonb({'type': 'output.again'}))
    a_handler(
        worker.store,
        name='Again',
        type_pattern='output.again',
        action='agent',
        emit=None,
        agent_id=worker.agent,
        prompt='Again',
        output_schema_id=schema['id'],
    )
    first = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    ran = loop_until_quiet(worker, first)
    # run.output 0 → emit 1 → run → run.output 2 → emit 3 → run → run.output 4 → emit 5 → skipped
    assert len(ran) == 3
    assert [e['depth'] for e in output_events(worker.store)] == [0, 2, 4]
    with worker.store.connect() as conn:
        last = conn.execute("SELECT * FROM ka.events WHERE type='output.again' ORDER BY depth DESC LIMIT 1").fetchone()
    assert last['depth'] == event_processor.MAX_DEPTH
    [stopped] = runs_of(worker.store, last['id'])
    assert stopped['status'] == 'skipped' and 'deep' in stopped['message']


def test_a_direct_run_output_loop_stops_at_the_deepest_level(worker):
    schema = created(worker.client)
    a_handler(
        worker.store,
        name='Again',
        type_pattern='run.output',
        action='agent',
        emit=None,
        agent_id=worker.agent,
        prompt='Again',
        output_schema_id=schema['id'],
    )
    first = start(worker.client, agent_id=worker.agent, output_schema_id=schema['id']).json()['id']
    ran = loop_until_quiet(worker, first)
    events_ = output_events(worker.store)
    assert len(ran) == event_processor.MAX_DEPTH + 1
    assert [e['depth'] for e in events_] == list(range(event_processor.MAX_DEPTH + 1))
    assert {e['root_id'] for e in events_} == {events_[0]['id']}  # one chain
    [stopped] = runs_of(worker.store, events_[-1]['id'])
    assert stopped['status'] == 'skipped'
