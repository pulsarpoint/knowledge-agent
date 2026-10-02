"""Run native commands in the separate shell worker, with the agent's selected paths."""

import json
import os

import httpx
from agents import RunContextWrapper, function_tool

from knowledge_admin.agent_activity import redact
from knowledge_admin.tools import AgentContext
from knowledge_admin.tools.files import _roots


@function_tool
async def shell_run(ctx: RunContextWrapper[AgentContext], command: str,
                    timeout_seconds: int = 60) -> str:
    """Run Bash commands or Python scripts in an isolated Linux workspace.

    Available programs include bash, python/python3, rg, ast-grep, git, sed, awk,
    find and standard Linux utilities. Pipelines, Bash arrays and heredocs work.
    For Python use: python - <<'PY'\nimport json\nprint(json.dumps({'ok': True}))\nPY
    Use rg -n 'symbol' /work/code/project to locate code, then ast-grep or sed
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

    Args:
        command: Bash command or multiline script, up to 20000 characters.
        timeout_seconds: Command timeout from 1 to 300 seconds.
    """
    if not ctx.context['config']['enabled'] or not ctx.context['shell_enabled']:
        raise ValueError('Local command execution is disabled for this agent run.')
    if not command.strip() or '\0' in command or len(command) > 20000 or not 1 <= timeout_seconds <= 300:
        raise ValueError('Provide a command up to 20000 characters and a timeout from 1 to 300 seconds.')
    endpoint = os.environ.get('AGENT_SHELL_URL')
    if not endpoint:
        raise ValueError('The shell worker is not configured. Set AGENT_SHELL_URL.')
    workspace = ctx.context['workspace']
    paths = [str(path) for path in _roots(ctx.context) if path != workspace]
    if ctx.context['config']['code']['enabled']:
        paths.extend(graph['graph_path'] for graph in ctx.context['config']['code'].get('graphs', []))
    async with httpx.AsyncClient(timeout=timeout_seconds + 10, trust_env=False) as client:
        response = await client.post(endpoint.rstrip('/') + '/run', json={
            'command': command, 'timeout_seconds': timeout_seconds,
            'workspace': str(workspace), 'read_paths': paths})
        response.raise_for_status()
        return redact(json.dumps(response.json()), ctx.context['credentials'].values())
