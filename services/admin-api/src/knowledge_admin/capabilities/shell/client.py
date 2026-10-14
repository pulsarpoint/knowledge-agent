"""Run native commands in the separate shell worker, with the agent's selected paths."""

import json
import os

import httpx

from knowledge_admin.sessions.activity import redact
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.files import _roots


async def shell_run(context: RunContext, command: str, timeout_seconds: int = 60) -> str:
    if not context['config']['enabled'] or not context['shell_enabled']:
        raise ValueError('Local command execution is disabled for this agent run.')
    if not command.strip() or '\0' in command or len(command) > 20000 or not 1 <= timeout_seconds <= 300:
        raise ValueError('Provide a command up to 20000 characters and a timeout from 1 to 300 seconds.')
    endpoint = os.environ.get('AGENT_SHELL_URL')
    if not endpoint:
        raise ValueError('The shell worker is not configured. Set AGENT_SHELL_URL.')
    workspace = context['workspace']
    paths = [str(path) for path in _roots(context) if path != workspace]
    if context['config']['code']['enabled']:
        paths.extend(graph['graph_path'] for graph in context['config']['code'].get('graphs', []))
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
