"""The run context every capability receives, and the per-run rule for kinds a run was given nothing of."""
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from knowledge_admin.collections import document_collections
from knowledge_admin.knowledge_agents.config import per_run, require_run_resources


class RunContext(TypedDict):
    """Backend-only inputs of one run: the configuration snapshot, resolved credentials, the scratch workspace and
    whether the shell may run. None of these is a model-supplied argument."""

    config: dict[str, Any]
    credentials: Mapping[str, str]
    workspace: Path
    shell_enabled: bool
    tools: frozenset[str]  # the catalog tools offered to this run
    mcp_tools: NotRequired[dict[str, str]]  # the MCP tools as the model sees them → mcp:<server id>:<tool name>
    subagents: NotRequired[Any]  # the turn's knowledge_agents.subagents.Subagents (native sessions only)


# The id of the tool call running in this task: tools/sdk.py sets it around each invocation, run_subagent records its
# subagent under it. Concurrent calls run in their own tasks, so each sees its own id.
TOOL_CALL_ID: ContextVar[str | None] = ContextVar('tool_call_id', default=None)


NOTHING_GIVEN = {
    'code': 'No code project was given for this run.',
    'documents': 'No document collection was given for this run.',
    'ssh': 'No server was given for this run.',
    'files': 'No code project or document collection was given for this run.',
}


def require_given(config: dict[str, Any], kind: str) -> None:
    """Per-run agents: refuse a tool whose kind the run was given nothing of. Their configuration lists exactly the
    run's resources, so an empty list means nothing, never every resource. Fixed agents are unaffected."""
    if not per_run(config):
        return
    require_run_resources(config)
    code = config['code']['projects'] if config['code']['enabled'] else []
    collections = document_collections(config)[1] if config['documents']['enabled'] else []
    empty = {'code': not code, 'documents': not collections, 'ssh': not config['ssh']['servers'],
             'files': not code and not collections}
    if empty[kind]:
        raise ValueError(NOTHING_GIVEN[kind])


NOT_AVAILABLE = 'This {kind} is not available in this run.'
KIND_NAMES = {'server': 'server', 'document': 'document', 'service': 'service', 'code_project': 'code project'}
ACCESS = {
    'server': lambda config: config['ssh']['servers'] if config['ssh']['enabled'] else [],
    'document': lambda config: config.get('document_access', []),
    'service': lambda config: config['services']['items'] if config['services']['enabled'] else [],
    'code_project': lambda config: config['code']['projects'] if config['code']['enabled'] else [],
}


def allowed(config: dict[str, Any], kind: str, item_id: str, *, write: bool = False) -> dict[str, Any]:
    """The pinned entry of one resource this run may use: the run's allowlist, fixed when it started. Every tool that
    takes a resource id checks it here; anything else — and, with write, an entry that is not editable — is refused."""
    entry = next((item for item in ACCESS[kind](config) if str(item['id']) == str(item_id)), None)
    if entry is None or (write and not entry.get('editable')):
        raise ValueError(NOT_AVAILABLE.format(kind=KIND_NAMES[kind]))
    return entry
