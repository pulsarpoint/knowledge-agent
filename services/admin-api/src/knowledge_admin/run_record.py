"""Complete, durable record of an agent run: instructions, input, reasoning, tool calls, outputs and answer."""

from collections.abc import Iterable
from typing import Any

from agents import RunHooks
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from knowledge_admin.agent_activity import redact

MAX_TEXT = 1_000_000
TRUNCATED = '\n[truncated]'

_store = None
_app_name = None


def configure(store, app_name: str | None) -> None:
    """Set where records are written; called once by each process that runs agents."""
    global _store, _app_name
    _store, _app_name = store, app_name


def _insert(store, events: list[dict[str, Any]]) -> None:
    with store.connect() as conn:
        project = None
        for event in events:
            if event['project_id'] is None:
                project = project or store.project(conn, _app_name)
            conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''', (event['project_id'] or project, event['run_key'], event['seq'],
                event['kind'], event['agent_id'], event['actor_id'], Jsonb(event['payload'])))


class RunRecorder(RunHooks):
    """Buffer one run's events in order; flush() writes them in one transaction."""

    def __init__(self, *, run_key: str, project_id: str | None, agent_id: str, actor_id: str | None,
                 secrets: Iterable[str]):
        self.run_key, self.project_id, self.agent_id, self.actor_id = run_key, project_id, agent_id, actor_id
        self._secrets = secrets  # read live: callers may add keys after the run starts
        self.events: list[dict[str, Any]] = []
        self.flushed = 0

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            text = redact(value, tuple(secret for secret in self._secrets if secret))
            return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + TRUNCATED
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append({'run_key': self.run_key, 'seq': len(self.events) + 1, 'kind': kind,
                            'project_id': self.project_id, 'agent_id': self.agent_id, 'actor_id': self.actor_id,
                            'payload': self._clean(jsonable_encoder(payload))})

    def flush(self) -> None:
        pending = self.events[self.flushed:]
        if not pending:
            return
        if _store is None:
            raise RuntimeError('Run records are not configured')
        _insert(_store, pending)
        self.flushed = len(self.events)

    async def on_agent_start(self, context, agent) -> None:
        instructions = agent.instructions if isinstance(agent.instructions, str) else '(generated at run time)'
        self.add('instructions', {'agent': agent.name, 'text': instructions or ''})

    async def on_llm_end(self, context, agent, response) -> None:
        for item in response.output:
            kind = getattr(item, 'type', None)
            if kind == 'reasoning':
                parts = [part.text for part in (getattr(item, 'summary', None) or [])]
                parts += [part.text for part in (getattr(item, 'content', None) or [])]
                if parts:
                    self.add('reasoning', {'agent': agent.name, 'text': '\n'.join(parts)})
                else:
                    self.add('reasoning', {'agent': agent.name, 'available': False})
            elif kind == 'message':
                text = ''.join(getattr(part, 'text', '') for part in item.content)
                self.add('message', {'agent': agent.name, 'text': text})
            elif kind == 'function_call':
                self.add('tool_call', {'agent': agent.name, 'name': item.name, 'call_id': item.call_id,
                                       'arguments': item.arguments})
        usage = response.usage
        self.add('usage', {'agent': agent.name, 'input_tokens': usage.input_tokens,
                           'output_tokens': usage.output_tokens, 'total_tokens': usage.total_tokens})

    async def on_tool_end(self, context, agent, tool, result) -> None:
        self.add('tool_result', {'agent': agent.name, 'name': tool.name,
                                 'call_id': getattr(context, 'tool_call_id', None), 'output': str(result)})


class CompositeHooks(RunHooks):
    """Forward every SDK lifecycle hook to several hook objects, in order."""

    def __init__(self, *hooks: RunHooks):
        self.hooks = [hook for hook in hooks if hook is not None]

    async def on_llm_start(self, *args):
        for hook in self.hooks:
            await hook.on_llm_start(*args)

    async def on_llm_end(self, *args):
        for hook in self.hooks:
            await hook.on_llm_end(*args)

    async def on_agent_start(self, *args):
        for hook in self.hooks:
            await hook.on_agent_start(*args)

    async def on_agent_end(self, *args):
        for hook in self.hooks:
            await hook.on_agent_end(*args)

    async def on_handoff(self, *args):
        for hook in self.hooks:
            await hook.on_handoff(*args)

    async def on_tool_start(self, *args):
        for hook in self.hooks:
            await hook.on_tool_start(*args)

    async def on_tool_end(self, *args):
        for hook in self.hooks:
            await hook.on_tool_end(*args)
