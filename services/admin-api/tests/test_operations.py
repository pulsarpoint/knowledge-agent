import asyncio
import json
from uuid import UUID, uuid4

import asyncssh
import pytest
from agents.tool_context import ToolContext
from fastapi import HTTPException

from test_api import _client
from test_repositories import HEADERS
from knowledge_admin.capabilities import ssh
from knowledge_admin.servers.operations_store import OperationsStore


def setup(client):
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Test host', 'hostname': 'host.test', 'ssh_allowed': True}
    ).json()
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    host_key = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode().strip()
    endpoint = f'/api/servers/{server["id"]}/ssh'
    value = {
        'revision': 1,
        'username': 'operator',
        'port': 22,
        'allow_sudo': False,
        'known_hosts': f'host.test {host_key}',
        'private_key': key,
    }
    response = client.put(endpoint, headers=HEADERS, json=value)
    assert response.status_code == 200, response.text
    return response.json(), value


def configured(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server, value = setup(client)
    settings = {'revision': 1, 'instructions': 'Verify every change.', 'server_ids': [server['id']], 'skill_ids': []}
    assert client.put('/api/operations/settings', headers=HEADERS, json=settings).status_code == 200
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'test',
            'provider': 'openai',
            'model': 'test',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()
    with store.connect() as conn:
        conn.execute('UPDATE ka.llm_assignments SET operations_model_id=%s', (UUID(model['id']),))
    return client, server, value, OperationsStore(store, 'shop')


def test_ssh_secret_encrypted_write_only_rotation_removal_and_server_delete(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server, value = setup(client)
    path = f'/api/servers/{server["id"]}/ssh'
    assert server['has_private_key'] and server['enabled'] and server['revision'] == 2
    assert value['private_key'] not in json.dumps(server)
    assert client.get(path).status_code == 403
    assert client.get('/api/operations/settings').status_code == 403
    assert client.put(path, headers=HEADERS, json=value).status_code == 409
    with store.connect() as conn:
        row = conn.execute(
            'SELECT c.* FROM ka.credentials c JOIN ka.servers s ON s.credential_id=c.id WHERE s.id=%s',
            (UUID(server['id']),),
        ).fetchone()
        first = bytes(row['ciphertext'])
        assert value['private_key'].encode() not in first
        decoded = store.cipher.decrypt(
            first[:12], first[12:], f'{row["project_id"]}:{row["id"]}:ssh_private_key:v1'.encode()
        ).decode()
        assert decoded == value['private_key']
        assert value['private_key'] not in str(conn.execute('SELECT details FROM ka.audit_events').fetchall())
    # Editing other settings omits the hidden key; its encrypted bytes must stay identical.
    preserved = client.put(
        path,
        headers=HEADERS,
        json={k: v for k, v in {**value, 'revision': 2, 'allow_sudo': True}.items() if k != 'private_key'},
    )
    assert preserved.status_code == 200 and preserved.json()['has_private_key']
    with store.connect() as conn:
        assert (
            bytes(
                conn.execute('SELECT ciphertext FROM ka.credentials WHERE id=%s', (row['id'],)).fetchone()['ciphertext']
            )
            == first
        )
    changed = client.put(path, headers=HEADERS, json={**value, 'revision': 3}).json()
    with store.connect() as conn:
        assert (
            bytes(
                conn.execute('SELECT ciphertext FROM ka.credentials WHERE id=%s', (row['id'],)).fetchone()['ciphertext']
            )
            != first
        )
    assert (
        client.delete(
            f'/api/servers/{server["id"]}', headers=HEADERS, params={'revision': changed['revision'], 'confirmed': True}
        ).status_code
        == 200
    )
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (row['id'],)).fetchone() is None


def test_partial_settings_key_clear_and_invalid_secret_never_echoed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server, value = setup(client)
    path = f'/api/servers/{server["id"]}/ssh'
    invalid = client.put(path, headers=HEADERS, json={**value, 'revision': 2, 'private_key': 'DO-NOT-EXPOSE'})
    assert invalid.status_code == 400 and 'DO-NOT-EXPOSE' not in invalid.text
    cleared = client.put(path, headers=HEADERS, json={**value, 'revision': 2, 'private_key': None}).json()
    assert not cleared['has_private_key'] and not cleared['enabled']
    assert client.post(path + '/test', headers=HEADERS, json={'revision': 3}).status_code == 409
    with store.connect() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.credentials WHERE kind='ssh_private_key'").fetchone()['n'] == 0
        )


def test_settings_validate_ssh_skills_revisions_and_empty_allowlist(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    initial = client.get('/api/operations/settings', headers=HEADERS).json()['settings']
    assert 'enabled' not in initial and initial['server_ids'] == []
    settings = {'revision': 1, 'instructions': '', 'server_ids': [], 'skill_ids': []}
    assert client.put('/api/operations/settings', headers=HEADERS, json=settings).status_code == 200
    settings['revision'] = 2
    server, _ = setup(client)
    settings['server_ids'] = [server['id']]
    settings['skill_ids'] = [str(uuid4())]
    assert client.put('/api/operations/settings', headers=HEADERS, json=settings).status_code == 400
    settings['skill_ids'] = []
    assert client.put('/api/operations/settings', headers=HEADERS, json=settings).status_code == 200
    assert client.put('/api/operations/settings', headers=HEADERS, json=settings).status_code == 409


def test_builtin_mcp_endpoint_removed_and_model_gateway_rejects_unleased_requests(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/_operations/mcp/', headers=HEADERS, json={}).status_code == 404
    assert client.post('/_operations/models/v1/responses', headers=HEADERS, json={}).status_code == 404


def test_operations_prompt_seed_and_assignment_compatibility(store, tmp_path):
    from knowledge_llm.prompts import DEFAULT_PROMPTS

    client = _client(tmp_path, repository_store=store)
    assert (
        client.get('/api/agents/operations', headers=HEADERS).json()['system_prompt'] == DEFAULT_PROMPTS['operations']
    )
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'chat',
            'provider': 'openai',
            'model': 'chat',
            'api_format': 'chat_completions',
            'base_url': 'http://test/v1',
        },
    ).json()
    result = client.put(
        '/api/llm/assignments',
        headers=HEADERS,
        json={'revision': 1, 'search_model_id': None, 'code_model_id': None, 'operations_model_id': model['id']},
    )
    assert result.status_code == 400
