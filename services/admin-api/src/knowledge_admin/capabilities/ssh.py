"""SSH execution with optional host verification. Credentials stay in the backend, outside model context."""

import asyncio
import errno
import json
import shlex
import socket
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated

import asyncssh
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.sessions.activity import redact
from knowledge_admin.tools.registry import tool


class SSHError(Exception):
    pass


@dataclass(frozen=True)
class SSHConnection:
    hostname: str
    port: int
    username: str
    known_hosts: str
    private_key: str = field(repr=False)
    allow_sudo: bool = False


def remote_command(command: str, as_root: bool, seconds: int) -> str:
    # no_new_privs is inherited by descendants and blocks setuid/capability gains,
    # including a nested sudo command. OS account permissions still matter.
    bounded = (
        f'timeout --signal=TERM --kill-after=3s {seconds}s setpriv --no-new-privs -- /bin/sh -c {shlex.quote(command)}'
    )
    return f'sudo -n -u root -- {bounded}' if as_root else bounded


@asynccontextmanager
async def _connect(connection: SSHConnection):
    try:
        key = asyncssh.import_private_key(connection.private_key)
        known = asyncssh.import_known_hosts(connection.known_hosts) if connection.known_hosts.strip() else None
        async with asyncssh.connect(
            connection.hostname,
            port=connection.port,
            username=connection.username,
            client_keys=[key],
            known_hosts=known,
            agent_path=None,
            config=None,
            preferred_auth='publickey',
            password_auth=False,
            kbdint_auth=False,
            connect_timeout=10,
            login_timeout=10,
            disable_trivial_auth=True,
        ) as conn:
            yield conn
    except SSHError:
        raise
    except asyncssh.HostKeyNotVerifiable:
        raise SSHError(
            'Host key verification failed. Check the trusted known_hosts entry for this hostname and port.'
        ) from None
    except asyncssh.PermissionDenied:
        raise SSHError('SSH authentication failed. Check the login user and private key.') from None
    except socket.gaierror:
        raise SSHError(
            f'Cannot resolve SSH hostname "{connection.hostname}" from the administration server. Use a full DNS hostname or IP address reachable from Docker.'
        ) from None
    except ConnectionRefusedError:
        raise SSHError(
            f'SSH connection refused by {connection.hostname}:{connection.port}. Check that SSH is running on this port.'
        ) from None
    except TimeoutError:
        raise SSHError('SSH connection or command timed out.') from None
    except OSError as exc:
        if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            raise SSHError(
                f'No network route to {connection.hostname}:{connection.port} from the administration server. Check Docker network and VPN access.'
            ) from None
        raise SSHError(
            'SSH network connection failed. Check the hostname, port, and network access from the administration server.'
        ) from None
    except (asyncssh.Error, ValueError):
        raise SSHError(
            'SSH connection failed. Check the hostname, port, private key, trusted host key, and network access.'
        ) from None


async def _check_tools(conn: asyncssh.SSHClientConnection, *, allow_root: bool):
    probe = await conn.run('id -u; command -v setpriv; command -v timeout', check=False, timeout=10)
    lines = probe.stdout.strip().splitlines()
    if len(lines) != 3 or not lines[0].isdigit() or probe.exit_status != 0:
        raise SSHError('This agent requires Linux with setpriv (util-linux) and GNU timeout installed.')
    if lines[0] == '0' and not allow_root:
        raise SSHError(
            'This SSH account is root. Use a non-root login, or request root execution on a server that allows it.'
        )


async def _execute(
    connection: SSHConnection,
    command: str,
    *,
    as_root: bool = False,
    seconds: int = 60,
    cancelled: threading.Event | None = None,
) -> dict:
    if as_root and not connection.allow_sudo:
        raise SSHError('Root execution is not allowed for this server.')
    if cancelled is not None and cancelled.is_set():
        raise SSHError('Run cancelled.')
    async with _connect(connection) as conn:
        await _check_tools(conn, allow_root=as_root)
        if cancelled is not None and cancelled.is_set():
            raise SSHError('Run cancelled.')
        async with conn.create_process(
            remote_command(command, as_root, seconds), encoding='utf-8', errors='replace'
        ) as process:

            async def capture(stream):
                text = ''
                truncated = False
                while chunk := await stream.read(4096):
                    available = 32768 - len(text)
                    text += chunk[: max(available, 0)]
                    truncated = truncated or len(chunk) > available
                return text + ('\n[Output truncated at 32,768 characters]' if truncated else '')

            async def collect():
                stdout, stderr = await asyncio.gather(capture(process.stdout), capture(process.stderr))
                await process.wait_closed()
                return {'exit_code': process.exit_status, 'stdout': stdout, 'stderr': stderr}

            pending = asyncio.create_task(collect())
            try:
                async with asyncio.timeout(seconds + 8):
                    while not pending.done():
                        if cancelled is not None and cancelled.is_set():
                            raise SSHError(
                                'Run cancelled. The SSH session was closed; completed changes are not undone.'
                            )
                        await asyncio.wait({pending}, timeout=0.1)
                    return await pending
            finally:
                if not pending.done():
                    process.terminate()
                    process.close()
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)


async def execute(
    connection: SSHConnection,
    command: str,
    *,
    as_root: bool = False,
    seconds: int = 60,
    cancelled: threading.Event | None = None,
) -> dict:
    worker = asyncio.create_task(_execute(connection, command, as_root=as_root, seconds=seconds, cancelled=cancelled))
    try:
        while not worker.done():
            if cancelled is not None and cancelled.is_set():
                raise SSHError('Run cancelled. The SSH session was closed; completed changes are not undone.')
            await asyncio.wait({worker}, timeout=0.1)
        return await worker
    finally:
        if not worker.done():
            # Covers connection establishment and diagnostics as well as commands.
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def inspect_connection(connection: SSHConnection) -> dict:
    """Return the host key from this authenticated session without saving trust."""
    async with _connect(connection) as conn:
        key = conn.get_server_host_key()
        if key is None:
            raise SSHError('SSH server did not provide a public host key.')
        public_key = key.export_public_key().decode().strip()
        host = connection.hostname if connection.port == 22 else f'[{connection.hostname}]:{connection.port}'
        host_key = {
            'public_key': public_key,
            'fingerprint': key.get_fingerprint('sha256'),
            'algorithm': key.get_algorithm(),
            'known_hosts_line': f'{host} {public_key}',
            'verified': bool(connection.known_hosts.strip()),
        }
        try:
            await _check_tools(conn, allow_root=connection.allow_sudo)
            if connection.allow_sudo:
                root = await conn.run('sudo -n -u root -- id -u', check=False, timeout=10)
                if root.exit_status != 0 or root.stdout.strip() != '0':
                    raise SSHError('SSH connected, but passwordless sudo to root failed.')
        except (SSHError, asyncssh.Error, OSError, TimeoutError) as exc:
            message = (
                str(exc) if isinstance(exc, SSHError) else 'SSH connected, but server diagnostics failed or timed out.'
            )
            return {'ok': False, 'message': message, 'host_key': host_key}
        message = 'SSH connected. ' + ('Host key verified. ' if host_key['verified'] else 'Host verification is off. ')
        message += 'Linux command tools are available.'
        if connection.allow_sudo:
            message += ' Passwordless sudo to root verified.'
        return {'ok': True, 'message': message, 'host_key': host_key}


def check_ssh_run(access: dict, server_id: str, **_) -> str | None:
    """None when allowed, else the refusal text."""
    if not access_rules.has_any(access, 'server'):
        return access_rules.refuse_none('server')
    if not access_rules.can_use(access, 'server', server_id):
        return access_rules.refuse_use('server')
    return None


# Run one command on a server selected for this agent; the backend supplies the key and host verification.
@tool
async def ssh_run(
    context: RunContext,
    server_id: Annotated[str, Field(description="Exact server ID from the agent's configured SSH server list.")],
    command: Annotated[str, Field(description='Shell command to run remotely; check the returned exit_code.')],
    as_root: Annotated[bool, Field(description='Request noninteractive sudo to root for this command.')] = False,
    timeout_seconds: Annotated[int, Field(description='Remote command time limit, from 1 to 300 seconds.')] = 60,
) -> str:
    """Execute a command on a server selected for this agent and return its output.

    Read the server with get_resource first and follow its instructions. Use as_root for sudo only when
    its allow_sudo is true. The backend supplies the login, key and host verification;
    never request credentials from the user or include them in command arguments.

    Use ssh_run with the server ID from get_available_resources."""
    config = context['config']
    if refusal := check_ssh_run(config['access'], server_id=server_id):
        return refusal
    if not config['enabled']:
        raise ValueError('SSH is disabled for this agent.')
    # the server's connection as the run's configuration pins it
    server = next((item for item in config['ssh']['servers'] if str(item['id']) == str(server_id)), None)
    if server is None or not server['enabled'] or not server['ssh_allowed']:
        return access_rules.refuse_use('server')
    if not command.strip() or '\0' in command or len(command) > 20000 or not 1 <= timeout_seconds <= 300:
        raise ValueError('Provide a command up to 20000 characters and a timeout from 1 to 300 seconds.')
    reference = server['private_key_ref']
    if reference is None or not context['credentials'].get(reference['id']):
        raise ValueError('The configured SSH credential was not supplied by the backend.')
    connection = SSHConnection(
        hostname=server['hostname'],
        port=server['port'],
        username=server['username'],
        known_hosts=server['known_hosts'],
        private_key=context['credentials'][reference['id']],
        allow_sudo=server['allow_sudo'],
    )
    result = await execute(connection, command, as_root=as_root, seconds=timeout_seconds)
    return redact(json.dumps({'server_id': server_id, **result}), context['credentials'].values())
