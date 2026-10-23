import pytest
from fastapi.testclient import TestClient

from knowledge_admin.config import App, Config, ServerConfig
from knowledge_admin.app import create_app


HEADERS = {
    'x-forwarded-groups': 'docs-admins',
    'x-forwarded-user': 'admin-subject',
    'x-forwarded-email': 'admin@example.test',
}


@pytest.fixture
def env(store, tmp_path, monkeypatch):
    """Admin client with a code root, a code-index root and no RAG."""
    monkeypatch.delenv('RAG_BACKOFFICE_KEY', raising=False)
    code, index = tmp_path / 'code', tmp_path / 'code-index'
    for folder in ('api', 'shared', 'tools/cli'):
        (code / folder).mkdir(parents=True)
        (code / folder / 'main.py').write_text('print(1)\n')
    index.mkdir()
    monkeypatch.setenv('CODE_ROOT', str(code))
    monkeypatch.setenv('CODE_INDEX_ROOT', str(index))
    client = TestClient(create_app(Config(apps=[App(name='docs')]), server=ServerConfig(), repository_store=store))

    def add(name, folders):
        created = client.post(
            '/api/code-projects',
            headers=HEADERS,
            json={'name': name, 'description': '', 'remote_url': None, 'folder_paths': folders},
        )
        assert created.status_code == 201, created.text
        project = created.json()['id']
        (index / project).mkdir()
        (index / project / 'graph.sqlite').write_text('graph')
        return project

    yield client, add, code, index


def listed(client):
    return [p['id'] for p in client.get('/api/code-projects', headers=HEADERS).json()['projects']]


def test_remove_keeps_files_by_default_and_releases_the_folders(env):
    client, add, code, index = env
    project = add('Payments', ['api'])
    removed = client.post(f'/api/code-projects/{project}/archive', headers=HEADERS)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {'id': project, 'deleted': [], 'errors': []}
    assert project not in listed(client)
    assert (code / 'api' / 'main.py').exists() and (index / project).exists()
    # The folder is free again for a new project.
    again = add('Payments again', ['api'])
    assert again in listed(client)


def test_remove_with_delete_files_deletes_folders_and_code_graph_without_asking_anyone(env):
    client, add, code, index = env
    project = add('Payments', ['api', 'tools/cli'])
    removed = client.post(f'/api/code-projects/{project}/archive', headers=HEADERS, json={'delete_files': True})
    assert removed.status_code == 200, removed.text
    assert removed.json()['deleted'] == ['code/api', 'code/tools/cli', f'code-index/{project}']
    assert removed.json()['errors'] == []
    assert not (code / 'api').exists() and not (code / 'tools' / 'cli').exists()
    assert (code / 'tools').exists() and (code / 'shared' / 'main.py').exists()
    assert not (index / project).exists()
    assert project not in listed(client)


@pytest.mark.parametrize('other', [['api'], ['api/sub'], ['tools']])
def test_delete_files_refuses_a_folder_another_project_maps(env, other):
    client, add, code, index = env
    project = add('Payments', ['api', 'tools/cli'])
    (code / 'api' / 'sub').mkdir()
    blocker = add('Other', other)
    removed = client.post(f'/api/code-projects/{project}/archive', headers=HEADERS, json={'delete_files': True})
    assert removed.status_code == 409
    assert 'Other' in removed.json()['error']['message']
    assert project in listed(client) and blocker in listed(client)
    assert (code / 'api' / 'main.py').exists() and (index / project).exists()


def test_remove_is_refused_while_a_job_runs(env, store):
    client, add, code, index = env
    project = add('Payments', ['api'])
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.jobs(id,project_id,repository_id,kind,idempotency_key)
            SELECT gen_random_uuid(),project_id,id,'code_index',gen_random_uuid()::text FROM ka.repositories WHERE id=%s""",
            (project,),
        )
    removed = client.post(f'/api/code-projects/{project}/archive', headers=HEADERS, json={'delete_files': True})
    assert removed.status_code == 409
    assert project in listed(client) and (code / 'api').exists()


def test_a_folder_that_fails_to_delete_is_reported_after_removal(env, monkeypatch):
    client, add, code, index = env
    project = add('Payments', ['api', 'shared'])
    import shutil

    real = shutil.rmtree

    def flaky(path, *args, **kwargs):
        if str(path).endswith('/shared'):
            raise PermissionError('denied')
        return real(path, *args, **kwargs)

    monkeypatch.setattr('knowledge_admin.code_projects.api.shutil.rmtree', flaky)
    removed = client.post(f'/api/code-projects/{project}/archive', headers=HEADERS, json={'delete_files': True})
    assert removed.status_code == 200
    body = removed.json()
    assert body['deleted'] == ['code/api', f'code-index/{project}']
    assert body['errors'] == ['code/shared could not be deleted: denied']
    assert project not in listed(client)


def test_remove_body_rejects_unknown_fields(env):
    client, add, code, index = env
    project = add('Payments', ['api'])
    assert (
        client.post(f'/api/code-projects/{project}/archive', headers=HEADERS, json={'delete': True}).status_code == 400
    )
