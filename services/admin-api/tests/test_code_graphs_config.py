"""The loader lists one graph per selected code project that has a generated graph; per-run runs keep only the graphs
of their own code projects. Availability comes from what was stored when the graph was generated (its manifest and
snapshot): loading a config never reads or hashes source files."""
import json
from uuid import UUID, uuid4

import pytest

from knowledge_admin import code_processing
from knowledge_admin.capabilities import code_graph
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.run_resources import RunResources, resolve
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import EMPTY_CHANGES, new_version
from test_repositories import HEADERS

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


def write_graph(index_root, project_id, folders, code_root='/work/code'):
    """What process_code leaves behind: a manifest and the snapshot it names."""
    directory = index_root / project_id
    (directory / 'h1').mkdir(parents=True)
    (directory / 'h1' / 'graph.sqlite').write_bytes(b'')
    (directory / 'manifest.json').write_text(json.dumps({
        'id': project_id, 'name': 'Old name', 'source_hash': 'h1', 'relative_root': '.',
        'folder_paths': [f'{code_root}/{folder}' for folder in folders], 'files': {'app/a.py': 'x'}}))
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
        'graph_path': str(directory / 'h1' / 'graph.sqlite'), 'code_root': '/work/code', 'source_hash': 'h1'}
    # Saved before the code mount moved: the manifest's legacy /work/code paths still match.
    monkeypatch.setenv('CODE_ROOT', '/data/code')
    moved = {**project, 'folder_paths': ['/data/code/app']}
    assert code_graph.current_graph(moved)['code_root'] == '/data/code'
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    # The folder selection changed since the graph was generated.
    assert code_graph.current_graph({**project, 'folder_paths': ['/work/code/app', '/work/code/lib']}) is None
    (directory / 'h1' / 'graph.sqlite').unlink()  # the snapshot is gone
    assert code_graph.current_graph(project) is None
    (directory / 'manifest.json').write_text('not json')
    assert code_graph.current_graph(project) is None
    monkeypatch.delenv('CODE_INDEX_ROOT')
    assert code_graph.current_graph(project) is None
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    monkeypatch.delenv('CODE_ROOT')
    assert code_graph.current_graph(project) is None


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
