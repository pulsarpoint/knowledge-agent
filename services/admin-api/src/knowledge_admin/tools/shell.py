"""Local command execution for an explicitly enabled, isolated agent worker.

The caller supplies process/filesystem isolation. This tool's cwd and clean
subprocess environment are not a sandbox; do not enable it in the admin API.
"""

import asyncio
import json
import os
import signal

from agents import RunContextWrapper, function_tool

from knowledge_admin.agent_activity import redact
from knowledge_admin.tools import AgentContext


@function_tool
async def shell_run(ctx: RunContextWrapper[AgentContext], command: str,
                    timeout_seconds: int = 60) -> str:
    """Execute a local shell command in this agent's working directory.

    Use this for local programs and command pipelines in an enabled execution
    worker. Inspect exit_code; timeouts or cancellation do not undo completed work.
    Use ssh_run for remote operations.

    Args:
        command: Command to execute with /bin/sh; maximum 20000 characters.
        timeout_seconds: Time limit for this command, from 1 to 300 seconds.
    """
    if not ctx.context['config']['enabled'] or not ctx.context['shell_enabled']:
        raise ValueError('Local command execution is disabled for this agent run.')
    if not command.strip() or '\0' in command or len(command) > 20000 or not 1 <= timeout_seconds <= 300:
        raise ValueError('Provide a command up to 20000 characters and a timeout from 1 to 300 seconds.')
    workspace = ctx.context['workspace']
    if not workspace.is_absolute() or not workspace.is_dir() or workspace.resolve() != workspace:
        raise ValueError('The agent workspace is unavailable.')
    process = await asyncio.create_subprocess_exec('/bin/sh', '-c', command, cwd=workspace,
        env={'PATH': os.defpath, 'HOME': str(workspace), 'LANG': 'C.UTF-8'},
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = {'stdout': False, 'stderr': False}

    async def capture(name: str, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(4096):
            available = 32768 - len(output[name])
            output[name].extend(chunk[:available])
            truncated[name] = truncated[name] or len(chunk) > available

    readers = [asyncio.create_task(capture('stdout', process.stdout)),
               asyncio.create_task(capture('stderr', process.stderr))]
    timed_out = False
    try:
        async with asyncio.timeout(timeout_seconds):
            await process.wait()
            await asyncio.gather(*readers)
    except TimeoutError:
        timed_out = True
    finally:
        # Include background children, even if their parent shell has exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    return json.dumps({'exit_code': process.returncode, 'timed_out': timed_out,
        **{name: redact(data.decode('utf-8', errors='replace'), ctx.context['credentials'].values())
           for name, data in output.items()}, 'truncated': truncated})
