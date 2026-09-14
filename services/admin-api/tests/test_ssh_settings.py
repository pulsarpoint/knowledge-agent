"""Connection preview, trust enrollment and optional verification against isolated SSH."""
from pathlib import Path
from uuid import UUID

import asyncssh

from test_api import _client
from test_operations import configured
from test_repositories import HEADERS
from test_ssh import ssh_server


def test_unsaved_connection_then_save_and_enforce_host_key(store, tmp_path, ssh_server):
    client = _client(tmp_path, repository_store=store)
    server = client.post('/api/servers', headers=HEADERS, json={
        'name': 'Isolated preview', 'hostname': ssh_server.hostname, 'ssh_allowed': True}).json()
    path = f"/api/servers/{server['id']}/ssh"
    value = {'revision': server['revision'], 'username': ssh_server.username, 'port': ssh_server.port,
        'private_key': ssh_server.private_key, 'known_hosts': ''}
    assert client.post(path + '/test', json=value).status_code == 403
    assert client.post(path + '/test', headers=HEADERS, json={**value, 'revision': 99}).status_code == 409
    result = client.post(path + '/test', headers=HEADERS, json=value)
    assert result.status_code == 200 and result.json()['ok']
    discovered = result.json()['host_key']
    assert discovered['known_hosts_line'] == ssh_server.known_hosts and not discovered['verified']
    untouched = client.get(path, headers=HEADERS).json()
    assert not untouched['has_private_key'] and untouched['username'] is None and untouched['last_tested_at'] is None
    assert untouched['revision'] == server['revision']
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.credentials WHERE kind='ssh_private_key'").fetchone()['n'] == 0
        assert value['private_key'] not in str(conn.execute('SELECT details FROM ka.audit_events').fetchall())
    saved = client.put(path, headers=HEADERS, json=value).json()
    assert saved['enabled'] and saved['known_hosts'] == ''
    # Omitted key preserves and uses the encrypted stored key.
    result = client.post(path + '/test', headers=HEADERS, json={'revision': saved['revision']})
    assert result.json()['ok'] and not result.json()['host_key']['verified']
    assert client.get(path, headers=HEADERS).json()['last_tested_at'] is not None
    trusted = client.put(path, headers=HEADERS, json={**value, 'revision': saved['revision'],
        'known_hosts': discovered['known_hosts_line']}).json()
    result = client.post(path + '/test', headers=HEADERS, json={'revision': trusted['revision']})
    assert result.json()['ok'] and result.json()['host_key']['verified']
    wrong = asyncssh.generate_private_key('ssh-ed25519')
    # Bad saved trust never falls back to an unverified connection.
    bad_trust = client.put(path, headers=HEADERS, json={**value, 'revision': trusted['revision'],
        'known_hosts': f'[{ssh_server.hostname}]:{ssh_server.port} {wrong.export_public_key().decode()}'}).json()
    result = client.post(path + '/test', headers=HEADERS, json={'revision': bad_trust['revision']}).json()
    assert not result['ok'] and result['host_key'] is None and 'Host key verification' in result['message']
    bad_auth = client.post(path + '/test', headers=HEADERS, json={**value, 'revision': bad_trust['revision'],
        'private_key': wrong.export_private_key().decode()}).json()
    assert not bad_auth['ok'] and bad_auth['host_key'] is None and 'authentication' in bad_auth['message']
    # Explicit removal doesn't silently use the saved key during a preview.
    assert client.post(path + '/test', headers=HEADERS, json={**value, 'revision': bad_trust['revision'], 'private_key': None}).status_code == 409
    invalid = client.post(path + '/test', headers=HEADERS, json={**value, 'private_key': 'DO-NOT-EXPOSE'})
    assert invalid.status_code == 400 and 'DO-NOT-EXPOSE' not in invalid.text
    assert value['private_key'] not in result['message']
    assert client.put(f"/api/servers/{server['id']}", headers=HEADERS, json={
        'revision': bad_trust['revision'], 'name': server['name'], 'hostname': server['hostname'], 'ssh_allowed': False}).status_code == 200
    assert client.post(path + '/test', headers=HEADERS, json={**value, 'revision': bad_trust['revision'] + 1}).status_code == 409


def test_agent_accepts_credentials_without_host_keys_and_migration_preserves_access(store, tmp_path):
    client, server, value, agent = configured(store, tmp_path)
    saved = client.put(f"/api/servers/{server['id']}/ssh", headers=HEADERS, json={**value, 'revision': 2, 'known_hosts': ''}).json()
    assert saved['enabled']
    settings = client.get('/api/operations/settings', headers=HEADERS).json()['settings']
    assert client.put('/api/operations/settings', headers=HEADERS, json={k: settings[k] for k in (
        'revision', 'instructions', 'server_ids', 'skill_ids')}).status_code == 200
    with store.connect() as conn:
        connection = agent.connection(conn, UUID(server['id']))
    assert connection.known_hosts == '' and connection.username == 'operator'
    # Simulate a credential configured before host verification became optional.
    with store.connect() as conn:
        conn.execute('UPDATE ka.servers SET enabled=false,ssh_allowed=false WHERE id=%s', (UUID(server['id']),))
        migration = Path(__file__).resolve().parents[3] / 'db/migrations/0016_optional_ssh_host_keys.up.sql'
        conn.execute(migration.read_text())
        row = conn.execute('SELECT enabled,ssh_allowed,revision FROM ka.servers WHERE id=%s', (UUID(server['id']),)).fetchone()
        assert row['enabled'] and not row['ssh_allowed'] and row['revision'] == saved['revision'] + 1
