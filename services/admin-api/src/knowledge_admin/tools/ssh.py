"""The SSH function tool uses the application's existing SSH transport."""

import json

from agents import RunContextWrapper, function_tool

from knowledge_admin.agent_activity import redact
from knowledge_admin.ssh import SSHConnection, execute
from knowledge_admin.tools import AgentContext


@function_tool
async def ssh_run(ctx: RunContextWrapper[AgentContext], server_id: str, command: str,
                  as_root: bool = False, timeout_seconds: int = 60) -> str:
    """Execute a command on a server selected for this agent and return its output.

    Follow the selected server's instructions. Use as_root for sudo only when
    allow_sudo is enabled. The backend supplies the login, key and host verification;
    never request credentials from the user or include them in command arguments.

    Args:
        server_id: Exact server ID from the agent's configured SSH server list.
        command: Shell command to run remotely; check the returned exit_code.
        as_root: Request noninteractive sudo to root for this command.
        timeout_seconds: Remote command time limit, from 1 to 300 seconds.
    """
    config = ctx.context['config']
    if not config['enabled'] or not config['ssh']['enabled']:
        raise ValueError('SSH is disabled for this agent.')
    server = next((item for item in config['ssh']['servers'] if item['id'] == server_id), None)
    if server is None or not server['enabled'] or not server['ssh_allowed']:
        raise ValueError('This server is not available to this agent.')
    if not command.strip() or '\0' in command or len(command) > 20000 or not 1 <= timeout_seconds <= 300:
        raise ValueError('Provide a command up to 20000 characters and a timeout from 1 to 300 seconds.')
    reference = server['private_key_ref']
    if reference is None or not ctx.context['credentials'].get(reference['id']):
        raise ValueError('The configured SSH credential was not supplied by the backend.')
    connection = SSHConnection(hostname=server['hostname'], port=server['port'], username=server['username'],
        known_hosts=server['known_hosts'], private_key=ctx.context['credentials'][reference['id']],
        allow_sudo=server['allow_sudo'])
    result = await execute(connection, command, as_root=as_root, seconds=timeout_seconds)
    return redact(json.dumps({'server_id': server_id, **result}), ctx.context['credentials'].values())
