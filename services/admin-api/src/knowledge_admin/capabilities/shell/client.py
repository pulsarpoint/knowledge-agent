"""Run native commands in the separate shell worker, with the agent's selected paths."""

import json
import os
from typing import Annotated

import httpx
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.files import _roots
from knowledge_admin.sessions.activity import redact
from knowledge_admin.tools.registry import tool


def check_shell_run(access: dict, **_) -> str | None:
    """None when allowed, else the refusal text: the shell reads the run's code projects and collections."""
    if not access_rules.has_any(access, 'code_project') and not access_rules.has_any(access, 'collection'):
        return access_rules.refuse_none('code_project', 'collection')
    return None


@tool
async def shell_run(
    context: RunContext,
    command: Annotated[str, Field(description='Bash command or multiline script, up to 20000 characters.')],
    timeout_seconds: Annotated[int, Field(description='Command timeout from 1 to 300 seconds.')] = 60,
) -> str:
    """Run Bash commands or Python scripts in an isolated Linux workspace.

    Available programs include bash, python/python3, rg, ast-grep, git, sed, awk,
    find and standard Linux utilities. Pipelines, Bash arrays and heredocs work.
    For Python use: python - <<'PY'
    import json
    print(json.dumps({'ok': True}))
    PY
    Use rg -n 'symbol' /work/data/code/project to locate code, then ast-grep or sed
    to inspect complete functions or focused ranges. Python's sqlite3 module can
    query configured graph databases; use sqlite3.connect('file:PATH?mode=ro', uri=True).
    Quote paths and use single quotes around ast-grep patterns containing $.

    The working directory is writable scratch space shared across shell calls
    within this run. Selected code, documents and graph paths retain the absolute
    paths in the agent resources and are read-only. Other projects, credentials
    and network access are unavailable. Use ssh_run for configured remote servers.
    Shell variables do not persist between calls; save intermediate work to files.
    Inspect exit_code, stderr and timed_out. Output over 32 KiB per stream is
    truncated: write large results to scratch files and inspect focused sections.

    Use shell_run for Bash pipelines, rg, ast-grep and Python scripts. Selected resource paths are mounted read-only at
    the same absolute paths; pwd shows writable scratch space. Inspect focused source ranges; use code_graph for indexed
    relationships when available. Scratch files persist between tool calls within this run. Network access is disabled
    in the shell; use ssh_run for configured servers."""
    if refusal := check_shell_run(context['config']['access'], command=command):
        return refusal
    if not context['config']['enabled'] or not context['shell_enabled']:
        raise ValueError('Local command execution is disabled for this agent run.')
    if not command.strip() or '\0' in command or len(command) > 20000 or not 1 <= timeout_seconds <= 300:
        raise ValueError('Provide a command up to 20000 characters and a timeout from 1 to 300 seconds.')
    endpoint = os.environ.get('AGENT_SHELL_URL')
    if not endpoint:
        raise ValueError('The shell worker is not configured. Set AGENT_SHELL_URL.')
    workspace = context['workspace']
    paths = [str(path) for path in _roots(context) if path != workspace]
    paths.extend(
        project['graph_path']
        for project in access_rules.listing(context['config']['access'], 'code_project')
        if project.get('graph_path')
    )
    async with httpx.AsyncClient(timeout=timeout_seconds + 10, trust_env=False) as client:
        response = await client.post(
            endpoint.rstrip('/') + '/run',
            json={
                'command': command,
                'timeout_seconds': timeout_seconds,
                'workspace': str(workspace),
                'read_paths': paths,
            },
        )
        response.raise_for_status()
        return redact(json.dumps(response.json()), context['credentials'].values())
