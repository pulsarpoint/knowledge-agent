"""The run context every capability receives. What a run may use is its access object (config['access']), decided by
knowledge_admin.access in each tool's check."""

from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NotRequired, TypedDict


class RunContext(TypedDict):
    """Backend-only inputs of one run: the configuration snapshot, resolved credentials, the scratch workspace and
    the tools offered. None of these is a model-supplied argument."""

    config: dict[str, Any]
    credentials: Mapping[str, str]
    workspace: Path
    tools: frozenset[str]  # the catalog tools offered to this run
    subagents: NotRequired[Any]  # the turn's knowledge_agents.subagents.Subagents (native sessions only)


# The id of the tool call running in this task: tools/sdk.py sets it around each invocation, run_subagent records its
# subagent under it. Concurrent calls run in their own tasks, so each sees its own id.
TOOL_CALL_ID: ContextVar[str | None] = ContextVar('tool_call_id', default=None)
