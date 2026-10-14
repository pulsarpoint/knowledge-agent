import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledge_admin.repositories import RepositoryInput
from live_documents import write_live_document
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
    service_value = {
        'name': 'Payments',
        'repository_ids': [repo],
        'deployments': [{'server_id': server['id'], 'environment': 'production'}],
    }
    service = client.post('/api/services', headers=HEADERS, json=service_value).json()
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
    edited_service = {
        **service_value,
        'revision': 1,
        'description': 'Updated',
        'deployments': [{**service_value['deployments'][0], 'id': deployment['id'], 'deployed_version': '2'}],
    }
    assert client.put('/api/services/' + service['id'], headers=HEADERS, json=edited_service).status_code == 200
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
        assert conn.execute("SELECT to_regclass('ka.agent_collections') AS name").fetchone()['name'] is not None


def test_edit_service_can_swap_deployments_without_losing_document_links(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Shared host', 'hostname': 'app.internal'}
    ).json()
    value = {
        'name': 'API',
        'deployments': [
            {'server_id': server['id'], 'environment': 'production'},
            {'server_id': server['id'], 'environment': 'staging'},
        ],
    }
    service = client.post('/api/services', headers=HEADERS, json=value).json()
    deployments = client.get('/api/documentation/resources', headers=HEADERS).json()['deployments']
    doc = live(
        store,
        VALUE,
        service_ids=[service['id']],
        server_ids=[server['id']],
        deployment_ids=[d['id'] for d in deployments],
    )
    swapped = [
        {
            'id': d['id'],
            'server_id': server['id'],
            'environment': 'staging' if d['environment'] == 'production' else 'production',
        }
        for d in deployments
    ]
    response = client.put(
        '/api/services/' + service['id'], headers=HEADERS, json={'name': 'API', 'revision': 1, 'deployments': swapped}
    )
    assert response.status_code == 200, response.text
    current = client.get('/api/documentation/documents/' + doc['id'], headers=HEADERS).json()
    assert set(current['deployment_ids']) == {d['id'] for d in deployments}
    assert (
        client.delete(
            '/api/services/' + service['id'], headers=HEADERS, params={'revision': 2, 'confirmed': True}
        ).status_code
        == 200
    )
    current = client.get('/api/documentation/documents/' + doc['id'], headers=HEADERS).json()
    assert current['service_ids'] == current['deployment_ids'] == []
    assert current['source_snapshot']['services'][0]['name'] == 'API'


def test_saving_exports_current_version_in_place(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    created = write_live_document(store, 'payments-runbook', 'Payments', '# Payments\n\n```d2\na -> b\n```\n')
    assert (out / 'payments-runbook' / 'README.md').read_text().startswith('# Payments')
    assert (out / 'payments-runbook' / 'architecture-1.d2').exists()
    save_and_promote(client, created['id'], markdown='# Payments v2')
    assert (out / 'payments-runbook' / 'README.md').read_text().startswith('# Payments v2')
    assert not (out / 'payments-runbook' / 'architecture-1.d2').exists()
    assert not list(out.glob('payments-runbook/v*'))


def test_export_failure_keeps_the_saved_version(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/nonexistent/folder')
    draft = client.post(f'{DOCS}/drafts', headers=HEADERS, json={'slug': 'x', 'title': 'X', 'markdown': '# X'}).json()
    response = client.post(f'{DOCS}/drafts/{draft["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert response.status_code == 200 and response.json()['export_error']
    assert client.get(f'{DOCS}/documents/{draft["id"]}', headers=HEADERS).json()['version'] == 1


def test_editor_save_keeps_source_commit_metadata(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))
    created = live(store, VALUE, code_project_ids=[repo], source_commits={repo: 'a' * 40})
    assert (out / 'payments-runbook' / 'metadata.json').exists()
    save_and_promote(client, created['id'], markdown='# Two')
    assert 'a' * 40 in (out / 'payments-runbook' / 'metadata.json').read_text()


def test_cleanup_leaves_subdirectories_and_symlinks(tmp_path, monkeypatch):
    from knowledge_admin.documents.api import export_document

    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    target = tmp_path / 'target.txt'
    target.write_text('keep')
    folder = tmp_path / 'doc'
    (folder / 'v1').mkdir(parents=True)
    (folder / 'stale.txt').write_text('x')
    (folder / 'link.txt').symlink_to(target)
    export_document({'slug': 'doc', 'markdown': '# D', 'revision': 1})
    assert not (folder / 'stale.txt').exists() and (folder / 'v1').is_dir()
    assert (folder / 'link.txt').is_symlink() and target.read_text() == 'keep'


def test_deleting_a_document_removes_its_export(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    created = write_live_document(store, 'gone', 'G', '# G')
    keep = write_live_document(store, 'kept', 'K', '# K')
    assert (out / 'gone' / 'README.md').exists()
    (out / 'gone' / 'v1').mkdir()
    (out / 'gone' / 'v1' / 'README.md').write_text('old')
    endpoint = '/api/documentation/documents/' + created['id']
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 1, 'confirmed': True}).status_code == 200
    assert not (out / 'gone' / 'README.md').exists() and (out / 'gone' / 'v1' / 'README.md').exists()
    assert (out / 'kept' / 'README.md').exists()
    # A missing export folder never fails the delete.
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/nonexistent/folder')
    response = client.delete(
        '/api/documentation/documents/' + keep['id'], headers=HEADERS, params={'revision': 1, 'confirmed': True}
    )
    assert response.status_code == 200


def test_remove_export_of_a_phase_document_keeps_other_files(tmp_path, monkeypatch):
    from knowledge_admin.documents.api import export_document, export_location, remove_export

    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    metadata = {'service_name': 'Payments API', 'service_id': str(uuid4()), 'phase': 'api'}
    document = {'slug': 'payments-api', 'markdown': '# API\n\n```d2\na -> b\n```\n', 'revision': 1}
    folder = Path(export_document(document, phase_metadata=metadata))
    assert export_location(document['slug'], metadata) == (folder, 'api.md')
    (folder / 'v1').mkdir()
    (folder / 'notes.txt').write_text('keep')
    remove_export({**document, 'phase_metadata': metadata})
    assert {p.name for p in folder.iterdir()} == {'v1', 'notes.txt'}
    (folder / 'notes.txt').unlink()
    (folder / 'v1').rmdir()
    export_document(document, phase_metadata=metadata)
    remove_export({**document, 'phase_metadata': metadata})
    assert not folder.exists()


def test_startup_backfill_writes_missing_exports_only(store, tmp_path, monkeypatch):
    import os
    from knowledge_admin.documents.api import backfill_exports

    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    write_live_document(store, 'legacy', 'L', '# Legacy')
    write_live_document(store, 'current', 'C', '# Current')
    # legacy only has an old <slug>/v1/ export from before the upgrade
    (out / 'legacy' / 'README.md').unlink()
    (out / 'legacy' / 'v1').mkdir()
    (out / 'legacy' / 'v1' / 'README.md').write_text('# Legacy')
    current = out / 'current' / 'README.md'
    old = time.time() - 3600
    os.utime(current, (old, old))
    assert backfill_exports(store) == 1
    assert (out / 'legacy' / 'README.md').read_text() == '# Legacy' and (out / 'legacy' / 'v1').is_dir()
    assert current.stat().st_mtime == old
    assert backfill_exports(store) == 0
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/nonexistent/folder')
    assert backfill_exports(store) == 0  # logged, never raised


def test_app_startup_runs_the_export_backfill(store, tmp_path, monkeypatch):
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    write_live_document(store, 'legacy', 'L', '# Legacy')
    (out / 'legacy' / 'README.md').unlink()
    with _client(tmp_path, repository_store=store) as client:  # entering the client runs the lifespan
        for _ in range(100):
            if (out / 'legacy' / 'README.md').exists():
                break
            time.sleep(0.05)
        assert client.get('/health').status_code == 200
    assert (out / 'legacy' / 'README.md').read_text() == '# Legacy'


def test_app_starts_when_the_backfill_fails(store, tmp_path, monkeypatch):
    import knowledge_admin.app as app_module

    def broken(store):
        raise RuntimeError('boom')

    monkeypatch.setattr(app_module, 'backfill_exports', broken)
    with _client(tmp_path, repository_store=store) as client:
        assert client.get('/health').status_code == 200


def test_document_slug_comes_from_the_name():
    from knowledge_admin.documents.api import document_slug

    assert document_slug('Payments – API reference') == 'payments-api-reference'
    assert document_slug('  Café v2.0 / Ops  ') == 'caf-v2-0-ops'
    assert document_slug('– –') == ''
    assert document_slug('a' * 119 + ' b') == 'a' * 119


def test_folder_documents_export_into_their_folder_and_keep_it_on_edit(store, tmp_path, monkeypatch):
    from knowledge_admin.documents.api import DocumentInput, export_args, export_document, export_input, save_document

    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        saved = save_document(
            conn,
            project,
            None,
            DocumentInput(slug='api-reference', title='API reference', markdown='# API\n\n```d2\na -> b\n```\n'),
            folder='team/payments',
        )
        export_document(**export_args(export_input(conn, saved['id'])))
    folder = out / 'team' / 'payments' / 'api-reference'
    assert (folder / 'README.md').read_text().startswith('# API') and (folder / 'architecture-1.d2').is_file()
    assert not (out / 'api-reference').exists()
    endpoint = '/api/documentation/documents/' + str(saved['id'])
    assert client.get(endpoint, headers=HEADERS).json()['folder'] == 'team/payments'
    # A promoted draft keeps the document's folder and name; its export is rewritten in place.
    save_and_promote(client, str(saved['id']), title='API guide', markdown='# Guide')
    assert client.get(endpoint, headers=HEADERS).json()['folder'] == 'team/payments'
    assert (out / 'team/payments/api-reference/README.md').read_text() == '# Guide'
    assert not (out / 'team/payments/api-reference/architecture-1.d2').exists()
    # The same slug may exist once per folder and once without a folder.
    with store.connect() as conn:
        for other in ('other', None):
            save_document(
                conn, project, None, DocumentInput(slug='api-guide', title='Elsewhere', markdown='# E'), folder=other
            )
    with pytest.raises(psycopg.errors.UniqueViolation):
        with store.connect() as conn:
            save_document(
                conn,
                project,
                None,
                DocumentInput(slug='api-reference', title='Twice', markdown='# T'),
                folder='team/payments',
            )
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 2, 'confirmed': True}).status_code == 200
    assert not (out / 'team/payments/api-reference').exists()


def test_a_folder_never_shares_a_top_level_export_folder_with_a_document(store, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from knowledge_admin.documents.api import DocumentInput, save_document

    client = _client(tmp_path, repository_store=store)
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    write_live_document(store, 'payments', 'P', '# P')
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        with pytest.raises(HTTPException) as error:
            save_document(
                conn, project, None, DocumentInput(slug='api', title='A', markdown='# A'), folder='payments/api'
            )
        assert error.value.status_code == 409
        assert (
            error.value.detail
            == 'The folder payments holds the export of the document payments. Choose another folder.'
        )
        save_document(conn, project, None, DocumentInput(slug='api', title='A', markdown='# A'), folder='team/payments')
    # A new document's export folder is checked when it is published.
    draft = client.post(f'{DOCS}/drafts', headers=HEADERS, json={'slug': 'team', 'title': 'T', 'markdown': '# T'})
    assert draft.status_code == 201, draft.text
    refused = client.post(f'{DOCS}/drafts/{draft.json()["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert refused.status_code == 409
    assert refused.json()['error']['message'] == 'The slug team is used by a documentation folder. Choose another slug.'


def test_colliding_folder_and_folderless_saves_are_serialized(store):
    import threading
    from fastapi import HTTPException
    from knowledge_admin.documents.api import DocumentInput, save_document

    outcome = {}

    def other(project):
        try:
            with store.connect() as conn:
                save_document(conn, project, None, DocumentInput(slug='x', title='X', markdown='# X'), folder='team/x')
            outcome['result'] = 'saved'
        except HTTPException as error:
            outcome['result'] = error.status_code

    with store.connect() as conn:
        project = store.project(conn, 'shop')
        save_document(conn, project, None, DocumentInput(slug='team', title='T', markdown='# T'))
        thread = threading.Thread(target=other, args=(project,))
        thread.start()
        thread.join(1)
        assert thread.is_alive()  # waits for the first transaction instead of reading around it
    thread.join(10)
    assert outcome == {'result': 409}


def test_exports_in_a_shared_folder_leave_siblings_alone_and_refuse_symlinked_parents(store, tmp_path, monkeypatch):
    from knowledge_admin.documents.api import DocumentInput, export_args, export_document, export_input, save_document

    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        first = save_document(
            conn,
            project,
            None,
            DocumentInput(slug='one', title='One', markdown='# One\n\n```d2\na -> b\n```\n'),
            folder='team/payments',
        )
        second = save_document(
            conn,
            project,
            None,
            DocumentInput(slug='two', title='Two', markdown='# Two\n\n```d2\nc -> d\n```\n'),
            folder='team/payments',
        )
        for saved in (first, second):
            export_document(**export_args(export_input(conn, saved['id'])))
        assert (out / 'team/payments/two/architecture-1.d2').is_file()
        edited = save_document(
            conn,
            project,
            None,
            DocumentInput(slug='one', title='One', markdown='# One', revision=1),
            document_id=first['id'],
        )
        export_document(**export_args(export_input(conn, edited['id'])))
    assert not (out / 'team/payments/one/architecture-1.d2').exists()
    assert (out / 'team/payments/two/README.md').is_file() and (out / 'team/payments/two/architecture-1.d2').is_file()
    # A symbolic link anywhere in the folder path is refused and nothing is written through it.
    outside = tmp_path / 'outside'
    outside.mkdir()
    (out / 'linked').symlink_to(outside)
    with store.connect() as conn:
        escaped = save_document(
            conn, project, None, DocumentInput(slug='three', title='Three', markdown='# Three'), folder='linked/deep'
        )
        with pytest.raises(ValueError):
            export_document(**export_args(export_input(conn, escaped['id'])))
    assert list(outside.iterdir()) == []


def test_renames_respect_folder_collisions_and_never_conflict_with_themselves(store, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from knowledge_admin.documents.api import DocumentInput, save_document

    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        plain = save_document(conn, project, None, DocumentInput(slug='notes', title='N', markdown='# N'))
        save_document(conn, project, None, DocumentInput(slug='x', title='X', markdown='# X'), folder='team/x')
        with pytest.raises(HTTPException) as error:
            save_document(
                conn,
                project,
                None,
                DocumentInput(slug='team', title='N', markdown='# N', revision=1),
                document_id=plain['id'],
            )
        assert error.value.status_code == 409
        again = save_document(
            conn,
            project,
            None,
            DocumentInput(slug='notes', title='N2', markdown='# N2', revision=1),
            document_id=plain['id'],
        )
        assert again['revision'] == 2


def test_nested_exports_never_share_a_folder_in_either_direction(store, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from knowledge_admin.documents.api import DocumentInput, export_conflict, save_document

    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path))

    def refused(conn, project, slug, folder, message):
        with pytest.raises(HTTPException) as error:
            with conn.transaction():
                save_document(conn, project, None, DocumentInput(slug=slug, title='T', markdown='# T'), folder=folder)
        assert error.value.status_code == 409 and error.value.detail == message
        assert export_conflict(conn, project, slug, folder) == message

    with store.connect() as conn:
        project = store.project(conn, 'shop')
        # Document b in folder a exports to a/b/: the folders a/b and a/b/c (at any depth) are refused.
        save_document(conn, project, None, DocumentInput(slug='b', title='B', markdown='# B'), folder='a')
        message = 'The folder a/b holds the export of the document b in the folder a. Choose another folder.'
        refused(conn, project, 'x', 'a/b', message)
        refused(conn, project, 'x', 'a/b/c', message)
        save_document(conn, project, None, DocumentInput(slug='x', title='X', markdown='# X'), folder='a/bc')
        # The other direction: folder p/q/r exists, so documents q in p and r in p/q are refused.
        save_document(conn, project, None, DocumentInput(slug='y', title='Y', markdown='# Y'), folder='p/q/r')
        refused(conn, project, 'q', 'p', 'The slug q is used by the documentation folder p/q. Choose another slug.')
        refused(conn, project, 'r', 'p/q', 'The slug r is used by the documentation folder p/q/r. Choose another slug.')
        save_document(conn, project, None, DocumentInput(slug='q', title='Q', markdown='# Q'), folder='p/q/r')
        # '_' in a folder is matched literally, not as a LIKE wildcard: m_n/c/ is unrelated to mxn/c/d.
        save_document(conn, project, None, DocumentInput(slug='y', title='Y', markdown='# Y'), folder='mxn/c/d')
        save_document(conn, project, None, DocumentInput(slug='c', title='C', markdown='# C'), folder='m_n')
        # Renaming a nested document onto a folder is refused too.
        doc = save_document(conn, project, None, DocumentInput(slug='z', title='Z', markdown='# Z'), folder='p')
        with pytest.raises(HTTPException) as error:
            save_document(
                conn,
                project,
                None,
                DocumentInput(slug='q', title='Z', markdown='# Z', revision=1),
                document_id=doc['id'],
            )
        assert error.value.status_code == 409


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
