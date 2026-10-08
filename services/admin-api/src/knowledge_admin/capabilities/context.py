"""The run context every capability receives, and the per-run rule for kinds a run was given nothing of."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from knowledge_admin.collections import document_collections
from knowledge_admin.knowledge_agents.config import per_run, require_run_resources


class RunContext(TypedDict):
    """Backend-only inputs of one run: the configuration snapshot, resolved credentials, the scratch workspace and
    whether the shell may run. None of these is a model-supplied argument."""

    config: dict[str, Any]
    credentials: Mapping[str, str]
    workspace: Path
    shell_enabled: bool


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
