"""One code project, a chosen prompt and comments, one document saved into a documentation folder."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from knowledge_admin import documentation_generation as generation
from knowledge_admin.code_processing import CodeProcessingError
from knowledge_admin.data_root import FOLDER_RULE
from knowledge_admin.documents import DocumentInput, save_document
from test_documentation_generation import setup_request
from test_repositories import HEADERS

BASE = {'scope': 'code_project', 'model_id': str(uuid4()), 'code_project_id': str(uuid4()), 'prompt_id': str(uuid4()),
        'prompt_version_id': str(uuid4()), 'document_name': ' Payments – API reference ', 'folder': 'team/payments'}


def project_request(store, tmp_path, monkeypatch):
    """The generation fixture's code project with the code analysis prompt, comments, a name and a folder."""
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    prompts = {p['name']: p for p in client.get('/api/documentation/prompts', headers=HEADERS).json()['prompts']}
    prompt = prompts['Code analysis and architecture']
    project = value['code_project_ids'][0]
    request = {'scope': 'code_project', 'model_id': value['model_id'], 'code_project_id': project,
               'prompt_id': prompt['id'], 'prompt_version_id': prompt['active_version_id'],
               'code_graph_hashes': {project: 'fixture'}, 'comments': 'Focus on the public API.',
               'document_name': 'Payments – API reference', 'folder': 'team/payments'}
    return client, request, prompts, value


def post(client, request, **change):
    return client.post('/api/documentation/generations', headers=HEADERS, json={**request, **change})


def test_code_project_input_is_validated():
    value = generation.GenerationInput(**BASE)
    assert (value.document_name, value.folder, value.units, value.comments) == ('Payments – API reference', 'team/payments', [], '')
    for change in ({'folder': '../payments'}, {'folder': '.hidden'}, {'folder': 'a//b'}, {'folder': '/srv/docs'},
                   {'folder': 'team/node_modules'}, {'folder': 'services/payments'}, {'folder': 'my docs'},
                   {'folder': 'x' * 201}, {'folder': None}, {'document_name': '  '}, {'document_name': '– –'},
                   {'document_name': None}, {'document_name': 'x' * 241}, {'comments': 'x' * 20001}, {'comments': 'a\x00b'},
                   {'code_project_id': None}, {'prompt_id': None}, {'prompt_version_id': None},
                   {'service_id': str(uuid4())}, {'environment_id': str(uuid4())}, {'server_ids': [str(uuid4())]},
                   {'code_project_ids': [str(uuid4())]}, {'instructions': 'Use comments instead'},
                   {'workflow_id': str(uuid4())},
                   {'units': [{'kind': 'code', 'prompt_id': str(uuid4()), 'prompt_version_id': str(uuid4())}]}):
        with pytest.raises(ValidationError):
            generation.GenerationInput(**{**BASE, **change})
    unit = {'kind': 'code', 'prompt_id': str(uuid4()), 'prompt_version_id': str(uuid4())}
    service = {'scope': 'service', 'model_id': str(uuid4()), 'service_id': str(uuid4())}
    generation.GenerationInput(**service, units=[unit])
    for change in ({'units': []}, {'units': [unit], 'folder': 'payments'}, {'units': [unit], 'comments': 'x'},
                   {'units': [unit], 'document_name': 'Doc'}, {'units': [unit], 'code_project_id': str(uuid4())}):
        with pytest.raises(ValidationError):
            generation.GenerationInput(**{**service, **change})


def test_code_project_generation_snapshots_the_prompt_comments_and_code_tools_only(store, tmp_path, monkeypatch):
    client, request, prompts, _ = project_request(store, tmp_path, monkeypatch)
    response = post(client, request)
    assert response.status_code == 202, response.text
    detail = client.get('/api/documentation/prompts/' + request['prompt_id'], headers=HEADERS).json()
    active = next(v['content'] for v in detail['versions'] if v['id'] == detail['active_version_id'])
    with store.connect() as conn:
        row = conn.execute('''SELECT mode,instructions,agent_snapshot,prompt_snapshot,source_snapshot
            FROM ka.documentation_generations''').fetchone()
        assert conn.execute('SELECT 1 FROM ka.documentation_workflow_jobs').fetchone() is None
    assert set(row['agent_snapshot']) == {'project'}
    config = row['agent_snapshot']['project']
    assert config['id'] == 'documentation-project' and config['model']['id'] == request['model_id']
    assert config['system_prompt']['content'] == active + '\n\n## Comments for this run\n\nFocus on the public API.'
    assert config['code']['enabled'] and [p['id'] for p in config['code']['projects']] == [request['code_project_id']]
    assert config['code']['projects'][0]['folder_paths'] == ['/work/code/app'] and len(config['code']['graphs']) == 1
    assert config['ssh'] == {'enabled': False, 'servers': []}
    assert not any(config[key]['enabled'] for key in ('documents', 'mcp', 'skills', 'services'))
    assert (row['mode'], row['instructions']) == ('create', 'Focus on the public API.')
    prompt = row['prompt_snapshot'][0]
    assert (prompt['kind'], prompt['id'], prompt['version_id'], prompt['comments'], prompt['overridden']) == (
        'project', request['prompt_id'], request['prompt_version_id'], 'Focus on the public API.', False)
    sources = row['source_snapshot']
    assert sources['scope'] == 'code_project' and sources['code_project_ids'] == [request['code_project_id']]
    assert sources['targets'] == [{'kind': 'project', 'slug': 'payments-api-reference', 'folder': 'team/payments', 'existing': None}]
    history = client.get('/api/documentation/generations', headers=HEADERS, params={'scope': 'code_project'}).json()['generations']
    assert [(h['scope'], h['document_name'], h['folder'], h['status']) for h in history] == [
        ('code_project', 'Payments – API reference', 'team/payments', 'queued')]
    assert client.get('/api/documentation/generations', headers=HEADERS, params={'scope': 'service'}).json()['generations'] == []
    # A second run for the same project waits for the first.
    duplicate = post(client, request, document_name='Other name')
    assert duplicate.status_code == 409
    assert duplicate.json()['error']['message'] == 'Documentation for this code project or document is already queued or running'
    # Without comments the system prompt is the prompt alone.
    with store.connect() as conn:
        conn.execute("UPDATE ka.jobs SET status='succeeded',started_at=now(),finished_at=now() WHERE status='queued'")
    assert post(client, request, comments='', document_name='Second').status_code == 202
    with store.connect() as conn:
        latest = conn.execute('''SELECT g.agent_snapshot FROM ka.documentation_generations g JOIN ka.jobs j ON j.id=g.job_id
            ORDER BY j.created_at DESC LIMIT 1''').fetchone()
    assert latest['agent_snapshot']['project']['system_prompt']['content'] == active


def test_code_project_generation_refuses_wrong_prompts_graphs_names_and_folders(store, tmp_path, monkeypatch):
    client, request, prompts, _ = project_request(store, tmp_path, monkeypatch)
    server = prompts['Deployment and service operations']
    refused = post(client, request, prompt_id=server['id'], prompt_version_id=server['active_version_id'])
    assert refused.status_code == 400
    assert refused.json()['error']['message'] == 'Select an active documentation prompt for code (code or mixed input)'
    stale = post(client, request, prompt_version_id=str(uuid4()))
    assert stale.status_code == 409 and stale.json()['error']['message'] == 'The prompt changed. Reload the prompt before generating.'
    bad = post(client, request, folder='../payments')
    assert bad.status_code == 400 and FOLDER_RULE in bad.json()['error']['message']
    assert post(client, request, code_project_id=str(uuid4())).status_code == 400
    assert client.post('/api/documentation/documents', headers=HEADERS,
                       json={'slug': 'team', 'title': 'Team', 'markdown': '# Team'}).status_code == 201
    shadow = post(client, request)
    assert shadow.status_code == 409
    assert shadow.json()['error']['message'] == 'The folder team holds the export of the document team. Choose another folder.'
    # Nested levels, both directions: document api in folder ops/x vs folder ops/x/api, and the reverse.
    with store.connect() as conn:
        from knowledge_admin.documents import DocumentInput, save_document
        project = conn.execute('SELECT id FROM ka.projects LIMIT 1').fetchone()['id']
        save_document(conn, project, None, DocumentInput(slug='api', title='A', markdown='# A'), folder='ops/x')
    nested = post(client, request, folder='ops/x/api')
    assert nested.status_code == 409 and nested.json()['error']['message'] == (
        'The folder ops/x/api holds the export of the document api in the folder ops/x. Choose another folder.')
    reverse = post(client, request, folder='ops', document_name='X')
    assert reverse.status_code == 409 and reverse.json()['error']['message'] == (
        'The slug x is used by the documentation folder ops/x. Choose another slug.')
    def missing(project, expected_hash=None):
        raise CodeProcessingError('Generate and inspect the code graph in Phase 1 before generating documentation.')
    monkeypatch.setattr(generation, 'load_code_graph', missing)
    no_graph = post(client, request, folder='payments')
    assert no_graph.status_code == 409 and 'Phase 1' in no_graph.json()['error']['message']
    assert not generation.run_once(store)


def test_service_generation_ignores_folder_documents_with_the_same_slug(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    code = next(u for u in value['units'] if u['kind'] == 'code')
    slug = 'application-' + value['service_id'][:8] + '-code'
    with store.connect() as conn:
        save_document(conn, store.project(conn, 'shop'), None,
                      DocumentInput(slug=slug, title='Folder copy', markdown='# Folder copy'), folder='payments')
    response = client.post('/api/documentation/generations', headers=HEADERS,
                           json={**value, 'units': [code], 'environment_id': None, 'server_ids': []})
    assert response.status_code == 202, response.text
    with store.connect() as conn:
        target = conn.execute('SELECT source_snapshot FROM ka.documentation_generations').fetchone()['source_snapshot']['targets'][0]
    assert target['slug'] == slug and target['existing'] is None


def test_code_project_document_names_need_letters_and_are_unique_per_folder(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    for name in ('Документация', '文档', '– –'):
        refused = post(client, request, document_name=name)
        assert refused.status_code == 400
        assert 'Use letters or digits in the document name' in refused.json()['error']['message']
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        save_document(conn, project, None, DocumentInput(slug='payments-api-reference', title='Payments', markdown='# Payments'),
                      folder='team/payments')
    same = post(client, request)
    assert same.status_code == 409 and same.json()['error']['message'] == generation.SAME_DOCUMENT_NAME
    # The same name in another folder is a new document.
    other = post(client, request, folder='payments')
    assert other.status_code == 202, other.text
    with store.connect() as conn:
        conn.execute("UPDATE ka.jobs SET status='succeeded',started_at=now(),finished_at=now() WHERE status='queued'")
    confirmed = post(client, request, update_existing=True)
    assert confirmed.status_code == 202, confirmed.text
    with store.connect() as conn:
        rows = conn.execute("""SELECT g.mode,g.source_snapshot->'targets'->0 AS target FROM ka.documentation_generations g
            JOIN ka.jobs j ON j.id=g.job_id ORDER BY j.created_at""").fetchall()
    assert [(r['mode'], r['target']['folder'], r['target']['existing'] is None) for r in rows] == [
        ('create', 'payments', True), ('update', 'team/payments', False)]
    # A folder-less document named like the folder's first segment is refused at queue time.
    with store.connect() as conn:
        conn.execute("UPDATE ka.jobs SET status='succeeded',started_at=now(),finished_at=now() WHERE status='queued'")
    assert client.post('/api/documentation/documents', headers=HEADERS,
                       json={'slug': 'reference', 'title': 'Reference', 'markdown': '# Reference'}).status_code == 201
    shadow = post(client, request, folder='reference/api')
    assert shadow.status_code == 409
    assert shadow.json()['error']['message'] == 'The folder reference holds the export of the document reference. Choose another folder.'


def history(client):
    return client.get('/api/documentation/generations', headers=HEADERS, params={'scope': 'code_project'}).json()['generations']


def test_code_project_document_is_saved_linked_and_exported_into_its_folder(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    calls = []
    async def run(config, task, **kwargs):
        calls.append((config, json.loads(task), kwargs))
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='Model title', description='API reference',
            markdown='# Payments API\n\n```d2\napi -> db\n```\n'))
    monkeypatch.setattr(generation, 'agent', run)
    assert post(client, request).status_code == 202
    assert generation.run_once(store)
    [(config, task, kwargs)] = calls
    assert kwargs['output_type'] is generation.GeneratedDocument and kwargs['max_turns'] == 60
    assert config['code']['enabled'] and not config['ssh']['enabled'] and not config['mcp']['enabled']
    assert not config['documents']['enabled'] and config['system_prompt']['content'].endswith('Focus on the public API.')
    assert task['document_kind'] == 'project' and task['existing_document'] is None
    assert task['sources']['code_project']['id'] == request['code_project_id']
    assert task['sources']['document_name'] == request['document_name'] and 'comments' not in task['sources']
    [run_row] = history(client)
    assert run_row['status'] == 'succeeded' and run_row['result']['export_error'] is None
    folder = tmp_path / 'team' / 'payments' / 'payments-api-reference'
    assert [Path(p) for p in run_row['result']['output_folders']] == [folder.resolve()]
    assert (folder / 'README.md').read_text().startswith('# Payments API') and (folder / 'architecture-1.d2').is_file()
    assert not (tmp_path / 'payments-api-reference').exists()
    doc_id = run_row['result']['documents'][0]['id']
    document = client.get('/api/documentation/documents/' + doc_id, headers=HEADERS).json()
    assert (document['title'], document['slug'], document['folder'], document['purpose']) == (
        'Payments – API reference', 'payments-api-reference', 'team/payments', 'code')
    assert document['code_project_ids'] == [request['code_project_id']] and document['service_ids'] == []
    assert document['environment_ids'] == [] and document['server_ids'] == []
    assert document['current_version']['origin'] == 'generated'
    with store.connect() as conn:
        recorded = conn.execute('''SELECT g.agent_snapshot,g.prompt_snapshot FROM ka.documentation_generations g
            JOIN ka.document_versions v ON v.generation_id=g.job_id WHERE v.document_id=%s''', (doc_id,)).fetchone()
    assert recorded['agent_snapshot']['project']['model']['id'] == request['model_id']
    assert recorded['prompt_snapshot'][0]['version_id'] == request['prompt_version_id']


def test_same_name_in_the_same_folder_is_refused_or_saved_as_a_new_version(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    async def run(config, task, **kwargs):
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='T', description='',
            markdown='# ' + json.loads(task)['sources']['document_name']))
    monkeypatch.setattr(generation, 'agent', run)
    def generate(**change):
        response = post(client, request, **change)
        if response.status_code == 202:
            assert generation.run_once(store)
        return response
    assert generate().status_code == 202
    refused = generate()
    assert refused.status_code == 409 and refused.json()['error']['message'] == generation.SAME_DOCUMENT_NAME
    assert generate(update_existing=True).status_code == 202
    assert generate(folder='other').status_code == 202
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert sorted((d['folder'], d['slug'], d['revision']) for d in docs) == [
        ('other', 'payments-api-reference', 1), ('team/payments', 'payments-api-reference', 2)]
    assert (tmp_path / 'other/payments-api-reference/README.md').is_file()
    assert (tmp_path / 'team/payments/payments-api-reference/README.md').is_file()
    assert [row['mode'] for row in history(client)] == ['create', 'update', 'create']


def test_code_project_document_is_not_saved_when_the_graph_changes_during_the_run(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    assert post(client, request).status_code == 202
    def changed(project, expected_hash=None):
        raise CodeProcessingError('Source or graph selection changed. Generate and inspect the graph again before continuing.')
    async def run(config, task, **kwargs):
        monkeypatch.setattr(generation, 'load_code_graph', changed)
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='T', description='', markdown='# Doc'))
    monkeypatch.setattr(generation, 'agent', run)
    assert generation.run_once(store)
    [run_row] = history(client)
    assert run_row['status'] == 'failed' and 'Generate and inspect the graph again' in run_row['error']
    assert client.get('/api/documentation/documents', headers=HEADERS).json()['total'] == 0
    assert not (tmp_path / 'team').exists()


def test_confirmed_document_deleted_before_the_run_is_created_again(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    async def run(config, task, **kwargs):
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='T', description='', markdown='# New'))
    monkeypatch.setattr(generation, 'agent', run)
    with store.connect() as conn:
        old = save_document(conn, store.project(conn, 'shop'), None,
                            DocumentInput(slug='payments-api-reference', title='Old', markdown='# Old'), folder='team/payments')
    assert post(client, request, update_existing=True).status_code == 202
    assert client.delete('/api/documentation/documents/' + str(old['id']), headers=HEADERS,
                         params={'revision': 1, 'confirmed': True}).status_code == 200
    assert generation.run_once(store)
    [run_row] = history(client)
    assert run_row['status'] == 'succeeded', run_row['error']
    [doc] = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert (doc['folder'], doc['slug'], doc['revision']) == ('team/payments', 'payments-api-reference', 1)
    assert doc['id'] != str(old['id'])
    assert (tmp_path / 'team/payments/payments-api-reference/README.md').read_text().startswith('# New')


def test_folder_clash_that_appears_after_queuing_fails_the_job_clearly(store, tmp_path, monkeypatch):
    client, request, _, _ = project_request(store, tmp_path, monkeypatch)
    async def run(config, task, **kwargs):
        return SimpleNamespace(final_output=generation.GeneratedDocument(title='T', description='', markdown='# Doc'))
    monkeypatch.setattr(generation, 'agent', run)
    assert post(client, request).status_code == 202
    assert client.post('/api/documentation/documents', headers=HEADERS,
                       json={'slug': 'team', 'title': 'Team', 'markdown': '# Team'}).status_code == 201
    assert generation.run_once(store)
    [run_row] = history(client)
    assert run_row['status'] == 'failed'
    assert run_row['error'] == 'The folder team holds the export of the document team. Choose another folder.'
    docs = client.get('/api/documentation/documents', headers=HEADERS).json()['documents']
    assert [d['slug'] for d in docs] == ['team']
    assert not (tmp_path / 'team/payments').exists()
