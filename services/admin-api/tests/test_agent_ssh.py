"""Per-agent SSH overrides, key isolation, and real local connection previews."""
import json
from uuid import UUID

import asyncssh
import pytest
from fastapi import HTTPException

from knowledge_admin.servers.operations_store import OperationsStore
from test_api import _client
from test_operations import configured
from test_repositories import HEADERS
from test_ssh import ssh_server


def endpoint(server, agent='operations'):
    return f"/api/agents/{agent}/servers/{server['id']}/ssh"


def save(client, path, **changes):
    current = client.get(path, headers=HEADERS).json()
    payload = {key: current[key] for key in ('revision','server_revision','username','port','known_hosts','allow_sudo')}
    response = client.put(path, headers=HEADERS, json={**payload, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_override_isolated_defaults_key_rotation_removal_and_revocation(store, tmp_path):
    client, server, initial, runtime = configured(store, tmp_path)
    path = endpoint(server)
    inherited = client.get(path, headers=HEADERS).json()
    assert inherited['connection_source'] == 'server' and inherited['revision'] == 0
    assert inherited['server_revision'] == server['revision']
    assert client.get(path).status_code == 403
    assert client.get(endpoint(server, 'missing'), headers=HEADERS).status_code == 404
    # Starting an override with an omitted key retains the current server key.
    overridden = save(client, path, username='agent-user', allow_sudo=True)
    assert overridden['connection_source'] == 'agent' and overridden['has_private_key']
    assert initial['private_key'] not in json.dumps(overridden)
    with store.connect() as conn:
        connection = runtime.agent_connection(conn, UUID(server['id']))
        assert connection.username == 'agent-user' and connection.private_key == initial['private_key']
        assert connection.allow_sudo
        default = OperationsStore(store, 'shop', 'code').agent_connection(conn, UUID(server['id']))
        assert default.username == 'operator' and not default.allow_sudo
    # Replacing shared server defaults must not mutate the saved agent override.
    new_key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    global_saved = client.put(f"/api/servers/{server['id']}/ssh", headers=HEADERS,
        json={**initial, 'revision': server['revision'], 'username': 'new-default', 'private_key': new_key}).json()
    with store.connect() as conn:
        assert runtime.agent_connection(conn, UUID(server['id'])).private_key == initial['private_key']
        assert OperationsStore(store, 'shop', 'code').agent_connection(conn, UUID(server['id'])).private_key == new_key
    # Explicit key removal means no authentication, never a silent fallback.
    empty = save(client, path, private_key=None)
    assert not empty['has_private_key'] and not empty['enabled']
    with store.connect() as conn, pytest.raises(HTTPException, match='private key'):
        runtime.agent_connection(conn, UUID(server['id']))
    assert client.post(path + '/test', headers=HEADERS, json={
        'revision': empty['revision'], 'server_revision': empty['server_revision']}).status_code == 409
    current = client.get(path, headers=HEADERS).json()
    body = {'revision': current['revision'], 'server_revision': current['server_revision'], 'confirmed': True}
    assert client.request('DELETE', path, headers=HEADERS, json={**body, 'confirmed': False}).status_code == 400
    restored = client.request('DELETE', path, headers=HEADERS, json=body)
    assert restored.status_code == 200 and restored.json()['connection_source'] == 'server'
    assert restored.json()['username'] == global_saved['username']
    with store.connect() as conn:
        assert runtime.agent_connection(conn, UUID(server['id'])).private_key == new_key


def test_preview_saved_override_host_verification_and_cleanup(store, tmp_path, ssh_server):
    client = _client(tmp_path, repository_store=store)
    server = client.post('/api/servers', headers=HEADERS, json={
        'name': 'Isolated SSH', 'hostname': ssh_server.hostname, 'ssh_allowed': True}).json()
    path = endpoint(server)
    value = {'revision': 0, 'server_revision': server['revision'], 'username': ssh_server.username,
        'port': ssh_server.port, 'known_hosts': '', 'private_key': ssh_server.private_key}
    preview = client.post(path + '/test', headers=HEADERS, json=value)
    assert preview.status_code == 200 and preview.json()['ok']
    host = preview.json()['host_key']
    assert host['known_hosts_line'] == ssh_server.known_hosts and not host['verified']
    assert client.get(path, headers=HEADERS).json()['connection_source'] == 'server'
    saved = save(client, path, **value)
    assert saved['has_private_key'] and saved['connection_source'] == 'agent'
    assert not client.get(f"/api/servers/{server['id']}/ssh", headers=HEADERS).json()['has_private_key']
    assert not client.get(endpoint(server, 'code'), headers=HEADERS).json()['has_private_key']
    assert client.put(path, headers=HEADERS, json=value).status_code == 409
    invalid = client.put(path, headers=HEADERS, json={**value, 'revision': saved['revision'], 'private_key': 'DO-NOT-EXPOSE'})
    assert invalid.status_code == 400 and 'DO-NOT-EXPOSE' not in invalid.text
    trusted = save(client, path, known_hosts=host['known_hosts_line'])
    result = client.post(path + '/test', headers=HEADERS, json={
        'revision': trusted['revision'], 'server_revision': trusted['server_revision']}).json()
    assert result['ok'] and result['host_key']['verified']
    assert client.get(path, headers=HEADERS).json()['last_tested_at'] is not None
    wrong = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode()
    bad = save(client, path, known_hosts=f'[{ssh_server.hostname}]:{ssh_server.port} {wrong}')
    failed = client.post(path + '/test', headers=HEADERS, json={
        'revision': bad['revision'], 'server_revision': bad['server_revision']}).json()
    assert not failed['ok'] and 'Host key verification' in failed['message']
    with store.connect() as conn:
        credential = conn.execute('SELECT c.* FROM ka.credentials c JOIN ka.agent_server_connections a ON a.credential_id=c.id').fetchone()
        assert ssh_server.private_key.encode() not in bytes(credential['ciphertext'])
        assert ssh_server.private_key not in str(conn.execute('SELECT details FROM ka.audit_events').fetchall())
    deleted = client.delete(f"/api/servers/{server['id']}", headers=HEADERS, params={'revision': server['revision'], 'confirmed': True})
    assert deleted.status_code == 200, deleted.text
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.agent_server_connections').fetchone() is None
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (credential['id'],)).fetchone() is None
