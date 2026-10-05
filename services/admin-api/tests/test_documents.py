import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS

VALUE = {'slug':'payments-runbook', 'title':'Payments runbook', 'description':'Diagnose failed payments',
         'purpose':'troubleshooting', 'audience':'agents', 'tags':['payments'], 'markdown':'# Payments\n\n  Preserve indentation.\n'}
PROMPT = {'name':'Code architecture', 'description':'Explain components', 'input_kind':'code', 'content':'Read source code; cite evidence.'}


def test_document_commits_are_versioned_preserved_and_validated(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))
    value = {**VALUE, 'code_project_ids': [repo], 'source_commits': {repo: 'a' * 40}}
    created = client.post('/api/documentation/documents', headers=HEADERS, json=value)
    assert created.status_code == 201, created.text
    endpoint = '/api/documentation/documents/' + created.json()['id']
    first = client.get(endpoint, headers=HEADERS).json()['current_version']
    assert first['source_snapshot']['code_projects'][0]['git_commit'] == 'a' * 40
    value.pop('source_commits')
    edited = client.put(endpoint, headers=HEADERS, json={**value, 'revision': 1})
    assert edited.status_code == 200, edited.text
    assert client.get(endpoint, headers=HEADERS).json()['current_version']['source_snapshot']['code_projects'][0]['git_commit'] == 'a' * 40
    updated = client.put(endpoint, headers=HEADERS, json={**value, 'revision': 2, 'source_commits': {repo: 'b' * 40}})
    assert updated.status_code == 200, updated.text
    assert client.get(endpoint, headers=HEADERS).json()['current_version']['source_snapshot']['code_projects'][0]['git_commit'] == 'b' * 40
    assert client.get(endpoint + '/versions/' + first['id'], headers=HEADERS).json()['source_snapshot']['code_projects'][0]['git_commit'] == 'a' * 40
    assert client.put(endpoint, headers=HEADERS, json={**value, 'revision': 3, 'source_commits': {repo: 'HEAD'}}).status_code == 400
    assert client.put(endpoint, headers=HEADERS, json={**value, 'revision': 3, 'source_commits': {str(uuid4()): 'b' * 40}}).status_code == 400
    cleared = client.put(endpoint, headers=HEADERS, json={**value, 'revision': 3, 'source_commits': {}})
    assert cleared.status_code == 200
    assert 'git_commit' not in client.get(endpoint, headers=HEADERS).json()['current_version']['source_snapshot']['code_projects'][0]


def test_document_versions_filters_links_and_deletion(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api',token='never-show-this'), 'admin',''))
    server = client.post('/api/servers',headers=HEADERS,json={'name':'Payments host','hostname':'payments.internal'}).json()
    service_value = {'name':'Payments','repository_ids':[repo],'deployments':[{'server_id':server['id'],'environment':'production'}]}
    service = client.post('/api/services',headers=HEADERS,json=service_value).json()
    resources = client.get('/api/documentation/resources',headers=HEADERS)
    assert resources.status_code == 200 and 'never-show-this' not in resources.text and 'credential' not in resources.text
    deployment = resources.json()['deployments'][0]
    value = {**VALUE,'code_project_ids':[repo],'server_ids':[server['id']], 'service_ids':[service['id']], 'deployment_ids':[deployment['id']]}
    created = client.post('/api/documentation/documents',headers=HEADERS,json=value)
    assert created.status_code == 201, created.text
    doc_id = created.json()['id']
    endpoint = '/api/documentation/documents/' + doc_id
    original = client.get(endpoint,headers=HEADERS).json()
    first = original['current_version']
    assert original['version'] == original['revision'] == 1
    assert first['markdown'] == VALUE['markdown']
    assert first['content_hash'] == hashlib.sha256(VALUE['markdown'].encode()).hexdigest()
    assert first['source_snapshot']['servers'][0]['hostname'] == 'payments.internal'
    filters = {'q':'payments','code_project_id':repo,'service_id':service['id'],'server_id':server['id'],'deployment_id':deployment['id']}
    listing = client.get('/api/documentation/documents',params=filters,headers=HEADERS).json()
    assert listing['total'] == 1 and listing['documents'][0]['id'] == doc_id
    assert 'markdown' not in listing['documents'][0]
    assert client.get('/api/documentation/documents',params={**filters,'server_id':str(uuid4())},headers=HEADERS).json()['total'] == 0
    edited_service = {**service_value,'revision':1,'description':'Updated', 'deployments':[{**service_value['deployments'][0],'id':deployment['id'],'deployed_version':'2'}]}
    assert client.put('/api/services/'+service['id'],headers=HEADERS,json=edited_service).status_code == 200
    assert client.get(endpoint,headers=HEADERS).json()['deployment_ids'] == [deployment['id']]
    updated = client.put(endpoint,headers=HEADERS,json={**value,'revision':1,'markdown':'# Revised\n','service_ids':[],'deployment_ids':[]})
    assert updated.status_code == 200, updated.text
    current = client.get(endpoint,headers=HEADERS).json()
    assert current['version'] == current['revision'] == 2 and len(current['versions']) == 2
    assert current['current_version']['base_version_id'] == first['id']
    assert client.get(endpoint+'/versions/'+first['id'],headers=HEADERS).json() == first
    assert client.get('/api/documentation/documents',params={'service_id':service['id']},headers=HEADERS).json()['total'] == 0
    assert client.delete(endpoint,headers=HEADERS,params={'revision':2}).status_code == 400
    assert client.delete(endpoint,headers=HEADERS,params={'revision':1,'confirmed':True}).status_code == 409
    assert client.delete(endpoint,headers=HEADERS,params={'revision':2,'confirmed':True}).status_code == 200
    assert client.get(endpoint,headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.document_versions WHERE document_id=%s',(UUID(doc_id),)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.servers').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM ka.repositories').fetchone()['n'] == 1
        actions = conn.execute("SELECT action FROM ka.audit_events WHERE resource_type='document' ORDER BY id").fetchall()
    assert [r['action'] for r in actions] == ['document.created','document.updated','document.deleted']


def test_stale_concurrent_edits_foreign_links_and_hash_constraints(store, tmp_path):
    client = _client(tmp_path,repository_store=store)
    response = client.post('/api/documentation/documents',headers=HEADERS,json=VALUE)
    assert response.status_code == 201, response.text
    endpoint = '/api/documentation/documents/' + response.json()['id']
    foreign_repo = str(store.save('other',RepositoryInput(remote_url='example/other'),'admin',''))
    assert client.put(endpoint,headers=HEADERS,json={**VALUE,'revision':1,'code_project_ids':[foreign_repo]}).status_code == 400
    assert client.post('/api/documentation/documents',headers=HEADERS,json=VALUE).status_code == 409
    assert client.put(endpoint,headers=HEADERS,json=VALUE).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda text: client.put(endpoint,headers=HEADERS,json={**VALUE,'revision':1,'markdown':text}),['First edit','Second edit']))
    assert sorted(r.status_code for r in responses) == [200,409]
    result = client.get(endpoint,headers=HEADERS).json()
    assert len(result['versions']) == 2
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE ka.document_versions SET markdown='changed' WHERE id=%s",(UUID(result['current_version_id']),))
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute('INSERT INTO ka.document_code_projects(document_id,project_id,code_project_id) VALUES (%s,%s,%s)',
                         (UUID(result['id']),UUID(result['project_id']),UUID(foreign_repo)))
    assert client.get('/api/documentation/documents/'+str(uuid4()),headers=HEADERS).status_code == 404
    assert client.get(endpoint+'/versions/'+str(uuid4()),headers=HEADERS).status_code == 404


def test_prompt_drafts_activation_disable_and_delete(store, tmp_path):
    client = _client(tmp_path,repository_store=store)
    created = client.post('/api/documentation/prompts',headers=HEADERS,json=PROMPT)
    assert created.status_code == 201, created.text
    endpoint = '/api/documentation/prompts/' + created.json()['id']
    original = client.get(endpoint,headers=HEADERS).json()
    version1 = original['versions'][0]
    assert original['active_version_id'] is None and version1['activated_at'] is None
    assert client.post(endpoint+'/versions/'+version1['id']+'/activate',headers=HEADERS,json={'revision':1}).status_code == 200
    updated = client.put(endpoint,headers=HEADERS,json={**PROMPT,'revision':2,'content':'Explain architecture and failure modes.'})
    assert updated.status_code == 200, updated.text
    current = client.get(endpoint,headers=HEADERS).json()
    version2 = current['versions'][0]
    assert current['active_version_id'] == version1['id'] and version2['activated_at'] is None
    assert client.delete(endpoint+'/versions/'+version1['id'],headers=HEADERS,params={'revision':3,'confirmed':True}).status_code == 409
    assert client.post(endpoint+'/versions/'+version2['id']+'/activate',headers=HEADERS,json={'revision':2}).status_code == 409
    assert client.post(endpoint+'/versions/'+version2['id']+'/activate',headers=HEADERS,json={'revision':3}).status_code == 200
    assert client.put(endpoint,headers=HEADERS,json={**PROMPT,'revision':4,'content':version2['content'],'enabled':False}).status_code == 200
    disabled = client.get(endpoint,headers=HEADERS).json()
    assert not disabled['enabled'] and len(disabled['versions']) == 2
    assert client.delete(endpoint+'/versions/'+version1['id'],headers=HEADERS,params={'revision':5,'confirmed':True}).status_code == 200
    assert client.put(endpoint,headers=HEADERS,json={**PROMPT,'revision':6,'content':'Another draft.'}).status_code == 200
    assert client.get(endpoint,headers=HEADERS).json()['versions'][0]['version'] == 3
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.CheckViolation),conn.transaction():
            conn.execute("UPDATE ka.documentation_prompt_versions SET content='replace' WHERE id=%s",(UUID(version2['id']),))
    assert client.delete(endpoint,headers=HEADERS,params={'revision':7}).status_code == 400
    assert client.delete(endpoint,headers=HEADERS,params={'revision':7,'confirmed':True}).status_code == 200
    assert client.get(endpoint,headers=HEADERS).status_code == 404
    assert client.get('/api/documentation/generations',headers=HEADERS).json() == {'generations':[]}


@pytest.mark.parametrize('method,path', [
    ('GET','/documentation/resources'),('GET','/documentation/generations'),
    ('GET','/documentation/documents'),('POST','/documentation/documents'),
    ('GET','/documentation/prompts'),('POST','/documentation/prompts'),
    ('GET','/documentation/documents/'+str(uuid4())),('PUT','/documentation/documents/'+str(uuid4())),
    ('DELETE','/documentation/documents/'+str(uuid4())),('GET','/documentation/prompts/'+str(uuid4())),
    ('PUT','/documentation/prompts/'+str(uuid4())),('DELETE','/documentation/prompts/'+str(uuid4())),
    ('POST','/documentation/prompts/'+str(uuid4())+'/versions/'+str(uuid4())+'/activate'),
    ('DELETE','/documentation/prompts/'+str(uuid4())+'/versions/'+str(uuid4())),
])
def test_documentation_requires_admin(tmp_path,path,method):
    assert _client(tmp_path).request(method,'/api'+path,json=VALUE).status_code == 403


@pytest.mark.parametrize('change',[{'title':'  '},{'markdown':'  '},{'markdown':'a\x00b'},{'slug':'../no'},{'audience':'everyone'},{'tags':['']},{'purpose':' '},{'revision':True}])
def test_invalid_document_input(tmp_path,change):
    assert _client(tmp_path).post('/api/documentation/documents',headers=HEADERS,json={**VALUE,**change}).status_code == 400


def test_document_migrations_roundtrip_and_preserve_populated_tables(store):
    from pathlib import Path
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    # Every migration from 0039 to the latest, so later migrations never collide with the restored legacy schema.
    newer = sorted(path.name.removesuffix('.up.sql') for path in migrations.glob('*.up.sql')
                   if path.name >= '0039_retire_document_pipeline.up.sql')
    with store.connect() as conn:
        for name in reversed(newer):
            conn.execute((migrations/f'{name}.down.sql').read_text(),prepare=False)
        project = store.project(conn,'shop')
        conn.execute("INSERT INTO ka.document_sets(project_id,slug,name) VALUES (%s,'legacy','Legacy')",(project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute((migrations/'0039_retire_document_pipeline.up.sql').read_text(),prepare=False)
        conn.rollback()
        assert conn.execute('SELECT count(*) AS n FROM ka.document_sets').fetchone()['n'] == 1
        conn.execute('DELETE FROM ka.document_sets')
        conn.commit()
        for name in newer:
            conn.execute((migrations/f'{name}.up.sql').read_text(),prepare=False)
        assert conn.execute("SELECT to_regclass('ka.document_details') AS name").fetchone()['name'] is not None
        assert conn.execute("SELECT to_regclass('ka.agent_collections') AS name").fetchone()['name'] is not None


def test_edit_service_can_swap_deployments_without_losing_document_links(store,tmp_path):
    client = _client(tmp_path,repository_store=store)
    server = client.post('/api/servers',headers=HEADERS,json={'name':'Shared host','hostname':'app.internal'}).json()
    value = {'name':'API','deployments':[{'server_id':server['id'],'environment':'production'}, {'server_id':server['id'],'environment':'staging'}]}
    service = client.post('/api/services',headers=HEADERS,json=value).json()
    deployments = client.get('/api/documentation/resources',headers=HEADERS).json()['deployments']
    doc = client.post('/api/documentation/documents',headers=HEADERS,json={**VALUE,'service_ids':[service['id']], 'server_ids':[server['id']], 'deployment_ids':[d['id'] for d in deployments]}).json()
    swapped = [{ 'id':d['id'],'server_id':server['id'],'environment':'staging' if d['environment']=='production' else 'production' } for d in deployments]
    response = client.put('/api/services/'+service['id'],headers=HEADERS,json={'name':'API','revision':1,'deployments':swapped})
    assert response.status_code == 200,response.text
    current = client.get('/api/documentation/documents/'+doc['id'],headers=HEADERS).json()
    assert set(current['deployment_ids']) == {d['id'] for d in deployments}
    assert client.delete('/api/services/'+service['id'],headers=HEADERS,params={'revision':2,'confirmed':True}).status_code == 200
    current = client.get('/api/documentation/documents/'+doc['id'],headers=HEADERS).json()
    assert current['service_ids'] == current['deployment_ids'] == []
    assert current['current_version']['source_snapshot']['services'][0]['name'] == 'API'


def test_saving_exports_current_version_in_place(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    created = client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'payments-runbook', 'title': 'Payments', 'markdown': '# Payments\n\n```d2\na -> b\n```\n'}).json()
    assert (out / 'payments-runbook' / 'README.md').read_text().startswith('# Payments')
    assert (out / 'payments-runbook' / 'architecture-1.d2').exists()
    client.put(f"/api/documentation/documents/{created['id']}", headers=HEADERS, json={
        'slug': 'payments-runbook', 'title': 'Payments', 'markdown': '# Payments v2', 'revision': 1})
    assert (out / 'payments-runbook' / 'README.md').read_text().startswith('# Payments v2')
    assert not (out / 'payments-runbook' / 'architecture-1.d2').exists()
    assert not list(out.glob('payments-runbook/v*'))


def test_export_failure_keeps_the_saved_version(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/nonexistent/folder')
    response = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'x', 'title': 'X', 'markdown': '# X'})
    assert response.status_code == 201 and 'export_error' in response.json()


def test_editor_save_keeps_source_commit_metadata(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))
    value = {**VALUE, 'code_project_ids': [repo], 'source_commits': {repo: 'a' * 40}}
    created = client.post('/api/documentation/documents', headers=HEADERS, json=value).json()
    assert (out / 'payments-runbook' / 'metadata.json').exists()
    value.pop('source_commits')
    client.put('/api/documentation/documents/' + created['id'], headers=HEADERS, json={**value, 'markdown': '# Two', 'revision': 1})
    assert 'a' * 40 in (out / 'payments-runbook' / 'metadata.json').read_text()


def test_slug_rename_removes_old_files_but_not_subfolders(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    created = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'old-name', 'title': 'T', 'markdown': '# T'}).json()
    endpoint = '/api/documentation/documents/' + created['id']
    client.put(endpoint, headers=HEADERS, json={'slug': 'new-name', 'title': 'T', 'markdown': '# T', 'revision': 1})
    assert (out / 'new-name' / 'README.md').exists() and not (out / 'old-name').exists()
    (out / 'new-name' / 'v1').mkdir()
    (out / 'new-name' / 'v1' / 'README.md').write_text('old')
    client.put(endpoint, headers=HEADERS, json={'slug': 'newer-name', 'title': 'T', 'markdown': '# T', 'revision': 2})
    assert not (out / 'new-name' / 'README.md').exists() and (out / 'new-name' / 'v1' / 'README.md').exists()


def test_cleanup_leaves_subdirectories_and_symlinks(tmp_path, monkeypatch):
    from knowledge_admin.documents import export_document
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
    created = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'gone', 'title': 'G', 'markdown': '# G'}).json()
    keep = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'kept', 'title': 'K', 'markdown': '# K'}).json()
    assert (out / 'gone' / 'README.md').exists()
    (out / 'gone' / 'v1').mkdir()
    (out / 'gone' / 'v1' / 'README.md').write_text('old')
    endpoint = '/api/documentation/documents/' + created['id']
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 1, 'confirmed': True}).status_code == 200
    assert not (out / 'gone' / 'README.md').exists() and (out / 'gone' / 'v1' / 'README.md').exists()
    assert (out / 'kept' / 'README.md').exists()
    # A missing export folder never fails the delete.
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/nonexistent/folder')
    response = client.delete('/api/documentation/documents/' + keep['id'], headers=HEADERS, params={'revision': 1, 'confirmed': True})
    assert response.status_code == 200


def test_remove_export_of_a_phase_document_keeps_other_files(tmp_path, monkeypatch):
    from knowledge_admin.documents import export_document, export_location, remove_export
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
    from knowledge_admin.documents import backfill_exports
    client = _client(tmp_path, repository_store=store)
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'legacy', 'title': 'L', 'markdown': '# Legacy'})
    client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'current', 'title': 'C', 'markdown': '# Current'})
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
    _client(tmp_path, repository_store=store).post('/api/documentation/documents', headers=HEADERS,
                                                   json={'slug': 'legacy', 'title': 'L', 'markdown': '# Legacy'})
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
