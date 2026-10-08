"""The SSH function tool uses the application's existing SSH transport."""

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities import ssh
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
    return await ssh.ssh_run(ctx.context, server_id, command, as_root, timeout_seconds)
