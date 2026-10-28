"""The session explorer: an agent session's run records (one per turn) as an object with query methods
(sessions/explorer.py), the same methods as LLM tools and as GET/POST /api/agent-runs/{run_key}/explore."""

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


def rows_of(*events, agent='lead', run_key='run:1', at=0):
    """One run key's event rows in order: (kind, payload) or (kind, payload, agent_id)."""
    rows = []
    for seq, event in enumerate(events, start=1):
        kind, payload, *rest = event
        rows.append(
            {
                'run_key': run_key,
                'seq': seq,
                'kind': kind,
                'agent_id': rest[0] if rest else agent,
                'payload': payload,
                'created_at': T0 + timedelta(seconds=at + seq),
                'user': 'alice@example.test',
            }
        )
    return rows


EXPERT = {'id': 'payments', 'name': 'Payments'}


def turn_one():
    """Session turn 1: three steps with tools, an expert call (with its own read_file and a nested subagent), a
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
        run_key='cc:s1:1',
    )


def turn_two():
    """Session turn 2: one step with a tool, then a failure."""
    return rows_of(
        ('start', {'kind': 'chat_session', 'title': 'Plan the release', 'agent': 'Lead', 'turn': 2}),
        ('input', {'task': 'Now write the release notes'}),
        ('tool_call', {'name': 'read_file', 'call_id': 'c3', 'arguments': {'path': 'NOTES.md'}}),
        ('usage', {'total_tokens': 60}),
        ('tool_result', {'name': 'read_file', 'call_id': 'c3', 'output': 'old notes with retries', 'is_error': False}),
        ('error', {'type': 'TimeoutError', 'message': 'the model took too long'}),
        ('end', {'status': 'failed'}),
        run_key='cc:s1:2',
        at=100,
    )


def session_rows():
    return turn_one() + turn_two()


@pytest.fixture
def record():
    return build(session_rows(), run_key='cc:s1:1', meta={'session_id': 's1', 'model': 'gpt-test'}, secrets=[SECRET])


# --- building from rows ---------------------------------------------------------------------------------------------


def test_turns_are_the_session_run_keys_in_turn_order(record):
    assert [(t.turn, t.run_key, t.status) for t in record.turns] == [
        (1, 'cc:s1:1', 'completed'),
        (2, 'cc:s1:2', 'failed'),
    ]
    assert record.focus_turn == 1 and record.status == 'failed' and record.session_id == 's1'
    assert record.turns[0].answer == 'Release plan: ship v1.2 with retries.' and record.turns[1].answer == ''
    # turn 10 comes after turn 2, whatever order the rows come in
    later = rows_of(('start', {}), ('end', {'status': 'completed'}), run_key='cc:s1:10')
    again = build(later + turn_two(), run_key='cc:s1:10')
    assert [t.turn for t in again.turns] == [2, 10] and again.focus_turn == 10


def test_steps_are_model_responses_with_their_inputs(record):
    turn = record.turns[0]
    assert [step.n for step in turn.steps] == [1, 2, 3]
    first, second, third = turn.steps
    assert [item['text'] for item in first.inputs] == ['Plan the payments release']
    assert first.reasoning == ['I should read the changelog.'] and first.messages == ['Reading the changelog.']
    assert [c.call_id for c in first.calls] == ['c1', 'e1'] and first.usage['total_tokens'] == 120
    assert second.inputs == [{'kind': 'comment', 'text': 'Also check refunds'}]
    assert [c.call_id for c in second.calls] == ['c2']
    assert third.messages == ['Release plan: ship v1.2 with retries.'] and third.end == 'completed'
    assert turn.instructions == 'You plan releases.'
    assert record.model == 'gpt-test' and record.agent == 'Lead'
    assert record.turns[1].steps[0].errors == [{'type': 'TimeoutError', 'message': 'the model took too long'}]


def test_tool_calls_pair_with_results(record):
    c1, e1 = record.turns[0].steps[0].calls
    assert (c1.status, c1.size, c1.output.startswith('v1.2'), c1.turn, c1.step) == ('ok', 700, True, 1, 1)
    c2 = record.turns[0].steps[1].calls[0]
    assert c2.status == 'error'  # a non-zero exit code (activity.tool_failed) when is_error is absent
    assert e1.status == 'ok' and e1.output == 'Payments map done'
    assert record.turns[1].steps[0].calls[0].turn == 2


def test_expert_calls_nest_under_their_tool_call(record):
    expert = record.turns[0].steps[0].calls[1].child
    assert expert.kind == 'expert' and expert.agent == 'Payments' and expert.status == 'completed'
    assert [c.call_id for c in expert.own_calls()] == ['x1', 's1']
    assert expert.steps[0].inputs == [{'kind': 'expert', 'text': 'Map payments'}]
    subagent = expert.steps[0].calls[1].child
    assert subagent.kind == 'subagent' and subagent.answer == 'found 3'
    # the expert's own events never split the caller's steps
    assert len(record.turns[0].steps) == 3 and len(expert.steps) == 2


def test_errors_and_an_unfinished_turn():
    rows = rows_of(
        ('start', {'kind': 'chat_session', 'title': 't', 'agent': 'Lead'}),
        ('input', {'task': 'go'}),
        ('error', {'type': 'RateLimitError', 'message': 'slow down'}),
        ('end', {'status': 'retrying'}),
        ('input', {'task': 'go', 'retry': True}),
        ('tool_call', {'name': 'read_file', 'call_id': 'c1', 'arguments': {}}),
        ('usage', {'total_tokens': 5}),
        run_key='cc:s9:1',
    )
    record = build(rows, run_key='cc:s9:1')
    (turn,) = record.turns
    assert [step.n for step in turn.steps] == [1, 2]
    assert turn.steps[0].errors == [{'type': 'RateLimitError', 'message': 'slow down'}]
    assert turn.steps[0].end == 'retrying'
    assert turn.steps[1].inputs == [{'kind': 'retry', 'text': 'go'}] and turn.steps[1].end is None
    assert turn.steps[1].calls[0].status == 'pending'
    assert record.status == 'unfinished' and turn.ended_at is None


def test_a_run_outside_a_session_is_one_turn():
    rows = rows_of(('input', {'task': 'x'}), ('end', {'status': 'completed'}), run_key='run:7')
    record = build(rows, run_key='run:7')
    assert [(t.turn, t.run_key) for t in record.turns] == [(1, 'run:7')] and record.focus_turn == 1


def test_an_unknown_run_is_not_found():
    with pytest.raises(RunNotFound, match='Run record not found'):
        build([], run_key='cc:none:1')


def test_every_string_is_redacted_and_masked(record):
    text = json.dumps([call(record, name, {}) for name in ('overview', 'timeline', 'tool_calls', 'digest')])
    assert SECRET not in text and 'https://u:' not in text
    assert 'https://***@h/x?***' in json.dumps(call(record, 'tool_calls', {}))


# --- methods ----------------------------------------------------------------------------------------------------------


def test_overview_has_a_line_per_turn(record):
    result = record.overview()
    session = result['session']
    assert (session['session_id'], session['run_key'], session['focus_turn']) == ('s1', 'cc:s1:1', 1)
    assert session['status'] == 'failed' and session['started_at'] == (T0 + timedelta(seconds=1)).isoformat()
    lines = result['turns']
    assert [(t['turn'], t['steps'], t['status']) for t in lines] == [(1, 3, 'completed'), (2, 1, 'failed')]
    assert lines[0]['tools'] == ['read_file', 'ask_payments', 'shell_run'] and lines[0]['tokens'] == 690
    assert lines[1]['input'] == 'Now write the release notes' and lines[1]['errors'] == 1
    totals = result['totals']
    assert totals['turns'] == 2 and totals['steps'] == 4
    assert totals['tool_calls'] == {'read_file': 2, 'ask_payments': 1, 'shell_run': 1}
    assert totals['tool_errors'] == 1 and totals['errors'] == 1 and totals['expert_calls'] == 1
    assert totals['tokens'] == 750 and totals['expert_tokens'] == 87
    assert result['truncated'] is False and result['total_chars'] > 0


def test_timeline_filters_and_omits_outputs(record):
    events = record.timeline()['events']
    assert [(e['turn'], e['seq']) for e in events] == sorted((e['turn'], e['seq']) for e in events)
    assert all('step' in e for e in events)
    results = [e for e in events if e['kind'] == 'tool_result']
    assert results and all('output' not in e and 'size' in e for e in results)
    assert {e['kind'] for e in record.timeline(kinds=['tool_call'])['events']} == {'tool_call'}
    assert {e['turn'] for e in record.timeline(turn=2)['events']} == {2}
    assert {(e['turn'], e['name']) for e in record.timeline(tool='read_file')['events']} == {
        (1, 'read_file'),
        (2, 'read_file'),
    }
    # the expert's own events are in expert_call, not in the session timeline
    assert all(e.get('call_id') != 'x1' for e in events)


def test_tool_calls_across_turns(record):
    calls = record.tool_calls()['tool_calls']
    assert [(c['call_id'], c['turn'], c['step']) for c in calls] == [
        ('c1', 1, 1),
        ('e1', 1, 1),
        ('c2', 1, 2),
        ('c3', 2, 1),
    ]
    assert calls[1]['expert_call'] is True and calls[1]['nested_run'] == {'kind': 'expert', 'agent': 'Payments'}
    assert calls[0] | {'arguments': None} == {
        'call_id': 'c1',
        'turn': 1,
        'step': 1,
        'name': 'read_file',
        'arguments': None,
        'status': 'ok',
        'output_size': 700,
        'expert_call': False,
        'nested_run': None,
    }
    assert [c['call_id'] for c in record.tool_calls(turn=2)['tool_calls']] == ['c3']
    assert [c['call_id'] for c in record.tool_calls(name='read_file')['tool_calls']] == ['c1', 'c3']
    assert [c['call_id'] for c in record.tool_calls(errors_only=True)['tool_calls']] == ['c2']
    with pytest.raises(ValueError, match='turn: there is no turn 5'):
        record.tool_calls(turn=5)


def test_tool_output_pages_and_finds_the_call_in_any_turn(record):
    page = record.tool_output('c1', offset=0, max_chars=300)
    assert page['total_chars'] == 700 and len(page['text']) == 300 and page['next_offset'] == 300
    last = record.tool_output('c1', offset=600, max_chars=300)
    assert len(last['text']) == 100 and last['next_offset'] is None
    assert record.tool_output('c3') | {'text': None} == {
        'call_id': 'c3',
        'turn': 2,
        'name': 'read_file',
        'status': 'ok',
        'total_chars': 22,
        'offset': 0,
        'text': None,
        'next_offset': None,
    }
    assert record.tool_output('x1')['text'] == 'def pay(): ...'
    assert record.tool_output('s1')['text'] == 'found 3'
    with pytest.raises(ValueError, match='call_id: no tool call nope'):
        record.tool_output('nope')


def test_turn_is_a_session_turn_with_its_steps(record):
    result = record.turn(1)
    assert result['turn'] == 1 and result['run_key'] == 'cc:s1:1' and result['status'] == 'completed'
    assert result['answer'] == 'Release plan: ship v1.2 with retries.'
    assert [step['step'] for step in result['steps']] == [1, 2, 3]
    second = result['steps'][1]
    assert second['inputs'] == [{'kind': 'comment', 'text': 'Also check refunds'}]
    (c2,) = second['tool_calls']
    assert c2['output']['size'] == len('{"exit_code": 1, "stderr": "boom"}')
    assert c2['output']['error'].startswith('{"exit_code": 1')
    assert 'error' not in result['steps'][0]['tool_calls'][0]['output']
    assert record.turn(2)['steps'][0]['errors'][0]['type'] == 'TimeoutError'
    with pytest.raises(ValueError, match='turn: there is no turn 9; the session has turns 1, 2'):
        record.turn(9)


def test_search_across_turns(record):
    hits = record.search('RETRIES')['hits']
    assert {(h['kind'], h['turn'], h.get('call_id')) for h in hits} >= {
        ('tool_result', 1, 'c1'),
        ('message', 1, None),
        ('tool_result', 2, 'c3'),
    }
    assert all('retries' in h['snippet'].lower() and 'step' in h for h in hits)
    assert {h['turn'] for h in record.search('retries', turn=2)['hits']} == {2}
    expert_hits = record.search('def pay')['hits']
    assert expert_hits[0]['in_call'] == 'e1' and expert_hits[0]['call_id'] == 'x1'
    assert {h['kind'] for h in record.search('payments', kinds=['input'])['hits']} == {'input'}
    capped = record.search('e', max_hits=2)
    assert len(capped['hits']) == 2 and capped['total_hits'] > 2
    assert record.search(SECRET)['hits'] == []


def test_expert_call(record):
    result = record.expert_call('e1')
    assert result['call_id'] == 'e1' and result['status'] == 'ok' and result['turn'] == 1
    overview = result['overview']
    assert overview['run']['agent'] == 'Payments' and overview['totals']['expert_calls'] == 1
    assert [line['step'] for line in overview['steps']] == [1, 2]
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


def test_digest_has_the_compressed_session(record):
    result = record.digest()
    text = result['text']
    assert result['format'] == 'text' and result['size_chars'] == len(text) and result['focus_turn'] == 1
    assert result['full_chars'] > result['size_chars']
    assert 'You plan releases.' in text and 'Plan the payments release' in text
    assert '## Turn 1 (cc:s1:1) · 3 steps' in text and '## Turn 2 (cc:s1:2) · 1 steps · status failed' in text
    assert 'read_file({"path":"CHANGELOG.md"}) -> ok, 700 chars' in text
    assert 'shell_run(' in text and '-> error' in text
    assert 'I should read the changelog.' in text and 'Answer: Release plan' in text
    assert 'error: TimeoutError' in text and text.endswith('End: failed')
    assert 'v1.2: retries' not in text  # outputs are never included
    assert result['shortened_turns'] == [] and result['shortened_steps'] == {}


def long_session(turns=20, error_turn=7):
    rows = []
    for n in range(1, turns + 1):
        output = 'An error occurred while running the tool. boom' if n == error_turn else 'ok'
        rows += rows_of(
            ('start', {'kind': 'chat_session', 'title': 't', 'agent': 'Lead', 'turn': n}),
            ('input', {'task': f'question {n} ' + 'q' * 200}),
            ('message', {'text': f'answer {n} ' + 'x' * 300}),
            ('tool_call', {'name': 'read_file', 'call_id': f'c{n}', 'arguments': {'path': f'f{n}'}}),
            ('usage', {'total_tokens': 10}),
            ('tool_result', {'name': 'read_file', 'call_id': f'c{n}', 'output': output}),
            ('message', {'text': f'final {n}'}),
            ('usage', {'total_tokens': 10}),
            ('end', {'status': 'completed'}),
            run_key=f'cc:long:{n}',
            at=n * 100,
        )
    return rows


def test_digest_keeps_first_last_error_and_focus_turns_over_budget():
    record = build(long_session(), run_key='cc:long:10')
    result = record.digest(budget_chars=9000)
    text = result['text']
    assert result['size_chars'] <= 9000 and result['focus_turn'] == 10
    shortened = result['shortened_turns']
    assert shortened and not {1, 7, 10, 20} & set(shortened)
    assert f'{len(shortened)} turns shortened; use turn(n)' in text
    assert 'answer 1 ' in text and 'answer 7 ' in text and 'answer 10 ' in text and 'answer 20 ' in text
    assert f'Turn {shortened[0]}: ' in text and 'answer 3 ' not in text
    # another focus turn stays in full instead
    assert 4 not in record.digest(budget_chars=9000, turn=4)['shortened_turns']
    assert record.digest(budget_chars=100_000)['shortened_turns'] == []


def test_digest_shortens_middle_steps_of_a_long_turn():
    events = [('start', {'kind': 'chat_session', 'title': 't', 'agent': 'Lead'}), ('input', {'task': 'go'})]
    for n in range(1, 31):
        events.append(('message', {'text': f'step {n} ' + 'x' * 300}))
        events.append(('tool_call', {'name': 'read_file', 'call_id': f'c{n}', 'arguments': {'path': f'f{n}'}}))
        events.append(('usage', {'total_tokens': 10}))
        output = 'An error occurred while running the tool. boom' if n == 12 else 'ok'
        events.append(('tool_result', {'name': 'read_file', 'call_id': f'c{n}', 'output': output}))
    events += [('message', {'text': 'done'}), ('usage', {'total_tokens': 10}), ('end', {'status': 'completed'})]
    record = build(rows_of(*events, run_key='cc:one:1'), run_key='cc:one:1')
    result = record.digest(budget_chars=4000)
    assert result['size_chars'] <= 4000 and result['shortened_turns'] == []
    steps = result['shortened_steps']['1']
    assert steps and not {1, 12, 31} & set(steps)
    assert f'{len(steps)} steps shortened; use turn(1)' in result['text']
    assert f'Step {steps[0]}: read_file -> ok' in result['text'] and 'step 12 ' in result['text']


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
    for name in ('timeline', 'tool_calls', 'search', 'digest'):
        assert 'turn' in METHODS[name].schema()['properties']


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
    assert call(record, 'search', {'text': 'retries', 'kinds': None, 'turn': 2, 'max_hits': None})['total_hits'] == 1


# --- API --------------------------------------------------------------------------------------------------------------


def seed(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.test')
        for row in session_rows():
            conn.execute(
                """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (project, row['run_key'], row['seq'], row['kind'], row['agent_id'], alice, Jsonb(row['payload'])),
            )


def test_api_lists_the_methods_the_llm_sees(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    response = client.get('/api/agent-runs/cc:s1:2/explore', headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {'methods': listing(), 'session_id': 's1', 'turn': 2}
    assert client.get('/api/agent-runs/cc:none:1/explore', headers=HEADERS).status_code == 404


def test_api_explores_the_whole_session_of_the_run(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    post = lambda method, args=None: client.post(
        '/api/agent-runs/cc:s1:2/explore', headers=HEADERS, json={'method': method, 'args': args or {}}
    ).json()
    overview = post('overview')['result']
    assert [t['run_key'] for t in overview['turns']] == ['cc:s1:1', 'cc:s1:2']
    assert overview['session']['focus_turn'] == 2
    body = post('tool_calls', {'turn': 1})
    assert [c['call_id'] for c in body['result']['tool_calls']] == ['c1', 'e1', 'c2']
    assert body['size_chars'] == len(json.dumps(body['result'], ensure_ascii=False))
    assert body['approx_tokens'] == -(-body['size_chars'] // 4)
    assert post('tool_output', {'call_id': 'x1'})['result']['text'] == 'def pay(): ...'
    assert post('digest')['result']['focus_turn'] == 2


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
    missing_run = client.post('/api/agent-runs/cc:none:1/explore', headers=HEADERS, json={'method': 'overview'})
    assert missing_run.status_code == 404


def test_api_access_is_the_run_records(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    for headers in ({}, {'X-Forwarded-Groups': 'readers', 'X-Forwarded-User': 'u'}):
        assert client.get('/api/agent-runs/cc:s1:1/explore', headers=headers).status_code == 403
        response = client.post('/api/agent-runs/cc:s1:1/explore', headers=headers, json={'method': 'overview'})
        assert response.status_code == 403
    # another workspace's turn of a session with the same id is not part of this workspace's session
    with store.connect() as conn:
        other = store.project(conn, 'elsewhere')
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,'cc:s1:3',1,'start','x','{}'),(%s,'run:other',1,'start','x','{}')""",
            (other, other),
        )
    assert client.get('/api/agent-runs/run:other/explore', headers=HEADERS).status_code == 404
    assert client.get('/api/agent-runs/cc:s1:3/explore', headers=HEADERS).status_code == 404
    overview = client.post('/api/agent-runs/cc:s1:1/explore', headers=HEADERS, json={'method': 'overview'}).json()
    assert [t['turn'] for t in overview['result']['turns']] == [1, 2]


def test_load_from_a_run_key_or_a_session_id(store):
    seed(store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        by_key = explorer.SessionRecord.load(conn, project, 'cc:s1:1')
        by_id = explorer.SessionRecord.load(conn, project, 's1')
        with pytest.raises(RunNotFound):
            explorer.SessionRecord.load(conn, project, 'nobody')
    assert [t.run_key for t in by_key.turns] == [t.run_key for t in by_id.turns] == ['cc:s1:1', 'cc:s1:2']
    assert (by_key.focus_turn, by_id.focus_turn, by_id.run_key) == (1, 2, 'cc:s1:2')
