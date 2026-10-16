from pathlib import Path

import pytest

from access_helpers import give
from knowledge_admin.agents.runner import get_agent_config
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def workspace(store, tmp_path, monkeypatch):
    root = tmp_path / 'code'
    root.mkdir()
    for folder in ('api', 'shared', 'other'):
        (root / folder).mkdir()
    (root / 'api' / 'app.py').write_text('print("project code")\n')
    (root / '.notes').write_text('Host notes')
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path))
    monkeypatch.setenv('DATA_ROOT', '/opt/knowledge-agent')
    return _client(tmp_path, repository_store=store), root


def test_local_project_maps_multiple_folders_and_is_available_to_agents_and_services(store, workspace):
    client, root = workspace
    value = {'name': 'Payments', 'description': 'Payment source', 'remote_url': None, 'folder_paths': ['api', 'shared']}
    response = client.post('/api/code-projects', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    project_id = response.json()['id']
    row = client.get('/api/code-projects', headers=HEADERS).json()['projects'][0]
    assert row['folder_paths'] == ['api', 'shared'] and row['description'] == 'Payment source'
    assert row['remote_url'] is None and not row['has_token'] and row['sync_status'] is None
    assert client.post(f'/api/code-projects/{project_id}/pull', headers=HEADERS).status_code == 400
    assert client.get('/api/agents/code/options', headers=HEADERS).json()['repositories'] == [
        {'id': project_id, 'name': 'Payments'}
    ]
    service = client.post(
        '/api/services', headers=HEADERS, json={'name': 'Payment API', 'repository_ids': [project_id]}
    )
    assert service.status_code == 201, service.text
    with store.connect() as conn:
        give(conn, 'code', [('code_project', project_id)])
    config = get_agent_config('code', conninfo=store.conninfo)
    assert config['code']['projects'][0]['folder_paths'] == [str(root / 'api'), str(root / 'shared')]
    assert config['code']['projects'][0]['id'] == project_id
    changed = client.put(
        f'/api/code-projects/{project_id}',
        headers=HEADERS,
        json={**value, 'revision': row['revision'], 'folder_paths': ['shared']},
    )
    assert changed.status_code == 200, changed.text
    assert client.get('/api/code-projects', headers=HEADERS).json()['projects'][0]['folder_paths'] == ['shared']
    assert (
        client.put(
            f'/api/code-projects/{project_id}', headers=HEADERS, json={**value, 'revision': row['revision']}
        ).status_code
        == 409
    )
    assert (root / 'api' / 'app.py').read_text() == 'print("project code")\n'
    (root / 'shared' / 'billing').mkdir()
    row = client.get('/api/code-projects', headers=HEADERS).json()['projects'][0]
    changed = client.put(
        f'/api/code-projects/{project_id}',
        headers=HEADERS,
        json={**value, 'revision': row['revision'], 'folder_paths': ['api', 'shared/billing']},
    )
    assert changed.status_code == 200, changed.text
    assert get_agent_config('code', conninfo=store.conninfo)['code']['projects'][0]['folder_paths'] == [
        str(root / 'api'),
        str(root / 'shared/billing'),
    ]


def test_git_creation_requires_clone_folder_and_queues_only_when_selected(store, workspace):
    client, root = workspace
    value = {
        'name': 'Remote code',
        'remote_url': 'https://git.example.test/team/code.git',
        'folder_paths': [],
        'clone_path': 'remote-code',
        'token': 'git-test-token',
        'sync_interval_minutes': 60,
    }
    response = client.post('/api/code-projects', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    project_id = response.json()['id']
    row = client.get('/api/code-projects', headers=HEADERS).json()['projects'][0]
    assert row['folder_paths'] == ['remote-code'] and row['clone_path'] == 'remote-code'
    assert row['sync_interval_minutes'] == 60 and row['next_sync_at'] is not None
    assert row['sync_status'] is None and row['has_token']
    assert not (root / 'remote-code').exists()
    assert 'git-test-token' not in str(row)
    clone = client.post(
        '/api/code-projects',
        headers=HEADERS,
        json={**value, 'remote_url': 'example/second', 'clone_path': 'second-code', 'clone_now': True},
    )
    assert clone.status_code == 201, clone.text
    rows = client.get('/api/code-projects', headers=HEADERS).json()['projects']
    assert next(p for p in rows if p['id'] == clone.json()['id'])['sync_status'] == 'queued'
    missing = client.post(
        '/api/code-projects',
        headers=HEADERS,
        json={'name': 'Missing folder', 'remote_url': 'example/missing', 'folder_paths': []},
    )
    assert missing.status_code == 400
    assert (
        client.post(
            '/api/code-projects', headers=HEADERS, json={**value, 'remote_url': 'example/collision'}
        ).status_code
        == 409
    )
    assert (
        client.post(
            '/api/code-projects', headers=HEADERS, json={**value, 'remote_url': 'example/existing', 'clone_path': 'api'}
        ).status_code
        == 409
    )
    saved = client.put(
        f'/api/code-projects/{project_id}',
        headers=HEADERS,
        json={**value, 'revision': row['revision'], 'token': None, 'sync_interval_minutes': None},
    )
    assert saved.status_code == 200, saved.text
    row = next(p for p in client.get('/api/code-projects', headers=HEADERS).json()['projects'] if p['id'] == project_id)
    assert row['next_sync_at'] is None and not row['has_token']


def test_code_browser_lists_hidden_entries_reads_files_and_stays_inside_mount(workspace, tmp_path):
    client, root = workspace
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text('outside-content')
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    listing = client.get('/api/code/files', headers=HEADERS).json()
    assert listing['root'] == str(root) and listing['host_path'] == '/opt/knowledge-agent/code'
    assert {f['name'] for f in listing['entries']} == {'api', 'shared', 'other', '.notes', 'escape'}
    assert next(f for f in listing['entries'] if f['name'] == 'escape')['kind'] == 'symlink'
    nested = client.get('/api/code/files', headers=HEADERS, params={'path': 'api'}).json()
    assert nested['entries'][0]['path'] == 'api/app.py'
    file = client.get('/api/code/file', headers=HEADERS, params={'path': 'api/app.py'})
    assert file.json()['text'] == 'print("project code")\n'
    for path in ('../outside/secret', str(outside / 'secret'), 'escape/secret'):
        response = client.get('/api/code/file', headers=HEADERS, params={'path': path})
        assert response.status_code in (400, 403) and 'outside-content' not in response.text
    (root / 'binary').write_bytes(b'\0binary')
    (root / 'large').write_text('x' * 512001)
    (root / 'api' / 'src').mkdir()
    (root / 'api' / 'src' / 'billing').mkdir()
    (root / '.git').mkdir()
    (root / '.ka-sync').mkdir()
    folders = client.get('/api/code/files', headers=HEADERS, params={'folders_only': True}).json()
    assert {entry['name'] for entry in folders['entries']} == {'api', 'shared', 'other'}
    assert all(entry['kind'] == 'folder' for entry in folders['entries']) and folders['total'] == 3
    nested = client.get('/api/code/files', headers=HEADERS, params={'folders_only': True, 'path': 'api/src'}).json()
    assert nested['entries'][0]['path'] == 'api/src/billing'
    assert client.get('/api/code/file', headers=HEADERS, params={'path': 'binary'}).status_code == 415
    assert client.get('/api/code/file', headers=HEADERS, params={'path': 'large'}).status_code == 413
    for path in ('../outside', 'escape', 'missing', '/tmp', 'api/../shared', '.git', '.ka-sync'):
        response = client.post(
            '/api/code-projects', headers=HEADERS, json={'name': 'Invalid', 'remote_url': None, 'folder_paths': [path]}
        )
        assert response.status_code in (400, 403), response.text
    for number in range(205):
        (root / 'other' / f'{number:03}.txt').touch()
    first = client.get('/api/code/files', headers=HEADERS, params={'path': 'other'}).json()
    second = client.get(
        '/api/code/files', headers=HEADERS, params={'path': 'other', 'offset': first['next_offset']}
    ).json()
    assert len(first['entries']) == 200 and len(second['entries']) == 5 and second['next_offset'] is None
    for number in range(201):
        (root / 'other' / f'folder-{number:03}').mkdir()
    folders = client.get('/api/code/files', headers=HEADERS, params={'path': 'other', 'folders_only': True}).json()
    more = client.get(
        '/api/code/files',
        headers=HEADERS,
        params={'path': 'other', 'folders_only': True, 'offset': folders['next_offset']},
    ).json()
    assert folders['total'] == 201 and len(folders['entries']) == 200 and len(more['entries']) == 1
    assert more['next_offset'] is None and all(entry['kind'] == 'folder' for entry in folders['entries'])


@pytest.mark.parametrize('path', ['/api/code-projects', '/api/code/files', '/api/code/file?path=api/app.py'])
def test_code_administration_requires_admin_before_mount_access(workspace, path):
    client, _ = workspace
    assert client.get(path).status_code == 403


def test_migration_preserves_existing_checkouts_and_agent_links(store):
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with store.connect() as conn:
        conn.execute((migrations / '0026_code_projects.down.sql').read_text(), prepare=False)
        project = store.project(conn, 'shop')
        repo = conn.execute(
            """INSERT INTO ka.repositories(project_id,slug,name,remote_url,checkout_path,current_commit)
            VALUES (%s,'existing','Existing','https://github.com/example/existing.git','old/checkout',%s) RETURNING id""",
            (project, 'a' * 40),
        ).fetchone()['id']
        conn.execute("INSERT INTO ka.agent_repositories VALUES ('code',%s)", (repo,))
        conn.execute((migrations / '0026_code_projects.up.sql').read_text(), prepare=False)
        assert (
            conn.execute('SELECT path FROM ka.code_project_folders WHERE code_project_id=%s', (repo,)).fetchone()[
                'path'
            ]
            == 'old/checkout'
        )
        assert (
            conn.execute("SELECT repository_id FROM ka.agent_repositories WHERE agent_id='code'").fetchone()[
                'repository_id'
            ]
            == repo
        )


def test_agent_code_config_contains_projects_folders_and_only_applicable_git_urls(store, workspace):
    client, root = workspace
    local = client.post(
        '/api/code-projects',
        headers=HEADERS,
        json={
            'name': 'Local code',
            'description': 'Shared source',
            'remote_url': None,
            'folder_paths': ['api', 'shared'],
        },
    ).json()
    git = client.post(
        '/api/code-projects',
        headers=HEADERS,
        json={
            'name': 'Git code',
            'description': 'Remote source',
            'remote_url': 'example/service',
            'clone_path': 'service',
            'folder_paths': ['other'],
        },
    ).json()
    with store.connect() as conn:
        give(conn, 'code', [('code_project', local['id'])])
    response = client.get('/api/agents/code/config', headers=HEADERS)
    assert response.status_code == 200, response.text
    local_config = {
        'id': local['id'],
        'name': 'Local code',
        'description': 'Shared source',
        'folder_paths': [str(root / 'api'), str(root / 'shared')],
    }
    assert 'git' not in response.json()
    assert response.json()['code'] == {'enabled': True, 'all_projects': False, 'projects': [local_config], 'graphs': []}
    with store.connect() as conn:
        give(conn, 'code', [('code_project', local['id']), ('code_project', git['id'])])
    config = client.get('/api/agents/code/config', headers=HEADERS).json()['code']
    assert config['projects'] == [
        {
            'id': git['id'],
            'name': 'Git code',
            'description': 'Remote source',
            'folder_paths': [str(root / 'other'), str(root / 'service')],
            'remote_git_url': 'https://github.com/example/service.git',
        },
        local_config,
    ]
