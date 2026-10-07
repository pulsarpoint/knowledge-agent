"""Test chats of a per-run agent: the first message passes the run resources, the conversation pins the narrowed
configuration and records them, and follow-ups cannot change them."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.agent_sessions import KEEPS_RESOURCES
from knowledge_admin.knowledge_agents.run_resources import FIXED
from test_agent_runs import direct, events  # noqa: F401  (fixture)
from test_sdk_agent import responses_api  # noqa: F401  (fixture)
from test_repositories import HEADERS


@pytest.fixture
def analyst(direct, store, tmp_path):
    """The direct test agent as a per-run agent that may be given any code project: App and Worker."""
    client, agent_id, path = direct
    app, worker = uuid4(), uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for project_id, slug in ((app, 'app'), (worker, 'worker')):
            (tmp_path / slug).mkdir()
            conn.execute('INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)',
                         (project_id, project, slug, slug.title()))
            conn.execute('INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,%s)',
                         (project_id, project, slug))
        conn.execute("UPDATE ka.agents SET resources='per_run',repositories_enabled=true,all_repositories=true WHERE id=%s",
                     (agent_id,))
    return SimpleNamespace(client=client, agent=agent_id, path=path, app=str(app), worker=str(worker), root=tmp_path.resolve())


def test_a_test_chat_runs_with_exactly_the_resources_it_started_with(analyst, store, responses_api):
    a = analyst
    first = a.client.post(a.path, headers=HEADERS, json={'task': 'Where are the limits?',
                                                          'resources': {'code_project_ids': [a.app]}})
    started, *_, completed = events(first)
    assert completed['status'] == 'completed', completed
    config = completed['context']['config']
    assert [p['id'] for p in config['code']['projects']] == [a.app]
    assert config['run_resources'] == {'code_project_ids': [a.app], 'collection_ids': [], 'server_ids': [], 'service_ids': []}
    prompt = json.dumps(responses_api[1][-1]['body'])
    assert f'{a.root}/app' in prompt and f'{a.root}/worker' not in prompt
    session = started['session_id']
    with store.connect() as conn:
        assert conn.execute('SELECT run_resources FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()[
            'run_resources'] == config['run_resources']
        assert conn.execute('SELECT run_resources FROM ka.operations_runs WHERE id=%s', (completed['id'],)).fetchone()[
            'run_resources'] == config['run_resources']
    follow = events(a.client.post(a.path, headers=HEADERS, json={'task': 'And the callers?', 'session_id': session}))[-1]
    assert follow['context']['config']['run_resources']['code_project_ids'] == [a.app]
    changed = a.client.post(a.path, headers=HEADERS, json={'task': 'Now the worker', 'session_id': session,
                                                           'resources': {'code_project_ids': [a.worker]}})
    assert changed.status_code == 409 and changed.json()['error']['message'] == KEEPS_RESOURCES
    detail = a.client.get(f'/api/agents/{a.agent}/sessions/{session}', headers=HEADERS).json()
    assert detail['run_resources']['code_project_ids'] == [a.app]


def test_test_chats_refuse_resources_a_per_run_agent_may_not_get_and_fixed_agents_any(analyst, store):
    a = analyst
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET all_repositories=false WHERE id=%s', (a.agent,))
        conn.execute('INSERT INTO ka.agent_repositories(agent_id,repository_id) VALUES (%s,%s)', (a.agent, a.app))
    refused = a.client.post(a.path, headers=HEADERS, json={'task': 'x', 'resources': {'code_project_ids': [a.worker]}})
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Code project Worker is not allowed for this agent.'
    created = a.client.post(f'/api/agents/{a.agent}/sessions', headers=HEADERS, json={
        'title': 'Check', 'is_test': True, 'resources': {'server_ids': [str(uuid4())]}})
    assert created.status_code == 400 and created.json()['error']['message'] == 'This agent does not use servers.'
    nothing = events(a.client.post(a.path, headers=HEADERS, json={'task': 'What can you read?'}))[-1]
    assert nothing['status'] == 'completed'
    assert nothing['context']['config']['code']['projects'] == []  # nothing passed: nothing, never all
    assert nothing['context']['config']['run_resources']['code_project_ids'] == []
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    fixed = a.client.post(a.path, headers=HEADERS, json={'task': 'x', 'resources': {}})
    assert fixed.status_code == 400 and fixed.json()['error']['message'] == FIXED
    run = events(a.client.post(a.path, headers=HEADERS, json={'task': 'Fixed run'}))[-1]
    with store.connect() as conn:
        assert conn.execute('SELECT run_resources FROM ka.operations_runs WHERE id=%s', (run['id'],)).fetchone()[
            'run_resources'] is None


def test_the_test_tab_offers_what_a_run_may_be_given(analyst, store):
    a = analyst
    offered = a.client.get(f'/api/agents/{a.agent}/run-resources', headers=HEADERS)
    assert offered.status_code == 200 and offered.headers['cache-control'] == 'no-store'
    assert offered.json() == {'resources': 'per_run', 'code_projects': [{'id': a.app, 'name': 'App'},
                              {'id': a.worker, 'name': 'Worker'}], 'collections': None, 'servers': None, 'services': None}
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    assert a.client.get(f'/api/agents/{a.agent}/run-resources', headers=HEADERS).json() == {'resources': 'fixed'}
    assert a.client.get('/api/agents/missing/run-resources', headers=HEADERS).status_code == 404


def test_a_suggestion_from_a_per_run_chat_drafts_the_whole_configuration(analyst, store):
    a = analyst
    session = events(a.client.post(a.path, headers=HEADERS, json={
        'task': 'Where are the limits?', 'resources': {'code_project_ids': [a.app]}}))[0]['session_id']
    with store.connect() as conn:
        row = conn.execute('SELECT actor_id,config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        review = conn.execute('''INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion)
            VALUES (%s,%s,'Shorter','{}','Be shorter.') RETURNING id''', (session, row['actor_id'])).fetchone()['id']
        conn.execute('UPDATE ka.agent_session_reviews SET context=%s WHERE id=%s', (Jsonb({
            'agent_config': row['config'], 'changes': {'system_prompt': None, 'instructions': 'Be brief.',
                                                       'server_instructions': []}}), review))
    source = a.client.get(f'/api/agents/{a.agent}/versions/source', headers=HEADERS, params={'review_id': str(review)}).json()
    assert 'run_resources' not in source['config']
    assert source['config']['code']['all_projects'] is True  # the agent's own selection, not one run's
    assert source['changes']['instructions'] == 'Be brief.'
