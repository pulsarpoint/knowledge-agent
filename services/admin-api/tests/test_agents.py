"""Configuration loading against the same PostgreSQL records edited by the UI."""

import json
from uuid import UUID

import pytest
from knowledge_llm.prompts import DEFAULT_PROMPTS

from access_helpers import give


from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.servers.operations_store import OperationsStore
from test_api import _client
from test_operations import configured
from test_profiles import create
from test_repositories import HEADERS


@pytest.fixture(autouse=True)
def agent_mounts(monkeypatch):
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')


@pytest.fixture
def saved_agent(store, tmp_path):
    client, server, ssh, _ = configured(store, tmp_path)
    skill = client.post(
        '/api/skills',
        headers=HEADERS,
        json={'name': 'inspect', 'description': 'Inspect service health', 'instructions': 'Check the exit code.'},
    ).json()
    mcp = client.post(
        '/api/mcp/servers',
        headers=HEADERS,
        json={'name': 'Docs', 'url': 'http://mock.test/mcp', 'token': 'test-mcp-secret'},
    ).json()
    repo = client.post(
        '/api/repositories',
        headers=HEADERS,
        json={'name': 'Payments', 'remote_url': 'example/payments', 'token': 'test-git-secret'},
    ).json()
    other_repo = client.post('/api/repositories', headers=HEADERS, json={'remote_url': 'example/unselected'}).json()
    service = client.post(
        '/api/services', headers=HEADERS, json={'name': 'Payments', 'description': 'Check the queue.'}
    ).json()
    built_from = {'from_service_id': service['id'], 'kind': 'built_from', 'to_code_project_id': other_repo['id']}
    assert client.post('/api/relations', headers=HEADERS, json=built_from).status_code == 201
    environment = client.post('/api/environments', headers=HEADERS, json={'name': 'test'}).json()
    deployment = client.post(
        '/api/deployments',
        headers=HEADERS,
        json={
            'service_id': service['id'],
            'server_id': server['id'],
            'environment_id': environment['id'],
            'runtime_name': 'payments',
            'log_location': '/var/log/payments/app.log',
        },
    )
    assert deployment.status_code == 201, deployment.text
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Claude',
            'provider': 'bedrock',
            'model': 'test-claude',
            'api_format': 'native',
            'aws_region': 'eu-west-1',
            'api_key': 'test-model-secret',
        },
    ).json()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (project,))
        for name, path in (
            ('payments', 'payments'),
            ('runbooks', 'payments/runbooks'),
            ('payments-old', 'payments-old'),
            ('private', 'private'),
        ):
            collection = conn.execute(
                'INSERT INTO ka.rag_collections(project_id,name,description) VALUES (%s,%s,%s) RETURNING id',
                (project, name, f'Description of {path}'),
            ).fetchone()['id']
            conn.execute(
                'INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)', (collection, path)
            )
        conn.execute(
            "UPDATE ka.repositories SET checkout_path='managed/payments',current_commit=%s WHERE id=%s",
            ('a' * 40, UUID(repo['id'])),
        )
        conn.execute(
            "UPDATE ka.repositories SET checkout_path='managed/unselected' WHERE id=%s", (UUID(other_repo['id']),)
        )
        conn.execute(
            "INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'managed/payments')",
            (UUID(repo['id']), project),
        )
        payments = conn.execute("SELECT id FROM ka.rag_collections WHERE name='payments'").fetchone()['id']
        give(
            conn,
            'operations',
            [
                ('server', server['id']),
                ('service', service['id']),
                ('code_project', repo['id']),
                ('collection', payments),
                ('skill', skill['id']),
                ('mcp_server', mcp['id']),
            ],
        )
        conn.execute('UPDATE ka.mcp_servers SET tools=\'[{"name":"lookup"}]\' WHERE id=%s', (UUID(mcp['id']),))
        conn.execute("UPDATE ka.agents SET model_id=%s WHERE id='operations'", (UUID(model['id']),))
    return client, server, ssh, skill, mcp, repo, model


def of(config, type_):
    return [item for item in config['access']['resources'] if item['type'] == type_]


def test_complete_config_active_prompt_and_secret_locations(store, saved_agent):
    _, server, ssh, skill, mcp, repo, model = saved_agent
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert config['id'] == 'operations' and config['enabled']
    assert config['system_prompt'] == {'content': DEFAULT_PROMPTS['operations']}
    assert config['instructions'] == 'Verify every change.'
    assert config['model']['provider'] == 'bedrock' and config['model']['aws_region'] == 'eu-west-1'
    assert not {'code', 'documents', 'ssh', 'services', 'skills', 'mcp', 'document_access'} & set(config)
    assert [(c['name'], c['paths'], c['description_short']) for c in of(config, 'collection')] == [
        ('payments', ['/work/data/payments'], 'Description of payments')
    ]
    assert of(config, 'code_project') == [
        {
            'type': 'code_project',
            'id': repo['id'],
            'name': 'Payments',
            'description_short': '',
            'folder_paths': ['/work/code/managed/payments'],
        }
    ]
    [found] = of(config, 'skill')
    assert (found['id'], found['description']) == (skill['id'], 'Inspect service health')
    assert 'instructions' not in found  # load_skill reads them
    [tools] = of(config, 'mcp_server')
    assert set(tools) == {'type', 'id', 'name', 'description_short', 'url', 'enabled', 'bearer_token_ref'}
    [service] = of(config, 'service')
    assert service['description_short'] == 'Check the queue.' and set(service) == {
        'type',
        'id',
        'name',
        'description_short',
    }
    [host] = of(config, 'server')
    assert host['id'] == server['id'] and host['username'] == 'operator' and host['known_hosts'] == ssh['known_hosts']
    assert set(host) == {
        'type',
        'id',
        'name',
        'description_short',
        'hostname',
        'port',
        'username',
        'allow_sudo',
        'known_hosts',
        'enabled',
        'ssh_allowed',
        'private_key_ref',
    }  # no per-server settings
    # The returned references locate and decrypt exactly the saved credentials.
    with store.connect() as conn:
        for reference, query, expected in [
            (
                host['private_key_ref'],
                'SELECT ciphertext AS secret FROM ka.credentials WHERE id=%s',
                ssh['private_key'],
            ),
            (
                config['model']['api_key_ref'],
                'SELECT api_key_ciphertext AS secret FROM ka.llm_models WHERE id=%s',
                'test-model-secret',
            ),
            (
                tools['bearer_token_ref'],
                'SELECT token_ciphertext AS secret FROM ka.mcp_servers WHERE id=%s',
                'test-mcp-secret',
            ),
        ]:
            encrypted = bytes(conn.execute(query, (UUID(reference['id']),)).fetchone()['secret'])
            assert store.cipher.decrypt(encrypted[:12], encrypted[12:], reference['aad'].encode()).decode() == expected
        connection = OperationsStore(store, 'shop').connection(conn, UUID(host['id']))
        assert connection.private_key == ssh['private_key']
    serialized = json.dumps(config)
    for excluded in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret'):
        assert excluded not in serialized
    assert config['model']['api_key_ref']['id'] == model['id']
    assert tools['bearer_token_ref']['id'] == mcp['id']


def test_config_preview_is_admin_only_read_only_and_never_executes(store, saved_agent, monkeypatch):
    client, _, ssh, *_ = saved_agent

    async def forbidden(*args, **kwargs):
        pytest.fail('Configuration preview must not execute tools or contact a provider')

    monkeypatch.setattr('knowledge_admin.capabilities.ssh.execute', forbidden)
    monkeypatch.setattr('httpx.AsyncClient.send', forbidden)
    path = '/api/agents/operations/config'
    assert client.get(path).status_code == 403
    assert client.get(path, headers={'X-Forwarded-Groups': 'readers'}).status_code == 403
    assert client.get('/api/agents/missing/config', headers=HEADERS).status_code == 404
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json() == get_agent_config('operations', conninfo=store.conninfo)
    for secret in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret'):
        assert secret not in response.text
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='operations'")
    assert client.get(path, headers=HEADERS).json()['enabled'] is False


def test_a_server_uses_its_own_ssh_settings(store, saved_agent):
    """Phase 2: references only; there are no per-agent connections or per-server overrides."""
    _, _, ssh, *_ = saved_agent
    [host] = of(get_agent_config('operations', conninfo=store.conninfo), 'server')
    assert host['username'] == 'operator' and host['known_hosts'] == ssh['known_hosts']
    with store.connect() as conn:
        encrypted = bytes(
            conn.execute(
                'SELECT ciphertext FROM ka.credentials WHERE id=%s', (UUID(host['private_key_ref']['id']),)
            ).fetchone()['ciphertext']
        )
    key = store.cipher.decrypt(encrypted[:12], encrypted[12:], host['private_key_ref']['aad'].encode()).decode()
    assert key == ssh['private_key']  # the server's own key


def test_missing_items_are_skipped_and_switched_off_ones_stay_listed(store, saved_agent):
    """The access object decides: an archived code project and a deleted collection it references are skipped; a
    server switched off stays listed as it is; a skill or an MCP server switched off is not held (a run never gets
    it)."""
    _, server, _, skill, mcp, repo, _ = saved_agent
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='operations'")
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (UUID(server['id']),))
        conn.execute('UPDATE ka.agent_skills SET enabled=false WHERE id=%s', (UUID(skill['id']),))
        conn.execute('UPDATE ka.mcp_servers SET enabled=false WHERE id=%s', (UUID(mcp['id']),))
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (UUID(repo['id']),))
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='payments'")
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert not config['enabled']
    assert of(config, 'code_project') == of(config, 'collection') == []
    assert len(of(config, 'service')) == 1
    assert [item['ssh_allowed'] for item in of(config, 'server')] == [False]
    assert of(config, 'skill') == of(config, 'mcp_server') == []


def test_an_agent_without_access_has_nothing(store, saved_agent):
    client = saved_agent[0]
    other = create(client, 'Other agent')
    isolated = get_agent_config(other['id'], conninfo=store.conninfo)
    assert isolated['access'] == {'resources': []} and 'grounded' not in isolated


def test_paths_use_runtime_mounts_without_host_resolution_or_database_changes(
    store, saved_agent, tmp_path, monkeypatch
):
    monkeypatch.setenv('CODE_ROOT', '/agent/source')
    monkeypatch.setenv('DATA_ROOT_DIR', '/agent/documents')
    monkeypatch.setenv('DATA_ROOT', '/host/data')  # the host folder is display-only; agents get container paths
    config = get_agent_config('operations', conninfo=store.conninfo)
    monkeypatch.chdir(tmp_path)
    assert get_agent_config('operations', conninfo=store.conninfo) == config
    assert of(config, 'code_project')[0]['folder_paths'] == ['/agent/source/managed/payments']
    assert of(config, 'collection')[0]['paths'] == ['/agent/documents/payments']
    with store.connect() as conn:
        assert conn.execute('SELECT path FROM ka.code_project_folders').fetchone()['path'] == 'managed/payments'
        assert (
            conn.execute("""SELECT p.source_path FROM ka.rag_collection_paths p JOIN ka.rag_collections c ON c.id=p.collection_id
            WHERE c.name='runbooks'""").fetchone()['source_path']
            == 'payments/runbooks'
        )


@pytest.mark.parametrize('variable', ['CODE_ROOT', 'DATA_ROOT_DIR'])
@pytest.mark.parametrize('value', ['', 'relative/root', '/work/../elsewhere'])
def test_config_requires_absolute_runtime_mounts(store, saved_agent, monkeypatch, variable, value):
    if variable == 'DATA_ROOT_DIR' and not value:
        pytest.skip('an empty DATA_ROOT_DIR takes its default')
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=variable + ' must be an absolute path'):
        get_agent_config('operations', conninfo=store.conninfo)


def test_load_by_id_is_read_only_independent_and_uses_pg_environment(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Independent agent')
    with store.connect() as conn:
        parameters = conn.info.get_parameters()
    for name in ('host', 'port', 'user', 'dbname'):
        monkeypatch.setenv('PGDATABASE' if name == 'dbname' else 'PG' + name.upper(), parameters[name])
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    config = get_agent_config(agent['id'])
    assert config['name'] == 'Independent agent' and config['model'] is None
    assert config['system_prompt']['content'] == 'Instructions for Independent agent.'
    assert config['settings_revision'] is None and config['instructions'] == ''
    assert config['access']['resources'] == []
    for missing in (agent['name'], "' OR true --"):
        with pytest.raises(LookupError):
            get_agent_config(missing)
