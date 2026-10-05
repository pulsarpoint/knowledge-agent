import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from agents.usage import Usage

from knowledge_admin import documentation_generation as generation
from test_documentation_generation import setup_request
from test_repositories import HEADERS


@pytest.fixture(params=['overview', 'api', 'messaging'])
def phase(request):
    return request.param


def phase_request(client, value, phase):
    prompt = next(p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts'] if p['name'] == {'overview':'Application overview','api':'API documentation','messaging':'Messaging documentation'}[phase])
    return {**value, 'environment_id': None, 'server_ids': [], 'update_existing': True,
        'code_graph_hashes': {value['code_project_ids'][0]: 'fixture'},
        'units': [{'kind': phase, 'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id']}]}


def test_overview_accepts_bedrock_json_encoded_diagram_and_still_validates_its_edges():
    value = {'title': 'Overview', 'description': '', 'markdown': '# Overview\n\n' + 'Verified source evidence. ' * 10,
        'diagram': json.dumps({'nodes': [{'id': 'api', 'label': 'API', 'parent_id': None}],
            'edges': [], 'explanation': 'One component.'})}
    assert generation.CodeDocumentation.model_validate(value).diagram.nodes[0].id == 'api'
    value['diagram'] = json.dumps({'nodes': [{'id': 'api', 'label': 'API', 'parent_id': None}],
        'edges': [{'source': 'api', 'target': 'missing', 'label': ''}], 'explanation': ''})
    with pytest.raises(ValueError):
        generation.CodeDocumentation.model_validate(value)


@pytest.mark.parametrize('phase', ['overview', 'messaging'])
def test_phase_is_independent_inspectable_versioned_and_exported(store, tmp_path, monkeypatch, phase):
    client, original, _ = setup_request(store, tmp_path, monkeypatch)
    value = phase_request(client, original, phase)
    value['units'][0]['prompt_override'] = 'Explain the customer journeys.'
    calls = []
    async def run(config, task, **kwargs):
        calls.append((copy.deepcopy(config), task))
        assert kwargs['stream'] is False
        if kwargs['output_type'] is None:
            return SimpleNamespace(final_output='Evidence: app/main.py:10',
                to_input_list=lambda: [{'role':'assistant','content':'Evidence: app/main.py:10'}])
        assert kwargs['output_type'] is generation.CodeDocumentation
        assert kwargs['history'][-1]['content'] == 'Evidence: app/main.py:10'
        assert not config['code']['enabled']
        return SimpleNamespace(final_output=generation.CodeDocumentation(title='Application overview', description='Purpose and flows',
            markdown='# Overview\n\nEvidence: app/main.py:10. HTTP input changes a stored record. The API receives a customer request, validates the supplied fields, and passes them to the storage component. The stored record becomes available to later requests; current deployment and live status are not verified.',
            diagram=generation.ComponentGraph(nodes=[{'id': 'api', 'label': 'API', 'parent_id': None},
                {'id': 'db', 'label': 'Storage', 'parent_id': None}], edges=[{'source':'api','target':'db','label':'writes'}], explanation='Source: app/main.py:10')))
    monkeypatch.setattr(generation, 'agent', run)
    response = client.post('/api/documentation/generations', headers=HEADERS, json=value)
    assert response.status_code == 202, response.text
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 409
    assert generation.run_once(store)
    assert len(calls) == 2
    config, task = calls[0]
    task = json.loads(task)
    assert config['code']['graphs'] and config['code']['projects'][0]['folder_paths'] == ['/work/code/app']
    assert not config['ssh']['enabled'] and not config['mcp']['enabled']
    assert config['system_prompt']['content'] == value['units'][0]['prompt_override']
    assert task['existing_document'] is None and 'supporting_documents' not in task['sources']
    query = {'service_id': value['service_id'], 'kind': phase}
    rows = client.get('/api/documentation/generations', headers=HEADERS, params=query).json()['generations']
    assert len(rows) == 1 and rows[0]['status'] == 'succeeded', rows
    row = rows[0]
    assert row['code_graph_hashes'] == value['code_graph_hashes']
    assert row['prompt_snapshot'][0]['content'] == 'Explain the customer journeys.'
    assert row['prompt_snapshot'][0]['overridden']
    artifact = row['result']['documents'][0]
    folder = Path(row['result']['output_folders'][0])
    assert folder.name == phase and folder.parent.parent.name == 'services'
    assert {f.name for f in folder.iterdir()} == {f'{phase}.md', 'architecture-1.d2', 'metadata.json'}
    metadata = json.loads((folder / 'metadata.json').read_text())
    assert metadata['version_id'] == artifact['version_id'] and metadata['code_graph_hashes'] == value['code_graph_hashes']
    assert metadata['prompt_overridden'] and metadata['version'] == 1
    version_url = '/api/documentation/documents/' + artifact['id'] + '/versions/' + artifact['version_id']
    first = client.get(version_url, headers=HEADERS).json()
    assert first['purpose'] == phase and '```d2' in first['markdown']
    assert client.get('/api/documentation/generations', headers=HEADERS, params={**query, 'service_id':str(uuid4())}).json()['generations'] == []
    assert client.get('/api/documentation/generations', headers=HEADERS, params={**query, 'kind':'code'}).json()['generations'] == []
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    latest = client.get('/api/documentation/generations', headers=HEADERS, params=query).json()['generations'][0]
    assert latest['result']['documents'][0]['revision'] == 2
    assert client.get(version_url, headers=HEADERS).json() == first
    assert client.post('/api/documentation/documents/' + artifact['id'] + '/export', headers=HEADERS).json()['folder'].endswith(f'/{phase}')
    assert not generation.run_once(store)  # No later phase was queued.


def test_phase_rejects_mixed_units_and_stale_graph_and_handles_missing_code(store, tmp_path, monkeypatch, phase):
    client, original, _ = setup_request(store, tmp_path, monkeypatch)
    value = phase_request(client, original, phase)
    assert client.post('/api/documentation/generations', headers=HEADERS,
        json={**value,'units': [*value['units'],original['units'][0]]}).status_code == 400
    assert client.post('/api/documentation/generations', json=value).status_code == 403
    def stale(*args):
        raise generation.CodeProcessingError('Source changed. Generate the graph again.')
    monkeypatch.setattr(generation, 'load_code_graph', stale)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 409
    # No linked source is a saved, explicit missing-code result, with no model call.
    service = client.post('/api/services', headers=HEADERS, json={'name': 'External database','service_type':'builtin'}).json()
    monkeypatch.setattr(generation, 'agent', lambda *args, **kwargs: pytest.fail('Missing code must skip the model'))
    value.update(service_id=service['id'],code_project_ids=[],code_graph_hashes={})
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    doc = client.get('/api/documentation/documents', headers=HEADERS, params={'service_id':service['id']}).json()['documents'][0]
    assert doc['purpose'] == phase and doc['description'] == 'Code is not available for this service.'
    usage = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]['token_usage']
    assert usage[0]['requests'] == usage[0]['total_tokens'] == 0


@pytest.mark.parametrize('phase', ['overview', 'messaging'])
def test_phase_does_not_publish_if_source_changes_during_analysis(store, tmp_path, monkeypatch, phase):
    client, original, _ = setup_request(store, tmp_path, monkeypatch)
    value = phase_request(client, original, phase)
    async def run(*args, **kwargs):
        if kwargs['output_type'] is None:
            return SimpleNamespace(to_input_list=lambda: [])
        def changed(*args):
            raise generation.CodeProcessingError('Source changed during analysis.')
        monkeypatch.setattr(generation, 'load_code_graph', changed)
        return SimpleNamespace(final_output=generation.CodeDocumentation(title='Overview',description='',
            markdown='# Overview\n\nThe application handles customer requests and stores validated records. Source evidence is in app/main.py:10. Its API delegates writes to storage and returns a response. This describes the inspected checkout; deployment and live behavior remain unverified.'))
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'failed' and 'Source changed' in row['error'] and row['document_count'] == 0


@pytest.mark.parametrize('phase', ['overview', 'messaging'])
def test_placeholder_output_is_not_saved_as_documentation(store, tmp_path, monkeypatch, phase):
    client, original, _ = setup_request(store, tmp_path, monkeypatch)
    async def placeholder(*args, **kwargs):
        if kwargs['output_type'] is None:
            return SimpleNamespace(to_input_list=lambda: [])
        return SimpleNamespace(final_output={'title':'x','description':'x','markdown':'placeholder'})
    monkeypatch.setattr(generation, 'agent', placeholder)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=phase_request(client, original, phase)).status_code == 202
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'failed' and row['document_count'] == 0


@pytest.mark.parametrize('phase', ['overview', 'messaging'])
def test_phase_finishes_from_collected_evidence_when_analysis_budget_is_reached(store, tmp_path, monkeypatch, phase):
    client, original, _ = setup_request(store, tmp_path, monkeypatch)
    evidence = {'type': 'function_call_output', 'call_id': 'read-1', 'output': 'app/main.py defines the HTTP entry point.'}
    calls = []
    async def run(config, task, **kwargs):
        calls.append(copy.deepcopy(config))
        if len(calls) == 1:
            assert kwargs['max_turns'] == (20 if phase == 'overview' else 40)
            await kwargs['on_usage'](Usage(requests=1, input_tokens=1200, output_tokens=300, total_tokens=1500))
            await kwargs['on_usage'](Usage(requests=1, input_tokens=600, output_tokens=200, total_tokens=800))
            error = generation.MaxTurnsExceeded('limit')
            error.run_data = SimpleNamespace(input=[{'role': 'user', 'content': task}],
                new_items=[SimpleNamespace(to_input_item=lambda: evidence)])
            raise error
        assert len(calls) == 2 and kwargs['max_turns'] == 1
        assert kwargs['history'][-1] == evidence
        assert json.loads(kwargs['history'][0]['content'])['document_kind'] == phase
        assert not config['code']['enabled'] and not config['ssh']['enabled'] and not config['mcp']['enabled']
        assert 'remaining gaps' in task
        assert kwargs['output_type'] is generation.CodeDocumentation
        await kwargs['on_usage'](Usage(requests=1, input_tokens=2000, output_tokens=400, total_tokens=2400))
        return SimpleNamespace(final_output={'title': 'Overview', 'description': 'HTTP service',
            'markdown': '# Application overview\n\nThe application receives HTTP requests through app/main.py. '
                'This overview is based on the source and graph evidence gathered during a bounded analysis. '
                'The complete API surface, database behavior and production deployment still need verification.', 'diagram': None})
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=phase_request(client, original, phase)).status_code == 202
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'succeeded' and row['document_count'] == 1
    assert [(u['step'], u['requests'], u['input_tokens'], u['output_tokens'], u['total_tokens']) for u in row['token_usage']] == [
        ('inspection', 2, 1800, 500, 2300), ('writing', 1, 2000, 400, 2400)]
    assert len(calls) == 2 and not generation.run_once(store)


def test_phase_fixture_is_idempotent_and_customizations_survive(store, tmp_path, monkeypatch, phase):
    client, original, _ = setup_request(store, tmp_path, monkeypatch)
    unit = phase_request(client, original, phase)['units'][0]
    endpoint = '/api/documentation/prompts/' + unit['prompt_id']
    before = client.get(endpoint, headers=HEADERS).json()
    assert before['active_version_id'] and 'code_graph' in before['versions'][0]['content']
    assert client.put(endpoint, headers=HEADERS, json={'name': before['name'], 'input_kind':'code',
        'content':'Our overview instructions', 'revision':before['revision']}).status_code == 200
    customized = client.get(endpoint, headers=HEADERS).json()
    with store.connect() as conn:
        project = store.project(conn,'shop')
        seed = {'overview':'seed_application_overview_prompt','api':'seed_api_documentation_prompt','messaging':'seed_messaging_documentation_prompt'}[phase]
        conn.execute(f'SELECT ka.{seed}(%s)',(project,))
    assert client.get(endpoint, headers=HEADERS).json() == customized
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            conn.execute((Path(__file__).resolve().parents[3] / {'overview':'db/migrations/0053_application_overview.down.sql','api':'db/migrations/0054_api_documentation.down.sql','messaging':'db/migrations/0056_messaging_documentation.down.sql'}[phase]).read_text(), prepare=False)


def test_messaging_prompt_covers_contracts_effects_reliability_and_missing_evidence(store,tmp_path,monkeypatch):
    client,value,_=setup_request(store,tmp_path,monkeypatch)
    unit=phase_request(client,value,'messaging')['units'][0]
    prompt=client.get('/api/documentation/prompts/'+unit['prompt_id'],headers=HEADERS).json()['versions'][0]['content']
    for term in ['NATS Core/JetStream','Consumer inventory','producer inventory','transaction boundaries',
                 'acknowledgements','idempotency','outbox/inbox','Messaging-to-state matrix','no messaging is found',
                 'Do not connect to brokers','code_graph','Coverage and remaining gaps']:
        assert term in prompt


def test_editor_save_of_a_phase_document_keeps_the_phase_folder_and_metadata(store, tmp_path, monkeypatch):
    client, original, calls = setup_request(store, tmp_path, monkeypatch)
    value = phase_request(client, original, 'overview')
    async def run(config, task, **kwargs):
        if kwargs['output_type'] is None:
            return SimpleNamespace(final_output='Evidence: app/main.py:10',
                to_input_list=lambda: [{'role':'assistant','content':'Evidence: app/main.py:10'}])
        return SimpleNamespace(final_output=generation.CodeDocumentation(title='Application overview', description='Purpose and flows',
            markdown='# Overview\n\nEvidence: app/main.py:10. HTTP input changes a stored record. The API receives a customer request, validates the supplied fields, and passes them to the storage component. The stored record becomes available to later requests; current deployment and live status are not verified.',
            diagram=generation.ComponentGraph(nodes=[{'id': 'api', 'label': 'API', 'parent_id': None},
                {'id': 'db', 'label': 'Storage', 'parent_id': None}], edges=[{'source':'api','target':'db','label':'writes'}], explanation='Source: app/main.py:10')))
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS, params={'service_id': value['service_id'], 'kind': 'overview'}).json()['generations'][0]
    artifact = row['result']['documents'][0]
    folder = Path(row['result']['output_folders'][0])
    endpoint = '/api/documentation/documents/' + artifact['id']
    detail = client.get(endpoint, headers=HEADERS).json()
    edited = client.put(endpoint, headers=HEADERS, json={'slug': detail['slug'], 'title': detail['title'], 'markdown': '# Edited overview',
        'revision': detail['revision'], 'code_project_ids': detail['code_project_ids'], 'service_ids': detail['service_ids']})
    assert edited.status_code == 200 and 'export_error' not in edited.json(), edited.text
    assert (folder / 'overview.md').read_text().startswith('# Edited overview')
    assert (folder / 'metadata.json').exists()
    assert not (tmp_path / detail['slug']).exists()
