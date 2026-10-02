"""Run one documentation objective with native Claude tools and record the result.

Standalone benchmark: no application imports, database access, or production changes.
Run inside the accompanying container with read-only source and graph mounts.
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from claude_agent_sdk.types import StreamEvent


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--objective', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--graph', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max'], default='xhigh')
    args = parser.parse_args()
    token = os.environ['AWS_BEARER_TOKEN_BEDROCK']
    region = os.environ['AWS_REGION']
    objective = args.objective.read_text(encoding='utf-8')
    source = args.source.resolve(strict=True)
    graph = args.graph.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'metadata.json').exists():
        raise FileExistsError('Use a fresh output directory for each benchmark.')

    # Never record credentials or private reasoning blocks in benchmark artifacts.
    def redact(value: str) -> str:
        return value.replace(token, '[REDACTED]')

    def write_json(name: str, value: dict) -> None:
        (output / name).write_text(redact(json.dumps(value, indent=2)), encoding='utf-8')

    tools = ['Read', 'Glob', 'Grep', 'Bash']
    workspace = Path('/tmp/claude-workspace')
    workspace.mkdir(exist_ok=True)
    options = ClaudeAgentOptions(
        model=args.model,
        effort=args.effort,
        cwd=workspace,
        system_prompt={'type': 'preset', 'preset': 'claude_code'},
        tools=tools,
        allowed_tools=tools,
        permission_mode='dontAsk',
        setting_sources=[],
        mcp_servers={},
        strict_mcp_config=True,
        include_partial_messages=True,
        sandbox={
            'enabled': True,
            'autoAllowBashIfSandboxed': True,
            'allowUnsandboxedCommands': False,
            'failIfUnavailable': True,
            'enableWeakerNestedSandbox': True,
            'network': {'allowedDomains': []},
        },
        env={
            'CLAUDE_CODE_USE_BEDROCK': '1',
            'AWS_REGION': region,
            'AWS_BEARER_TOKEN_BEDROCK': token,
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'CLAUDE_CONFIG_DIR': '/tmp/claude-benchmark',
        },
        extra_args={'no-session-persistence': None},
    )
    metadata = {
        'sdk': 'claude-agent-sdk', 'sdk_version': version('claude-agent-sdk'),
        'model': args.model, 'effort': args.effort, 'region': region,
        'source': str(source), 'graph': str(graph), 'tools': tools, 'cwd': str(workspace),
        'objective_characters': len(objective),
        'objective_sha256': hashlib.sha256(objective.encode()).hexdigest(),
        'graph_sha256': hashlib.sha256(graph.read_bytes()).hexdigest(),
        'system_prompt': 'claude_code preset', 'max_turns': None,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    write_json('metadata.json', metadata)
    (output / 'objective.md').write_text(objective, encoding='utf-8')
    started = time.monotonic()
    counts: Counter[str] = Counter()
    calls: dict[str, dict] = {}
    current_id = ''
    final = None

    with (output / 'events.jsonl').open('w', encoding='utf-8') as events:
        def record(kind: str, **fields: object) -> None:
            payload = {'event': kind, 'elapsed_seconds': round(time.monotonic() - started, 2), **fields}
            line = redact(json.dumps(payload))
            events.write(line + '\n')
            events.flush()
            print(line, flush=True)

        record('started', **metadata)
        try:
            async for message in query(prompt=objective, options=options):
                if isinstance(message, SystemMessage):
                    if message.subtype == 'init':
                        record('init', model=message.data.get('model'), tools=message.data.get('tools'),
                               session_id=message.data.get('session_id'),
                               claude_code_version=message.data.get('claude_code_version'))
                    elif message.subtype == 'compact_boundary':
                        record('compaction', data=message.data)
                elif isinstance(message, StreamEvent):
                    event = message.event
                    if event['type'] == 'message_start':
                        data = event['message']
                        current_id = data['id']
                        calls[current_id] = {'model': data.get('model'), 'usage': data.get('usage', {})}
                    elif event['type'] == 'message_delta' and current_id:
                        calls[current_id]['usage'].update(event.get('usage', {}))
                        calls[current_id]['stop_reason'] = event.get('delta', {}).get('stop_reason')
                    elif event['type'] == 'message_stop' and current_id:
                        record('model_call', message_id=current_id, **calls[current_id])
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            counts[block.name] += 1
                            record('tool_call', id=block.id, tool=block.name, arguments=block.input)
                        elif isinstance(block, TextBlock):
                            record('assistant_text', text=block.text)
                elif isinstance(message, UserMessage) and isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            record('tool_result', id=block.tool_use_id, is_error=block.is_error,
                                   result=block.content)
                elif isinstance(message, ResultMessage):
                    final = message
                    record('result', **asdict(message))
        except Exception as error:
            record('error', error_type=type(error).__name__, message=str(error))
            write_json('result.json', {**metadata, 'status': 'error', 'error': str(error),
                                      'elapsed_seconds': round(time.monotonic() - started, 2),
                                      'observed_model_calls': calls, 'tool_calls': dict(counts)})
            raise RuntimeError(redact(str(error))) from None

    if final is None:
        raise RuntimeError('Claude returned no terminal result; inspect events.jsonl.')
    result = asdict(final)
    # Anthropic's ordinary input excludes both cache creation and cache reads.
    usage = final.usage or {}
    ordinary = usage.get('input_tokens', 0)
    written = usage.get('cache_creation_input_tokens', 0)
    cached = usage.get('cache_read_input_tokens', 0)
    generated = usage.get('output_tokens', 0)
    usage_source = 'ResultMessage.usage (main loop only)'
    if final.model_usage:
        # Native compaction is billable too, but is absent from main-loop usage.
        ordinary = sum(item['inputTokens'] for item in final.model_usage.values())
        written = sum(item['cacheCreationInputTokens'] for item in final.model_usage.values())
        cached = sum(item['cacheReadInputTokens'] for item in final.model_usage.values())
        generated = sum(item['outputTokens'] for item in final.model_usage.values())
        usage_source = 'ResultMessage.model_usage (includes native compaction)'
    normalized = {
        'ordinary_input_tokens': ordinary, 'cache_write_input_tokens': written,
        'cached_input_tokens': cached, 'uncached_input_tokens': ordinary + written,
        'input_tokens': ordinary + written + cached, 'output_tokens': generated,
        'total_tokens': ordinary + written + cached + generated,
        'source': usage_source,
    }
    success = not final.is_error and final.subtype == 'success' and bool(final.result)
    write_json('result.json', {
        **metadata, 'status': 'completed' if success else 'error',
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'tool_calls': dict(counts), 'observed_model_calls': len(calls),
        'normalized_usage': normalized, 'result': result,
    })
    if not success:
        raise RuntimeError(f'Claude benchmark failed: {final.subtype}; inspect result.json.')
    (output / 'document.md').write_text(redact(final.result), encoding='utf-8')
    print(json.dumps({'completed': True, 'usage': normalized, 'tool_calls': dict(counts)}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
