"""Clone agent: a new agent with an identical configuration. Saved credentials are referenced, history is not
copied, and the clone outlives its original."""

import copy


from access_helpers import of
from knowledge_admin.agents.clones import clone_name
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from knowledge_admin.agents.profiles_api import agent_history
from session_rows import a_model, add_test_session
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

NO_HISTORY = {'test_chats': 0, 'chats': 0, 'documentation_sessions': 0, 'run_records': 0}


def comparable(config: dict) -> dict:
    """The whole configuration without the agent's identity and the revisions of its own rows."""
    config = copy.deepcopy(config)
    for key in ('id', 'name', 'description', 'revision', 'settings_revision'):
        config.pop(key)
    return config


def clone(client, agent_id: str = 'operations') -> dict:
    response = client.post(f'/api/agents/{agent_id}/clone', headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_clone_runs_exactly_what_its_original_runs(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.agents SET system_prompt='Answer from evidence.',
            access=access || '{"roles": {"content_editor": true}}' WHERE id='operations'"""
        )
        credentials = conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n']
    copy_id = clone(client)['id']
    original = get_agent_config('operations', conninfo=store.conninfo)
    copied = get_agent_config(copy_id, conninfo=store.conninfo)
    assert comparable(copied) == comparable(original)
    assert sections(copied) == sections(original) and 'roles' not in copied['access']  # an old role: ignored
    assert of(copied, 'server')[0]['private_key_ref'] == of(original, 'server')[0]['private_key_ref']
    with store.connect() as conn:  # the saved key is referenced, not duplicated
        assert conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n'] == credentials


def test_a_clone_is_named_after_its_original_and_keeps_its_switch(store, saved_agent):
    client = saved_agent[0]
    original = client.get('/api/agents/operations', headers=HEADERS).json()
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='operations'")
    first, second = clone(client), clone(client)
    assert (first['name'], second['name']) == (f'{original["name"]} (copy)', f'{original["name"]} (copy 2)')
    detail = client.get(f'/api/agents/{first["id"]}', headers=HEADERS).json()
    assert (detail['enabled'], detail['cloned_from'], detail['description']) == (
        False,
        'operations',
        original['description'],
    )
    assert detail['original'] == {'id': 'operations', 'name': original['name']}
    listed = {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[first['id']]['original_name'] == original['name'] and listed['operations']['original_name'] is None
    assert client.post('/api/agents/missing/clone', headers=HEADERS).status_code == 404
    assert client.post('/api/agents/operations/clone').status_code == 403
    with store.connect() as conn:
        audit = conn.execute(
            "SELECT details FROM ka.audit_events WHERE action='agent.cloned' ORDER BY created_at"
        ).fetchall()
    assert [row['details'] for row in audit] == [
        {'agent_id': first['id'], 'cloned_from': 'operations'},
        {'agent_id': second['id'], 'cloned_from': 'operations'},
    ]


def test_clone_names_stay_within_the_name_limit(store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET name=%s WHERE id='search'", ('x' * 100,))
        assert clone_name(conn, 'x' * 100) == 'x' * 93 + ' (copy)'
        conn.execute("UPDATE ka.agents SET name=%s WHERE id='code'", ('x' * 93 + ' (copy)',))
        assert clone_name(conn, 'x' * 100) == 'x' * 91 + ' (copy 2)'


def test_history_stays_with_the_original_and_the_clone_outlives_it(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin', 'admin@test')
        add_test_session(
            conn, project, actor, 'operations', a_model(conn, actor), message='Check disk', status='waiting', turns=1
        )
    created = clone(client)
    with store.connect() as conn:
        assert agent_history(conn, created['id']) == NO_HISTORY
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    deleted = client.request(
        'DELETE',
        '/api/agents/operations',
        headers=HEADERS,
        json={'revision': revision, 'confirmed': True, 'with_history': True},
    )
    assert deleted.status_code == 204, deleted.text
    detail = client.get(f'/api/agents/{created["id"]}', headers=HEADERS).json()
    assert (detail['cloned_from'], detail['original']) == (None, None)
    host = of(get_agent_config(created['id'], conninfo=store.conninfo), 'server')[0]
    assert host['private_key_ref'] is not None  # the server's own key
