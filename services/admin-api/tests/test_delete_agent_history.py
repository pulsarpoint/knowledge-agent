"""An agent with sessions or runs is deleted together with its history only when asked; nothing else goes with it."""

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from live_documents import live_document
from session_rows import add_review, add_chat_session
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS

FULL = {'conversations': 2, 'documentation_sessions': 1, 'run_records': 2}
NONE = {'conversations': 0, 'documentation_sessions': 0, 'run_records': 0}
WORKING = (
    'This agent is working right now. Stop its documentation sessions and runs, '
    'or wait for them to finish, then delete it.'
)


def add_history(conn, agent_id: str, model_id: str) -> dict:
    """Give an agent one of every kind of history the delete has to remove, and return the ids."""
    project = conn.execute('SELECT id FROM ka.projects WHERE archived_at IS NULL LIMIT 1').fetchone()['id']
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    ids = {'project': project, 'actor': actor}
    ids['tests'] = [
        add_chat_session(conn, project, actor, agent_id, model_id, message='Check disk', status='waiting', turns=1)
        for _ in range(2)
    ]
    ids['review'] = add_review(conn, ids['tests'][0], actor, changes={})
    document = live_document(conn, project, f'payments-{uuid4().hex[:6]}', 'Payments', '# Payments', actor=actor)['id']
    ids['document'] = document
    cc = uuid4()
    conn.execute(
        """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,document_name,turns,document_id,status,agent_config) VALUES (%s,%s,%s,%s,%s,'Payments','{}','{}',
        'Payments',2,%s,'waiting',%s)""",
        (cc, project, agent_id, actor, model_id, document, Jsonb({'id': agent_id})),
    )
    conn.execute(
        "INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Write it')", (cc, actor)
    )
    ids['cc'] = cc
    old = [uuid4(), uuid4()]  # Test chat turns from before 0081: only their run records are left
    keys = [f'run:{old[0]}', f'run:{old[1]}', f'cc:{cc}:1', f'cc:{cc}:2', f'cc:{ids["tests"][0]}:1']
    for key in keys:
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'start',%s,'{}')""",
            (project, key, agent_id),
        )
    ids['keys'] = keys
    return ids


@pytest.fixture
def agents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'GPT',
            'provider': 'openai',
            'model': 'gpt-5',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()['id']
    target, other = create(client, 'Target agent'), create(client, 'Other agent')
    with store.connect() as conn:
        mine = add_history(conn, target['id'], model)
        theirs = add_history(conn, other['id'], model)
    return client, target['id'], other['id'], mine, theirs


def delete(client, agent_id, **body):
    revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    return client.request(
        'DELETE', f'/api/agents/{agent_id}', headers=HEADERS, json={'revision': revision, 'confirmed': True, **body}
    )


def test_history_counts_what_a_delete_would_remove(agents):
    client, target, _, _, _ = agents
    history = client.get(f'/api/agents/{target}/history', headers=HEADERS)
    assert history.status_code == 200, history.text
    assert history.json() == FULL
    assert client.get(f'/api/agents/{target}/history').status_code == 403
    assert client.get('/api/agents/missing/history', headers=HEADERS).status_code == 404


def test_event_sessions_count_as_conversations(agents, store):
    client, target, _, mine, _ = agents
    with store.connect() as conn:
        add_chat_session(
            conn,
            mine['project'],
            mine['actor'],
            target,
            conn.execute('SELECT model_id FROM ka.agent_sessions WHERE id=%s', (mine['tests'][0],)).fetchone()[
                'model_id'
            ],
            message='Triage',
            status='waiting',
            turns=1,
            purpose='event',
        )
    assert client.get(f'/api/agents/{target}/history', headers=HEADERS).json() == {**FULL, 'conversations': 3}


def test_an_agent_without_history_has_none(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Fresh agent')
    assert client.get(f'/api/agents/{agent["id"]}/history', headers=HEADERS).json() == NONE


def test_delete_without_history_flag_is_refused(agents, store):
    client, target, _, _, _ = agents
    refused = delete(client, target)
    assert refused.status_code == 409
    assert refused.json()['error']['message'] == (
        'This agent has sessions or run history. Delete it with its history, or disable it to keep the history.'
    )
    assert client.get(f'/api/agents/{target}', headers=HEADERS).status_code == 200


def test_delete_with_history_removes_only_this_agents_history(agents, store):
    client, target, other, mine, theirs = agents
    deleted = delete(client, target, with_history=True)
    assert deleted.status_code == 204, deleted.text
    assert client.get(f'/api/agents/{target}', headers=HEADERS).status_code == 404
    with store.connect() as conn:

        def count(sql, *args):
            return conn.execute(f'SELECT count(*) AS n FROM {sql}', args).fetchone()['n']

        for agent_id, expected in ((target, 0), (other, 1)):
            ids = mine if agent_id == target else theirs
            assert count("ka.agent_sessions WHERE agent_id=%s AND purpose='chat'", agent_id) == 2 * expected
            assert count('ka.agent_session_reviews WHERE id=%s', ids['review']) == expected
            assert count("ka.agent_sessions WHERE agent_id=%s AND purpose='documents'", agent_id) == expected
            assert count('ka.agent_session_inputs WHERE session_id=ANY(%s)', [ids['cc'], *ids['tests']]) == 3 * expected
            assert count('ka.agent_run_events WHERE run_key=ANY(%s)', ids['keys']) == 5 * expected
            # Saved documents stay.
            assert count('ka.documents WHERE id=%s', ids['document']) == 1
        audit = conn.execute("""SELECT details FROM ka.audit_events WHERE action='agent.deleted'
            ORDER BY created_at DESC LIMIT 1""").fetchone()['details']
    assert audit['agent_id'] == target
    assert audit['history'] == FULL
    assert client.get(f'/api/agents/{other}/history', headers=HEADERS).json()['run_records'] == 2


def test_delete_with_history_still_checks_the_revision(agents):
    client, target, _, _, _ = agents
    stale = client.request(
        'DELETE',
        f'/api/agents/{target}',
        headers=HEADERS,
        json={'revision': 999, 'confirmed': True, 'with_history': True},
    )
    assert stale.status_code == 409 and 'Reload before deleting' in stale.text
    assert client.get(f'/api/agents/{target}/history', headers=HEADERS).json()['run_records'] == 2


@pytest.mark.parametrize(
    'working',
    [
        "UPDATE ka.agent_sessions SET status='working' WHERE agent_id=%(a)s",
        "UPDATE ka.agent_sessions SET status='starting' WHERE agent_id=%(a)s",
        "UPDATE ka.agent_sessions SET status='working' WHERE agent_id=%(a)s AND purpose='chat'",
    ],
)
def test_a_working_agent_is_not_deleted(agents, store, working):
    client, target, _, _, _ = agents
    with store.connect() as conn:
        conn.execute(working, {'a': target})
    for body in ({'with_history': True}, {}):
        refused = delete(client, target, **body)
        assert refused.status_code == 409
        assert refused.json()['error']['message'] == WORKING
    history = client.get(f'/api/agents/{target}/history', headers=HEADERS).json()
    assert history == FULL


def test_waiting_or_closed_documentation_sessions_do_not_block(agents, store):
    client, target, _, _, _ = agents
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE agent_id=%s", (target,))
    assert delete(client, target, with_history=True).status_code == 204
