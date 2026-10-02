import copy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin import documentation_generation as generation
from test_documentation_generation import setup_request
from test_code_documentation_phases import phase_request
from test_repositories import HEADERS


def create(client, value):
    result = client.post('/api/documentation/workflows', headers=HEADERS, json={
        'scope':'service','service_id':value['service_id'],'model_id':value['model_id']})
    assert result.status_code == 201, result.text
    return result.json()['id']


@pytest.mark.parametrize('phase',['api','messaging'])
def test_workflow_drafts_survive_reload_and_conflicting_writes_are_rejected(store,tmp_path,monkeypatch,phase):
    client,value,_ = setup_request(store,tmp_path,monkeypatch)
    workflow = create(client,value)
    url = '/api/documentation/workflows/'+workflow
    initial = client.get(url,headers=HEADERS).json()
    form = copy.deepcopy(initial['drafts']['form'])
    form['value']['instructions'] = 'Document our API contracts'
    endpoint = url+'/drafts/form'
    assert client.put(endpoint,headers=HEADERS,json={'content':form,'previous':initial['drafts']['form']}).status_code == 200
    assert client.get(url,headers=HEADERS).json()['drafts']['form'] == form
    assert client.put(endpoint,headers=HEADERS,json={'content':initial['drafts']['form'],'previous':initial['drafts']['form']}).status_code == 409
    # Another section can be edited without overwriting the form.
    prompt = {'content':'Trace all entry points','instructions':'Include private APIs'}
    assert client.put(url+'/drafts/'+phase,headers=HEADERS,json={'content':prompt,'previous':None}).status_code == 200
    reloaded = client.get(url,headers=HEADERS).json()
    assert reloaded['drafts'][phase] == prompt and reloaded['drafts']['form'] == form
    assert client.put(url+'/drafts/'+phase,json={'content':prompt,'previous':prompt}).status_code == 403
    assert client.get('/api/documentation/workflows/'+str(uuid4()),headers=HEADERS).status_code == 404


@pytest.mark.parametrize('phase',['api','messaging'])
def test_phase_attempts_are_scoped_and_documents_link_back_to_their_workflow(store,tmp_path,monkeypatch,phase):
    client,value,_ = setup_request(store,tmp_path,monkeypatch)
    if phase == 'api':
        from test_api_evidence import graph_fixture, section_result
        graph = graph_fixture(tmp_path, value['code_project_ids'][0])
        monkeypatch.setenv('CODE_ROOT', graph['code_root'])
        monkeypatch.setattr(generation, 'load_code_graph', lambda *_: graph)
    first,second = create(client,value),create(client,value)
    async def run(config,task,**kwargs):
        if phase == 'api':
            return section_result(task)
        if kwargs['output_type'] is None:
            return SimpleNamespace(to_input_list=lambda:[])
        return SimpleNamespace(final_output={'title':'API contracts','description':'Endpoint evidence',
            'markdown':'# API\n\n'+('POST /items validates input in app/main.py, writes items and responds with the stored identifier. ' * 4),'diagram':None})
    monkeypatch.setattr(generation,'agent',run)
    payload={**phase_request(client,value,phase),'workflow_id':first}
    for _ in range(2):
        assert client.post('/api/documentation/generations',headers=HEADERS,json=payload).status_code == 202
        assert generation.run_once(store)
    runs=client.get('/api/documentation/generations',headers=HEADERS,params={'workflow_id':first}).json()['generations']
    assert len(runs)==2 and all(r['workflow_id']==first for r in runs)
    assert client.get('/api/documentation/generations',headers=HEADERS,params={'workflow_id':second}).json()['generations']==[]
    workflow=client.get('/api/documentation/workflows/'+first,headers=HEADERS).json()
    assert workflow['phases'][0]['status']=='succeeded' and workflow['phases'][0]['job_id']==runs[0]['job_id']
    document_id=runs[0]['result']['documents'][0]['id']
    doc=client.get('/api/documentation/documents/'+document_id,headers=HEADERS).json()
    assert doc['workflows']==[{'id':first,'name':workflow['name']}]
    payload['service_id']=client.post('/api/services',headers=HEADERS,json={'name':'Other'}).json()['id']
    assert client.post('/api/documentation/generations',headers=HEADERS,json=payload).status_code==400


def test_graph_jobs_belong_to_workflow_and_validate_the_service_source(store,tmp_path,monkeypatch):
    client,value,_=setup_request(store,tmp_path,monkeypatch)
    workflow=create(client,value)
    repo=value['code_project_ids'][0]
    response=client.post(f'/api/code-projects/{repo}/process?workflow_id={workflow}',headers=HEADERS)
    assert response.status_code==202,response.text
    duplicate=client.post(f'/api/code-projects/{repo}/process?workflow_id={workflow}',headers=HEADERS)
    assert duplicate.json()==response.json()
    detail=client.get('/api/documentation/workflows/'+workflow,headers=HEADERS).json()
    assert len(detail['graph_runs'])==1 and detail['graph_runs'][0]['status']=='queued'
    unrelated=client.post('/api/services',headers=HEADERS,json={'name':'Unrelated'}).json()['id']
    other=create(client,{**value,'service_id':unrelated})
    assert client.post(f'/api/code-projects/{repo}/process?workflow_id={other}',headers=HEADERS).status_code==400


def test_migration_groups_existing_service_generations_without_modifying_them(store,tmp_path,monkeypatch):
    client,value,_=setup_request(store,tmp_path,monkeypatch)
    async def run(*args,**kwargs):
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Existing',description='',markdown='# Existing code'))
    monkeypatch.setattr(generation,'agent',run)
    value.update(units=[value['units'][0]],server_ids=[],update_existing=True)
    for _ in range(2):
        response=client.post('/api/documentation/generations',headers=HEADERS,json=value)
        assert response.status_code==202,response.text
        assert generation.run_once(store)
    with store.connect() as conn:
        before=conn.execute('SELECT * FROM ka.documentation_generations ORDER BY job_id').fetchall()
        migrations=Path(__file__).resolve().parents[3]/'db/migrations'
        conn.execute((migrations/'0055_documentation_sessions.down.sql').read_text(),prepare=False)
        conn.execute((migrations/'0055_documentation_sessions.up.sql').read_text(),prepare=False)
        assert conn.execute('SELECT * FROM ka.documentation_generations ORDER BY job_id').fetchall()==before
    workflows=client.get('/api/documentation/workflows',headers=HEADERS).json()['workflows']
    assert len(workflows)==1
    detail=client.get('/api/documentation/workflows/'+workflows[0]['id'],headers=HEADERS).json()
    assert detail['drafts']['form']['value']['service_id']==value['service_id']
    assert detail['drafts']['form']['value']['model_id']==value['model_id']
    assert len(client.get('/api/documentation/generations',headers=HEADERS,params={'workflow_id':detail['id']}).json()['generations'])==2


def test_deleting_inventory_preserves_workflow_history(store,tmp_path,monkeypatch):
    client,value,_=setup_request(store,tmp_path,monkeypatch)
    workflow=create(client,value)
    service=next(s for s in client.get('/api/services',headers=HEADERS).json()['services'] if s['id']==value['service_id'])
    response=client.delete('/api/services/'+service['id'],headers=HEADERS,params={'confirmed':'true','revision':service['revision']})
    assert response.status_code==200,response.text
    detail=client.get('/api/documentation/workflows/'+workflow,headers=HEADERS).json()
    assert detail['service_id'] is None and detail['name']=='Application documentation'
    env_workflow=client.post('/api/documentation/workflows',headers=HEADERS,json={
        'scope':'environment','environment_id':value['environment_id'],'model_id':value['model_id']}).json()['id']
    env=next(e for e in client.get('/api/environments',headers=HEADERS).json()['environments'] if e['id']==value['environment_id'])
    response=client.delete('/api/environments/'+env['id'],headers=HEADERS,params={'confirmed':'true','revision':env['revision']})
    assert response.status_code==200,response.text
    assert client.get('/api/documentation/workflows/'+env_workflow,headers=HEADERS).json()['environment_id'] is None
