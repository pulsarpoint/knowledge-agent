import base64
import json
import socket
import threading
import time
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from psycopg.conninfo import conninfo_to_dict

from knowledge_admin.mcp_settings import credential_aad
from test_api import _client
from test_operations import configured
from test_repositories import HEADERS


@contextmanager
def serving(app, sock=None):
    if sock is None:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
    uv = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: uv.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if uv.started:
                break
            time.sleep(0.05)
        assert uv.started
        yield origin
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
        sock.close()


@pytest.fixture
def remote_mcp():
    calls = []
    mcp = FastMCP('Isolated MCP', stateless_http=False, json_response=False, streamable_http_path='/mcp')
    @mcp.tool()
    def read_info() -> str:
        """Read test deployment information."""
        calls.append('read_info')
        return 'isolated-mcp-evidence'
    class Auth:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http' and dict(scope['headers']).get(b'authorization') != b'Bearer isolated-token':
                return await JSONResponse({'error': 'unauthorized'}, status_code=401)(scope, receive, send)
            await self.app(scope, receive, send)
    with serving(Auth(mcp.streamable_http_app())) as origin:
        yield origin + '/mcp', calls


def create(client, url, *, test=True):
    result = client.post('/api/mcp/servers', headers=HEADERS, json={'name': 'Test tools', 'url': url, 'token': 'isolated-token'})
    assert result.status_code == 201, result.text
    server = result.json()
    if test:
        result = client.post(f"/api/mcp/servers/{server['id']}/test", headers=HEADERS, json={'revision': server['revision']})
        assert result.status_code == 200 and result.json()['ok'], result.text
        server = result.json()['server']
    return server


def test_registry_encryption_discovery_assignment_and_deletion(store, tmp_path, remote_mcp):
    client = _client(tmp_path, repository_store=store)
    url, calls = remote_mcp
    server = create(client, url, test=False)
    path = f"/api/mcp/servers/{server['id']}"
    assert client.get('/api/mcp/servers').status_code == 403
    assert client.put('/api/mcp/code', headers=HEADERS, json={'revision': 1, 'mcp_server_ids': [server['id']]}).status_code == 400
    with store.connect() as conn:
        encrypted = bytes(conn.execute('SELECT token_ciphertext FROM ka.mcp_servers WHERE id=%s', (UUID(server['id']),)).fetchone()['token_ciphertext'])
        assert b'isolated-token' not in encrypted
        assert store.cipher.decrypt(encrypted[:12], encrypted[12:], credential_aad(UUID(server['id']))) == b'isolated-token'
    changed = client.put(path, headers=HEADERS, json={'revision': 1, 'name': server['name'], 'url': url, 'description': 'Updated', 'token': ''})
    assert changed.status_code == 200 and changed.json()['has_token']
    with store.connect() as conn:
        assert bytes(conn.execute('SELECT token_ciphertext FROM ka.mcp_servers WHERE id=%s', (UUID(server['id']),)).fetchone()['token_ciphertext']) == encrypted
    assert client.put(path, headers=HEADERS, json={'revision': 2, 'name': server['name'], 'url': 'https://other.test/mcp'}).status_code == 400
    result = client.post(path + '/test', headers=HEADERS, json={'revision': 2})
    assert result.json()['ok'], result.text
    server = result.json()['server']
    assert calls == []
    assert server['tools'][0]['name'] == 'read_info'
    config = client.get('/api/mcp/code', headers=HEADERS).json()
    assert client.put('/api/mcp/code', headers=HEADERS, json={**config, 'mcp_server_ids': [server['id']]}).status_code == 200
    assert client.put('/api/mcp/code', headers=HEADERS, json=config).status_code == 409
    assert 'isolated-token' not in client.get('/api/mcp/servers', headers=HEADERS).text
    assert client.request('DELETE', path, headers=HEADERS, json={'revision': server['revision']}).status_code == 400
    assert client.request('DELETE', path, headers=HEADERS, json={'revision': server['revision'], 'confirmed': True}).status_code == 200
    assert client.get('/api/mcp/code', headers=HEADERS).json()['mcp_server_ids'] == []
    with store.connect() as conn:
        assert not conn.execute('SELECT * FROM ka.agent_config_mcp_servers').fetchall()
        assert 'isolated-token' not in str(conn.execute('SELECT details FROM ka.audit_events').fetchall())


def test_failures_do_not_echo_credentials_and_invalid_urls_rejected(store, tmp_path, remote_mcp):
    client = _client(tmp_path, repository_store=store)
    for url in ['file:///tmp/test', 'https://user:secret@test/mcp', 'http://host/mcp?token=secret', 'http://host/mcp#secret', 'http://host:bad/mcp']:
        response = client.post('/api/mcp/servers', headers=HEADERS, json={'name': 'bad', 'url': url})
        assert response.status_code == 400 and 'secret' not in response.text
    server = create(client, remote_mcp[0], test=False)
    path = f"/api/mcp/servers/{server['id']}"
    changed = client.put(path, headers=HEADERS, json={'name': server['name'], 'url': server['url'], 'revision': 1, 'token': 'invalid-private-token'}).json()
    response = client.post(path + '/test', headers=HEADERS, json={'revision': changed['revision']})
    assert not response.json()['ok'] and 'invalid-private-token' not in response.text
    assert response.json()['server']['tools'] == []


def test_ssh_global_inherited_explicit_empty_mcp_selections(store, tmp_path, remote_mcp):
    client, ssh_server, _, agent = configured(store, tmp_path)
    server = create(client, remote_mcp[0])
    settings = client.get('/api/operations/settings', headers=HEADERS).json()['settings']
    value = {k: settings[k] for k in ('revision', 'instructions', 'skill_ids', 'server_ids')}
    assert client.put('/api/operations/settings', headers=HEADERS, json={**value, 'mcp_server_ids': [server['id']]}).status_code == 200
    assert client.get('/api/operations/settings', headers=HEADERS).json()['settings']['mcp_server_ids'] == [server['id']]
    settings = client.get('/api/operations/settings', headers=HEADERS).json()['settings']
    response = client.put(f"/api/operations/configs/{ssh_server['id']}", headers=HEADERS, json={
        'revision': None, 'settings_revision': settings['revision'], 'instructions': None, 'skill_ids': None, 'mcp_server_ids': []})
    assert response.status_code == 200, response.text
    assert response.json()['mcp_server_ids'] == []
