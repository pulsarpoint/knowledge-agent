"""Stand-ins for the Agents SDK objects a native turn's hooks receive (model responses, tool contexts), shaped like the
real ones (test_native_session_events checks the fields)."""
import asyncio
import json
from types import SimpleNamespace

from agents.run_config import CallModelData, ModelInputData

from claude_code_fakes import Recorder as _Recorder


class Recorder(_Recorder):
    """Collects events like RunRecorder.add and counts flushes."""

    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def reasoning(text: str):
    return SimpleNamespace(type='reasoning', summary=[SimpleNamespace(text=text)], content=[])


def message(text: str):
    return SimpleNamespace(type='message', content=[SimpleNamespace(text=text)])


def call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(type='function_call', call_id=call_id, name=name, arguments=json.dumps(arguments))


def response(*items, input_tokens: int = 0, output_tokens: int = 0, cached: int = 0):
    """A ModelResponse: its output items and its Usage."""
    return SimpleNamespace(output=list(items), usage=SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached)))


class Turn:
    """One scripted native turn while it runs: the model input so far (history, the user item, then each model call's
    output and tool results), the hooks, and the call_model_input_filter the runner passed."""

    def __init__(self, record: dict, hooks, writer, model_filter, items: list):
        self.record, self.hooks, self.writer, self.filter, self.items = record, hooks, writer, model_filter, items

    async def model_call(self) -> None:
        """What the SDK does before each model request: the filter sees (and may change) the input the model gets."""
        data = CallModelData(model_data=ModelInputData(input=list(self.items), instructions=self.writer.instructions),
                             agent=self.writer, context=None)
        model = data.model_data
        if self.filter is not None:
            model = await self.filter(data)
        self.record['inputs'].append(list(model.input))


class FakeAgent:
    """Stands in for knowledge_admin.agents.agent. Each call plays the next scripted turn like the real runtime: the
    SDK reads the session, adds the user item, runs the steps (model calls go through the call_model_input_filter, then
    the hooks), then adds the answer and returns, or raises the turn's error (after the user item was buffered). A turn
    listed in `gates` waits before it ends, until the gate opens or the turn is cancelled. `running` counts turns that
    have not ended yet."""

    def __init__(self, turns: list[dict], gates: dict | None = None):
        self.turns, self.gates = turns, gates or {}
        self.calls: list[dict] = []
        self.running = 0

    async def __call__(self, config, task, *, credentials, session, hooks, max_turns, turn_timeout_seconds,
                       call_model_input_filter, stream):
        index = len(self.calls)
        record = {'config': config, 'task': task, 'credentials': dict(credentials), 'history': await session.get_items(),
                  'max_turns': max_turns, 'turn_timeout_seconds': turn_timeout_seconds, 'stream': stream, 'inputs': [],
                  'tools': []}
        self.calls.append(record)
        self.running += 1
        try:
            script = self.turns[index]
            user = {'role': 'user', 'content': task}
            await session.add_items([user])
            writer = SimpleNamespace(name=config['name'], instructions='Native prompt for ' + config['name'])
            turn = Turn(record, hooks, writer, call_model_input_filter, [*record['history'], user])
            await hooks.on_agent_start(None, writer)
            for step in script.get('steps', []):
                await step(turn)
            if index in self.gates:
                await self.gates[index].wait()
            if 'error' in script:
                raise script['error']
            await session.add_items([{'role': 'assistant', 'content': script['answer']}])
            return SimpleNamespace(final_output=script['answer'])
        finally:
            self.running -= 1


def _input_item(item) -> dict:
    if item.type == 'function_call':
        return {'type': 'function_call', 'call_id': item.call_id, 'name': item.name}
    if item.type == 'message':
        return {'role': 'assistant', 'content': ''.join(part.text for part in item.content)}
    return {'type': item.type}


def llm(*items, **usage):
    """A model call: its input through the filter, its response through the hooks, its output into the next input."""
    async def step(turn: Turn):
        await turn.model_call()
        await turn.hooks.on_llm_end(None, turn.writer, response(*items, **usage))
        turn.items.extend(_input_item(item) for item in items)
    return step


def tool(call_id: str, name: str, output: str, *, started: asyncio.Event | None = None,
         gate: asyncio.Event | None = None):
    """A tool call's result; with `gate`, a slow tool that has `started` and runs until the gate opens."""
    async def step(turn: Turn):
        context, function = SimpleNamespace(tool_call_id=call_id), SimpleNamespace(name=name)
        await turn.hooks.on_tool_start(context, turn.writer, function)  # as the SDK, before the tool runs
        turn.record['tools'].append(call_id)
        if started is not None:
            started.set()
        if gate is not None:
            await gate.wait()
        await turn.hooks.on_tool_end(context, turn.writer, function, output)
        turn.items.append({'type': 'function_call_output', 'call_id': call_id, 'output': output})
    return step


def hang(where: str, started: asyncio.Event, cancelled: list[str]):
    """Work that never ends by itself: 'connect' (MCP servers, before the first model call), 'model' (a model request,
    after its input) or 'tool' (a slow tool call such as shell_run). Records its cancellation."""
    async def step(turn: Turn):
        if where == 'model':
            await turn.model_call()
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(where)
            raise
    return step


def stubborn(started: asyncio.Event, ignored: list[str], release: asyncio.Event):
    """Work that ignores cancellation (a tool that swallows CancelledError and keeps going) until `release` is set."""
    async def step(turn: Turn):
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                ignored.append('cancel')
    return step
