import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import MaxTurnsExceeded, ModelBehaviorError
from agents.usage import Usage

from knowledge_admin import documentation_generation as generation
from knowledge_admin.api_evidence import api_batches
from knowledge_admin.code_processing import CodeProcessingError
from test_code_documentation_phases import phase_request
from test_documentation_generation import setup_request
from test_repositories import HEADERS


def graph_fixture(tmp_path, project_id='project'):
    root = tmp_path / 'code'
    source = root / 'app'
    source.mkdir(parents=True)
    (source / 'routes.py').write_text('from handlers import get_user, save_user\n' +
        'router = Router(prefix="/v1")\n' +
        'router.get("/user", get_user)\nrouter.post("/user", save_user)\nrouter.get("/admin", get_user)\n')
    (source / 'handlers.py').write_text('def get_user():\n    return load_user()\n\ndef save_user():\n    write_user()\n\ndef load_user():\n    return "record"\n\ndef write_user():\n    pass\n')
    (source / 'unrelated.py').write_text('UNRELATED_CONTENT_MUST_NOT_ENTER_PROMPT\n')
    database = tmp_path / 'graph.sqlite'
    with sqlite3.connect(database) as conn:
        conn.executescript('CREATE TABLE nodes(id TEXT,kind TEXT,name TEXT,file_path TEXT,start_line INT,end_line INT);'
            'CREATE TABLE edges(id INTEGER PRIMARY KEY,source TEXT,target TEXT,kind TEXT);')
        conn.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?)', [
            ('route1', 'route', 'GET /user', 'app/routes.py', 3, 3),
            ('route2', 'route', 'POST /user', 'app/routes.py', 4, 4),
            ('route3', 'route', 'GET /admin', 'app/routes.py', 5, 5),
            ('get', 'function', 'get_user', 'app/handlers.py', 1, 2),
            ('save', 'function', 'save_user', 'app/handlers.py', 4, 5),
            ('load', 'function', 'load_user', 'app/handlers.py', 7, 8),
            ('write', 'function', 'write_user', 'app/handlers.py', 10, 11),
            ('noise', 'constant', 'unused', 'app/unrelated.py', 1, 1)])
        conn.executemany('INSERT INTO edges(source,target,kind) VALUES (?,?,?)', [('get', 'load', 'calls'), ('save', 'write', 'calls'), ('load', 'get', 'calls')])
    return {'id': project_id, 'name': 'App code', 'source_hash': 'fixture', 'source_file_count': 3,
        'code_root': str(root), 'folder_paths': [str(source)], 'graph_path': str(database),
        'parse_error_files': [], 'missing_folders': [], 'unresolved_references': 1, 'excluded': {}}


def test_graph_selects_complete_related_source_without_unrelated_files(tmp_path):
    graph = graph_fixture(tmp_path)
    batches, coverage = api_batches([graph])
    assert [b['routes'][0]['id'] for b in batches] == ['route1', 'route2', 'route3']
    assert all(len(b['routes']) == 1 for b in batches)
    assert coverage[0]['route_count'] == 3
    assert all(b['path_base'] == graph['code_root'] for b in batches)
    assert 'return load_user' in json.dumps(batches)
    assert 'return \"record\"' in '\n'.join(e['source'] for e in batches[0]['excerpts'])
    assert 'UNRELATED_CONTENT' not in json.dumps(batches)
    assert len({(e['path'], e['start_line'], e['end_line']) for e in batches[0]['excerpts']}) == len(batches[0]['excerpts'])
    assert batches == api_batches([graph])[0]


def test_graph_routes_cannot_point_outside_selected_folders(tmp_path):
    graph = graph_fixture(tmp_path)
    outside = tmp_path / 'outside.py'
    outside.write_text('SECRET_OUTSIDE_SELECTED_CODE')
    path = Path(graph['folder_paths'][0]) / 'routes.py'
    path.unlink(); path.symlink_to(outside)
    with pytest.raises(CodeProcessingError, match='outside'):
        api_batches([graph])


def api_request(store, tmp_path, monkeypatch):
    client, value, _ = setup_request(store, tmp_path, monkeypatch)
    graph = graph_fixture(tmp_path, value['code_project_ids'][0])
    monkeypatch.setenv('CODE_ROOT', graph['code_root'])
    monkeypatch.setattr(generation, 'load_code_graph', lambda *_: graph)
    return client, phase_request(client, value, 'api'), graph


def section_result(task):
    route = json.loads(task)['evidence']['routes'][0]
    return SimpleNamespace(final_output=f'''# {route['name']}

## Contract

The route is mounted under /v1. The source declares no request parameters.
Registration: {route['file_path']}:{route['start_line']}.

## Authentication and middleware

No authorization middleware is attached in the inspected routes.py registration.

## Processing flow

The registration selects a handler in app/handlers.py, which delegates to its storage function.

## State and side effects

The fixture has no database definition; storage semantics remain unresolved.

## Responses and examples

A read returns "record"; the write handler has no explicit return value. The Router implementation
is unavailable, so HTTP status and serialization are unknown. No endpoints were executed.

## Evidence and gaps

Inspected app/handlers.py:1-11. The external Router implementation is not in the selected source.
''')



def test_api_saves_partial_sections_retries_and_exports_document(store, tmp_path, monkeypatch):
    client, value, _ = api_request(store, tmp_path, monkeypatch)
    calls = []
    async def run(config, task, **kwargs):
        calls.append(task)
        assert 'history' not in kwargs and kwargs.get('output_type') is None and 'max_output_tokens' not in kwargs
        assert config['code']['enabled'] and not config['ssh']['enabled']
        assert 'shell_run' in config['instructions']
        assert 'excerpts' not in json.loads(task)['evidence']
        assert len(task) < 12000 and 'UNRELATED_CONTENT' not in task
        await kwargs['on_usage'](Usage(requests=1, input_tokens=1500, output_tokens=500, total_tokens=2000))
        if len(calls) == 2:
            raise ModelBehaviorError('failure SECRET_PROVIDER_BODY')
        return section_result(task)
    monkeypatch.setattr(generation, 'agent', run)
    first = client.post('/api/documentation/generations', headers=HEADERS, json=value).json()['job_id']
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'failed' and 'endpoint 2/3' in row['error'] and 'SECRET_PROVIDER_BODY' not in json.dumps(row)
    inspection = client.get(f'/api/documentation/generations/{first}/api-analysis', headers=HEADERS).json()
    assert len(inspection['inventory']['batches']) == 3 and len(inspection['sections']) == 1
    assert 'run_token_limit' not in inspection['inventory']
    assert 'excerpts' not in inspection['inventory']['batches'][0]
    second = client.post('/api/documentation/generations', headers=HEADERS, json=value).json()['job_id']
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'succeeded', row['error']
    assert len(calls) == 4  # Only the unfinished endpoints were regenerated.
    assert row['token_usage'][0]['total_tokens'] == 4000
    inspection = client.get(f'/api/documentation/generations/{second}/api-analysis', headers=HEADERS).json()
    assert inspection['sections'][0]['reused_from'] == first
    exported = Path(row['result']['output_folders'][0]) / 'api.md'
    assert 'GET /admin' in exported.read_text() and 'POST /user' in exported.read_text()
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store) and len(calls) == 7


@pytest.mark.parametrize('failure', ['empty_document', 'source_changed', 'timeout', 'max_turns'])
def test_api_rejects_empty_documents_stale_source_and_timeouts(store, tmp_path, monkeypatch, failure):
    client, value, _ = api_request(store, tmp_path, monkeypatch)
    async def run(config, task, **kwargs):
        result = section_result(task)
        if failure == 'empty_document':
            result.final_output = ''
        elif failure == 'timeout':
            raise TimeoutError()
        elif failure == 'max_turns':
            raise MaxTurnsExceeded('provider details must not be exposed')
        else:
            def changed(*_):
                raise CodeProcessingError('Source changed during analysis.')
            monkeypatch.setattr(generation, 'load_code_graph', changed)
        return result
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'failed' and row['document_count'] == 0
    assert {'empty_document': 'did not finish', 'source_changed': 'Source changed', 'timeout': 'timed out', 'max_turns': 'stopped before completing'}[failure] in row['error']


def test_api_without_routes_records_coverage_without_calling_model(store, tmp_path, monkeypatch):
    client, value, graph = api_request(store, tmp_path, monkeypatch)
    with sqlite3.connect(graph['graph_path']) as conn:
        conn.execute("DELETE FROM nodes WHERE kind='route'")
    async def no_model(*args, **kwargs):
        pytest.fail('No graph routes should mean no paid model call')
    monkeypatch.setattr(generation, 'agent', no_model)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'succeeded' and row['token_usage'][0]['requests'] == 0
    assert 'does not mean' in (Path(row['result']['output_folders'][0]) / 'api.md').read_text()


@pytest.mark.parametrize('output', [
    'wip', 'pending', 'reading handler next', 'placeholder', 'noop',
    '{"title":"notes","description":"scratch","markdown":"wip"}',
    {'title': 'notes', 'description': 'scratch', 'markdown': 'wip'},
])
def test_api_rejects_observed_progress_outputs(tmp_path, monkeypatch, output):
    batch = api_batches([graph_fixture(tmp_path)])[0][0]
    async def run(*args, **kwargs):
        return SimpleNamespace(final_output=output)
    monkeypatch.setattr(generation, 'agent', run)
    with pytest.raises(CodeProcessingError, match='Markdown endpoint contract'):
        asyncio.run(generation.generate_api_endpoint({'instructions': ''}, {}, '', batch, {}, None))


@pytest.mark.parametrize('change', ['empty_section', 'placeholder_section', 'wrong_endpoint', 'missing_citation', 'invalid_line', 'fenced_outline'])
def test_api_rejects_incomplete_or_unattributed_contract(tmp_path, monkeypatch, change):
    batch = api_batches([graph_fixture(tmp_path)])[0][0]
    async def run(config, task, **kwargs):
        result = section_result(task)
        if change == 'empty_section':
            result.final_output = result.final_output.split('## Evidence and gaps')[0] + '## Evidence and gaps\n'
        elif change == 'placeholder_section':
            result.final_output = result.final_output.split('## Evidence and gaps')[0] + '## Evidence and gaps\n\npending'
        elif change == 'wrong_endpoint':
            result.final_output = result.final_output.replace('GET /user', 'POST /unrelated')
        elif change == 'missing_citation':
            result.final_output = result.final_output.replace('app/routes.py:3', 'app/handlers.py:3')
        elif change == 'invalid_line':
            result.final_output = result.final_output.replace('app/routes.py:3', 'app/routes.py:1-99999')
        else:
            result.final_output = '# Plan\n\n```markdown\n' + result.final_output + '\n```\n'
        return result
    monkeypatch.setattr(generation, 'agent', run)
    with pytest.raises(CodeProcessingError):
        asyncio.run(generation.generate_api_endpoint({'instructions': ''}, {}, '', batch, {}, None))


def test_api_progress_note_fails_before_saving_or_processing_more_endpoints(store, tmp_path, monkeypatch):
    client, value, _ = api_request(store, tmp_path, monkeypatch)
    calls = []
    async def run(config, task, **kwargs):
        calls.append(task)
        await kwargs['on_usage'](Usage(requests=1, input_tokens=100, output_tokens=10, total_tokens=110))
        return SimpleNamespace(final_output='{"title":"notes","description":"scratch","markdown":"wip"}')
    monkeypatch.setattr(generation, 'agent', run)
    job_id = client.post('/api/documentation/generations', headers=HEADERS, json=value).json()['job_id']
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'failed' and row['document_count'] == 0 and len(calls) == 1
    assert row['token_usage'][0]['total_tokens'] == 110
    inspection = client.get(f'/api/documentation/generations/{job_id}/api-analysis', headers=HEADERS).json()
    assert inspection['sections'] == []


def test_api_keeps_tools_until_natural_completion(store, tmp_path, monkeypatch):
    client, value, graph = api_request(store, tmp_path, monkeypatch)
    with sqlite3.connect(graph['graph_path']) as conn:
        conn.execute("DELETE FROM nodes WHERE kind='route' AND id!='route1'")
    tasks = []
    async def run(config, task, **kwargs):
        tasks.append(task)
        assert config['code']['enabled']
        assert kwargs['max_turns'] is None
        assert kwargs['timeout_seconds'] == 3600
        assert kwargs.get('output_type') is None and kwargs.get('history') is None
        with store.connect() as conn:
            conn.execute("UPDATE ka.jobs SET lease_expires_at=now()+interval '1 minute' WHERE status='running'")
        await kwargs['on_usage'](Usage(requests=42, input_tokens=100, output_tokens=10, total_tokens=110))
        with store.connect() as conn:
            assert conn.execute("SELECT lease_expires_at>now()+interval '80 minutes' AS renewed FROM ka.jobs WHERE status='running'").fetchone()['renewed']
        return section_result(task)
    monkeypatch.setattr(generation, 'agent', run)
    job_id = client.post('/api/documentation/generations', headers=HEADERS, json=value).json()['job_id']
    assert generation.run_once(store)
    row = client.get('/api/documentation/generations', headers=HEADERS).json()['generations'][0]
    assert row['status'] == 'succeeded', row['error']
    assert row['token_usage'][0]['total_tokens'] == 110 and len(tasks) == 1
    sections = client.get(f'/api/documentation/generations/{job_id}/api-analysis', headers=HEADERS).json()['sections']
    assert len(sections) == 1 and '40-call limit' not in sections[0]['markdown']


@pytest.mark.parametrize('change', ['prompt', 'source', 'model'])
def test_api_retry_invalidates_saved_sections_after_configuration_changes(store, tmp_path, monkeypatch, change):
    client, value, graph = api_request(store, tmp_path, monkeypatch)
    calls = []
    async def run(config, task, **kwargs):
        calls.append(task)
        if len(calls) == 2:
            raise ModelBehaviorError('invalid')
        return section_result(task)
    monkeypatch.setattr(generation, 'agent', run)
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    if change == 'prompt':
        value['units'][0]['prompt_override'] = 'Also explain validations.'
    elif change == 'source':
        graph['source_hash'] = 'changed'
        value['code_graph_hashes'][graph['id']] = 'changed'
    else:
        with store.connect() as conn:
            conn.execute("UPDATE ka.llm_models SET model='different-model',revision=revision+1 WHERE id=%s", (value['model_id'],))
    assert client.post('/api/documentation/generations', headers=HEADERS, json=value).status_code == 202
    assert generation.run_once(store)
    assert len(calls) == 5


def test_api_migration_preserves_custom_prompts_and_upgrades_only_unchanged_drafts(store, tmp_path, monkeypatch):
    client, value, _ = api_request(store, tmp_path, monkeypatch)
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with store.connect() as conn:
        conn.execute((migrations / '0058_graph_directed_api_documentation.down.sql').read_text(), prepare=False)
        project_id = conn.execute('SELECT project_id FROM ka.services WHERE id=%s', (value['service_id'],)).fetchone()['project_id']
        old = conn.execute('SELECT v.content FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id WHERE p.id=%s',
            (value['units'][0]['prompt_id'],)).fetchone()['content']
        from psycopg.types.json import Jsonb
        ids = []
        for content in (old, 'Custom saved draft'):
            ids.append(conn.execute("INSERT INTO ka.documentation_workflows(project_id,name,scope,service_id,drafts) VALUES (%s,'API','service',%s,%s) RETURNING id",
                (project_id, value['service_id'], Jsonb({'api': {'content': content, 'instructions': 'Keep this note'}}))).fetchone()['id'])
        conn.execute((migrations / '0058_graph_directed_api_documentation.up.sql').read_text(), prepare=False)
        updated = conn.execute('SELECT drafts FROM ka.documentation_workflows WHERE id=%s', (ids[0],)).fetchone()['drafts']['api']
        assert 'selectively inspecting' in updated['content'] and updated['instructions'] == 'Keep this note'
        assert conn.execute('SELECT drafts FROM ka.documentation_workflows WHERE id=%s', (ids[1],)).fetchone()['drafts']['api']['content'] == 'Custom saved draft'
        conn.execute((migrations / '0058_graph_directed_api_documentation.down.sql').read_text(), prepare=False)
    url = '/api/documentation/prompts/' + value['units'][0]['prompt_id']
    original = client.get(url, headers=HEADERS).json()
    assert client.put(url, headers=HEADERS, json={'name': original['name'], 'input_kind': 'code',
        'content': 'Custom API instructions to preserve', 'revision': original['revision']}).status_code == 200
    before = client.get(url, headers=HEADERS).json()
    with store.connect() as conn:
        conn.execute((migrations / '0058_graph_directed_api_documentation.up.sql').read_text(), prepare=False)
    assert client.get(url, headers=HEADERS).json() == before


def test_related_source_is_not_cut_at_old_character_or_hop_budgets(tmp_path):
    graph = graph_fixture(tmp_path)
    path = Path(graph['folder_paths'][0]) / 'handlers.py'
    path.write_text('def get_user():\n' + '    # Complete related source\n' * 5000 + '    return load_user()\n')
    with sqlite3.connect(graph['graph_path']) as conn:
        conn.execute("UPDATE nodes SET end_line=5002 WHERE id='get'")
    batches, _ = api_batches([graph])
    handler = next(e for e in batches[0]['excerpts'] if e['symbol'] == 'get_user')
    assert handler['end_line'] == 5002 and handler['source'].endswith('5002:     return load_user()')
    assert len(handler['source']) > 48000


def test_route_inside_router_function_does_not_expand_whole_entry_point(tmp_path):
    graph = graph_fixture(tmp_path)
    with sqlite3.connect(graph['graph_path']) as conn:
        conn.execute("INSERT INTO nodes VALUES ('bootstrap','function','start_app','app/routes.py',1,5)")
        conn.execute("INSERT INTO edges(source,target,kind) VALUES ('bootstrap','noise','calls')")
    batches, _ = api_batches([graph])
    assert not any(e['symbol'] == 'start_app' for e in batches[0]['excerpts'])
    assert 'UNRELATED_CONTENT' not in json.dumps(batches)


def test_resolves_missing_call_edges_and_follows_more_than_two_hops(tmp_path):
    graph = graph_fixture(tmp_path)
    path = Path(graph['folder_paths'][0]) / 'handlers.py'
    path.write_text(path.read_text() + '\ndef validate_user():\n    return persist_user()\n\ndef persist_user():\n    return "third-hop database operation"\n')
    with sqlite3.connect(graph['graph_path']) as conn:
        conn.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?)', [
            ('validate', 'function', 'validate_user', 'app/handlers.py', 13, 14),
            ('persist', 'function', 'persist_user', 'app/handlers.py', 16, 17)])
        conn.execute("INSERT INTO edges(source,target,kind) VALUES ('load','validate','calls')")
        # validate -> persist has no graph edge, so resolve its unique called symbol in source.
    batches, _ = api_batches([graph])
    assert 'third-hop database operation' in json.dumps(batches[0])
