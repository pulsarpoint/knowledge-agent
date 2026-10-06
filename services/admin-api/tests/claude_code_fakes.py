"""Stand-ins for claude_agent_sdk message and block types: the worker recognises them by class name and fields."""
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
