"""Clone agent: a new agent with an identical configuration. Saved credentials are referenced, history is not
copied, and the clone outlives its original."""
import copy
from uuid import UUID, uuid4

import asyncssh

from knowledge_admin.agent_clones import clone_name
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from knowledge_admin.profiles_api import agent_history
from session_rows import a_model, add_test_session
from test_agent_ssh import save
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

NO_HISTORY = {'test_chats': 0, 'documentation_sessions': 0, 'run_records': 0}


def comparable(config: dict) -> dict:
    """The whole configuration without the agent's identity and the revisions of its own rows."""
    config = copy.deepcopy(config)
    for key in ('id', 'name', 'description', 'revision', 'settings_revision'):
        config.pop(key)
    for server in config['ssh']['servers']:
        server.pop('config_revision')
        server.pop('connection_revision')
    return config


def clone(client, agent_id: str = 'operations') -> dict:
    response = client.post(f'/api/agents/{agent_id}/clone', headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def per_server(conn, server, skill, mcp, agent_id: str = 'operations') -> None:
    """A per-server override with its own skill and MCP server rows (the per-server copy loop must copy them)."""
    config = conn.execute("""INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,
            inherit_mcp_servers)
        SELECT project_id,%s,id,'Check only the queue.',false,false FROM ka.servers WHERE id=%s RETURNING id,project_id""",
        (agent_id, UUID(server['id']))).fetchone()
    conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (config['id'], config['project_id'], UUID(skill['id'])))
    conn.execute('INSERT INTO ka.agent_config_mcp_servers VALUES (%s,%s,%s)',
                 (config['id'], config['project_id'], UUID(mcp['id'])))


def own_connection(client, server) -> None:
    """An SSH key saved for this agent only (a credential the clone must reference, not copy)."""
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/operations/servers/{server['id']}/ssh", username='agent-user', port=2222,
         known_hosts='', allow_sudo=False, private_key=key)


def test_a_clone_runs_exactly_what_its_original_runs(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    own_connection(client, server)
    with store.connect() as conn:
        per_server(conn, server, saved_agent[3], saved_agent[4])
        conn.execute("""INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('operations','read_file','Read narrowly.')
            ON CONFLICT (agent_id,tool) DO UPDATE SET usage=EXCLUDED.usage""")
        conn.execute("UPDATE ka.agents SET system_prompt='Answer from evidence.',resources='per_run' WHERE id='operations'")
        credentials = conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n']
    original = get_agent_config('operations', conninfo=store.conninfo)
    copied = get_agent_config(clone(client)['id'], conninfo=store.conninfo)
    assert comparable(copied) == comparable(original)
    assert sections(copied) == sections(original)
    assert copied['ssh']['servers'][0]['private_key_ref'] == original['ssh']['servers'][0]['private_key_ref']
    assert (copied['ssh']['servers'][0]['skills_source'], copied['ssh']['servers'][0]['mcp_source']) == (
        'server_override', 'server_override')
    assert [item['id'] for item in copied['ssh']['servers'][0]['mcp_servers']] == [saved_agent[4]['id']]
    with store.connect() as conn:  # the saved key is referenced, not duplicated
        assert conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n'] == credentials


def test_a_clone_is_named_after_its_original_and_keeps_its_switch(store, saved_agent):
    client = saved_agent[0]
    original = client.get('/api/agents/operations', headers=HEADERS).json()
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET enabled=false WHERE id='operations'")
    first, second = clone(client), clone(client)
    assert (first['name'], second['name']) == (f"{original['name']} (copy)", f"{original['name']} (copy 2)")
    detail = client.get(f"/api/agents/{first['id']}", headers=HEADERS).json()
    assert (detail['enabled'], detail['cloned_from'], detail['description']) == (False, 'operations', original['description'])
    assert detail['original'] == {'id': 'operations', 'name': original['name']}
    listed = {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[first['id']]['original_name'] == original['name'] and listed['operations']['original_name'] is None
    assert client.post('/api/agents/missing/clone', headers=HEADERS).status_code == 404
    assert client.post('/api/agents/operations/clone').status_code == 403
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.cloned' ORDER BY created_at").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': first['id'], 'cloned_from': 'operations'},
                                                {'agent_id': second['id'], 'cloned_from': 'operations'}]


def test_clone_names_stay_within_the_name_limit(store):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET name=%s WHERE id='search'", ('x' * 100,))
        assert clone_name(conn, 'x' * 100) == 'x' * 93 + ' (copy)'
        conn.execute("UPDATE ka.agents SET name=%s WHERE id='code'", ('x' * 93 + ' (copy)',))
        assert clone_name(conn, 'x' * 100) == 'x' * 91 + ' (copy 2)'


def test_history_stays_with_the_original_and_the_clone_outlives_it(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    own_connection(client, server)
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin', 'admin@test')
        add_test_session(conn, project, actor, 'operations', a_model(conn, actor), message='Check disk', status='waiting',
                         turns=1)
    created = clone(client)
    with store.connect() as conn:
        assert agent_history(conn, created['id']) == NO_HISTORY
        credential = conn.execute('SELECT credential_id FROM ka.agent_server_connections WHERE agent_id=%s',
                                  (created['id'],)).fetchone()['credential_id']
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    deleted = client.request('DELETE', '/api/agents/operations', headers=HEADERS,
                             json={'revision': revision, 'confirmed': True, 'with_history': True})
    assert deleted.status_code == 204, deleted.text
    detail = client.get(f"/api/agents/{created['id']}", headers=HEADERS).json()
    assert (detail['cloned_from'], detail['original']) == (None, None)
    with store.connect() as conn:  # the clone still uses the saved key
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (credential,)).fetchone() is not None
    host = get_agent_config(created['id'], conninfo=store.conninfo)['ssh']['servers'][0]
    assert host['private_key_ref']['id'] == str(credential)
