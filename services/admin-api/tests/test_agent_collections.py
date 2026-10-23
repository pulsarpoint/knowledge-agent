import pytest

from access_helpers import entry, give, with_access
from knowledge_admin.agents.runner import code_paths, document_paths
from knowledge_admin.knowledge_agents.config import get_agent_config
from test_api import _client
from test_profiles import update
from test_repositories import HEADERS


def seed(store, *names_and_paths):
    """Collections with their paths: ('name', 'area/path', ...)."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            'INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,\'{"max_file_bytes":2000000}\') ON CONFLICT DO NOTHING',
            (project,),
        )
        ids = {}
        for name, *paths in names_and_paths:
            ids[name] = conn.execute(
                'INSERT INTO ka.rag_collections(project_id,name,description) VALUES (%s,%s,%s) RETURNING id',
                (project, name, f'About {name}'),
            ).fetchone()['id']
            for path in paths:
                conn.execute(
                    'INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)', (ids[name], path)
                )
        return ids


def test_agent_config_lists_granted_collections_with_paths(store, monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('CODE_ROOT', '/work/data/code')
    ids = seed(store, ('jira', 'jira'), ('payments', 'docs/payments', 'code/payments'))
    with store.connect() as conn:
        give(conn, 'search', [('collection', ids['payments'])])
    config = get_agent_config('search', conninfo=store.conninfo)
    assert [(c['name'], c['paths']) for c in collections(config)] == [
        ('payments', ['/work/data/code/payments', '/work/data/docs/payments'])
    ]
    assert collections(config)[0] == {
        'type': 'collection',
        'id': str(ids['payments']),
        'name': 'payments',
        'description_short': 'About payments',
        'paths': ['/work/data/code/payments', '/work/data/docs/payments'],
    }
    with store.connect() as conn:
        give(conn, 'search', [('collection', ids['payments']), ('collection', ids['jira'])])
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='payments'")
    config = get_agent_config('search', conninfo=store.conninfo)  # a deleted collection is skipped
    assert [(c['name'], c['paths']) for c in collections(config)] == [('jira', ['/work/data/jira'])]


def collections(config):
    return [item for item in config['access']['resources'] if item['type'] == 'collection']


def test_document_agents_read_every_collection_path(monkeypatch):
    config = with_access(
        {},
        [
            entry('collection', 'a', 'payments', paths=['/work/code/payments', '/work/rag-data/jira']),
            entry('collection', 'b', 'old', paths=['/work/rag-data/old']),
        ],
    )
    assert [str(p) for p in [*code_paths(config), *document_paths(config)]] == [
        '/work/code/payments',
        '/work/rag-data/jira',
        '/work/rag-data/old',
    ]


def test_agent_config_maps_any_folder_under_the_data_root(store, monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('CODE_ROOT', '/work/data/code')
    ids = seed(store, ('jira', 'jira'), ('tools', 'exports/tools', 'code-index/abc'))
    with store.connect() as conn:
        give(conn, 'search', [('collection', item) for item in ids.values()])
    config = get_agent_config('search', conninfo=store.conninfo)
    assert [(c['name'], c['paths']) for c in collections(config)] == [
        ('jira', ['/work/data/jira']),
        ('tools', ['/work/data/code-index/abc', '/work/data/exports/tools']),
    ]
    monkeypatch.setenv('DATA_ROOT_DIR', 'relative')
    with pytest.raises(ValueError, match='DATA_ROOT_DIR'):
        get_agent_config('search', conninfo=store.conninfo)


def test_profile_api_grants_collections(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, ('jira', 'rag-data/jira'))
    options = client.get('/api/agents/search/options', headers=HEADERS).json()
    assert [(c['name'], c['description_short']) for c in options['collection']] == [('jira', 'About jira')]
    agent = client.get('/api/agents/search', headers=HEADERS).json()
    assert 'all_folders' not in agent and 'folder_ids' not in agent
    jira = {'resources': [{'type': 'collection', 'id': str(ids['jira'])}]}
    saved = update(client, agent, access=jira)
    assert saved.status_code == 200, saved.text
    found = client.get('/api/agents/search', headers=HEADERS).json()['access']['resources']
    assert [(r['type'], r['id']) for r in found] == [('collection', str(ids['jira']))]
    old = update(client, saved.json(), folder_ids=[])
    assert old.status_code == 400 and 'Extra inputs' in old.text
