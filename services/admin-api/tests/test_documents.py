import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledge_admin.repositories import RepositoryInput
from live_documents import live_document, replace_live_document, write_live_document
from test_api import _client
from test_repositories import HEADERS

VALUE = {
    'slug': 'payments-runbook',
    'title': 'Payments runbook',
    'description': 'Diagnose failed payments',
    'purpose': 'troubleshooting',
    'audience': 'agents',
    'tags': ['payments'],
    'markdown': '# Payments\n\n  Preserve indentation.\n',
}
DOCS = '/api/documentation'
NO_LINKS = {'code_project_ids': [], 'service_ids': [], 'server_ids': [], 'deployment_ids': [], 'environment_ids': []}
PROMPT = {
    'name': 'Code architecture',
    'description': 'Explain components',
    'tags': ['code'],
    'content': 'Read source code; cite evidence.',
}


def live(store, value: dict, **extra) -> dict:
    """A live document from a VALUE-like body (people only save drafts; see live_documents)."""
    fields = {**value, **extra}
    return write_live_document(store, fields.pop('slug'), fields.pop('title'), fields.pop('markdown'), **fields)


def save_and_promote(client, document_id: str, **content) -> dict:
    """A person's Save on a live document (a draft of it) and Promote: the next live version."""
    draft = client.post(f'{DOCS}/drafts', headers=HEADERS, json={'document_id': document_id, **content})
    assert draft.status_code == 201, draft.text
    promoted = client.post(
        f'{DOCS}/drafts/{draft.json()["id"]}/promote', headers=HEADERS, json={'revision': draft.json()['revision']}
    )
    assert promoted.status_code == 200, promoted.text
    return promoted.json()


def save_links(client, document_id: str, revision, **ids):
    return client.put(
        f'{DOCS}/documents/{document_id}/links', headers=HEADERS, json={**NO_LINKS, **ids, 'revision': revision}
    )


def test_document_commits_are_versioned_preserved_and_validated(store, tmp_path):
    from fastapi import HTTPException
    from knowledge_admin.documents.store import build_snapshot

    client = _client(tmp_path, repository_store=store)
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))
    created = live(store, VALUE, code_project_ids=[repo], source_commits={repo: 'a' * 40})
    endpoint = '/api/documentation/documents/' + created['id']
    first = client.get(endpoint, headers=HEADERS).json()
    assert first['source_snapshot']['code_projects'][0]['git_commit'] == 'a' * 40
    # A promoted draft keeps the live version's source commits.
    assert save_and_promote(client, created['id'], markdown='# Two')['version'] == 2
    assert client.get(endpoint, headers=HEADERS).json()['source_snapshot']['code_projects'][0]['git_commit'] == 'a' * 40
    assert (
        client.get(endpoint + '/versions/1', headers=HEADERS).json()['source_snapshot']['code_projects'][0][
            'git_commit'
        ]
        == 'a' * 40
    )
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = {'code_project_ids': [UUID(repo)]}
        with pytest.raises(HTTPException) as error:
            build_snapshot(conn, project, ids, {str(uuid4()): 'b' * 40}, None)
        assert error.value.status_code == 400
        assert 'git_commit' not in build_snapshot(conn, project, ids, {}, first['source_snapshot'])['code_projects'][0]


def test_document_versions_filters_links_and_deletion(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='never-show-this'), 'admin', ''))
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Payments host', 'hostname': 'payments.internal'}
    ).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Payments'}).json()
    production = client.post('/api/environments', headers=HEADERS, json={'name': 'production'}).json()
    built_from = {'from_service_id': service['id'], 'kind': 'built_from', 'to_code_project_id': repo}
    assert client.post('/api/relations', headers=HEADERS, json=built_from).status_code == 201
    placement = {'service_id': service['id'], 'server_id': server['id'], 'environment_id': production['id']}
    assert client.post('/api/deployments', headers=HEADERS, json=placement).status_code == 201
    resources = client.get('/api/documentation/resources', headers=HEADERS)
    assert (
        resources.status_code == 200 and 'never-show-this' not in resources.text and 'credential' not in resources.text
    )
    deployment = resources.json()['deployments'][0]
    value = {
        **VALUE,
        'code_project_ids': [repo],
        'server_ids': [server['id']],
        'service_ids': [service['id']],
        'deployment_ids': [deployment['id']],
    }
    doc_id = live(store, value)['id']
    endpoint = '/api/documentation/documents/' + doc_id
    original = client.get(endpoint, headers=HEADERS).json()
    first = client.get(endpoint + '/versions/1', headers=HEADERS).json()
    assert original['version'] == original['revision'] == 1 and first['live']
    assert original['markdown'] == VALUE['markdown'] and original['source_snapshot'] == first['source_snapshot']
    assert first['markdown'] == VALUE['markdown']
    assert first['content_hash'] == hashlib.sha256(VALUE['markdown'].encode()).hexdigest()
    assert first['source_snapshot']['servers'][0]['hostname'] == 'payments.internal'
    filters = {
        'q': 'payments',
        'code_project_id': repo,
        'service_id': service['id'],
        'server_id': server['id'],
        'deployment_id': deployment['id'],
    }
    listing = client.get('/api/documentation/documents', params=filters, headers=HEADERS).json()
    assert listing['total'] == 1 and listing['documents'][0]['id'] == doc_id
    assert 'markdown' not in listing['documents'][0]
    assert (
        client.get(
            '/api/documentation/documents', params={**filters, 'server_id': str(uuid4())}, headers=HEADERS
        ).json()['total']
        == 0
    )
    edited_service = {'revision': 1, 'description': 'Updated'}
    assert client.put('/api/services/' + service['id'], headers=HEADERS, json=edited_service).status_code == 200
    edited_deployment = {'revision': 1, 'deployed_version': '2'}
    assert (
        client.put('/api/deployments/' + deployment['id'], headers=HEADERS, json=edited_deployment).status_code == 200
    )
    assert client.get(endpoint, headers=HEADERS).json()['deployment_ids'] == [deployment['id']]
    # Links made when the document was created are built-for links: unticking one is refused.
    unticked = save_links(
        client, doc_id, 1, code_project_ids=[repo], server_ids=[server['id']], service_ids=[], deployment_ids=[]
    )
    assert unticked.status_code == 409
    assert (
        unticked.json()['error']['message']
        == "This document was built for service Payments; that link can't be removed."
    )
    assert save_and_promote(client, doc_id, markdown='# Revised\n')['revision'] == 2
    current = client.get(endpoint, headers=HEADERS).json()
    assert current['version'] == current['revision'] == 2 and len(current['versions']) == 2
    assert [v['version'] for v in current['versions']] == [2, 1] and current['markdown'] == '# Revised\n'
    assert client.get(endpoint + '/versions/1', headers=HEADERS).json() == {**first, 'live': False}
    assert (
        client.get('/api/documentation/documents', params={'service_id': service['id']}, headers=HEADERS).json()[
            'total'
        ]
        == 1
    )
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 2}).status_code == 400
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 1, 'confirmed': True}).status_code == 409
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 2, 'confirmed': True}).status_code == 200
    assert client.get(endpoint, headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert (
            conn.execute(
                'SELECT count(*) AS n FROM ka.document_archive WHERE document_id=%s', (UUID(doc_id),)
            ).fetchone()['n']
            == conn.execute(
                'SELECT count(*) AS n FROM ka.document_resources WHERE document_id=%s', (UUID(doc_id),)
            ).fetchone()['n']
            == 0
        )
        assert conn.execute('SELECT count(*) AS n FROM ka.servers').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM ka.repositories').fetchone()['n'] == 1
        actions = conn.execute(
            "SELECT action FROM ka.audit_events WHERE resource_type='document' ORDER BY id"
        ).fetchall()
    assert [r['action'] for r in actions] == ['document.created', 'document.updated', 'document.deleted']


def test_stale_concurrent_edits_foreign_links_and_hash_constraints(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    document_id = live(store, VALUE)['id']
    endpoint = '/api/documentation/documents/' + document_id
    foreign_repo = str(store.save('other', RepositoryInput(remote_url='example/other'), 'admin', ''))
    assert save_links(client, document_id, 1, code_project_ids=[foreign_repo]).status_code == 400
    assert client.put(endpoint + '/links', headers=HEADERS, json=NO_LINKS).status_code == 400  # no revision
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: save_links(client, document_id, 1), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    result = client.get(endpoint, headers=HEADERS).json()
    assert result['revision'] == 2 and len(result['versions']) == 1
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE ka.documents SET markdown='changed' WHERE id=%s", (UUID(result['id']),))
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                """INSERT INTO ka.document_resources(document_id,project_id,code_project_id,connection,added_how)
                VALUES (%s,%s,%s,'linked','person')""",
                (UUID(result['id']), UUID(result['project_id']), UUID(foreign_repo)),
            )
    assert client.get('/api/documentation/documents/' + str(uuid4()), headers=HEADERS).status_code == 404
    assert client.get(endpoint + '/versions/99', headers=HEADERS).status_code == 404


def test_prompt_drafts_activation_disable_and_delete(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    created = client.post('/api/documentation/prompts', headers=HEADERS, json=PROMPT)
    assert created.status_code == 201, created.text
    endpoint = '/api/documentation/prompts/' + created.json()['id']
    original = client.get(endpoint, headers=HEADERS).json()
    version1 = original['versions'][0]
    assert original['active_version_id'] is None and version1['activated_at'] is None
    assert (
        client.post(
            endpoint + '/versions/' + version1['id'] + '/activate', headers=HEADERS, json={'revision': 1}
        ).status_code
        == 200
    )
    updated = client.put(
        endpoint, headers=HEADERS, json={**PROMPT, 'revision': 2, 'content': 'Explain architecture and failure modes.'}
    )
    assert updated.status_code == 200, updated.text
    current = client.get(endpoint, headers=HEADERS).json()
    version2 = current['versions'][0]
    assert current['active_version_id'] == version1['id'] and version2['activated_at'] is None
    assert (
        client.delete(
            endpoint + '/versions/' + version1['id'], headers=HEADERS, params={'revision': 3, 'confirmed': True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint + '/versions/' + version2['id'] + '/activate', headers=HEADERS, json={'revision': 2}
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint + '/versions/' + version2['id'] + '/activate', headers=HEADERS, json={'revision': 3}
        ).status_code
        == 200
    )
    assert (
        client.put(
            endpoint, headers=HEADERS, json={**PROMPT, 'revision': 4, 'content': version2['content'], 'enabled': False}
        ).status_code
        == 200
    )
    disabled = client.get(endpoint, headers=HEADERS).json()
    assert not disabled['enabled'] and len(disabled['versions']) == 2
    assert (
        client.delete(
            endpoint + '/versions/' + version1['id'], headers=HEADERS, params={'revision': 5, 'confirmed': True}
        ).status_code
        == 200
    )
    assert (
        client.put(endpoint, headers=HEADERS, json={**PROMPT, 'revision': 6, 'content': 'Another draft.'}).status_code
        == 200
    )
    assert client.get(endpoint, headers=HEADERS).json()['versions'][0]['version'] == 3
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE ka.documentation_prompt_versions SET content='replace' WHERE id=%s", (UUID(version2['id']),)
            )
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 7}).status_code == 400
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 7, 'confirmed': True}).status_code == 200
    assert client.get(endpoint, headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    'method,path',
    [
        ('GET', '/documentation/resources'),
        ('GET', '/documentation/documents'),
        ('GET', '/documentation/prompts'),
        ('POST', '/documentation/prompts'),
        ('GET', '/documentation/documents/' + str(uuid4())),
        ('PUT', '/documentation/documents/' + str(uuid4()) + '/links'),
        ('DELETE', '/documentation/documents/' + str(uuid4())),
        ('GET', '/documentation/prompts/' + str(uuid4())),
        ('PUT', '/documentation/prompts/' + str(uuid4())),
        ('DELETE', '/documentation/prompts/' + str(uuid4())),
        ('POST', '/documentation/prompts/' + str(uuid4()) + '/versions/' + str(uuid4()) + '/activate'),
        ('DELETE', '/documentation/prompts/' + str(uuid4()) + '/versions/' + str(uuid4())),
    ],
)
def test_documentation_requires_admin(tmp_path, path, method):
    assert _client(tmp_path).request(method, '/api' + path, json=VALUE).status_code == 403


@pytest.mark.parametrize(
    'change',
    [
        {'title': '  '},
        {'markdown': '  '},
        {'markdown': 'a\x00b'},
        {'slug': '../no'},
        {'audience': 'everyone'},
        {'tags': ['']},
        {'purpose': ' '},
        {'revision': True},
    ],
)
def test_invalid_document_input(tmp_path, change):
    assert _client(tmp_path).post(f'{DOCS}/drafts', headers=HEADERS, json={**VALUE, **change}).status_code == 400


def test_document_migrations_roundtrip_and_preserve_populated_tables(store):
    from pathlib import Path

    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    # Every migration from 0039 to the latest, so later migrations never collide with the restored legacy schema.
    newer = sorted(
        path.name.removesuffix('.up.sql')
        for path in migrations.glob('*.up.sql')
        if path.name >= '0039_retire_document_pipeline.up.sql'
    )
    with store.connect() as conn:
        for name in reversed(newer):
            conn.execute((migrations / f'{name}.down.sql').read_text(), prepare=False)
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.document_sets(project_id,slug,name) VALUES (%s,'legacy','Legacy')", (project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute((migrations / '0039_retire_document_pipeline.up.sql').read_text(), prepare=False)
        conn.rollback()
        assert conn.execute('SELECT count(*) AS n FROM ka.document_sets').fetchone()['n'] == 1
        conn.execute('DELETE FROM ka.document_sets')
        conn.commit()
        for name in newer:
            conn.execute((migrations / f'{name}.up.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.document_details') AS name").fetchone()['name'] is not None
        assert conn.execute("SELECT to_regclass('ka.agent_collections') AS name").fetchone()['name'] is None  # 0114


def test_editing_deployments_keeps_document_links_and_deleting_them_removes_the_links(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Shared host', 'hostname': 'app.internal'}
    ).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    for name in ('production', 'staging'):
        environment = client.post('/api/environments', headers=HEADERS, json={'name': name}).json()
        placement = {'service_id': service['id'], 'server_id': server['id'], 'environment_id': environment['id']}
        assert client.post('/api/deployments', headers=HEADERS, json=placement).status_code == 201
    deployments = client.get('/api/documentation/resources', headers=HEADERS).json()['deployments']
    doc = live(
        store,
        VALUE,
        service_ids=[service['id']],
        server_ids=[server['id']],
        deployment_ids=[d['id'] for d in deployments],
    )
    for deployment in deployments:
        changed = client.put(
            '/api/deployments/' + deployment['id'], headers=HEADERS, json={'revision': 1, 'runtime_name': 'api'}
        )
        assert changed.status_code == 200, changed.text
    current = client.get('/api/documentation/documents/' + doc['id'], headers=HEADERS).json()
    assert set(current['deployment_ids']) == {d['id'] for d in deployments}
    for deployment in deployments:
        removed = client.delete('/api/deployments/' + deployment['id'], headers=HEADERS, params={'revision': 2})
        assert removed.status_code == 200
    assert client.delete('/api/services/' + service['id'], headers=HEADERS, params={'revision': 1}).status_code == 200
    current = client.get('/api/documentation/documents/' + doc['id'], headers=HEADERS).json()
    assert current['service_ids'] == current['deployment_ids'] == []
    assert current['source_snapshot']['services'][0]['name'] == 'API'


def test_document_slug_comes_from_the_name():
    from knowledge_admin.documents.api import document_slug

    assert document_slug('Payments – API reference') == 'payments-api-reference'
    assert document_slug('  Café v2.0 / Ops  ') == 'caf-v2-0-ops'
    assert document_slug('– –') == ''
    assert document_slug('a' * 119 + ' b') == 'a' * 119


def test_a_slug_names_one_document_in_the_project(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        saved = live_document(conn, project, 'api-reference', 'API reference', '# API')
    endpoint = '/api/documentation/documents/' + str(saved['id'])
    # A promoted draft keeps the document's name.
    save_and_promote(client, str(saved['id']), title='API guide', markdown='# Guide')
    assert client.get(endpoint, headers=HEADERS).json()['slug'] == 'api-reference'
    with pytest.raises(psycopg.errors.UniqueViolation):
        with store.connect() as conn:
            live_document(conn, project, 'api-reference', 'Twice', '# T')
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 2, 'confirmed': True}).status_code == 200


def test_publishing_and_deleting_write_no_files(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    draft = client.post(f'{DOCS}/drafts', headers=HEADERS, json={'slug': 'x', 'title': 'X', 'markdown': '# X'}).json()
    response = client.post(f'{DOCS}/drafts/{draft["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert response.status_code == 200 and 'export_error' not in response.json()
    document = response.json()
    assert client.post(f'{DOCS}/documents/{document["document_id"]}/export', headers=HEADERS).status_code in (404, 405)
    assert list(out.iterdir()) == []
    deleted = client.delete(
        f'{DOCS}/documents/{document["document_id"]}',
        headers=HEADERS,
        params={'revision': document['revision'], 'confirmed': True},
    )
    assert deleted.status_code == 200 and list(out.iterdir()) == []


def test_delete_takes_the_project_lock_first_as_promote_and_create_do(store, tmp_path):
    """Promote locks the project, then a draft, then the document; delete locks the document, then (cascade) its drafts.
    Taking the project first serialises delete with them instead of deadlocking."""
    import threading

    client = _client(tmp_path, repository_store=store)
    created = write_live_document(store, 'gone', 'G', '# G')
    outcome = {}

    def delete():
        outcome['status'] = client.delete(
            '/api/documentation/documents/' + created['id'], headers=HEADERS, params={'revision': 1, 'confirmed': True}
        ).status_code

    with store.connect() as conn:
        conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (store.project(conn, 'shop'),))
        thread = threading.Thread(target=delete)
        thread.start()
        thread.join(1)
        assert thread.is_alive()  # waits for the project lock
        assert conn.execute('SELECT 1 FROM ka.documents WHERE id=%s FOR UPDATE NOWAIT', (created['id'],)).fetchone()
    thread.join(10)
    assert outcome == {'status': 200}
