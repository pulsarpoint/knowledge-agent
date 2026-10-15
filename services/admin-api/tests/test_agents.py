"""Configuration loading against the same PostgreSQL records edited by the UI."""

import json
from uuid import UUID

import asyncssh
import pytest
from knowledge_llm.prompts import DEFAULT_PROMPTS

from knowledge_admin.agents.runner import get_agent_config
from knowledge_admin.servers.operations_store import OperationsStore
from test_agent_ssh import save
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
        '/api/services',
        headers=HEADERS,
        json={
            'name': 'Payments',
            'description': 'Check the queue.',
            'repository_ids': [other_repo['id']],
            'deployments': [
                {
                    'server_id': server['id'],
                    'environment': 'test',
                    'runtime_name': 'payments',
                    'log_location': '/var/log/payments/app.log',
                }
            ],
        },
    ).json()
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
            "INSERT INTO ka.agent_collections SELECT 'operations',id FROM ka.rag_collections WHERE name='payments'"
        )
        conn.execute("INSERT INTO ka.agent_repositories VALUES ('operations',%s)", (UUID(repo['id']),))
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
        conn.execute("INSERT INTO ka.agent_services VALUES ('operations',%s)", (UUID(service['id']),))
        conn.execute("INSERT INTO ka.skills_agents VALUES ('operations',%s,%s)", (UUID(skill['id']), project))
        conn.execute(
            "INSERT INTO ka.agent_config_mcp_servers SELECT id,project_id,%s FROM ka.agent_configs WHERE agent='operations' AND server_id IS NULL",
            (UUID(mcp['id']),),
        )
        conn.execute('UPDATE ka.mcp_servers SET tools=\'[{"name":"lookup"}]\' WHERE id=%s', (UUID(mcp['id']),))
        conn.execute(
            """UPDATE ka.agents SET model_id=%s,documents_enabled=true,repositories_enabled=true,
            ssh_enabled=true,skills_enabled=true,mcp_enabled=true,services_enabled=true WHERE id='operations'""",
            (UUID(model['id']),),
        )
    return client, server, ssh, skill, mcp, repo, model


def test_complete_config_active_prompt_and_secret_locations(store, saved_agent):
    _, server, ssh, skill, mcp, repo, model = saved_agent
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert config['id'] == 'operations' and config['enabled']
    assert config['system_prompt'] == {'content': DEFAULT_PROMPTS['operations']}
    assert config['instructions'] == 'Verify every change.'
    assert config['model']['provider'] == 'bedrock' and config['model']['aws_region'] == 'eu-west-1'
    assert config['documents']['all_collections'] is False and 'folders' not in config['documents']
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [
        ('payments', ['/work/data/payments'])
    ]
    assert config['documents']['collections'][0]['description'] == 'Description of payments'
    assert [r['id'] for r in config['code']['projects']] == [repo['id']]
    assert config['code']['projects'][0] == {
        'id': repo['id'],
        'name': 'Payments',
        'description': '',
        'folder_paths': ['/work/code/managed/payments'],
        'remote_git_url': 'https://github.com/example/payments.git',
    }
    assert config['skills']['items'][0]['id'] == skill['id']
    assert config['skills']['items'][0]['instructions'] == 'Check the exit code.'
    assert config['mcp']['servers'][0]['tools'] == [{'name': 'lookup'}]
    assert config['services']['items'][0]['description'] == 'Check the queue.'
    assert 'operations_notes' not in config['services']['items'][0]
    assert config['services']['items'][0]['repositories'][0]['id'] != repo['id']
    assert config['services']['items'][0]['repositories'][0]['checkout_path'] == '/work/code/managed/unselected'
    assert config['services']['items'][0]['deployments'][0]['log_location'] == '/var/log/payments/app.log'
    host = config['ssh']['servers'][0]
    assert host['id'] == server['id'] and host['connection_source'] == 'server'
    assert host['username'] == 'operator' and host['known_hosts'] == ssh['known_hosts']
    assert host['instructions'] == config['instructions'] and host['instructions_source'] == 'agent'
    assert host['skills'] == config['skills']['items'] and host['mcp_servers'] == config['mcp']['servers']
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
                config['mcp']['servers'][0]['bearer_token_ref'],
                'SELECT token_ciphertext AS secret FROM ka.mcp_servers WHERE id=%s',
                'test-mcp-secret',
            ),
        ]:
            encrypted = bytes(conn.execute(query, (UUID(reference['id']),)).fetchone()['secret'])
            assert store.cipher.decrypt(encrypted[:12], encrypted[12:], reference['aad'].encode()).decode() == expected
        connection = OperationsStore(store, 'shop').agent_connection(conn, UUID(host['id']))
        assert connection.private_key == ssh['private_key']
    serialized = json.dumps(config)
    for excluded in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret'):
        assert excluded not in serialized
    assert config['model']['api_key_ref']['id'] == model['id']
    assert config['mcp']['servers'][0]['bearer_token_ref']['id'] == mcp['id']


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


def test_complete_ssh_override_and_explicit_empty_server_instructions(store, saved_agent):
    client, server, ssh, *_ = saved_agent
    path = f'/api/agents/operations/servers/{server["id"]}/ssh'
    own_key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, path, username='agent-user', port=2222, known_hosts='', allow_sudo=False, private_key=own_key)
    with store.connect() as conn:
        conn.execute('UPDATE ka.servers SET allow_sudo=true WHERE id=%s', (UUID(server['id']),))
        conn.execute(
            """INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,inherit_mcp_servers)
            SELECT project_id,'operations',id,'',false,false FROM ka.servers WHERE id=%s""",
            (UUID(server['id']),),
        )
    host = get_agent_config('operations', conninfo=store.conninfo)['ssh']['servers'][0]
    assert (host['connection_source'], host['username'], host['port']) == ('agent', 'agent-user', 2222)
    assert not host['allow_sudo'] and host['known_hosts'] == ''
    assert host['instructions'] == '' and host['instructions_source'] == 'server_override'
    assert host['skills'] == host['mcp_servers'] == []
    assert host['skills_source'] == host['mcp_source'] == 'server_override'
    with store.connect() as conn:
        encrypted = bytes(
            conn.execute(
                'SELECT ciphertext FROM ka.credentials WHERE id=%s', (UUID(host['private_key_ref']['id']),)
            ).fetchone()['ciphertext']
        )
        assert (
            store.cipher.decrypt(encrypted[:12], encrypted[12:], host['private_key_ref']['aad'].encode()).decode()
            == own_key
        )
        assert own_key != ssh['private_key']
    save(client, path, private_key=None)
    host = get_agent_config('operations', conninfo=store.conninfo)['ssh']['servers'][0]
    assert host['private_key_ref'] is None and not host['enabled']  # No fallback to the shared key.
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.agent_configs SET instructions=NULL,inherit_skills=true,inherit_mcp_servers=true
            WHERE agent='operations' AND server_id=%s""",
            (UUID(server['id']),),
        )
    host = get_agent_config('operations', conninfo=store.conninfo)['ssh']['servers'][0]
    assert host['instructions'] == 'Verify every change.' and len(host['skills']) == len(host['mcp_servers']) == 1


def test_disabled_capabilities_preserve_selections_and_all_scope_is_current(store, saved_agent):
    _, server, _, skill, mcp, repo, _ = saved_agent
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agents SET enabled=false,documents_enabled=false,repositories_enabled=false,
            ssh_enabled=false,skills_enabled=false,mcp_enabled=false,services_enabled=false,
            all_collections=true,all_repositories=true WHERE id='operations'""")
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (UUID(server['id']),))
        conn.execute('UPDATE ka.agent_skills SET enabled=false WHERE id=%s', (UUID(skill['id']),))
        conn.execute('UPDATE ka.mcp_servers SET enabled=false WHERE id=%s', (UUID(mcp['id']),))
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (UUID(repo['id']),))
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='private'")
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert not config['enabled']
    assert config['code']['all_projects'] is True
    assert all(not config[key]['enabled'] for key in ('documents', 'code', 'ssh', 'skills', 'mcp', 'services'))
    assert {p for c in config['documents']['collections'] for p in c['paths']} == {
        '/work/data/payments',
        '/work/data/payments/runbooks',
        '/work/data/payments-old',
    }
    assert len(config['code']['projects']) == 1 and config['code']['projects'][0]['id'] != repo['id']
    assert not config['ssh']['servers'][0]['ssh_allowed']
    assert not config['skills']['items'][0]['enabled'] and not config['mcp']['servers'][0]['enabled']


def test_multiple_servers_have_independent_runbooks_skills_and_mcp(store, saved_agent):
    client, first, *_ = saved_agent
    second = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Second', 'hostname': 'second.test', 'ssh_allowed': True}
    ).json()
    skill = client.post(
        '/api/skills',
        headers=HEADERS,
        json={'name': 'server-only', 'description': 'Server procedures', 'instructions': 'Read the service log.'},
    ).json()
    mcp = client.post(
        '/api/mcp/servers', headers=HEADERS, json={'name': 'Server docs', 'url': 'http://other.test/mcp'}
    ).json()
    other = create(client, 'Other agent')
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            "INSERT INTO ka.operations_servers(project_id,agent,server_id) VALUES (%s,'operations',%s)",
            (project, UUID(second['id'])),
        )
        config_id = conn.execute(
            """INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,inherit_mcp_servers)
            VALUES (%s,'operations',%s,'Inspect this host only.',false,false) RETURNING id""",
            (project, UUID(first['id'])),
        ).fetchone()['id']
        conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (config_id, project, UUID(skill['id'])))
        conn.execute('INSERT INTO ka.agent_config_mcp_servers VALUES (%s,%s,%s)', (config_id, project, UUID(mcp['id'])))
    config = get_agent_config('operations', conninfo=store.conninfo)
    hosts = {host['id']: host for host in config['ssh']['servers']}
    assert set(hosts) == {first['id'], second['id']}
    assert hosts[first['id']]['instructions'] == 'Inspect this host only.'
    assert hosts[first['id']]['skills'][0]['id'] == skill['id']
    assert hosts[first['id']]['mcp_servers'][0]['id'] == mcp['id']
    assert hosts[first['id']]['mcp_servers'][0]['bearer_token_ref'] is None
    assert hosts[second['id']]['instructions'] == config['instructions']
    assert hosts[second['id']]['skills'] == config['skills']['items']
    assert hosts[second['id']]['mcp_servers'] == config['mcp']['servers']
    assert hosts[second['id']]['private_key_ref'] is None
    assert all(item['id'] != skill['id'] for item in config['skills']['items'])
    isolated = get_agent_config(other['id'], conninfo=store.conninfo)
    assert isolated['ssh']['servers'] == isolated['mcp']['servers'] == isolated['skills']['items'] == []


def test_paths_use_runtime_mounts_without_host_resolution_or_database_changes(
    store, saved_agent, tmp_path, monkeypatch
):
    monkeypatch.setenv('CODE_ROOT', '/agent/source')
    monkeypatch.setenv('DATA_ROOT_DIR', '/agent/documents')
    monkeypatch.setenv('DATA_ROOT', '/host/data')  # the host folder is display-only; agents get container paths
    config = get_agent_config('operations', conninfo=store.conninfo)
    monkeypatch.chdir(tmp_path)
    assert get_agent_config('operations', conninfo=store.conninfo) == config
    assert config['code']['projects'][0]['folder_paths'] == ['/agent/source/managed/payments']
    assert config['documents']['collections'][0]['paths'] == ['/agent/documents/payments']
    assert config['services']['items'][0]['repositories'][0]['checkout_path'] == '/agent/source/managed/unselected'
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
        conn.execute('DELETE FROM ka.agent_configs WHERE agent=%s', (agent['id'],))
        conn.execute('DELETE FROM ka.operations_settings WHERE agent=%s', (agent['id'],))
        parameters = conn.info.get_parameters()
    for name in ('host', 'port', 'user', 'dbname'):
        monkeypatch.setenv('PGDATABASE' if name == 'dbname' else 'PG' + name.upper(), parameters[name])
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    config = get_agent_config(agent['id'])
    assert config['name'] == 'Independent agent' and config['model'] is None
    assert config['system_prompt']['content'] == 'Instructions for Independent agent.'
    assert config['settings_revision'] is None and config['instructions'] == ''
    assert config['ssh']['servers'] == config['code']['projects'] == config['skills']['items'] == []
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.agent_configs WHERE agent=%s', (agent['id'],)).fetchone() is None
        assert conn.execute('SELECT 1 FROM ka.operations_settings WHERE agent=%s', (agent['id'],)).fetchone() is None
    for missing in (agent['name'], "' OR true --"):
        with pytest.raises(LookupError):
            get_agent_config(missing)


def test_agent_service_context_includes_dependency_directions(store, saved_agent):
    client = saved_agent[0]
    service = get_agent_config('operations', conninfo=store.conninfo)['services']['items'][0]
    dependency = client.post('/api/services', headers=HEADERS, json={'name': 'PostgreSQL'}).json()
    response = client.put(
        '/api/services/' + service['id'],
        headers=HEADERS,
        json={
            'name': service['name'],
            'revision': service['revision'],
            'dependencies': [{'service_id': dependency['id'], 'description': 'Stores payment history'}],
        },
    )
    assert response.status_code == 200, response.text
    current = get_agent_config('operations', conninfo=store.conninfo)['services']['items'][0]
    assert current['dependencies'] == [
        {'service_id': dependency['id'], 'name': 'PostgreSQL', 'description': 'Stores payment history'}
    ]
    assert current['dependents'] == []
