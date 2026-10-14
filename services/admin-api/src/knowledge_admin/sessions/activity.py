"""Bounded, redacted SDK activity for the UI; native SDK tracing owns spans."""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from agents import ItemHelpers, RunHooks
from agents.usage import Usage
from openai.types.responses import ResponseTextDeltaEvent, ResponseTextDoneEvent
from opentelemetry import trace

ActivityCallback = Callable[[dict[str, Any]], Awaitable[None]]
UsageCallback = Callable[[Usage], Awaitable[None]]
logger = logging.getLogger(__name__)
EXPERT_FAILED = re.compile(r'Expert [^\n]+? failed: ')  # experts.FAILED: Expert {name} failed: {message}


def redact(text: str, secrets: Iterable[str], *, partial: bool = False) -> str:
    """Withhold secret prefixes at stream boundaries before publishing snapshots."""
    values = sorted(
        {
            encoded
            for value in secrets
            if value
            for encoded in (value, json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1])
        },
        key=len,
        reverse=True,
    )
    for value in values:
        text = text.replace(value, '[redacted]')
    if partial:
        longest = 0
        for value in values:
            for length in range(min(len(text), len(value) - 1), longest, -1):
                if text.endswith(value[:length]):
                    longest = length
                    break
        if longest:
            text = text[:-longest] + '[redacted]'
    return text


def tool_failed(detail: str) -> bool:
    """A tool result reported as failed: the SDK's error text, a failed subagent (run_subagent) or expert
    (Expert <name> failed: ), or JSON with error, isError, timed_out or a non-zero exit_code."""
    failed = detail.startswith(('An error occurred while running the tool', 'Subagent failed: '))
    failed = failed or EXPERT_FAILED.match(detail) is not None
    try:
        output = json.loads(detail)
    except (ValueError, TypeError):
        output = None
    if isinstance(output, dict):
        failed = failed or bool(output.get('error') or output.get('isError') or output.get('timed_out'))
        failed = failed or output.get('exit_code', 0) not in (0, None)
    return failed


class SdkActivity(RunHooks):
    """Publish Agents SDK messages and tool lifecycle events to the UI."""

    def __init__(
        self, secrets: Iterable[str], callback: ActivityCallback | None, on_usage: UsageCallback | None = None
    ):
        self.secrets = tuple(secrets)
        self.callback = callback
        self.on_usage = on_usage
        self.run_id = uuid4().hex
        self.items: dict[str, dict[str, Any]] = {}
        self.published: dict[str, float] = {}

    async def on_llm_end(self, context, agent, response) -> None:
        if self.on_usage is not None:
            await self.on_usage(response.usage)

    async def on_tool_start(self, context, agent, tool) -> None:
        key = context.tool_call_id
        title = tool.name
        self.items[key] = {
            'id': self.run_id + ':' + key,
            'kind': 'tool',
            'title': title,
            'detail': context.tool_arguments,
            'status': 'inProgress',
            'time': datetime.now(UTC).isoformat(),
        }
        await self.publish(key)

    async def on_tool_end(self, context, agent, tool, result: object) -> None:
        key = context.tool_call_id
        detail = str(result)
        failed = tool_failed(detail)
        self.items[key]['detail'] = detail
        self.items[key]['status'] = 'failed' if failed else 'completed'
        await self.publish(key)

    async def notify(self, event) -> None:
        # Publish text only. Reasoning and raw provider payloads stay private.
        if event.type == 'raw_response_event' and isinstance(event.data, ResponseTextDeltaEvent):
            key = event.data.item_id
            previous = self.items.get(key, {})
            text = previous.get('detail', '') + event.data.delta
            status = 'inProgress'
        elif event.type == 'raw_response_event' and isinstance(event.data, ResponseTextDoneEvent):
            key, text, status = event.data.item_id, event.data.text, 'completed'
        elif event.type == 'run_item_stream_event' and event.item.type == 'message_output_item':
            key, text, status = event.item.raw_item.id, ItemHelpers.text_message_output(event.item), 'completed'
        else:
            return
        self.items[key] = {
            'id': self.run_id + ':' + key,
            'kind': 'agentMessage',
            'title': 'Agent update',
            'detail': text[:131072],
            'status': status,
            'time': self.items.get(key, {}).get('time', datetime.now(UTC).isoformat()),
        }
        if status != 'inProgress' or monotonic() - self.published.get(key, 0) >= 0.25:
            await self.publish(key)

    async def publish(self, key: str) -> None:
        public = {**self.items[key]}
        public['title'] = redact(public['title'], self.secrets)[:512]
        partial = public['status'] == 'inProgress' or len(public['detail']) >= 131072
        clean = redact(public['detail'], self.secrets, partial=partial)
        public['detail'] = clean[:4000]
        # PostgreSQL NOTIFY is bounded to 8 KB, including envelope fields.
        while len(json.dumps(public).encode()) > 6500:
            public['detail'] = public['detail'][: len(public['detail']) // 2]
        if len(public['detail']) < len(clean):
            public['detail'] += '\n[Preview truncated; open the trace for more.]'
        self.published[key] = monotonic()
        if self.callback is not None:
            try:
                await self.callback(public)
            except Exception:
                logger.warning('Live agent activity could not be delivered.')

    async def on_agent_start(self, context, agent):
        key = self.run_id + ':' + agent.name
        self.items[key] = {
            'id': key,
            'kind': 'agentMessage',
            'title': agent.name,
            'detail': 'Started.',
            'status': 'inProgress',
            'time': datetime.now(UTC).isoformat(),
        }
        current = trace.get_current_span().get_span_context()
        if current.is_valid:
            self.items[key].update(trace_id=format(current.trace_id, '032x'), span_id=format(current.span_id, '016x'))
        await self.publish(key)

    def close(self) -> None:
        self.items.clear()
