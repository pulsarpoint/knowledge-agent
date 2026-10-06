"""Stand-ins for claude_agent_sdk message and block types: the worker recognises them by class name and fields."""
import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str = ''


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool | None = None


@dataclass
class AssistantMessage:
    content: list[Any]
    model: str = 'claude'
    error: str | None = None


@dataclass
class UserMessage:
    content: Any


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultMessage:
    subtype: str
    is_error: bool
    session_id: str
    result: str | None = None
    usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None
    errors: list[str] | None = None


class Recorder:
    """Collects run events like RunRecorder.add."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def add(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))


class FakeClient:
    """A ClaudeSDKClient stand-in that is also its factory. Each query plays the next scripted turn: its items are
    yielded in order and the last one is the ResultMessage, or an exception to raise. A turn listed in `gates` waits
    before its last item until the gate opens or interrupt() is called; an interrupted turn ends with an error
    ResultMessage, as the CLI does."""

    def __init__(self, turns: list[list], gates: dict[int, asyncio.Event] | None = None):
        self.turns, self.gates = turns, gates or {}
        self.options: list[dict] = []
        self.queries: list[str] = []
        self.interrupts = 0
        self.entered = self.exited = 0
        self.interrupted = asyncio.Event()

    def __call__(self, options: dict):
        self.options.append(options)
        return self

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc):
        self.exited += 1
        return False

    async def query(self, text: str) -> None:
        self.queries.append(text)

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.interrupted.set()

    async def receive_response(self):
        index = len(self.queries) - 1
        *messages, last = self.turns[index]
        for message in messages:
            yield message
        if index in self.gates:
            gate = asyncio.ensure_future(self.gates[index].wait())
            stop = asyncio.ensure_future(self.interrupted.wait())
            await asyncio.wait({gate, stop}, return_when=asyncio.FIRST_COMPLETED)
            gate.cancel()
            stop.cancel()
            if self.interrupted.is_set():
                self.interrupted.clear()
                yield ResultMessage('error_during_execution', True, 'cli-session', result='')
                return
        if isinstance(last, BaseException):
            raise last
        yield last
