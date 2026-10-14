"""Public SDK activity is bounded and redacted."""

import asyncio
import json
from types import SimpleNamespace

from agents.stream_events import RawResponsesStreamEvent
from openai.types.responses import ResponseTextDeltaEvent, ResponseTextDoneEvent

from knowledge_admin.sessions.activity import SdkActivity, redact


def delta(text):
    return RawResponsesStreamEvent(
        ResponseTextDeltaEvent(
            type='response.output_text.delta',
            item_id='message',
            output_index=0,
            content_index=0,
            sequence_number=1,
            delta=text,
            logprobs=[],
        )
    )


def done(text):
    return RawResponsesStreamEvent(
        ResponseTextDoneEvent(
            type='response.output_text.done',
            item_id='message',
            output_index=0,
            content_index=0,
            sequence_number=2,
            text=text,
            logprobs=[],
        )
    )


def test_split_secret_redaction_and_tool_activity():
    secret = 'model-private-value'
    private_key = '-----BEGIN PRIVATE KEY-----\nprivate-bytes\n-----END PRIVATE KEY-----'
    public = []

    async def run():
        async def capture(value):
            public.append(value)

        activity = SdkActivity([secret, private_key], capture)
        context = SimpleNamespace(tool_call_id='call', tool_arguments='{"command":"uname -a"}')
        tool = SimpleNamespace(name='ssh_run')
        await activity.on_tool_start(context, None, tool)
        text = ''
        for part in ['public output ' + secret[:8], secret[8:] + '\n', json.dumps(private_key)]:
            text += part
            activity.published.clear()
            await activity.notify(delta(part))
        await activity.notify(SimpleNamespace(type='raw_response_event', data='HIDDEN_REASONING'))
        await activity.notify(done(text))
        await activity.on_tool_end(
            context, None, tool, json.dumps({'exit_code': 1, 'stdout': secret + '\n' + private_key})
        )
        activity.close()

    asyncio.run(run())
    serialized = json.dumps(public)
    assert 'public output' in serialized and '[redacted]' in serialized
    for excluded in (secret, secret[:8], 'private-bytes', 'HIDDEN_REASONING'):
        assert excluded not in serialized
    assert public[-1]['status'] == 'failed'
    assert redact(json.dumps({'key': private_key}), [private_key]) == '{"key": "[redacted]"}'


def test_unicode_snapshots_and_observer_failure():
    sizes = []

    async def run():
        async def disconnected(value):
            sizes.append(len(json.dumps({**value, 'task_id': 'x' * 36}).encode()))
            raise ConnectionError('Disconnected')

        activity = SdkActivity([], disconnected)
        await activity.notify(done('東京' * 10000))
        activity.close()

    asyncio.run(run())
    assert sizes and sizes[0] < 8000
