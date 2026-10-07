import asyncio
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from agents.tool_context import ToolContext

from knowledge_admin.code_processing import process_code, load_code_graph, CodeProcessingError
from knowledge_admin.tools.code_graph import code_graph
from test_api import _client
from test_repositories import HEADERS


def test_go_module_root_resolves_package_function_instead_of_same_named_method(tmp_path, monkeypatch):
    root = tmp_path / 'code'
    app = root / 'app'
    (app / 'controller').mkdir(parents=True)
    (app / 'store').mkdir()
    (app / 'go.mod').write_text('module example.test/app\n\ngo 1.25\n', encoding='utf-8')
    (app / 'controller/main.go').write_text('''package controller
import "example.test/app/store"
type Ctrl struct{}
func (c *Ctrl) Handle() { store.Read() }
func (c *Ctrl) Read() {}
''', encoding='utf-8')
    (app / 'store/main.go').write_text('package store\nfunc Read() { Fix() }\nfunc Fix() {}\n', encoding='utf-8')
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    project = {'id': str(uuid4()), 'name': 'Go module', 'folder_paths': ['app']}
    graph = process_code(project)
    assert graph['relative_root'] == 'app' and graph['code_root'] == str(app)
    assert process_code(project)['reused']
    assert load_code_graph(project, graph['source_hash'])['code_root'] == str(app)
    context = ToolContext({'config': {'enabled': True, 'code': {'enabled': True, 'graphs': [graph]}},
        'credentials': {}}, tool_name='code_graph', tool_call_id='test', tool_arguments='{}')
    async def inspect():
        result = json.loads(await code_graph.on_invoke_tool(context, json.dumps({
            'project_id': project['id'], 'action': 'search', 'query': 'Handle'})))
        return json.loads(await code_graph.on_invoke_tool(context, json.dumps({
            'project_id': project['id'], 'action': 'traverse', 'node_id': result['entries'][0]['id'], 'include_code': True})))
    result = asyncio.run(inspect())
    assert any(n['name'] == 'Read' and n['filePath'] == 'store/main.go' for n in result['entries'])
    assert any(n['name'] == 'Fix' for n in result['entries'])
    assert not any(n['name'] == 'Read' and n['filePath'].startswith('controller/') for n in result['entries'])
    assert all(n['source_status'] == 'matches_index' for n in result['entries'])
    assert not (app / '.codegraph').exists()


def test_real_multilanguage_graph_incremental_hashes_scope_and_queries(store, tmp_path, monkeypatch):
    if not (Path(__file__).resolve().parents[2] / 'code-graph/node_modules/@colbymchenry/codegraph').resolve().exists():
        pytest.skip('Install services/code-graph npm dependencies for the real engine integration test')
    root = (tmp_path / 'code').resolve()
    app = root / 'app'
    app.mkdir(parents=True)
    (root / 'unselected').mkdir()
    (root / 'unselected/private.py').write_text('def hidden(): pass\n')
    (app / 'escape.py').symlink_to(root / 'unselected/private.py')
    (app / '.env').write_text('DO_NOT_COPY=secret\n')
    (app / 'main.py').write_text('def save_record():\n    return 1\n\ndef handle():\n    return save_record()\n')
    (app / 'main.go').write_text('package main\nfunc store() {}\nfunc handler() { store() }\n')
    (app / 'state.sql').write_text('CREATE TABLE records(id integer PRIMARY KEY);\n')
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    project = {'id': str(uuid4()), 'name': 'Mixed service', 'folder_paths': ['app']}
    first = process_code(project)
    assert first['stats']['filesByLanguage'] == {'go': 1, 'python': 1}
    assert first['stats']['edgesByKind']['calls'] == 2
    assert first['source_file_count'] == 3  # SQL remains source evidence, not a supported graph language.
    mirror = tmp_path / 'indexes' / project['id'] / 'source'
    assert not (mirror / 'app/.env').exists() and not (mirror / 'app/escape.py').exists()
    assert not (mirror / 'unselected').exists()
    assert not (app / '.codegraph').exists()
    with pytest.raises(CodeProcessingError, match='changed'):
        load_code_graph(project)
    assert load_code_graph(project, first['source_hash'])['source_hash'] == first['source_hash']
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        workspace = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'mixed','Mixed')", (project['id'],workspace))
    view = client.get('/api/code-projects/' + project['id'] + '/graph', headers=HEADERS)
    assert view.status_code == 200 and view.json()['total'] == 2
    symbols = client.get('/api/code-projects/' + project['id'] + '/graph', headers=HEADERS, params={'file': 'app/main.py'}).json()
    assert {n['label'] for n in symbols['nodes']} == {'save_record', 'handle'}
    assert symbols['edges'][0]['label'] == 'calls'
    before = (mirror / 'app/main.py').stat().st_mtime_ns
    assert process_code(project)['reused'] is True
    assert (mirror / 'app/main.py').stat().st_mtime_ns == before
    context = ToolContext({'config': {'enabled': True, 'code': {'enabled': True, 'graphs': [first]}}, 'credentials': {}}, tool_name='code_graph', tool_call_id='test', tool_arguments='{}')
    async def query(**args):
        return json.loads(await code_graph.on_invoke_tool(context, json.dumps({'project_id': project['id'], **args})))
    found = asyncio.run(query(action='search', query='save_record'))
    assert len(found['entries']) == 1 and found['path_base'] == str(root)
    callers = asyncio.run(query(action='callers', node_id=found['entries'][0]['id']))
    assert [row['node']['name'] for row in callers['entries']] == ['handle']
    page = asyncio.run(query(action='search', limit=1))
    assert page['next_offset'] == 1
    assert not asyncio.run(query(action='search', offset=1000))['entries']
    denied = asyncio.run(code_graph.on_invoke_tool(context, json.dumps({'project_id': str(uuid4())})))
    assert 'Choose a configured' in denied
    # Detect actual content changes even when the source size and mtime are preserved.
    original_stat = (app / 'main.py').stat()
    (app / 'main.py').write_text('def store_value():\n    return 2\n\ndef handle():\n    return store_value()\n')
    import os
    os.utime(app / 'main.py', ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    (app / 'main.go').unlink()
    with pytest.raises(CodeProcessingError, match='changed'):
        load_code_graph(project, first['source_hash'])
    second = process_code(project)
    assert second['source_hash'] != first['source_hash'] and not second['reused']
    assert second['stats']['filesByLanguage'] == {'python': 1}
    with sqlite3.connect(second['graph_path']) as db:
        assert db.execute("SELECT count(*) FROM nodes WHERE name IN ('store','save_record')").fetchone()[0] == 0
    # Previously generated documentation can still query its immutable evidence snapshot.
    assert asyncio.run(query(action='search', query='save_record'))['entries']
    earlier = client.get('/api/code-projects/' + project['id'] + '/graph', headers=HEADERS,
        params={'source_hash': first['source_hash'], 'file': 'app/main.py'})
    assert earlier.json()['source_hash'] == first['source_hash']
    assert {n['label'] for n in earlier.json()['nodes']} == {'save_record', 'handle'}
    assert client.get('/api/code-projects/' + project['id'] + '/graph', headers=HEADERS,
        params={'source_hash': '../source'}).status_code == 400
    project['folder_paths'] = ['missing']
    empty = process_code(project)
    assert empty['status'] == 'unavailable' and empty['missing_folders'] == ['missing']
    assert empty['stats']['nodeCount'] == 0


def test_processing_api_and_worker_use_mapped_projects_without_git_credentials(store, tmp_path, monkeypatch):
    from knowledge_admin import repository_worker
    root = tmp_path / 'code'
    (root / 'app').mkdir(parents=True)
    monkeypatch.setenv('CODE_ROOT', str(root))
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/code-projects', headers=HEADERS, json={
        'name': 'Local source', 'remote_url': None, 'folder_paths': ['app']})
    assert response.status_code == 201
    project_id = response.json()['id']
    url = f'/api/code-projects/{project_id}/process'
    assert client.post(url).status_code == 403
    assert client.post(f'/api/code-projects/{uuid4()}/process', headers=HEADERS).status_code == 404
    queued = client.post(url, headers=HEADERS)
    assert queued.status_code == 202
    assert client.post(url, headers=HEADERS).json() == queued.json()
    calls = []
    def process(project):
        calls.append(project)
        return {'status': 'ready', 'source_hash': 'snapshot', 'stats': {'nodeCount': 3}}
    monkeypatch.setattr(repository_worker, 'process_code', process)
    monkeypatch.setattr(store, 'decrypt_git_credentials', lambda *_: pytest.fail('Code indexing must not resolve Git tokens'))
    assert repository_worker.run_once(store, root)
    assert calls[0]['folder_paths'] == ['app']
    row = client.get('/api/code-projects', headers=HEADERS).json()['projects'][0]
    assert row['processing']['status'] == 'succeeded'
    assert row['processing']['result']['source_hash'] == 'snapshot'


def _saved_before_data_root(tmp_path, monkeypatch):
    """A graph written while code lived at /work/code and graphs at /work/code-index."""
    from knowledge_admin.code_processing import source_inventory
    root = tmp_path / 'data' / 'code'
    (root / 'app').mkdir(parents=True)
    (root / 'app' / 'main.py').write_text('def handle():\n    return 1\n', encoding='utf-8')
    indexes = tmp_path / 'data' / 'code-index'
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('CODE_INDEX_ROOT', str(indexes))
    project = {'id': str(uuid4()), 'name': 'Upgraded', 'folder_paths': ['app']}
    inventory = source_inventory(project)
    directory = indexes / project['id']
    snapshot = directory / inventory['source_hash']
    snapshot.mkdir(parents=True)
    (snapshot / 'graph.sqlite').write_bytes(b'')
    old = {'id': project['id'], 'name': 'Upgraded', 'source_hash': inventory['source_hash'],
        'folder_paths': ['/work/code/app'], 'missing_folders': [], 'relative_root': '.',
        'graph_path': f"/work/code-index/{project['id']}/{inventory['source_hash']}/graph.sqlite", 'code_root': '/work/code/.'}
    (snapshot / 'summary.json').write_text(json.dumps(old))
    (directory / 'manifest.json').write_text(json.dumps({**old, 'files': inventory['files']}))
    return project, root, snapshot, inventory['source_hash']


def test_a_graph_saved_under_the_old_mounts_is_read_at_the_current_folders(tmp_path, monkeypatch):
    project, root, snapshot, source_hash = _saved_before_data_root(tmp_path, monkeypatch)
    for graph in (load_code_graph(project, source_hash), process_code(project)):
        assert graph['folder_paths'] == [str(root / 'app')]
        assert graph['code_root'] == str(root)
        assert graph['graph_path'] == str(snapshot / 'graph.sqlite')


def test_a_generation_queued_before_the_upgrade_reads_code_at_the_current_folder(tmp_path, monkeypatch):
    from knowledge_admin.code_paths import rebase_code_path
    project, root, _, source_hash = _saved_before_data_root(tmp_path, monkeypatch)
    assert rebase_code_path('/work/code/app') == str(root / 'app')
    assert rebase_code_path(str(root / 'app')) == str(root / 'app')
    assert rebase_code_path('/work/code-index/x') == '/work/code-index/x'
    graph = load_code_graph({**project, 'folder_paths': ['/work/code/app']}, source_hash)
    assert graph['folder_paths'] == [str(root / 'app')]


def test_loading_a_graph_that_was_never_generated_asks_to_generate_it(tmp_path, monkeypatch):
    (tmp_path / 'code/app').mkdir(parents=True)
    monkeypatch.setenv('CODE_ROOT', str(tmp_path / 'code'))
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    with pytest.raises(CodeProcessingError) as raised:
        load_code_graph({'id': str(uuid4()), 'name': 'App', 'folder_paths': ['app']})
    assert str(raised.value) == 'No code graph yet. Generate the graph in Code projects first.'
