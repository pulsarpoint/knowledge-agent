import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS

VALUE = {'slug':'payments-runbook', 'title':'Payments runbook', 'description':'Diagnose failed payments',
         'purpose':'troubleshooting', 'audience':'agents', 'tags':['payments'], 'markdown':'# Payments\n\n  Preserve indentation.\n'}
PROMPT = {'name':'Code architecture', 'description':'Explain components', 'input_kind':'code', 'content':'Read source code; cite evidence.'}


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
    with store.connect() as conn:
        conn.execute((migrations/'0049_layered_environment_architecture.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0048_server_infrastructure_documentation.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0047_documentation_workflows.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0046_documentation_generation.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0041_app_docs_prompt.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0040_documentation.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0039_retire_document_pipeline.down.sql').read_text(),prepare=False)
        project = store.project(conn,'shop')
        conn.execute("INSERT INTO ka.document_sets(project_id,slug,name) VALUES (%s,'legacy','Legacy')",(project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute((migrations/'0039_retire_document_pipeline.up.sql').read_text(),prepare=False)
        conn.rollback()
        assert conn.execute('SELECT count(*) AS n FROM ka.document_sets').fetchone()['n'] == 1
        conn.execute('DELETE FROM ka.document_sets')
        conn.commit()
        conn.execute((migrations/'0039_retire_document_pipeline.up.sql').read_text(),prepare=False)
        conn.execute((migrations/'0040_documentation.up.sql').read_text(),prepare=False)
        conn.execute((migrations/'0041_app_docs_prompt.up.sql').read_text(),prepare=False)
        assert conn.execute("SELECT to_regclass('ka.document_details') AS name").fetchone()['name'] is not None


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
