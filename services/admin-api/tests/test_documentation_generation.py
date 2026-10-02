import copy
import io
import json
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from agents.usage import Usage

from knowledge_admin import documentation_generation as generation
from test_api import _client
from test_repositories import HEADERS


def setup_request(store, tmp_path, monkeypatch):
    import asyncssh
    from test_llm import create
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    monkeypatch.setattr(generation, 'load_code_graph', lambda project, expected_hash=None: {'id': str(project['id']), 'name': project['name'], 'source_file_count': 1, 'missing_folders': [], 'source_hash': 'fixture'})
    client = _client(tmp_path, repository_store=store)
    model = create(client)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        repo_id = uuid4()
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'fixture-app','App code')", (repo_id, project))
        conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'app')", (repo_id, project))
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Application', 'service_type': 'custom', 'repository_ids': [str(repo_id)]}).json()
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'Host', 'hostname': 'test.internal', 'ssh_allowed': True}).json()
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    response = client.put('/api/servers/' + server['id'] + '/ssh', headers=HEADERS, json={
        'revision': server['revision'], 'username': 'inspector', 'port': 22, 'private_key': key, 'known_hosts': ''})
    assert response.status_code == 200, response.text
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'Dev', 'service_ids': [service['id']], 'server_ids': [server['id']]}).json()
    prompts = {p['name']: p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']}
    value = {'scope': 'service', 'model_id': model['id'], 'service_id': service['id'], 'environment_id': env['id'], 'code_project_ids': [str(repo_id)], 'server_ids': [server['id']],
             'units': [{'kind': kind, 'prompt_id': prompts[name]['id'], 'prompt_version_id': prompts[name]['active_version_id']} for kind, name in [('code', 'Code analysis and architecture'), ('diagram', 'Code component diagram (D2 / TALA)'), ('deployment', 'Deployment and service operations')]],
             'instructions': 'Explain operations'}
    return client, value, key


def test_generation_scopes_tools_saves_versions_and_diagram_bundle(store, tmp_path, monkeypatch):
    client, value, key = setup_request(store, tmp_path, monkeypatch)
    calls = []
    async def run(config, task, **kwargs):
        calls.append((copy.deepcopy(config), task))
        assert kwargs['max_output_tokens'] == 16384
        assert kwargs['stream'] is False
        if kwargs['output_type'] is generation.ComponentGraph:
            result = generation.ComponentGraph(nodes=[{'id': 'app', 'label': 'App', 'parent_id': None}, {'id': 'api', 'label': 'API', 'parent_id': 'app'}, {'id': 'db', 'label': 'Database', 'parent_id': None}], edges=[{'source': 'api', 'target': 'db', 'label': 'stores'}], explanation='Source: app/main.py')
        else:
            result = generation.GeneratedDocument(title='Analysis', description='Evidence', markdown='# Analysis\n\nSource: app/main.py')
        return SimpleNamespace(final_output=result)
    monkeypatch.setattr(generation, 'agent', run)
    response = client.post('/api/documentation/generations', headers=HEADERS, json=value)
    assert response.status_code == 202, response.text
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 409
    assert generation.run_once(store)
    assert len(calls) == 3
    assert calls[0][0]['code']['projects'][0]['folder_paths'] == ['/work/code/app']
    assert calls[0][0]['code']['projects'][0]['id'] == value['code_project_ids'][0]
    assert not calls[0][0]['ssh']['enabled'] and not calls[0][0]['mcp']['enabled']
    assert json.loads(calls[0][1])['sources']['environment'] is None
    assert calls[1][0]['code']['enabled'] and not calls[1][0]['ssh']['enabled']
    assert calls[-1][0]['ssh']['servers'][0]['id'] == value['server_ids'][0]
    assert calls[-1][0]['ssh']['servers'][0]['username'] == 'inspector'
    assert not calls[-1][0]['code']['enabled']
    assert json.loads(calls[1][1])['code_analysis'].startswith('# Analysis')
    with store.connect() as conn:
        snapshots = conn.execute('SELECT agent_snapshot,prompt_snapshot,source_snapshot FROM ka.documentation_generations').fetchone()
        assert key not in json.dumps(snapshots) and 'secret-test-key' not in json.dumps(snapshots)
        assert not conn.execute("SELECT 1 FROM ka.agents WHERE id LIKE 'documentation-%'").fetchone()
    credentials = generation.load_credentials(store, snapshots['agent_snapshot']['deployment'])
    assert key in credentials.values()
    jobs = client.get('/api/documentation/generations', headers=HEADERS).json()['generations']
    assert jobs[0]['status'] == 'succeeded', jobs
    assert jobs[0]['result']['export_error'] is None
    assert len(jobs[0]['result']['output_folders']) == 3
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert len(docs) == 3
    code = next(d for d in docs if d['purpose'] == 'code')
    assert code['environment_ids'] == [] and code['server_ids'] == []
    assert client.get('/api/documentation/documents', headers=HEADERS, params={'environment_id': value['environment_id']}).json()['total'] == 1
    diagram = next(d for d in docs if d['purpose'] == 'diagram')
    detail = client.get('/api/documentation/documents/' + diagram['id'], headers=HEADERS).json()
    assert detail['current_version']['origin'] == 'generated' and '```d2' in detail['current_version']['markdown']
    bundle = client.get('/api/documentation/documents/' + diagram['id'] + '/bundle', headers=HEADERS)
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.namelist() == [diagram['slug'] + '/README.md', diagram['slug'] + '/architecture-1.d2']
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 409
    assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'update_existing': True}).status_code == 202
    assert generation.run_once(store)
    assert client.get('/api/documentation/documents/' + code['id'], headers=HEADERS).json()['version'] == 2


def test_generation_rejects_unselected_resources(store, tmp_path, monkeypatch):
    client, value, key = setup_request(store, tmp_path, monkeypatch)
    assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'server_ids': [str(uuid4())]}).status_code == 400
    assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'code_project_ids': [str(uuid4())]}).status_code == 400
    with store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (value['server_ids'][0],))
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 400
    assert client.post('/api/documentation/generations', json=value).status_code == 403


@pytest.mark.parametrize('change_during_run', [False, True])
def test_generation_records_and_checks_source_commit(store, tmp_path, monkeypatch, change_during_run):
    import subprocess
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    root = tmp_path / 'code'
    checkout = root / 'app'
    checkout.mkdir(parents=True)
    monkeypatch.setenv('CODE_ROOT', str(root))
    def git(*args):
        return subprocess.run(['git', '-C', str(checkout), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    (checkout / 'main.py').write_text('print("hello")\n')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', 'Baseline')
    commit = git('rev-parse', 'HEAD')
    value['units'] = [value['units'][0]]
    value['server_ids'] = []
    value['environment_id'] = None
    value['source_commits'] = {value['code_project_ids'][0]: commit}
    async def run(*args, **kwargs):
        if change_during_run:
            (checkout / 'main.py').write_text('print("changed")\n')
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Code', description='Source', markdown='# Code'))
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'source_commits': {value['code_project_ids'][0]: 'a' * 40}}).status_code == 409
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    with store.connect() as conn:
        snapshot = conn.execute('SELECT source_snapshot FROM ka.documentation_generations').fetchone()['source_snapshot']
        assert snapshot['code_projects'][0]['current_commit'] == commit
    assert generation.run_once(store)
    rows = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    if change_during_run:
        assert rows == []
        assert client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]['status'] == 'failed'
    else:
        document = client.get('/api/documentation/documents/' + rows[0]['id'], headers=HEADERS).json()
        assert document['current_version']['source_snapshot']['code_projects'][0]['git_commit'] == commit


def test_failure_publishes_no_partial_documents_or_sensitive_error(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    async def fail(*args, **kwargs):
        await kwargs['on_usage'](Usage(requests=1, input_tokens=80, output_tokens=20, total_tokens=100))
        await kwargs['on_usage'](Usage())  # A completed response without provider usage.
        raise ValueError('sensitive-provider-key')
    monkeypatch.setattr(generation, 'agent', fail)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    assert client.get('/api/documentation/documents', headers=HEADERS).json()['total'] == 0
    response = client.get('/api/documentation/generations', headers=HEADERS)
    assert response.json()['generations'][0]['status'] == 'failed'
    row = response.json()['generations'][0]
    usage = row['token_usage'][0]
    assert usage['phase'] == 'code' and usage['requests'] == 2 and usage['reported_calls'] == 1
    assert usage['input_tokens'] == 80 and usage['output_tokens'] == 20 and usage['total_tokens'] == 100
    assert row['progress'] != 'Recorded model token usage'
    assert 'sensitive-provider-key' not in response.text


@pytest.mark.parametrize('nodes,edges', [
    ([{'id': 'a', 'label': 'A', 'parent_id': 'a'}], []),
    ([{'id': 'a', 'label': 'A', 'parent_id': None}], [{'source': 'a', 'target': 'missing', 'label': ''}]),
    ([{'id': 'a', 'label': 'A', 'parent_id': None}] * 2, []),
])
def test_invalid_graphs_are_rejected(nodes, edges):
    with pytest.raises(ValidationError):
        generation.ComponentGraph(nodes=nodes, edges=edges, explanation='')


def test_document_environment_links_and_export_escape(store, tmp_path, monkeypatch):
    from knowledge_admin.documents import export_document
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'symlink-doc').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        export_document({'slug': 'symlink-doc', 'revision': 1, 'markdown': 'text'})
    assert list(outside.iterdir()) == []
    client = _client(tmp_path, repository_store=store)
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'Prod'}).json()
    value = {'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook', 'environment_ids': [env['id']]}
    doc = client.post('/api/documentation/documents', headers=HEADERS, json=value)
    assert doc.status_code == 201
    endpoint = '/api/documentation/documents/' + doc.json()['id']
    assert client.get(endpoint, headers=HEADERS).json()['environment_ids'] == [env['id']]
    exported = client.post(endpoint + '/export', headers=HEADERS)
    assert exported.status_code == 200
    assert (tmp_path / 'runbook/v1/README.md').read_text() == '# Runbook'
    assert client.post('/api/documentation/documents', headers=HEADERS, json={**value, 'slug': 'invalid', 'environment_ids': [str(uuid4())]}).status_code == 400


def test_concurrent_document_edit_is_preserved_and_generation_is_atomic(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    value['units'] = [u for u in value['units'] if u['kind'] != 'diagram']
    async def run(*args, **kwargs):
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Analysis', description='Evidence', markdown='# Generated'))
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    generation.run_once(store)
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'update_existing': True}).status_code == 202
    deployment = next(d for d in docs if d['purpose'] == 'deployment')
    updated = {'slug': deployment['slug'], 'title': 'Human edit', 'markdown': '# Keep this edit', 'revision': 1}
    assert client.put('/api/documentation/documents/' + deployment['id'], headers=HEADERS, json=updated).status_code == 200
    generation.run_once(store)
    history = client.get('/api/documentation/generations', headers=HEADERS).json()['generations']
    assert history[0]['status'] == 'failed' and 'changed' in history[0]['error']
    after = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert {d['id']: d['revision'] for d in after} == {d['id']: 2 if d['id'] == deployment['id'] else 1 for d in docs}


def test_prompt_fixtures_are_active_idempotent_and_guarded_on_rollback(store, tmp_path):
    from pathlib import Path
    import psycopg
    client = _client(tmp_path, repository_store=store)
    prompts = client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']
    assert len(prompts) == 11 and all(p['active_version'] in (1, 2, 3) for p in prompts)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute('SELECT ka.seed_environment_documentation_prompt(%s)', (project,))
        assert conn.execute('SELECT count(*) AS n FROM ka.documentation_prompts').fetchone()['n'] == len(prompts)
    fixture = next(p for p in prompts if p['name'] == 'Deployment and service operations')
    detail = client.get('/api/documentation/prompts/' + fixture['id'], headers=HEADERS).json()
    assert client.put('/api/documentation/prompts/' + fixture['id'], headers=HEADERS, json={
        'name': fixture['name'], 'input_kind': 'server', 'content': detail['versions'][0]['content'] + '\nCustom note.', 'revision': fixture['revision']}).status_code == 200
    migration = Path(__file__).resolve().parents[3] / 'db/migrations/0046_documentation_generation.down.sql'
    with store.connect() as conn:
        conn.execute(migration.with_name('0058_graph_directed_api_documentation.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0057_bounded_api_documentation.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0056_messaging_documentation.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0055_documentation_sessions.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0054_api_documentation.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0053_application_overview.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0052_codegraph_documentation.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0049_layered_environment_architecture.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0048_server_infrastructure_documentation.down.sql').read_text(), prepare=False)
        conn.execute(migration.with_name('0047_documentation_workflows.down.sql').read_text(), prepare=False)
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            conn.execute(migration.read_text(), prepare=False)


def test_diagram_only_and_prompt_override(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    unit = next(u for u in value['units'] if u['kind'] == 'diagram')
    value.update(units=[{**unit, 'prompt_override': 'Focus on API boundaries with evidence.'}], environment_id=None, server_ids=[])
    calls = []
    async def run(config, task, **kwargs):
        calls.append((config, json.loads(task)))
        return SimpleNamespace(final_output=generation.ComponentGraph(nodes=[{'id': 'app', 'label': 'App', 'parent_id': None}], edges=[], explanation='Source: app/main.py'))
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    generation.run_once(store)
    assert len(calls) == 1 and calls[0][0]['code']['enabled']
    assert calls[0][0]['system_prompt']['content'] == 'Focus on API boundaries with evidence.'
    assert calls[0][1]['code_analysis'] == ''
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert [d['purpose'] for d in docs] == ['diagram'] and docs[0]['environment_ids'] == []
    with store.connect() as conn:
        prompt = conn.execute('SELECT prompt_snapshot FROM ka.documentation_generations').fetchone()['prompt_snapshot'][0]
        assert prompt['overridden'] and prompt['content'] != prompt['active_content']
    saved = client.get('/api/documentation/prompts/' + unit['prompt_id'], headers=HEADERS).json()
    assert saved['active_version_id'] == unit['prompt_version_id']
    assert saved['versions'][0]['content'] == prompt['active_content']


def test_stale_prompt_and_blank_override_rejected(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    value['units'][0]['prompt_version_id'] = str(uuid4())
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 409
    value['units'][0]['prompt_override'] = '  '
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 400
    assert not generation.run_once(store)


def test_environment_scope_filters_relationships_and_links_members(store, tmp_path, monkeypatch):
    client, request, _ = setup_request(store, tmp_path, monkeypatch)
    outside = client.post('/api/services', headers=HEADERS, json={'name': 'Outside'}).json()
    database = client.post('/api/services', headers=HEADERS, json={'name': 'Database', 'service_type': 'builtin',
        'dependencies': [{'service_id': outside['id'], 'description': 'Outside this environment'}],
        'dependents': [{'service_id': request['service_id'], 'description': 'Stores records'}]}).json()
    env_id, host_id = request['environment_id'], request['server_ids'][0]
    assert client.put('/api/environments/' + env_id, headers=HEADERS, json={'name': 'Dev', 'revision': 1,
        'service_ids': [request['service_id'], database['id']], 'server_ids': [host_id]}).status_code == 200
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime,runtime_name) SELECT project_id,id,%s,'Dev','systemd','app' FROM ka.services WHERE id=%s", (host_id,request['service_id']))
        conn.execute("INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime,runtime_name) SELECT project_id,id,%s,'Prod','systemd','db' FROM ka.services WHERE id=%s", (host_id,database['id']))
    prompt = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == 'Environment architecture')
    value = {'scope': 'environment', 'model_id': request['model_id'], 'environment_id': env_id,
        'units': [{'kind': 'architecture', 'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id']}]}
    calls = []
    async def run(config, task, **kwargs):
        calls.append((config,json.loads(task)))
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Dev architecture', description='Recorded inventory', markdown='# Dev'))
    monkeypatch.setattr(generation, 'agent', run)
    response = client.post('/api/documentation/generations', headers=HEADERS, json=value)
    assert response.status_code == 202, response.text
    generation.run_once(store)
    config, task = calls[0]
    assert not config['ssh']['enabled'] and not config['code']['enabled']
    services = task['sources']['services']
    assert len(services) == 2
    assert all(d['service_id'] != outside['id'] for s in services for d in s['dependencies'])
    assert len([d for s in services for d in s['deployments']]) == 1
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert len(docs) == 1
    assert set(docs[0]['service_ids']) == {request['service_id'], database['id']}
    assert docs[0]['server_ids'] == [host_id] and docs[0]['environment_ids'] == [env_id]
    assert docs[0]['code_project_ids'] == request['code_project_ids']
    detail = client.get('/api/documentation/documents/' + docs[0]['id'], headers=HEADERS).json()
    markdown = detail['current_version']['markdown']
    assert '```d2' in markdown and 'depends on' in markdown and 'recorded deployment' in markdown
    from knowledge_admin.documents import document_files
    diagrams = document_files(markdown)
    assert 'Outside' not in markdown and len(diagrams) == 4
    assert diagrams['architecture-2.d2'].count('recorded deployment') == 1


def test_environment_fixture_rollback_protects_customizations(store, tmp_path):
    from pathlib import Path
    import psycopg
    client = _client(tmp_path, repository_store=store)
    fixture = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == 'Environment architecture')
    assert client.put('/api/documentation/prompts/' + fixture['id'], headers=HEADERS, json={
        'name': fixture['name'], 'input_kind': 'mixed', 'content': 'My architecture instructions', 'revision': fixture['revision']}).status_code == 200
    down = Path(__file__).resolve().parents[3] / 'db/migrations/0047_documentation_workflows.down.sql'
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            conn.execute(down.read_text(), prepare=False)


def test_overview_and_server_infrastructure_are_separate_scoped_documents(store, tmp_path, monkeypatch):
    client, request, key = setup_request(store, tmp_path, monkeypatch)
    second_host = client.post('/api/servers', headers=HEADERS, json={'name': 'Second host', 'hostname': 'other.internal', 'ssh_allowed': True}).json()
    assert client.put('/api/servers/' + second_host['id'] + '/ssh', headers=HEADERS, json={
        'revision': second_host['revision'], 'username': 'observer', 'port': 22, 'private_key': key, 'known_hosts': ''}).status_code == 200
    second_service = client.post('/api/services', headers=HEADERS, json={'name': 'Other service'}).json()
    env_id, host_id = request['environment_id'], request['server_ids'][0]
    assert client.put('/api/environments/' + env_id, headers=HEADERS, json={'name': 'Dev', 'revision': 1,
        'service_ids': [request['service_id'], second_service['id']], 'server_ids': [host_id,second_host['id']]}).status_code == 200
    with store.connect() as conn:
        for service_id, server_id in [(request['service_id'],host_id),(second_service['id'],second_host['id'])]:
            conn.execute("INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime,runtime_name) SELECT project_id,id,%s,'Dev','systemd','app' FROM ka.services WHERE id=%s", (server_id,service_id))
    prompts = {p['name']: p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']}
    units = [{'kind': kind, 'prompt_id': prompts[name]['id'], 'prompt_version_id': prompts[name]['active_version_id']} for kind, name in [
        ('architecture','Environment architecture'), ('infrastructure','Server infrastructure architecture')]]
    value = {'scope': 'environment', 'model_id': request['model_id'], 'environment_id': env_id, 'server_ids': [host_id], 'units': units}
    calls = []
    async def run(config, task, **kwargs):
        calls.append((config,json.loads(task)))
        return SimpleNamespace(final_output=generation.GeneratedDocument(title=config['name'], description='Evidence', markdown='# Observations'))
    monkeypatch.setattr(generation, 'agent', run)
    response = client.post('/api/documentation/generations', headers=HEADERS, json=value)
    assert response.status_code == 202, response.text
    generation.run_once(store)
    assert len(calls) == 2
    infrastructure, overview = calls
    assert overview[1]['sources']['infrastructure_findings']['server_ids'] == [host_id]
    assert overview[1]['sources']['infrastructure_findings']['document']['markdown'] == '# Observations'
    assert 'supporting_documents' not in infrastructure[1]['sources']
    assert not overview[0]['ssh']['enabled'] and overview[1]['sources']['server_ids'] == []
    assert len(overview[1]['sources']['environment']['servers']) == 2
    assert not infrastructure[0]['code']['enabled'] and infrastructure[0]['ssh']['enabled']
    assert [s['id'] for s in infrastructure[0]['ssh']['servers']] == [host_id]
    assert [s['id'] for s in infrastructure[1]['sources']['services']] == [request['service_id']]
    assert [s['id'] for s in infrastructure[1]['sources']['environment']['servers']] == [host_id]
    assert key in generation.load_credentials(store, infrastructure[0]).values()
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert {d['purpose'] for d in docs} == {'architecture','infrastructure'}
    host_doc = next(d for d in docs if d['purpose'] == 'infrastructure')
    assert host_doc['environment_ids'] == [env_id] and host_doc['server_ids'] == [host_id]
    assert host_doc['service_ids'] == [request['service_id']] and host_doc['code_project_ids'] == request['code_project_ids']
    assert len(host_doc['deployment_ids']) == 1
    for doc in docs:
        detail = client.get('/api/documentation/documents/' + doc['id'], headers=HEADERS).json()
        assert ('```d2' in detail['current_version']['markdown']) == (doc['purpose'] == 'architecture')
    # Each server has its own document/version sequence within the same environment.
    value['units'] = [units[1]]
    value['server_ids'] = [second_host['id']]
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    generation.run_once(store)
    value.update(server_ids=[host_id], update_existing=True)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    generation.run_once(store)
    after = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert len(after) == 3
    assert all(d['revision'] == (2 if d['id'] == host_doc['id'] else 1) for d in after)


def test_infrastructure_requires_one_environment_member_with_saved_ssh(store, tmp_path, monkeypatch):
    client, request, _ = setup_request(store, tmp_path, monkeypatch)
    prompt = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == 'Server infrastructure architecture')
    value = {'scope': 'environment', 'model_id': request['model_id'], 'environment_id': request['environment_id'],
        'units': [{'kind': 'infrastructure', 'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id']}]}
    for servers in [[], [str(uuid4())], [request['server_ids'][0], str(uuid4())]]:
        assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'server_ids': servers}).status_code == 400
    value['server_ids'] = request['server_ids']
    assert client.post('/api/documentation/generations', headers=HEADERS, json={**value, 'scope': 'service', 'service_id': request['service_id']}).status_code == 400
    with store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (request['server_ids'][0],))
    response = client.post('/api/documentation/generations', headers=HEADERS, json=value)
    assert response.status_code == 400 and 'SSH enabled' in response.text
    assert not generation.run_once(store)


def test_infrastructure_fixture_preserves_edits_and_guards_rollback(store, tmp_path):
    from pathlib import Path
    import psycopg
    client = _client(tmp_path, repository_store=store)
    fixture = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == 'Server infrastructure architecture')
    assert fixture['active_version'] == 1 and fixture['input_kind'] == 'server'
    response = client.put('/api/documentation/prompts/' + fixture['id'], headers=HEADERS, json={
        'name': fixture['name'], 'input_kind': 'server', 'content': 'Inspect only our runtime layer', 'revision': fixture['revision']})
    assert response.status_code == 200
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute('SELECT ka.seed_server_infrastructure_prompt(%s)', (project,))
        assert conn.execute('SELECT count(*) AS n FROM ka.documentation_prompt_versions WHERE prompt_id=%s', (fixture['id'],)).fetchone()['n'] == 2
    down = Path(__file__).resolve().parents[3] / 'db/migrations/0048_server_infrastructure_documentation.down.sql'
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            conn.execute(down.read_text(), prepare=False)


def test_architecture_uses_pinned_scoped_document_evidence(store, tmp_path, monkeypatch):
    client, request, _ = setup_request(store, tmp_path, monkeypatch)
    other_environment = client.post('/api/environments', headers=HEADERS, json={'name': 'Production'}).json()
    fixtures = [
        {'slug': 'shared-code', 'purpose': 'code', 'code_project_ids': request['code_project_ids']},
        {'slug': 'shared-diagram', 'purpose': 'diagram', 'service_ids': [request['service_id']]},
        {'slug': 'dev-network', 'purpose': 'infrastructure', 'environment_ids': [request['environment_id']], 'server_ids': request['server_ids']},
        {'slug': 'prod-network', 'purpose': 'infrastructure', 'environment_ids': [other_environment['id']], 'server_ids': request['server_ids']},
        {'slug': 'old-overview', 'purpose': 'architecture', 'environment_ids': [request['environment_id']]},
        {'slug': 'unrelated-code', 'purpose': 'code'},
    ]
    saved = []
    for fixture in fixtures:
        response = client.post('/api/documentation/documents', headers=HEADERS, json={**fixture, 'title': fixture['slug'], 'markdown': '# Original ' + fixture['slug']})
        assert response.status_code == 201, response.text
        saved.append(response.json())
    prompt = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == 'Environment architecture')
    assert prompt['active_version'] == 2
    value = {'scope': 'environment', 'model_id': request['model_id'], 'environment_id': request['environment_id'],
        'units': [{'kind': 'architecture', 'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id']}]}
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert client.put('/api/documentation/documents/' + saved[0]['id'], headers=HEADERS, json={
        **fixtures[0], 'title': 'New code analysis', 'markdown': '# Changed after queue', 'revision': 1}).status_code == 200
    calls = []
    async def run(config, task, **kwargs):
        calls.append(json.loads(task))
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Architecture', description='', markdown='# Full architecture'))
    monkeypatch.setattr(generation, 'agent', run)
    generation.run_once(store)
    evidence = calls[0]['sources']['supporting_documents']
    assert {d['id'] for d in evidence} == {d['id'] for d in saved[:3]}
    assert all(d['version'] == 1 and d['markdown'].startswith('# Original') for d in evidence)
    assert all(d['version_id'] and d['content_hash'] for d in evidence)


def test_layered_diagrams_distinguish_purpose_placement_and_unverified_network():
    from knowledge_admin.documents import document_files
    unusual = 'API "public" ${untrusted} \\ <img>\n@import'
    sources = {'environment': {'servers': [{'id': 'host', 'name': 'Host', 'hostname': 'app.internal'}]}, 'services': [
        {'id': 'api', 'name': unusual, 'dependencies': [{'service_id': 'db', 'description': 'Persists customer records'}, {'service_id': 'outside', 'description': 'Exclude'}],
         'deployments': [{'server_id': 'host', 'port': 8080, 'runtime': 'container', 'runtime_name': 'api'}]},
        {'id': 'db', 'name': 'Database', 'dependencies': [], 'deployments': [{'server_id': 'host', 'port': None, 'runtime': 'systemd', 'runtime_name': 'db'}]},
    ]}
    files = document_files(generation.environment_views(sources))
    assert len(files) == 4
    functional, deployment, networking = (files[f'architecture-{n}.d2'] for n in (1,2,3))
    assert 'Persists customer records' in functional and 'Exclude' not in functional
    assert 's0 -> s1' in functional and 's1 -> s0' not in functional
    assert '\\${untrusted}' in functional and '\\"public\\"' in functional
    assert 'container / api' in deployment and 'systemd / db' in deployment
    assert 'app.internal:8080' in networking and 'app.internal:port unknown' in networking
    assert 'network route unverified' in networking and 'style.stroke-dash: 4' in networking
    assert '5432' not in networking and 'TLS' not in networking


def test_layered_fixture_upgrade_preserves_custom_prompt_and_rollback(store, tmp_path):
    from pathlib import Path
    import psycopg
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    client = _client(tmp_path, repository_store=store)
    fixture = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == 'Environment architecture')
    endpoint = '/api/documentation/prompts/' + fixture['id']
    content = client.get(endpoint, headers=HEADERS).json()['versions'][0]['content']
    for term in ['Functional architecture', 'Networking architecture', 'connection matrix', 'supporting_documents', 'infrastructure_findings', 'Data and reliability']:
        assert term in content
    with store.connect() as conn:
        conn.execute((migrations/'0049_layered_environment_architecture.down.sql').read_text(), prepare=False)
    old = client.get(endpoint, headers=HEADERS).json()
    assert len(old['versions']) == 1 and old['versions'][0]['version'] == 1
    assert client.put(endpoint, headers=HEADERS, json={
        'name': old['name'], 'input_kind': 'mixed', 'content': 'Custom system architecture', 'revision': old['revision']}).status_code == 200
    with store.connect() as conn:
        conn.execute((migrations/'0049_layered_environment_architecture.up.sql').read_text(), prepare=False)
    preserved = client.get(endpoint, headers=HEADERS).json()
    assert preserved['active_version_id'] == old['active_version_id']
    assert preserved['versions'][0]['content'] == 'Custom system architecture'
