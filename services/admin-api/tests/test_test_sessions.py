"""The agent Test tab's sessions: the first message creates a Test session (purpose 'test') with the agent's current
configuration pinned, narrowed to a per-run agent's run resources; the session API serves it like a documentation
session, except that it has no document."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin.sessions.review import NO_MODEL
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


@pytest.fixture
def tester(store, tmp_path, monkeypatch):
    """A native agent with an OpenAI Responses model and no capabilities, as the Test tab tests it."""
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Tester')
    model = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'api_format': 'responses', 'model': 'gpt-5',
        'base_url': 'http://model.test/v1', 'api_key': 'tester-model-secret'}).json()
    with store.connect() as conn:
        conn.execute('''UPDATE ka.agents SET model_id=%s,enabled=true,documents_enabled=false,repositories_enabled=false,
            ssh_enabled=false,skills_enabled=false,mcp_enabled=false,services_enabled=false WHERE id=%s''',
            (model['id'], agent['id']))
    return SimpleNamespace(client=client, store=store, agent=agent['id'], model=model['id'], root=tmp_path.resolve(),
                           path=f"/api/sessions?agent_id={agent['id']}")


def test_start(client, agent_id: str, message: str = 'How many retries?', run_resources=None) -> dict:
    """The body a Test tab sends: everything the agent has on (its tools, experts, subagents), with the given resources."""
    options = client.get(f'/api/sessions/options?agent_id={agent_id}', headers=HEADERS).json()
    return {'agent_id': agent_id, 'message': message, 'tools': [tool['name'] for tool in options['tools']],
            'subagents': options['agent']['subagents_allowed'], 'expert_ids': [e['id'] for e in options['experts']],
            'resources': run_resources or {}}


test_start.__test__ = False  # a helper, not a test


def start(t, message: str = 'How many retries?', **body) -> dict:
    run_resources = body.pop('run_resources', None)
    response = t.client.post('/api/sessions', headers=HEADERS, json={**test_start(t.client, t.agent, message, run_resources), **body})
    assert response.status_code == 201, response.text
    return response.json()


def refused(t, status: int, **body) -> str:
    run_resources = body.pop('run_resources', None)
    response = t.client.post('/api/sessions', headers=HEADERS, json={**test_start(t.client, t.agent, 'Hi', run_resources), **body})
    assert response.status_code == status, response.text
    return response.json()['error']['message']


def test_the_first_message_pins_the_configuration_for_the_whole_session(tester):
    t = tester
    session = start(t, '  How many retries?  ')
    assert (session['purpose'], session['status'], session['title'], session['agent_id']) == (
        'test', 'starting', 'How many retries?', t.agent)
    assert (session['document_name'], session['folder'], session['draft'], session['model_calls'],
            session['reasoning_tokens'], session['run_resources']) == (None, None, None, 0, 0, None)
    assert [(i['seq'], i['text'], i['status']) for i in session['inputs']] == [(1, 'How many retries?', 'queued')]
    assert session['reviews'] == [] and session['config']['id'] == t.agent
    assert session['context_snapshot'] == {'folders': [], 'code_project_ids': []}
    assert session['prompt_snapshot']['system_prompt'].startswith('Instructions for Tester.')
    assert 'tester-model-secret' not in json.dumps(session) and 'history' not in session
    profile = t.client.get(f'/api/agents/{t.agent}', headers=HEADERS).json()
    changed = t.client.put(f'/api/agents/{t.agent}/prompt', headers=HEADERS,
                           json={'revision': profile['revision'], 'system_prompt': 'A changed prompt.'})
    assert changed.status_code == 200, changed.text
    with t.store.connect() as conn:
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s',
                              (session['id'],)).fetchone()['agent_config']
    assert pinned['system_prompt'] == {'content': 'Instructions for Tester.'}  # the session keeps what it started with
    later = start(t, 'x' * 250)
    assert later['config']['system_prompt'] == {'content': 'A changed prompt.'} and later['title'] == 'x' * 120
    listed = t.client.get(t.path, headers=HEADERS)
    assert listed.headers['cache-control'] == 'no-store'
    rows = listed.json()['sessions']
    assert [(row['id'], row['started_by']) for row in rows] == [(later['id'], 'admin@example.test'),
                                                                 (session['id'], 'admin@example.test')]
    assert {'id', 'title', 'status', 'created_at', 'last_activity_at', 'started_by', 'purpose'} <= set(rows[0])
    assert t.client.get('/api/sessions?agent_id=code', headers=HEADERS).json() == {'sessions': []}
    assert t.client.get('/api/sessions?purpose=documents', headers=HEADERS).json() == {'sessions': []}  # documents only


def test_test_sessions_are_refused_with_todays_messages(tester):
    t = tester
    for body in ({'message': ' '}, {'message': 'a\x00b'}, {'message': 'x' * 20001}, {'message': 'Hi', 'title': 'Old'}):
        assert t.client.post('/api/sessions', headers=HEADERS, json={'agent_id': t.agent, **body}).status_code == 400, body
    assert t.client.post('/api/sessions', json={'agent_id': t.agent, 'message': 'Hi'}).status_code == 403
    assert t.client.post('/api/sessions', headers=HEADERS, json={'agent_id': 'missing', 'message': 'Hi'}).status_code == 404
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=NULL WHERE id=%s', (t.agent,))
    assert refused(t, 409) == NO_MODEL
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s,enabled=false WHERE id=%s', (t.model, t.agent))
    assert refused(t, 409) == 'Agent is disabled.'
    with t.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0


@pytest.fixture
def analyst(tester):
    """The tester as a per-run agent that may be given any code project: App and Worker."""
    t = tester
    app, worker = uuid4(), uuid4()
    with t.store.connect() as conn:
        project = t.store.project(conn, 'shop')
        for project_id, slug in ((app, 'app'), (worker, 'worker')):
            (t.root / slug).mkdir()
            conn.execute('INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)',
                         (project_id, project, slug, slug.title()))
            conn.execute('INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                         (project_id, project, slug))
        conn.execute("UPDATE ka.agents SET resources='per_run',repositories_enabled=true,all_repositories=true WHERE id=%s",
                     (t.agent,))
    t.app, t.worker = str(app), str(worker)
    return t


def test_a_per_run_agent_pins_exactly_the_resources_of_its_first_message(analyst):
    a = analyst
    session = start(a, run_resources={'code_project_ids': [a.app]})
    expected = {'code_project_ids': [a.app], 'collection_ids': [], 'server_ids': [], 'service_ids': []}
    assert session['run_resources'] == expected and session['config']['run_resources'] == expected
    assert [p['id'] for p in session['config']['code']['projects']] == [a.app]
    nothing = start(a)  # nothing passed: nothing, never all
    assert nothing['config']['code']['projects'] == [] and nothing['run_resources']['code_project_ids'] == []
    assert refused(a, 400, run_resources={'server_ids': [str(uuid4())]}) == 'This agent does not use servers.'
    with a.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET all_repositories=false WHERE id=%s', (a.agent,))
        conn.execute('INSERT INTO ka.agent_repositories(agent_id,repository_id) VALUES (%s,%s)', (a.agent, a.app))
    assert refused(a, 400, run_resources={'code_project_ids': [a.worker]}) == \
        'Code project Worker is not allowed for this agent.'
    with a.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    assert start(a)['run_resources'] is None


def test_the_session_endpoints_serve_test_sessions(tester):
    t = tester
    session = start(t)
    url = f"/api/sessions/{session['id']}"
    sent = t.client.post(url + '/messages', headers=HEADERS, json={'text': 'And timeouts?'})
    assert sent.status_code == 202 and sent.json() == {'seq': 2, 'status': 'queued', 'working': False}
    assert t.client.post(url + '/stop', headers=HEADERS).status_code == 409  # not working
    detail = t.client.get(url, headers=HEADERS).json()
    assert {'purpose', 'model_calls', 'reasoning_tokens', 'reviews', 'config'} <= set(detail)
    assert t.client.get(url + '/events/history', headers=HEADERS).json() == {'events': [], 'has_more': False}
    assert t.client.post(url + '/close', headers=HEADERS).status_code == 200
    assert t.client.post(url + '/messages', headers=HEADERS, json={'text': 'More'}).status_code == 409


def test_the_test_tab_offers_what_a_run_may_be_given(analyst):
    a = analyst
    offered = a.client.get(f'/api/agents/{a.agent}/run-resources', headers=HEADERS)
    assert offered.status_code == 200 and offered.headers['cache-control'] == 'no-store'
    assert offered.json() == {'resources': 'per_run', 'code_projects': [{'id': a.app, 'name': 'App'},
                              {'id': a.worker, 'name': 'Worker'}], 'collections': None, 'servers': None, 'services': None}
    with a.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    assert a.client.get(f'/api/agents/{a.agent}/run-resources', headers=HEADERS).json() == {'resources': 'fixed'}
    assert a.client.get('/api/agents/missing/run-resources', headers=HEADERS).status_code == 404

