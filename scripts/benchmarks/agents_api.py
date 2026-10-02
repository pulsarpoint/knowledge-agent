"""Run one exact objective through the application's Agents SDK runtime, without database writes."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from agents import set_tracing_disabled
from agents.usage import Usage
from fastapi.encoders import jsonable_encoder
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from openai.resources.responses import AsyncResponses
from openai.types.shared import Reasoning

from knowledge_admin.agent_activity import redact
from knowledge_admin.agents import agent, get_prompt


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--objective', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--api-key-env', default='AWS_BEARER_TOKEN_BEDROCK',
                        help='Name of the process variable containing the resolved model credential.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    objective = args.objective.read_text()
    secret = os.environ[args.api_key_env]
    credentials = {config['model']['api_key_ref']['id']: secret}
    set_tracing_disabled(True)
    started = time.monotonic()
    usage = Usage()
    tool_counts = {}
    metadata = {
        'status': 'running', 'started_at': datetime.now(UTC).isoformat(),
        'objective_sha256': hashlib.sha256(objective.encode()).hexdigest(),
        'model': {k: config['model'].get(k) for k in
                  ('id', 'name', 'revision', 'model', 'provider', 'api_format', 'base_url', 'aws_region', 'reasoning_effort')},
        'versions': {name: version(name) for name in ('openai-agents', 'openai', 'litellm')},
        'max_turns': None, 'timeout_seconds': args.timeout, 'stream': False,
        'automatic_compaction': False,
    }

    def save(name, value):
        (args.output / name).write_text(redact(json.dumps(value, indent=2), [secret]))

    def event(name, value):
        with (args.output / name).open('a') as file:
            file.write(redact(json.dumps(value), [secret]) + '\n')

    def progress():
        metadata.update(elapsed_seconds=round(time.monotonic() - started, 2),
            usage=jsonable_encoder(usage, exclude={'request_usage_entries'}), tool_counts=tool_counts)
        save('result.json', metadata)

    async def record_usage(value):
        usage.add(value)
        event('usage.jsonl', {'call': usage.requests, 'elapsed_seconds': round(time.monotonic() - started, 2),
            'usage': jsonable_encoder(value, exclude={'request_usage_entries'})})
        progress()
        print(json.dumps({'calls': usage.requests, 'input': usage.input_tokens,
            'cached': usage.input_tokens_details.cached_tokens, 'output': usage.output_tokens,
            'elapsed_seconds': metadata['elapsed_seconds']}), flush=True)

    async def record_activity(value):
        if value['kind'] == 'tool' and value['status'] == 'inProgress':
            tool_counts[value['title']] = tool_counts.get(value['title'], 0) + 1
        event('activity.jsonl', value)

    real_post = AsyncHTTPHandler.post
    real_response = AsyncResponses.create
    response_status = None

    async def observed_response(client, **kwargs):
        nonlocal response_status
        inputs = kwargs['input']
        details = {'elapsed_seconds': round(time.monotonic() - started, 2),
            'reasoning': kwargs.get('reasoning'), 'max_output_tokens': kwargs.get('max_output_tokens'),
            'input_items': len(inputs),
            'reasoning_items': sum(item.get('type') == 'reasoning' for item in inputs)}
        response = await real_response(client, **kwargs)
        response_status = response.status
        details.update(status=response.status, model=response.model,
            incomplete_details=response.incomplete_details.model_dump() if response.incomplete_details else None,
            usage=response.usage.model_dump() if response.usage else None)
        if isinstance(details['reasoning'], Reasoning):
            details['reasoning'] = details['reasoning'].model_dump(exclude_none=True)
        # SDK omitted values are sentinels, not JSON values. Never retain raw input/output reasoning.
        for key in ('reasoning', 'max_output_tokens'):
            if not isinstance(details[key], (dict, int, type(None))):
                details[key] = None
        event('wire-usage.jsonl', details)
        return response

    async def observed_post(client, url, **kwargs):
        # Record only request settings and raw counters, never credentials or thinking content.
        body = json.loads(kwargs['data'])
        messages = body.get('messages', [])
        blocks = [block for message in messages for block in message.get('content', []) if isinstance(block, dict)]
        details = {'elapsed_seconds': round(time.monotonic() - started, 2),
            'thinking': body.get('thinking'), 'output_config': body.get('output_config'),
            'max_tokens': body.get('max_tokens'), 'message_count': len(messages),
            'signed_thinking_blocks': sum(block.get('type') == 'thinking' and bool(block.get('signature')) for block in blocks),
            'cache_checkpoints': sum('cache_control' in block for block in [*body.get('system', []), *blocks])}
        response = await real_post(client, url, **kwargs)
        payload = response.json()
        details.update(status_code=response.status_code, stop_reason=payload.get('stop_reason'), usage=payload.get('usage'))
        event('wire-usage.jsonl', details)
        return response

    (args.output / 'objective.md').write_text(objective)
    (args.output / 'system-prompt.md').write_text(get_prompt(config, [], []))
    save('config.json', config)
    progress()
    try:
        observation = (patch.object(AsyncResponses, 'create', observed_response)
            if config['model']['api_format'] == 'responses'
            else patch.object(AsyncHTTPHandler, 'post', observed_post))
        with observation:
            result = await agent(config, objective, credentials=credentials,
                timeout_seconds=args.timeout, max_turns=None, stream=False,
                on_usage=record_usage, on_activity=record_activity)
        if config['model']['api_format'] == 'responses' and response_status != 'completed':
            raise RuntimeError('Provider returned an incomplete response')
        document = result.final_output
        (args.output / 'document.md').write_text(redact(document, [secret]))
        for item in result.to_input_list():
            if item.get('type') in ('function_call', 'function_call_output'):
                event('tools.jsonl', item)
        metadata.update(status='succeeded', document_characters=len(document))
    except Exception as error:
        metadata.update(status='failed', error_type=type(error).__name__, error_message=redact(str(error), [secret]))
        print(f'Benchmark failed: {type(error).__name__}. See recorded usage and activity.', flush=True)
        raise SystemExit(1) from None
    finally:
        metadata['finished_at'] = datetime.now(UTC).isoformat()
        progress()


if __name__ == '__main__':
    asyncio.run(main())
