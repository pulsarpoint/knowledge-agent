"""The session explorer: one run record as an object with query methods (sessions/explorer.py), the same methods as
LLM tools and as GET/POST /api/agent-runs/{run_key}/explore."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from agents.tool_context import ToolContext
from knowledge_admin.sessions import explorer
from knowledge_admin.sessions.explorer import METHODS, RunNotFound, build, call, listing, tools
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from test_api import _client
from test_repositories import HEADERS

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
SECRET = 'sk-live-0123456789'


def rows_of(*events, agent='lead'):
    """Run event rows in order: (kind, payload) or (kind, payload, agent_id)."""
    rows = []
    for seq, event in enumerate(events, start=1):
        kind, payload, *rest = event
        rows.append(
            {
                'seq': seq,
                'kind': kind,
                'agent_id': rest[0] if rest else agent,
                'payload': payload,
                'created_at': T0 + timedelta(seconds=seq),
                'user': 'alice@example.test',
            }
        )
    return rows


EXPERT = {'id': 'payments', 'name': 'Payments'}


def session_rows():
    """A session turn: two model turns with tools, an expert call (with its own read_file and a nested subagent), a
    comment, a failed tool and a final answer."""
    return rows_of(
        ('start', {'kind': 'chat_session', 'title': 'Plan the release', 'agent': 'Lead', 'turn': 1}),
        ('input', {'task': 'Plan the payments release'}),
        ('instructions', {'agent': 'Lead', 'text': 'You plan releases.'}),
        ('reasoning', {'text': 'I should read the changelog.'}),
        ('message', {'text': 'Reading the changelog.'}),
        ('tool_call', {'name': 'read_file', 'call_id': 'c1', 'arguments': {'path': 'CHANGELOG.md'}, 'summary': 'x'}),
        ('tool_call', {'name': 'ask_payments', 'call_id': 'e1', 'arguments': {'task': 'Map payments'}}),
        ('usage', {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}),
        ('tool_result', {'name': 'read_file', 'call_id': 'c1', 'output': 'v1.2: retries\n' * 50, 'is_error': False}),
        ('input', {'task': 'Map payments', 'expert': EXPERT, 'parent_call_id': 'e1'}, 'payments'),
        (
            'tool_call',
            {'name': 'read_file', 'call_id': 'x1', 'arguments': '{"path":"pay.py"}', 'parent_call_id': 'e1'},
            'payments',
        ),
        (
            'tool_call',
            {'name': 'run_subagent', 'call_id': 's1', 'arguments': {'task': 'grep'}, 'parent_call_id': 'e1'},
            'payments',
        ),
        ('usage', {'total_tokens': 50, 'parent_call_id': 'e1'}, 'payments'),
        ('input', {'task': 'grep', 'subagent': {'task': 'grep'}, 'parent_call_id': 's1'}, 'payments'),
        ('message', {'text': 'found 3', 'parent_call_id': 's1'}, 'payments'),
        ('usage', {'total_tokens': 7, 'parent_call_id': 's1'}, 'payments'),
        (
            'tool_result',
            {'name': 'run_subagent', 'call_id': 's1', 'output': 'found 3', 'parent_call_id': 'e1'},
            'payments',
        ),
        (
            'tool_result',
            {'name': 'read_file', 'call_id': 'x1', 'output': 'def pay(): ...', 'parent_call_id': 'e1'},
            'payments',
        ),
        ('message', {'text': 'Payments map done', 'parent_call_id': 'e1'}, 'payments'),
        ('usage', {'total_tokens': 30, 'parent_call_id': 'e1'}, 'payments'),
        ('tool_result', {'name': 'ask_payments', 'call_id': 'e1', 'output': 'Payments map done'}),
        ('input', {'task': 'Also check refunds', 'comment': True}),
        (
            'tool_call',
            {'name': 'shell_run', 'call_id': 'c2', 'arguments': {'command': f'curl https://u:{SECRET}@h/x?k=1'}},
        ),
        ('usage', {'input_tokens': 200, 'output_tokens': 30, 'total_tokens': 230}),
        ('tool_result', {'name': 'shell_run', 'call_id': 'c2', 'output': '{"exit_code": 1, "stderr": "boom"}'}),
        ('message', {'text': 'Release plan: ship v1.2 with retries.'}),
        ('usage', {'input_tokens': 300, 'output_tokens': 40, 'total_tokens': 340}),
        ('end', {'status': 'completed'}),
    )


@pytest.fixture
def record():
    return build('cc:s1:1', session_rows(), meta={'model': 'gpt-test'}, secrets=[SECRET])


# --- building from rows ---------------------------------------------------------------------------------------------


def test_turns_are_model_responses_with_their_inputs(record):
    assert [turn.n for turn in record.turns] == [1, 2, 3]
    first, second, third = record.turns
    assert [item['text'] for item in first.inputs] == ['Plan the payments release']
    assert first.reasoning == ['I should read the changelog.'] and first.messages == ['Reading the changelog.']
    assert [c.call_id for c in first.calls] == ['c1', 'e1'] and first.usage['total_tokens'] == 120
    assert second.inputs == [{'kind': 'comment', 'text': 'Also check refunds'}]
    assert [c.call_id for c in second.calls] == ['c2']
    assert third.messages == ['Release plan: ship v1.2 with retries.'] and third.end == 'completed'
    assert record.instructions == 'You plan releases.'
    assert record.answer == 'Release plan: ship v1.2 with retries.'
    assert record.status == 'completed' and record.model == 'gpt-test' and record.agent == 'Lead'


def test_tool_calls_pair_with_results(record):
    c1, e1 = record.turns[0].calls
    assert (c1.status, c1.size, c1.output.startswith('v1.2')) == ('ok', len('v1.2: retries\n' * 50), True)
    c2 = record.turns[1].calls[0]
    assert c2.status == 'error'  # a non-zero exit code (activity.tool_failed) when is_error is absent
    assert e1.status == 'ok' and e1.output == 'Payments map done'


def test_expert_calls_nest_under_their_tool_call(record):
    expert = record.turns[0].calls[1].child
    assert expert.kind == 'expert' and expert.agent == 'Payments' and expert.status == 'completed'
    assert [c.call_id for turn in expert.turns for c in turn.calls] == ['x1', 's1']
    assert expert.turns[0].inputs == [{'kind': 'expert', 'text': 'Map payments'}]
    subagent = expert.turns[0].calls[1].child
    assert subagent.kind == 'subagent' and subagent.answer == 'found 3'
    # the expert's own events never split the caller's turns
    assert len(record.turns) == 3 and len(expert.turns) == 2


def test_errors_and_an_unfinished_run():
    rows = rows_of(
        ('start', {'kind': 'chat_session', 'title': 't', 'agent': 'Lead'}),
        ('input', {'task': 'go'}),
        ('error', {'type': 'RateLimitError', 'message': 'slow down'}),
        ('end', {'status': 'retrying'}),
        ('input', {'task': 'go', 'retry': True}),
        ('tool_call', {'name': 'read_file', 'call_id': 'c1', 'arguments': {}}),
        ('usage', {'total_tokens': 5}),
    )
    record = build('cc:s9:1', rows)
    assert [turn.n for turn in record.turns] == [1, 2]
    assert record.turns[0].errors == [{'type': 'RateLimitError', 'message': 'slow down'}]
    assert record.turns[0].end == 'retrying'
    assert record.turns[1].inputs == [{'kind': 'retry', 'text': 'go'}] and record.turns[1].end is None
    assert record.turns[1].calls[0].status == 'pending'
    assert record.status == 'unfinished' and record.ended_at is None


def test_an_unknown_run_is_not_found():
    with pytest.raises(RunNotFound, match='Run record not found'):
        build('cc:none:1', [])


def test_every_string_is_redacted_and_masked(record):
    text = json.dumps([call(record, name, {}) for name in ('overview', 'timeline', 'tool_calls', 'digest')])
    assert SECRET not in text and 'https://u:' not in text
    assert 'https://***@h/x?***' in json.dumps(call(record, 'tool_calls', {}))


# --- methods ----------------------------------------------------------------------------------------------------------


def test_overview(record):
    result = record.overview()
    assert result['run']['run_key'] == 'cc:s1:1' and result['run']['status'] == 'completed'
    assert result['run']['started_at'] == (T0 + timedelta(seconds=1)).isoformat()
    assert [t['tools'] for t in result['turns']] == [['read_file', 'ask_payments'], ['shell_run'], []]
    assert result['turns'][0]['input'] == 'Plan the payments release' and result['turns'][2]['end'] == 'completed'
    assert result['turns'][0]['tokens'] == 120
    totals = result['totals']
    assert totals['tool_calls'] == {'read_file': 1, 'ask_payments': 1, 'shell_run': 1}
    assert totals['tool_errors'] == 1 and totals['errors'] == 0 and totals['expert_calls'] == 1
    assert totals['tokens'] == 690 and totals['expert_tokens'] == 87
    assert result['truncated'] is False and result['total_chars'] > 0


def test_timeline_filters_and_omits_outputs(record):
    events = record.timeline()['events']
    assert [e['seq'] for e in events] == sorted(e['seq'] for e in events)
    results = [e for e in events if e['kind'] == 'tool_result']
    assert results and all('output' not in e and 'size' in e for e in results)
    assert {e['kind'] for e in record.timeline(kinds=['tool_call'])['events']} == {'tool_call'}
    assert {e['turn'] for e in record.timeline(turn=2)['events']} == {2}
    assert {e['name'] for e in record.timeline(tool='read_file')['events']} == {'read_file'}
    # the expert's own events are in expert_call, not in the caller's timeline
    assert all(e.get('call_id') != 'x1' for e in events)


def test_tool_calls_filters(record):
    calls = record.tool_calls()['tool_calls']
    assert [c['call_id'] for c in calls] == ['c1', 'e1', 'c2']
    assert calls[1]['expert_call'] is True and calls[1]['nested_run'] == {'kind': 'expert', 'agent': 'Payments'}
    assert calls[0] | {'arguments': None} == {
        'call_id': 'c1',
        'turn': 1,
        'name': 'read_file',
        'arguments': None,
        'status': 'ok',
        'output_size': 700,
        'expert_call': False,
        'nested_run': None,
    }
    assert [c['call_id'] for c in record.tool_calls(turn=2)['tool_calls']] == ['c2']
    assert [c['call_id'] for c in record.tool_calls(name='read_file')['tool_calls']] == ['c1']
    assert [c['call_id'] for c in record.tool_calls(errors_only=True)['tool_calls']] == ['c2']


def test_tool_output_pages_and_reaches_expert_calls(record):
    page = record.tool_output('c1', offset=0, max_chars=300)
    assert page['total_chars'] == 700 and len(page['text']) == 300 and page['next_offset'] == 300
    last = record.tool_output('c1', offset=600, max_chars=300)
    assert len(last['text']) == 100 and last['next_offset'] is None
    assert record.tool_output('x1')['text'] == 'def pay(): ...'
    assert record.tool_output('s1')['text'] == 'found 3'
    with pytest.raises(ValueError, match='call_id: no tool call nope'):
        record.tool_output('nope')


def test_turn_in_full_with_output_sizes(record):
    result = record.turn(2)
    assert result['turn'] == 2 and result['inputs'] == [{'kind': 'comment', 'text': 'Also check refunds'}]
    (c2,) = result['tool_calls']
    assert c2['output']['size'] == len('{"exit_code": 1, "stderr": "boom"}')
    assert c2['output']['error'].startswith('{"exit_code": 1')
    assert 'error' not in record.turn(1)['tool_calls'][0]['output']
    with pytest.raises(ValueError, match='turn: there is no turn 9'):
        record.turn(9)


def test_search(record):
    hits = record.search('RETRIES')['hits']
    assert {(h['kind'], h.get('call_id')) for h in hits} >= {('tool_result', 'c1'), ('message', None)}
    assert all('retries' in h['snippet'].lower() for h in hits)
    expert_hits = record.search('def pay')['hits']
    assert expert_hits[0]['in_call'] == 'e1' and expert_hits[0]['call_id'] == 'x1'
    assert {h['kind'] for h in record.search('payments', kinds=['input'])['hits']} == {'input'}
    capped = record.search('e', max_hits=2)
    assert len(capped['hits']) == 2 and capped['total_hits'] > 2
    assert record.search(SECRET)['hits'] == []


def test_expert_call(record):
    result = record.expert_call('e1')
    assert result['call_id'] == 'e1' and result['status'] == 'ok'
    assert result['overview']['run']['agent'] == 'Payments' and result['overview']['totals']['expert_calls'] == 1
    assert [e['kind'] for e in result['timeline']][:2] == ['input', 'tool_call']
    with pytest.raises(ValueError, match='call_id: c1 is not an expert call'):
        record.expert_call('c1')


def test_results_are_capped(record, monkeypatch):
    full = len(record.timeline()['events'])
    monkeypatch.setattr(explorer, 'MAX_CHARS', 600)
    result = record.timeline()
    assert result['truncated'] is True and result['total_chars'] > 600
    assert len(json.dumps(result, ensure_ascii=False)) <= 600
    assert 0 < len(result['events']) < full and result['total_items'] == full


# --- digest -----------------------------------------------------------------------------------------------------------


def test_digest_has_the_compressed_run(record):
    result = record.digest()
    text = result['text']
    assert result['format'] == 'text' and result['size_chars'] == len(text)
    assert result['full_chars'] > result['size_chars']
    assert 'You plan releases.' in text and 'Plan the payments release' in text
    assert 'read_file({"path":"CHANGELOG.md"}) -> ok, 700 chars' in text
    assert 'shell_run(' in text and '-> error' in text
    assert 'I should read the changelog.' in text and 'Answer: Release plan' in text
    assert 'End: completed' in text
    assert 'v1.2: retries' not in text  # outputs are never included
    assert result['shortened_turns'] == []


def long_run(turns=30, error_turn=12):
    events = [('start', {'kind': 'chat_session', 'title': 't', 'agent': 'Lead'}), ('input', {'task': 'go'})]
    for n in range(1, turns + 1):
        events.append(('message', {'text': f'step {n} ' + 'x' * 300}))
        events.append(('tool_call', {'name': 'read_file', 'call_id': f'c{n}', 'arguments': {'path': f'f{n}'}}))
        events.append(('usage', {'total_tokens': 10}))
        output = 'An error occurred while running the tool. boom' if n == error_turn else 'ok'
        events.append(('tool_result', {'name': 'read_file', 'call_id': f'c{n}', 'output': output}))
    events.append(('message', {'text': 'done'}))
    events.append(('usage', {'total_tokens': 10}))
    events.append(('end', {'status': 'completed'}))
    return build('run:long', rows_of(*events))


def test_digest_shortens_middle_turns_over_budget():
    record = long_run()
    result = record.digest(budget_chars=4000)
    text = result['text']
    assert result['size_chars'] <= 4000
    shortened = result['shortened_turns']
    assert shortened and 1 not in shortened and 12 not in shortened and 31 not in shortened
    assert f'{len(shortened)} turns shortened; use turn(n)' in text
    assert 'step 1 ' in text and 'step 12 ' in text and 'done' in text
    assert f'Turn {shortened[0]}: read_file -> ok' in text
    assert record.digest(budget_chars=100_000)['shortened_turns'] == []


# --- the shared method table: tools and API ---------------------------------------------------------------------------


def test_tools_expose_the_listing(record):
    listed = {item['tool']: item for item in listing()}
    built = tools(record)
    assert [tool.name for tool in built] == [item['tool'] for item in listing()]
    for tool in built:
        assert tool.description == listed[tool.name]['description']
        assert tool.params_json_schema == listed[tool.name]['parameters']
        assert 'run_key' not in json.dumps(tool.params_json_schema)
    assert {item['method'] for item in listing()} == set(METHODS)


def test_a_tool_returns_what_the_method_returns(record):
    (turn_tool,) = [tool for tool in tools(record) if tool.name == 'record_turn']
    raw = json.dumps({'turn': 2})
    context = ToolContext(
        context={'tools': {'record_turn'}}, tool_name='record_turn', tool_call_id='t', tool_arguments=raw
    )
    assert json.loads(asyncio.run(turn_tool.on_invoke_tool(context, raw))) == call(record, 'turn', {'turn': 2})


def test_call_validates_arguments(record):
    with pytest.raises(ValidationError):
        call(record, 'turn', {'turn': 0})
    with pytest.raises(ValueError, match='method: unknown method nope'):
        call(record, 'nope', {})
    # a strict-mode tool call sends null for every optional argument
    assert call(record, 'tool_output', {'call_id': 'c1', 'offset': None, 'max_chars': None})['offset'] == 0


# --- API --------------------------------------------------------------------------------------------------------------


def seed(store, run_key='cc:s1:1'):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')
        for row in session_rows():
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (project, run_key, row['seq'], row['kind'], row['agent_id'], alice, Jsonb(row['payload'])),
            )


def test_api_lists_the_methods_the_llm_sees(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    response = client.get('/api/agent-runs/cc:s1:1/explore', headers=HEADERS)
    assert response.status_code == 200 and response.json() == {'methods': listing()}
    assert client.get('/api/agent-runs/cc:none:1/explore', headers=HEADERS).status_code == 404


def test_api_runs_a_method(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    body = client.post(
        '/api/agent-runs/cc:s1:1/explore', headers=HEADERS, json={'method': 'tool_calls', 'args': {'turn': 2}}
    ).json()
    assert [c['call_id'] for c in body['result']['tool_calls']] == ['c2']
    assert body['size_chars'] == len(json.dumps(body['result'], ensure_ascii=False))
    assert body['approx_tokens'] == -(-body['size_chars'] // 4)
    assert client.post('/api/agent-runs/cc:s1:1/explore', headers=HEADERS, json={'method': 'digest'}).status_code == 200


def test_api_validation_names_the_argument(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    post = lambda body: client.post('/api/agent-runs/cc:s1:1/explore', headers=HEADERS, json=body)
    bad = post({'method': 'turn', 'args': {'turn': 'two'}})
    assert bad.status_code == 400 and bad.json()['error']['message'].startswith('turn:')
    missing = post({'method': 'tool_output', 'args': {}})
    assert missing.status_code == 400 and 'call_id' in missing.json()['error']['message']
    assert 'turn: there is no turn 9' in post({'method': 'turn', 'args': {'turn': 9}}).json()['error']['message']
    unknown = post({'method': 'nope', 'args': {}})
    assert unknown.status_code == 400 and unknown.json()['error']['message'].startswith('method:')
    assert post({'method': 'overview', 'args': {'extra': 1}}).status_code == 400
    assert (
        client.post('/api/agent-runs/cc:none:1/explore', headers=HEADERS, json={'method': 'overview'}).status_code
        == 404
    )


def test_api_access_is_the_run_records(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    for headers in ({}, {'X-Forwarded-Groups': 'readers', 'X-Forwarded-User': 'u'}):
        assert client.get('/api/agent-runs/cc:s1:1/explore', headers=headers).status_code == 403
        assert (
            client.post('/api/agent-runs/cc:s1:1/explore', headers=headers, json={'method': 'overview'}).status_code
            == 403
        )
    # another workspace's run with the same key is not this workspace's record
    with store.connect() as conn:
        other = store.project(conn, 'elsewhere')
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,'run:other',1,'start','x','{}')""",
            (other,),
        )
    assert client.get('/api/agent-runs/run:other/explore', headers=HEADERS).status_code == 404
