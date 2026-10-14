"""Real Linux SSH tests; fresh keys, loopback-only port and disposable filesystem."""

import asyncio
import errno
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import asyncssh
import pytest

from knowledge_admin.capabilities.ssh import SSHConnection, SSHError, execute, inspect_connection


@pytest.fixture(scope='module')
def ssh_server(tmp_path_factory):
    root = Path(__file__).resolve().parents[3]
    subprocess.run(
        ['docker', 'build', '-t', 'knowledge-agent-v3-ssh-test', '-f', 'deploy/ssh-test/Dockerfile', '.'],
        cwd=root,
        check=True,
        capture_output=True,
    )
    directory = tmp_path_factory.mktemp('ssh-fixture')
    key = asyncssh.generate_private_key('ssh-ed25519')
    host = asyncssh.generate_private_key('ssh-ed25519')
    (directory / 'client_key.pub').write_bytes(key.export_public_key())
    (directory / 'host_key').write_bytes(host.export_private_key())
    (directory / 'host_key').chmod(0o600)
    name = f'ka-ssh-test-{uuid4().hex[:12]}'
    subprocess.run(
        [
            'docker',
            'run',
            '-d',
            '--rm',
            '--name',
            name,
            '-p',
            '127.0.0.1::22',
            '-v',
            f'{directory}:/fixtures:ro',
            'knowledge-agent-v3-ssh-test',
        ],
        check=True,
        capture_output=True,
    )
    try:
        port = int(subprocess.check_output(['docker', 'port', name, '22'], text=True).strip().rsplit(':', 1)[1])
        # Docker accepts the port before sshd is up, so wait for the SSH banner, not the socket.
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.5) as probe:
                    probe.settimeout(0.5)
                    if probe.recv(4).startswith(b'SSH-'):
                        break
            except OSError:
                pass
            time.sleep(0.1)
        yield SSHConnection(
            '127.0.0.1',
            port,
            'operator',
            f'[127.0.0.1]:{port} {host.export_public_key().decode().strip()}',
            key.export_private_key().decode(),
            False,
        )
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


def test_real_ssh_host_pin_and_authentication(ssh_server):
    result = asyncio.run(execute(ssh_server, 'id -un; hostname'))
    assert result['exit_code'] == 0 and result['stdout'].startswith('operator\n')
    wrong = asyncssh.generate_private_key('ssh-ed25519')
    with pytest.raises(SSHError, match='Host key verification'):
        asyncio.run(
            execute(
                replace(ssh_server, known_hosts=f'[127.0.0.1]:{ssh_server.port} {wrong.export_public_key().decode()}'),
                'id',
            )
        )
    with pytest.raises(SSHError, match='authentication'):
        asyncio.run(execute(replace(ssh_server, private_key=wrong.export_private_key().decode()), 'id'))


def test_privilege_escalation_blocked_and_explicit_root_supported(ssh_server):
    ordinary = asyncio.run(execute(ssh_server, 'id -u; sudo -n id -u'))
    assert ordinary['exit_code'] != 0 and 'no new privileges' in ordinary['stderr'].lower()
    assert ordinary['stdout'].strip() != '0'
    with pytest.raises(SSHError, match='not allowed'):
        asyncio.run(execute(ssh_server, 'id -u', as_root=True))
    elevated = asyncio.run(execute(replace(ssh_server, allow_sudo=True), 'id -u', as_root=True))
    assert elevated['exit_code'] == 0 and elevated['stdout'].strip() == '0'
    # Even on a server permitting root, normal commands must explicitly opt in.
    assert asyncio.run(execute(replace(ssh_server, allow_sudo=True), 'sudo -n id -u'))['exit_code'] != 0


def test_commands_quote_shell_and_bound_output_and_duration(ssh_server):
    assert (
        asyncio.run(execute(ssh_server, "printf '%s' 'quotes \" and $HOME and `id`'"))['stdout']
        == 'quotes " and $HOME and `id`'
    )
    result = asyncio.run(execute(ssh_server, 'yes x | head -c 100000'))
    assert len(result['stdout']) < 33000 and 'truncated' in result['stdout']
    start = time.monotonic()
    result = asyncio.run(execute(ssh_server, 'sleep 10', seconds=1))
    assert result['exit_code'] == 124 and time.monotonic() - start < 6


def test_cancel_command(ssh_server):
    cancelled = threading.Event()

    async def run():
        pending = asyncio.create_task(execute(ssh_server, 'sleep 30', cancelled=cancelled))
        await asyncio.sleep(0.5)
        cancelled.set()
        with pytest.raises(SSHError, match='cancelled'):
            await pending

    asyncio.run(run())


def test_optional_host_verification_discovery_and_pinning(ssh_server):
    unverified = replace(ssh_server, known_hosts=' \n\t ')
    assert asyncio.run(execute(unverified, 'id -un'))['stdout'].strip() == 'operator'
    result = asyncio.run(inspect_connection(unverified))
    host_key = result['host_key']
    assert result['ok'] and not host_key['verified']
    assert host_key['known_hosts_line'] == ssh_server.known_hosts
    public = asyncssh.import_public_key(host_key['public_key'])
    assert public.get_fingerprint() == host_key['fingerprint']
    assert host_key['algorithm'] == 'ssh-ed25519'
    trusted = asyncio.run(
        inspect_connection(replace(unverified, known_hosts=host_key['known_hosts_line'], allow_sudo=True))
    )
    assert trusted['ok'] and trusted['host_key']['verified']
    assert 'sudo to root verified' in trusted['message']
    wrong = asyncssh.generate_private_key('ssh-ed25519')
    with pytest.raises(SSHError, match='Host key verification'):
        asyncio.run(
            inspect_connection(
                replace(ssh_server, known_hosts=f'[127.0.0.1]:{ssh_server.port} {wrong.export_public_key().decode()}')
            )
        )
    with pytest.raises(SSHError, match='authentication'):
        asyncio.run(inspect_connection(replace(unverified, private_key=wrong.export_private_key().decode())))


@pytest.mark.parametrize(
    'error,expected',
    [
        (socket.gaierror(socket.EAI_NONAME, 'unresolved'), 'Cannot resolve SSH hostname "short-host"'),
        (ConnectionRefusedError(errno.ECONNREFUSED, 'refused'), 'SSH connection refused by short-host:2222'),
        (OSError(errno.ENETUNREACH, 'unreachable'), 'No network route to short-host:2222'),
        (TimeoutError(), 'timed out'),
    ],
)
def test_network_errors_identify_failure_without_leaking_credentials(monkeypatch, error, expected):
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    connection = SSHConnection('short-host', 2222, 'operator', '', key)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(asyncssh, 'connect', fail)
    with pytest.raises(SSHError, match=expected) as caught:
        asyncio.run(inspect_connection(connection))
    assert key not in str(caught.value)
