import pytest

from access_helpers import give
from knowledge_admin.agents.runner import get_agent_config, get_paths
from knowledge_admin.rag.collections import document_collections, slug
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
    assert config['documents']['all_collections'] is False
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [
        ('payments', ['/work/data/code/payments', '/work/data/docs/payments'])
    ]
    assert set(config['documents']['collections'][0]) == {'id', 'name', 'description', 'paths'}
    with store.connect() as conn:
        give(conn, 'search', [('collection', ids['payments']), ('collection', ids['jira'])])
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='payments'")
    config = get_agent_config('search', conninfo=store.conninfo)  # a deleted collection is skipped
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [('jira', ['/work/data/jira'])]


def test_path_era_items_map_to_paths():
    saved = {
        'documents': {
            'all_collections': False,
            'collections': [
                {'id': 'a', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'},
                {'name': 'docs', 'description': '', 'path': ''},
            ],
        }
    }
    assert document_collections(saved) == (
        False,
        [
            {'id': 'a', 'name': 'jira', 'description': '', 'paths': ['/work/rag-data/jira']},
            {'name': 'docs', 'description': '', 'paths': []},
        ],
    )
    current = {
        'documents': {
            'all_collections': True,
            'collections': [{'id': 'a', 'name': 'x', 'description': '', 'paths': ['/a', '/b']}],
        }
    }
    assert document_collections(current) == (True, current['documents']['collections'])


def test_ungrounded_document_agents_read_every_collection_path(monkeypatch):
    config = {
        'code': {'enabled': False, 'projects': []},
        'documents': {
            'enabled': True,
            'grounded': False,
            'all_collections': False,
            'collections': [
                {
                    'id': 'a',
                    'name': 'payments',
                    'description': '',
                    'paths': ['/work/code/payments', '/work/rag-data/jira'],
                },
                {'id': 'b', 'name': 'old', 'description': '', 'path': '/work/rag-data/old'},
            ],
        },
    }
    assert [str(p) for p in get_paths(config)] == ['/work/code/payments', '/work/rag-data/jira', '/work/rag-data/old']


def test_agent_config_maps_any_folder_under_the_data_root(store, monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('CODE_ROOT', '/work/data/code')
    ids = seed(store, ('jira', 'jira'), ('tools', 'exports/tools', 'code-index/abc'))
    with store.connect() as conn:
        give(conn, 'search', [('collection', item) for item in ids.values()])
    config = get_agent_config('search', conninfo=store.conninfo)
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [
        ('jira', ['/work/data/jira']),
        ('tools', ['/work/data/code-index/abc', '/work/data/exports/tools']),
    ]
    monkeypatch.setenv('DATA_ROOT_DIR', 'relative')
    with pytest.raises(ValueError, match='DATA_ROOT_DIR'):
        get_agent_config('search', conninfo=store.conninfo)


def test_legacy_folder_config_maps_to_collections(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    legacy = {
        'documents': {
            'enabled': True,
            'all_folders': False,
            'include_subfolders': True,
            'folders': [
                {
                    'id': 'x',
                    'path': '/work/rag-data/demo/payments',
                    'description': 'd',
                    'search_folder': 'demo.payments',
                },
                {'id': 'y', 'path': '/work/rag-data/Old_Stuff', 'description': 'o', 'search_folder': 'Old_Stuff'},
            ],
        }
    }
    everything, items = document_collections(legacy)
    assert everything is False
    assert [(i['name'], i['paths']) for i in items] == [
        ('demo', ['/work/rag-data/demo']),
        ('old-stuff', ['/work/rag-data/Old_Stuff']),
    ]
    assert document_collections({'documents': {'all_folders': True, 'folders': []}}) == (True, [])
    assert slug('Old_Stuff') == 'old-stuff'


def test_legacy_folders_without_search_folder_use_their_path(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    legacy = {
        'documents': {
            'enabled': True,
            'all_folders': False,
            'folders': [
                {'path': '/work/rag-data/Runbooks/payments', 'description': 'r'},
                {'path': '/work/rag-data/Runbooks', 'description': 'duplicate top'},
                {'path': '/elsewhere/notes', 'description': 'outside the RAG root'},
                {'path': '/work/rag-data', 'description': 'the root itself'},
            ],
        }
    }
    assert document_collections(legacy) == (
        False,
        [{'name': 'runbooks', 'description': 'r', 'paths': ['/work/rag-data/Runbooks']}],
    )


def test_legacy_folder_configs_find_jira_in_the_jira_folder_and_accept_data_root_paths(monkeypatch):
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    monkeypatch.setenv('JIRA_EXPORT_ROOT', '/work/data/exports/jira')
    # Migration 0067 rewrote /work/rag-data/jira paths to /work/data/jira; search_folder values stayed.
    legacy = {
        'documents': {
            'enabled': True,
            'all_folders': False,
            'folders': [
                {'path': '/work/data/jira/PAY', 'description': 'tickets', 'search_folder': 'jira.PAY'},
                {'path': '/work/rag-data/demo', 'description': 'demo', 'search_folder': 'demo'},
                {'path': '/work/data/exports/jira/OPS', 'description': 'ops'},
                {'path': '/work/data/runbooks/payments', 'description': 'runbooks'},
                {'path': '/work/rag-data/jira', 'description': 'unmigrated'},
                {'path': '/work/data', 'description': 'the root itself'},
            ],
        }
    }
    assert document_collections(legacy) == (
        False,
        [
            {'name': 'jira', 'description': 'tickets', 'paths': ['/work/data/exports/jira']},
            {'name': 'demo', 'description': 'demo', 'paths': ['/work/rag-data/demo']},
            {'name': 'runbooks', 'description': 'runbooks', 'paths': ['/work/data/runbooks']},
        ],
    )


def test_profile_api_grants_collections(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, ('jira', 'rag-data/jira'))
    options = client.get('/api/agents/search/options', headers=HEADERS).json()
    assert [(c['name'], c['description_short']) for c in options['collection']] == [('jira', 'About jira')]
    agent = client.get('/api/agents/search', headers=HEADERS).json()
    assert 'all_folders' not in agent and 'folder_ids' not in agent
    jira = {'resources': [{'type': 'collection', 'id': str(ids['jira'])}], 'roles': {'content_editor': False}}
    saved = update(client, agent, access=jira)
    assert saved.status_code == 200, saved.text
    found = client.get('/api/agents/search', headers=HEADERS).json()['access']['resources']
    assert [(r['type'], r['id']) for r in found] == [('collection', str(ids['jira']))]
    old = update(client, saved.json(), folder_ids=[])
    assert old.status_code == 400 and 'Extra inputs' in old.text
