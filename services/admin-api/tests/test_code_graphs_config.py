"""The loader lists one graph per selected code project that has a generated graph; per-run runs keep only the graphs
of their own code projects. Availability comes from what was stored when the graph was generated (its manifest and
snapshot): loading a config never reads or hashes source files."""
import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from knowledge_admin import code_processing
from knowledge_admin.capabilities import code_graph
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.run_resources import RunResources, resolve
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import EMPTY_CHANGES, new_version
from test_function_tools import context, invoke  # noqa: F401  (fixture)
from test_repositories import HEADERS
from tool_helpers import tool

GRAPH = {'name': 'Payments', 'graph_path': '/index/g.sqlite', 'code_root': '/work/code/managed/payments', 'source_hash': 'abc'}


@pytest.fixture
def graphs(monkeypatch):
    """current_graph answers from a table of project id → graph (None: no usable graph)."""
    table: dict[str, dict | None] = {}
    monkeypatch.setattr(code_graph, 'current_graph', lambda project: table.get(str(project['id'])))
    return table


def test_projects_with_a_current_graph_are_listed(store, saved_agent, graphs):
    repo = saved_agent[5]
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert config['code']['graphs'] == [{'id': repo['id'], **GRAPH}]
    graphs[repo['id']] = None
    assert get_agent_config('operations', conninfo=store.conninfo)['code']['graphs'] == []
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET repositories_enabled=false WHERE id='operations'")
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    assert get_agent_config('operations', conninfo=store.conninfo)['code']['graphs'] == []


def test_versions_get_the_current_graphs(store, saved_agent, graphs):
    client, repo = saved_agent[0], saved_agent[5]
    version = new_version(client, 'Before the graph')
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    pinned = get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])
    assert pinned['code']['graphs'] == [{'id': repo['id'], **GRAPH}]


def test_versions_never_store_graphs(store, saved_agent, graphs):
    client, repo = saved_agent[0], saved_agent[5]
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    source = client.get('/api/agents/operations/versions/source', headers=HEADERS).json()
    graphs[repo['id']] = {'id': repo['id'], **GRAPH, 'source_hash': 'regenerated'}  # an open draft stays current
    created = client.post('/api/agents/operations/versions', headers=HEADERS, json={
        'label': 'With a graph', 'source_hash': source['source_hash'], 'changes': EMPTY_CHANGES})
    assert created.status_code == 201, created.text
    with store.connect() as conn:
        stored = conn.execute("SELECT config FROM ka.agent_versions WHERE agent='operations'").fetchall()
    assert stored and all('graphs' not in row['config']['code'] for row in stored)


def test_a_per_run_run_gets_only_the_graphs_of_its_code_projects(store, saved_agent, graphs):
    repo = saved_agent[5]
    other = str(uuid4())
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'other','Other')", (UUID(other), project))
        conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'other')", (UUID(other), project))
        conn.execute("UPDATE ka.agents SET resources='per_run',all_repositories=true WHERE id='operations'")
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    graphs[other] = {'id': other, **GRAPH, 'name': 'Other'}
    config = get_agent_config('operations', conninfo=store.conninfo)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        only_other = resolve(conn, project, config, RunResources(code_project_ids=[UUID(other)]))
        nothing = resolve(conn, project, config, RunResources())
    assert [graph['id'] for graph in only_other['code']['graphs']] == [other]
    assert nothing['code']['graphs'] == []


HASH = 'a' * 64
PROCESSED = '2026-10-01T10:00:00+00:00'


def write_graph(index_root, project_id, folders, code_root='/work/code', **manifest):
    """What process_code leaves behind: a manifest and the snapshot it names."""
    saved = {'id': project_id, 'name': 'Old name', 'source_hash': HASH, 'relative_root': '.', 'processed_at': PROCESSED,
             'folder_paths': [f'{code_root}/{folder}' for folder in folders], 'files': {'app/a.py': 'x'}, **manifest}
    directory = index_root / project_id
    (directory / saved['source_hash']).mkdir(parents=True, exist_ok=True)
    (directory / saved['source_hash'] / 'graph.sqlite').write_bytes(b'')
    (directory / 'manifest.json').write_text(json.dumps(saved))
    return directory


def test_current_graph_reads_the_stored_manifest_without_hashing_sources(tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    monkeypatch.setattr(code_processing, 'source_inventory', lambda project: pytest.fail('hashed sources'))
    monkeypatch.setattr(code_graph, 'source_inventory', lambda project: pytest.fail('hashed sources'))
    project = {'id': str(uuid4()), 'name': 'App', 'folder_paths': ['/work/code/app']}
    assert code_graph.current_graph(project) is None  # never generated
    directory = write_graph(tmp_path / 'indexes', project['id'], ['app'])
    assert code_graph.current_graph(project) == {'id': project['id'], 'name': 'App',
        'graph_path': str(directory / HASH / 'graph.sqlite'), 'code_root': '/work/code', 'source_hash': HASH,
        'folder_paths': ['/work/code/app'], 'generated_at': PROCESSED}
    assert code_graph.current_graph({**project, 'folder_paths': ['app']})['folder_paths'] == ['/work/code/app']
    # Saved before the code mount moved: the manifest's legacy /work/code paths still match.
    monkeypatch.setenv('CODE_ROOT', '/data/code')
    moved = code_graph.current_graph({**project, 'folder_paths': ['/data/code/app']})
    assert (moved['code_root'], moved['folder_paths']) == ('/data/code', ['/data/code/app'])
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    # The folder selection changed since the graph was generated.
    assert code_graph.current_graph({**project, 'folder_paths': ['/work/code/app', '/work/code/lib']}) is None
    (directory / HASH / 'graph.sqlite').unlink()  # the snapshot is gone
    assert code_graph.current_graph(project) is None
    (directory / 'manifest.json').write_text('not json')
    assert code_graph.current_graph(project) is None
    monkeypatch.delenv('CODE_INDEX_ROOT')
    assert code_graph.current_graph(project) is None
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.delenv('CODE_ROOT')
    assert code_graph.current_graph(project) is None


@pytest.mark.parametrize('manifest', [{'source_hash': '../' + 'a' * 61}, {'source_hash': 'A' * 64}, {'source_hash': 'h1'},
    {'relative_root': '/etc'}, {'relative_root': '../outside'}, {'relative_root': 'app/../..'}])
def test_a_manifest_with_unsafe_values_is_not_offered(tmp_path, monkeypatch, manifest):
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    project = {'id': str(uuid4()), 'name': 'App', 'folder_paths': ['/work/code/app']}
    write_graph(tmp_path / 'indexes', project['id'], ['app'], **manifest)
    assert code_graph.current_graph(project) is None
    write_graph(tmp_path / 'indexes', project['id'], ['app'], relative_root='app')
    assert code_graph.current_graph(project)['code_root'] == '/work/code/app'


def test_code_synced_after_generation_carries_a_note(tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    project = {'id': str(uuid4()), 'name': 'App', 'folder_paths': ['/work/code/app']}
    write_graph(tmp_path / 'indexes', project['id'], ['app'])
    before = code_graph.current_graph({**project, 'synced_at': datetime(2026, 10, 1, 9, tzinfo=timezone.utc)})
    assert 'note' not in before and 'note' not in code_graph.current_graph({**project, 'synced_at': None})
    after = code_graph.current_graph({**project, 'synced_at': datetime(2026, 10, 1, 11, tzinfo=timezone.utc)})
    assert after['note'] == code_graph.SYNCED_AFTER == ('The code was synced after this graph was generated; '
                                                        'regenerate it or confirm with read_file.')


def test_loading_a_config_reads_no_source_files(store, saved_agent, tmp_path, monkeypatch):
    repo = saved_agent[5]
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.setattr(code_processing, 'source_inventory', lambda project: pytest.fail('hashed sources'))
    monkeypatch.setattr(code_graph, 'source_inventory', lambda project: pytest.fail('hashed sources'))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for index in range(20):
            other = uuid4()
            conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)",
                (other, project, f'p{index}', f'P{index}'))
            conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)",
                (other, project, f'p{index}'))
            write_graph(tmp_path / 'indexes', str(other), [f'p{index}'])
        conn.execute("UPDATE ka.agents SET all_repositories=true WHERE id='operations'")
    write_graph(tmp_path / 'indexes', repo['id'], ['managed/payments'])
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert len(config['code']['graphs']) == 21
    assert repo['id'] in [graph['id'] for graph in config['code']['graphs']]


def test_loader_reads_sync_time_and_takes_projects_added_after_the_snapshot(store, saved_agent, tmp_path, monkeypatch):
    repo = saved_agent[5]
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    write_graph(tmp_path / 'indexes', repo['id'], ['managed/payments'])
    with store.connect() as conn:
        conn.execute("UPDATE ka.repositories SET synced_at='2026-10-02T00:00:00Z' WHERE id=%s", (UUID(repo['id']),))
        conn.execute("UPDATE ka.agents SET resources='per_run',all_repositories=true WHERE id='operations'")
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert [graph['note'] for graph in config['code']['graphs']] == [code_graph.SYNCED_AFTER]
    added = uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'added','Added')", (added, project))
        conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'added')", (added, project))
        write_graph(tmp_path / 'indexes', str(added), ['added'])
        run = resolve(conn, project, config, RunResources(code_project_ids=[added]))
        native_only = resolve(conn, project, {**config, 'runtime': 'claude_code'}, RunResources(code_project_ids=[added]))
    assert [(graph['id'], graph['name']) for graph in run['code']['graphs']] == [(str(added), 'Added')]
    assert native_only['code']['graphs'] == []


def test_a_generated_graph_answers_code_queries_end_to_end(context, tmp_path, monkeypatch):
    """A real CodeGraph index, published as process_code publishes it and offered by current_graph."""
    root = (tmp_path / 'code').resolve()
    (root / 'app').mkdir()
    (root / 'app' / 'source.go').write_text('package example\nfunc Handle() { helper() }\nfunc helper() {}\n', encoding='utf-8')
    bridge = Path(__file__).resolve().parents[2] / 'code-graph'
    script = '''const { CodeGraph } = require('@colbymchenry/codegraph');
        (async () => { const graph = await CodeGraph.init(process.argv[1]);
          try { await graph.indexAll(); } finally { graph.close(); } })()
          .catch(error => { console.error(error); process.exitCode = 1; });'''
    subprocess.run(['node', '-e', script, str(root)], cwd=bridge, check=True, capture_output=True,
                   env=os.environ | {'CODEGRAPH_TELEMETRY': '0', 'DO_NOT_TRACK': '1', 'CODEGRAPH_NO_DAEMON': '1'}, timeout=60)
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.setenv('CODE_ROOT', str(root))
    project = {'id': str(uuid4()), 'name': 'App', 'folder_paths': ['app'],
               'synced_at': datetime(2026, 10, 2, tzinfo=timezone.utc)}
    directory = write_graph(tmp_path / 'indexes', project['id'], ['app'], code_root=str(root))
    shutil.copyfile(root / '.codegraph/codegraph.db', directory / HASH / 'graph.sqlite')
    shutil.rmtree(root / '.codegraph')
    graph = code_graph.current_graph(project)
    context['config']['code']['graphs'] = [graph]
    query = tool('code_graph')
    search = json.loads(asyncio.run(invoke(query, context, project_id=project['id'], action='search', query='Handle')))
    node_id = search['entries'][0]['id']
    assert (search['generated_at'], search['note'].startswith(code_graph.SYNCED_AFTER)) == (PROCESSED, True)
    source = json.loads(asyncio.run(invoke(query, context, project_id=project['id'], action='code', node_id=node_id)))
    assert source['entries'][0]['code'] == 'func Handle() { helper() }'
    assert source['entries'][0]['source_status'] == 'matches_index'
    callees = json.loads(asyncio.run(invoke(query, context, project_id=project['id'], action='callees',
                                            node_id=node_id, include_code=True)))
    assert [(entry['node']['name'], entry['code']) for entry in callees['entries']] == [('helper', 'func helper() {}')]
    overview = json.loads(asyncio.run(invoke(query, context, project_id=project['id'])))
    assert overview['generated_at'] == PROCESSED and overview['note'] == code_graph.SYNCED_AFTER
