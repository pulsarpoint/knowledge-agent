from knowledge_admin.agents import get_agent_config, get_paths
from knowledge_admin.collections import area_root, document_collections, slug
from test_api import _client
from test_profiles import update
from test_repositories import HEADERS


def seed(store, *names_and_paths):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{\"max_file_bytes\":2000000}') ON CONFLICT DO NOTHING", (project,))
        return {name: conn.execute("INSERT INTO ka.rag_collections(project_id,name,description,source_path) VALUES (%s,%s,%s,%s) RETURNING id",
                                   (project, name, f'About {name}', path)).fetchone()['id'] for name, path in names_and_paths}


def test_agent_config_lists_granted_collections_with_paths(store, monkeypatch):
    monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    ids = seed(store, ('jira', 'rag-data/jira'), ('payments-code', 'code/payments'))
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_collections=false,documents_enabled=true WHERE id='search'")
        conn.execute("INSERT INTO ka.agent_collections(agent_id,collection_id) VALUES ('search',%s)", (ids['payments-code'],))
    config = get_agent_config('search', conninfo=store.conninfo)
    assert config['documents']['all_collections'] is False
    assert [(c['name'], c['path']) for c in config['documents']['collections']] == [('payments-code', '/work/code/payments')]
    assert set(config['documents']['collections'][0]) == {'id', 'name', 'description', 'path'}
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_collections=true WHERE id='search'")
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='payments-code'")
    config = get_agent_config('search', conninfo=store.conninfo)
    assert config['documents']['all_collections'] is True
    assert [(c['name'], c['path']) for c in config['documents']['collections']] == [('jira', '/work/rag-data/jira')]


def test_area_root_maps_every_area_to_its_container_root(monkeypatch):
    for name, value in (('CODE_ROOT', '/c'), ('CODE_INDEX_ROOT', '/ci'), ('RAG_SOURCE_ROOT', '/r'),
                        ('DOCUMENT_OUTPUT_ROOT', '/g'), ('BOOTSTRAP_DOCS_ROOT', '/d')):
        monkeypatch.setenv(name, value)
    assert [str(area_root(p)) for p in ('code/a', 'code-index/b', 'rag-data', 'generated-documents/x/y', 'docs/z')] == [
        '/c/a', '/ci/b', '/r', '/g/x/y', '/d/z']
    monkeypatch.setenv('CODE_ROOT', 'relative')
    try:
        area_root('code/a')
    except ValueError as error:
        assert 'CODE_ROOT must be an absolute path' in str(error)
    else:
        raise AssertionError('relative roots are rejected')


def test_legacy_folder_config_maps_to_collections(monkeypatch):
    monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')
    legacy = {'documents': {'enabled': True, 'all_folders': False, 'include_subfolders': True, 'folders': [
        {'id': 'x', 'path': '/work/rag-data/demo/payments', 'description': 'd', 'search_folder': 'demo.payments'},
        {'id': 'y', 'path': '/work/rag-data/Old_Stuff', 'description': 'o', 'search_folder': 'Old_Stuff'}]}}
    everything, items = document_collections(legacy)
    assert everything is False
    assert [(i['name'], i['path']) for i in items] == [('demo', '/work/rag-data/demo'), ('old-stuff', '/work/rag-data/Old_Stuff')]
    assert document_collections({'documents': {'all_folders': True, 'folders': []}}) == (True, [])
    assert slug('Old_Stuff') == 'old-stuff'


def test_legacy_folders_without_search_folder_use_their_path(monkeypatch):
    monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')
    legacy = {'documents': {'enabled': True, 'all_folders': False, 'folders': [
        {'path': '/work/rag-data/Runbooks/payments', 'description': 'r'},
        {'path': '/work/rag-data/Runbooks', 'description': 'duplicate top'},
        {'path': '/elsewhere/notes', 'description': 'outside the RAG root'},
        {'path': '/work/rag-data', 'description': 'the root itself'}]}}
    assert document_collections(legacy) == (False, [{'name': 'runbooks', 'description': 'r', 'path': '/work/rag-data/Runbooks'}])


def test_ungrounded_document_agents_read_collection_folders(monkeypatch):
    config = {'code': {'enabled': False, 'projects': []}, 'documents': {'enabled': True, 'grounded': False,
              'all_collections': False, 'collections': [{'id': 'a', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'}]}}
    assert [str(p) for p in get_paths(config)] == ['/work/rag-data/jira']


def test_profile_api_grants_collections(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, ('jira', 'rag-data/jira'))
    options = client.get('/api/agents/search/options', headers=HEADERS).json()
    assert [(c['name'], c['description']) for c in options['collections']] == [('jira', 'About jira')]
    agent = client.get('/api/agents/search', headers=HEADERS).json()
    assert 'all_folders' not in agent and 'folder_ids' not in agent
    saved = update(client, agent, all_collections=False, collection_ids=[str(ids['jira'])])
    assert saved.status_code == 200, saved.text
    assert client.get('/api/agents/search', headers=HEADERS).json()['collection_ids'] == [str(ids['jira'])]
    both = update(client, saved.json(), all_collections=True, collection_ids=[str(ids['jira'])])
    assert both.status_code == 400
    old = update(client, saved.json(), folder_ids=[])
    assert old.status_code == 400 and 'Extra inputs' in old.text
