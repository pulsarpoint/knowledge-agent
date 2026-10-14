"""A native turn's SDK lifecycle becomes the run events a Claude Code turn writes, so the session page and Agent runs
show both the same way."""

import asyncio
import dataclasses
from types import SimpleNamespace

from native_fakes import Recorder, call, message, reasoning, response
from knowledge_admin.sessions.activity import tool_failed
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents.session_worker import MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT, TOOL_TRUNCATED

WRITER = SimpleNamespace(name='Native writer', instructions='Document the code.')
SDK_ERROR = 'An error occurred while running the tool. Please try again. Error: The requested path does not exist.'


def tool_end(events, call_id: str, name: str, output: str):
    return events.on_tool_end(SimpleNamespace(tool_call_id=call_id), WRITER, SimpleNamespace(name=name), output)


def test_a_native_turn_becomes_the_events_of_a_claude_code_turn():
    recorder = Recorder()
    events = native_sessions.SessionEvents(recorder, secrets=[], first=True)
    lines = '\n'.join(f'line {n}' for n in range(30))

    async def scenario():
        await events.on_agent_start(None, WRITER)
        await events.on_llm_end(
            None,
            WRITER,
            response(
                reasoning('Look at the routes first.'),
                call('c1', 'read_file', {'path': '/work/data/code/app/main.py'}),
                call('c2', 'shell_run', {'command': 'rg -n retry /work/data/code/app'}),
                call('c3', 'list_files', {'path': '/work/data/code/missing'}),
                input_tokens=100,
                output_tokens=20,
                cached=60,
            ),
        )
        await tool_end(events, 'c1', 'read_file', 'import app')
        await tool_end(events, 'c2', 'shell_run', lines)
        await tool_end(events, 'c3', 'list_files', SDK_ERROR)
        await events.on_agent_start(None, WRITER)  # instructions are written once per session
        await events.on_llm_end(
            None,
            WRITER,
            response(
                message('Draft below.'), message('   '), message('# Payments API'), input_tokens=50, output_tokens=30
            ),
        )

    asyncio.run(scenario())
    assert [kind for kind, _ in recorder.events] == [
        'instructions',
        'reasoning',
        'tool_call',
        'tool_call',
        'tool_call',
        'usage',
        'tool_result',
        'tool_result',
        'tool_result',
        'message',
        'message',
        'usage',
    ]
    assert recorder.events[0] == ('instructions', {'agent': 'Native writer', 'text': 'Document the code.'})
    assert recorder.events[1] == ('reasoning', {'text': 'Look at the routes first.'})
    calls = [payload for kind, payload in recorder.events if kind == 'tool_call']
    assert [(c['name'], c['call_id'], c['summary']) for c in calls] == [
        ('read_file', 'c1', '/work/data/code/app/main.py'),
        ('shell_run', 'c2', 'rg -n retry /work/data/code/app'),
        ('list_files', 'c3', '/work/data/code/missing'),
    ]
    assert calls[0]['arguments'] == {'path': '/work/data/code/app/main.py'}
    read, shell, failed = [payload for kind, payload in recorder.events if kind == 'tool_result']
    assert read == {
        'name': 'read_file',
        'call_id': 'c1',
        'output': 'import app',
        'preview': 'import app',
        'is_error': False,
    }
    assert shell['output'] == lines and shell['preview'] == '\n'.join(f'line {n}' for n in range(20))
    assert (failed['is_error'], failed['output']) == (True, SDK_ERROR)
    assert [payload['text'] for kind, payload in recorder.events if kind == 'message'] == [
        'Draft below.',
        '# Payments API',
    ]
    assert [payload for kind, payload in recorder.events if kind == 'usage'] == [  # one per model call
        {'input_tokens': 100, 'output_tokens': 20, 'cached_tokens': 60, 'reasoning_tokens': 0, 'total_tokens': 120},
        {'input_tokens': 50, 'output_tokens': 30, 'cached_tokens': 0, 'reasoning_tokens': 0, 'total_tokens': 80},
    ]
    assert events.usage == {
        'input_tokens': 150,
        'output_tokens': 50,
        'cached_tokens': 60,
        'reasoning_tokens': 0,
        'total_tokens': 200,
    }
    assert recorder.flushes == 6  # after the first agent start, each model call and each tool result
    assert events.draft('# Payments API\n\nFinal.') == '# Payments API\n\nFinal.'
    assert events.draft('  ') == events.draft(None) == '# Payments API'  # no final answer: the last message
    empty = native_sessions.SessionEvents(Recorder(), secrets=[], first=False)
    assert empty.recorder.events == [] and empty.draft(None) == ''


def test_each_model_call_records_its_usage_through_the_runner():
    """The runner writes a call's events and adds its tokens to the session together; no separate flush."""
    recorder, recorded = Recorder(), []
    events = native_sessions.SessionEvents(recorder, secrets=[], first=False, record_usage=recorded.append)
    asyncio.run(events.on_llm_end(None, WRITER, response(message('Hi'), input_tokens=5, output_tokens=2, cached=1)))
    assert recorded == [
        {'input_tokens': 5, 'output_tokens': 2, 'cached_tokens': 1, 'reasoning_tokens': 0, 'total_tokens': 7}
    ]
    assert recorder.flushes == 0


def test_tool_output_is_redacted_before_it_is_capped():
    secret = 'sk-native-secret'
    recorder = Recorder()
    events = native_sessions.SessionEvents(recorder, secrets=[secret], first=False)
    asyncio.run(tool_end(events, 'c1', 'shell_run', 'y' * (MAX_TOOL_OUTPUT - 8) + secret + '\x00'))
    asyncio.run(tool_end(events, 'c2', 'shell_run', 'x' * 100_000))
    first, second = [payload for _, payload in recorder.events]
    assert 'sk-native' not in repr(first) and '\x00' not in repr(first)  # no partial key survives the cut
    assert second['output'] == 'x' * MAX_TOOL_OUTPUT + TOOL_TRUNCATED
    assert second['preview'] == 'x' * MAX_PREVIEW_CHARS + TOOL_TRUNCATED


def test_tool_summaries_and_failures():
    summary = native_sessions.summary
    assert summary('search_files', {'query': 'retry', 'path': '/c/app'}) == 'retry in /c/app'
    assert summary('code_search', {'name': 'Charge', 'path': '/c/app'}) == 'Charge in /c/app'
    assert summary('ast_grep', {'rule': 'id: x', 'path': '/c/app'}) == 'id: x in /c/app'
    assert summary('ssh_run', {'server_id': 'prod-1', 'command': 'uptime'}) == 'prod-1: uptime'
    assert summary('search_documents', {'query': 'refunds'}) == 'refunds'
    assert summary('read_document', {'collection': 'runbooks', 'path': 'pay.md'}) == 'runbooks/pay.md'
    assert summary('code_graph', {'action': 'overview'}) == 'overview'
    assert summary('Docs__lookup', {'q': 'x'}) == '' and summary('read_file', 'not json') == ''
    assert tool_failed('{"exit_code": 2}') and tool_failed('{"timed_out": true}') and tool_failed(SDK_ERROR)
    assert not tool_failed('{"exit_code": 0}') and not tool_failed('plain text')


def test_fakes_match_the_sdk_types():
    from agents.items import ModelResponse
    from agents.tool_context import ToolContext
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseReasoningItem

    assert {'output', 'usage'} <= {field.name for field in dataclasses.fields(ModelResponse)}
    assert {'input_tokens', 'output_tokens', 'total_tokens', 'input_tokens_details', 'output_tokens_details'} <= {
        field.name for field in dataclasses.fields(Usage)
    }
    assert 'tool_call_id' in {field.name for field in dataclasses.fields(ToolContext)}
    assert {'type', 'call_id', 'name', 'arguments'} <= set(ResponseFunctionToolCall.model_fields)
    assert {'type', 'content'} <= set(ResponseOutputMessage.model_fields)
    assert {'type', 'summary', 'content'} <= set(ResponseReasoningItem.model_fields)
