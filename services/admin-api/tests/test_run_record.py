"""Every recorded agent run keeps its instructions, input, reasoning, tool calls, outputs and answer."""

import asyncio
from types import SimpleNamespace

import pytest

from knowledge_admin.sessions import run_record
from knowledge_admin.agents.runner import agent
from knowledge_admin.sessions.run_record import RunRecorder
from test_sdk_agent import config, responses_api  # noqa: F401 (fixtures)


@pytest.fixture
def captured(monkeypatch):
    rows = []
    monkeypatch.setattr(run_record, '_store', object())
    monkeypatch.setattr(run_record, '_insert', lambda store, events: rows.extend(events))
    return rows


def recorded(config):
    return {
        **config,
        'record': {
            'run_key': 'run:test-1',
            'project_id': 'p1',
            'actor_id': None,
            'kind': 'test',
            'title': 'List the files',
        },
    }


def test_agent_run_records_full_timeline(config, captured):
    result = asyncio.run(
        agent(recorded(config), 'RESPONSES_REASONING_CYCLE list the files', credentials={'model-key': 'fixture-secret'})
    )
    assert result.final_output == 'Configured agent completed the task.'
    kinds = [event['kind'] for event in captured]
    assert kinds[:3] == ['start', 'input', 'instructions']
    assert kinds[-1] == 'end'
    for kind in ('reasoning', 'tool_call', 'tool_result', 'message', 'usage'):
        assert kind in kinds
    assert [event['seq'] for event in captured] == list(range(1, len(captured) + 1))
    by_kind = {event['kind']: event['payload'] for event in captured}
    assert by_kind['start'] == {'kind': 'test', 'title': 'List the files', 'agent': 'Fixture agent'}
    assert by_kind['input']['task'] == 'RESPONSES_REASONING_CYCLE list the files'
    assert 'Use the active saved system prompt.' in by_kind['instructions']['text']
    assert by_kind['reasoning']['text'] == 'Synthetic fixture reasoning.'
    assert by_kind['tool_call']['name'] == 'list_files'
    assert by_kind['tool_result']['name'] == 'list_files' and by_kind['tool_result']['output']
    assert by_kind['message']['text'] == 'Configured agent completed the task.'
    assert by_kind['end']['status'] == 'completed'
    assert all(event['run_key'] == 'run:test-1' and event['agent_id'] == 'fixture' for event in captured)


def test_unrecorded_runs_write_nothing(config, captured):
    asyncio.run(agent(config, 'Explain', credentials={'model-key': 'fixture-secret'}))
    assert captured == []


def test_failed_run_records_error(config, captured, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError('provider exploded fixture-secret')

    monkeypatch.setattr('knowledge_admin.agents.runner.Runner.run_streamed', explode)
    with pytest.raises(RuntimeError):
        asyncio.run(agent(recorded(config), 'Explain', credentials={'model-key': 'fixture-secret'}))
    assert [event['kind'] for event in captured][-2:] == ['error', 'end']
    assert captured[-1]['payload']['status'] == 'failed'
    assert 'fixture-secret' not in repr(captured)


def test_tool_output_is_stored_in_full_and_redacted(captured):
    recorder = RunRecorder(run_key='run:x', project_id='p1', agent_id='a', actor_id=None, secrets=['TOKEN123'])
    output = 'x' * 50000 + ' TOKEN123'
    asyncio.run(
        recorder.on_tool_end(
            SimpleNamespace(tool_call_id='c1'), SimpleNamespace(name='Agent'), SimpleNamespace(name='shell_run'), output
        )
    )
    recorder.flush()
    payload = captured[0]['payload']
    assert len(payload['output']) == 50000 + len(' [redacted]') and payload['output'].endswith('[redacted]')


def test_oversized_values_are_truncated_with_marker(captured):
    recorder = RunRecorder(run_key='run:y', project_id='p1', agent_id='a', actor_id=None, secrets=[])
    recorder.add('message', {'text': 'y' * (run_record.MAX_TEXT + 10)})
    recorder.flush()
    text = captured[0]['payload']['text']
    assert text.endswith(run_record.TRUNCATED) and len(text) == run_record.MAX_TEXT + len(run_record.TRUNCATED)


def test_flush_is_idempotent(captured):
    recorder = RunRecorder(run_key='run:z', project_id='p1', agent_id='a', actor_id=None, secrets=[])
    recorder.add('message', {'text': 'hi'})
    recorder.flush()
    recorder.flush()
    assert len(captured) == 1


def test_an_event_can_belong_to_another_agent_than_the_run(captured):
    recorder = RunRecorder(run_key='run:e', project_id='p1', agent_id='lead', actor_id=None, secrets=[])
    recorder.add('message', {'text': 'lead'})
    recorder.add('message', {'text': 'expert'}, agent_id='payments')
    recorder.flush()
    assert [event['agent_id'] for event in captured] == ['lead', 'payments']


def test_unconfigured_store_raises(monkeypatch):
    monkeypatch.setattr(run_record, '_store', None)
    recorder = RunRecorder(run_key='run:q', project_id='p1', agent_id='a', actor_id=None, secrets=[])
    recorder.add('message', {'text': 'hi'})
    with pytest.raises(RuntimeError):
        recorder.flush()


def test_events_persist_to_database(store):
    run_record.configure(store, 'shop')
    try:
        recorder = RunRecorder(run_key='run:db', project_id=None, agent_id='search', actor_id=None, secrets=[])
        recorder.add('start', {'kind': 'test', 'title': 't', 'agent': 'Search agent'})
        recorder.add('message', {'text': 'answer'})
        recorder.flush()
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT seq,kind,payload FROM ka.agent_run_events WHERE run_key='run:db' ORDER BY seq"
            ).fetchall()
        assert [(r['seq'], r['kind']) for r in rows] == [(1, 'start'), (2, 'message')]
    finally:
        run_record.configure(None, None)


def test_secrets_added_after_creation_are_redacted(captured):
    secrets = []
    recorder = RunRecorder(run_key='run:late', project_id='p1', agent_id='a', actor_id=None, secrets=secrets)
    secrets.append('LATE-KEY')
    recorder.add('tool_result', {'output': 'value LATE-KEY'})
    recorder.flush()
    assert captured[0]['payload']['output'] == 'value [redacted]'


def test_null_characters_are_removed_before_redaction(store):
    """Postgres rejects U+0000 in jsonb: a tool output with a NUL byte must not fail the run, nor hide a key from redaction."""
    run_record.configure(store, 'shop')
    try:
        recorder = RunRecorder(
            run_key='run:nul', project_id=None, agent_id='search', actor_id=None, secrets=['TOKEN123']
        )
        recorder.add('tool_result', {'output': 'bin\x00ary TOK\x00EN123', 'ke\x00y': ['a\x00b']})
        recorder.flush()
        with store.connect() as conn:
            [row] = conn.execute("SELECT payload FROM ka.agent_run_events WHERE run_key='run:nul'").fetchall()
        assert row['payload'] == {'output': 'binary [redacted]', 'key': ['ab']}
    finally:
        run_record.configure(None, None)
