"""Apply to original: the clone's configuration replaces the original's, except name and description; refused for a
stale dialog, while the original works, and when there is no original."""
import threading
import time
from uuid import uuid4

import asyncssh

from knowledge_admin.agents.clones import APPLY_WORKING, NOT_A_CLONE, ORIGINAL_CHANGED
from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from test_agent_clones import clone, own_connection, per_server
from test_agent_ssh import save
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

# Every configuration table, by the columns that decide what runs (not ids, revisions or who saved them).
TABLES = {
    'agent_collections': ('agent_id', 'collection_id'), 'agent_repositories': ('agent_id', 'repository_id'),
    'agent_services': ('agent_id', 'service_id'), 'skills_agents': ('agent_id', 'skill_id,project_id'),
    'agent_tools': ('agent_id', 'tool,mode,usage,subagent_allowed'),
    'agent_configs': ('agent', 'project_id,server_id,instructions,inherit_skills,inherit_mcp_servers'),
    'operations_settings': ('agent', 'project_id'), 'operations_servers': ('agent', 'project_id,server_id'),
    'agent_server_connections': ('agent_id', 'server_id,project_id,username,port,allow_sudo,known_hosts,credential_id,'
                                             'credential_kind'),
}


def rows(conn, agent_id: str) -> dict[str, list]:
    """The agent's rows in every configuration table, comparable between two agents."""
    found = {}
    for table, (key, columns) in TABLES.items():
        found[table] = sorted(map(str, conn.execute(f'SELECT {columns} FROM ka.{table} WHERE {key}=%s',
                                                    (agent_id,)).fetchall()))
    for table, column in (('agent_config_skills', 'skill_id'), ('agent_config_mcp_servers', 'mcp_server_id')):
        found[table] = sorted(map(str, conn.execute(f'''SELECT c.project_id,c.server_id,t.{column} FROM ka.{table} t
            JOIN ka.agent_configs c ON c.id=t.config_id WHERE c.agent=%s''', (agent_id,)).fetchall()))
    return found


def preview(client, agent_id: str) -> dict:
    response = client.get(f'/api/agents/{agent_id}/apply', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, agent_id: str, value: dict):
    return client.post(f'/api/agents/{agent_id}/apply', headers=HEADERS,
                       json={'revision': value['original']['revision'], 'fingerprint': value['fingerprint']})


def edit_prompt(client, agent_id: str, text: str) -> None:
    revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    saved = client.put(f'/api/agents/{agent_id}/prompt', headers=HEADERS, json={'revision': revision, 'system_prompt': text})
    assert saved.status_code == 200, saved.text


def test_apply_copies_everything_but_name_and_description(store, saved_agent):
    client, server, skill, mcp = saved_agent[0], saved_agent[1], saved_agent[3], saved_agent[4]
    own_connection(client, server)  # a saved key of the original, which the clone references
    copy_id = clone(client)['id']
    original = client.get('/api/agents/operations', headers=HEADERS).json()
    edit_prompt(client, copy_id, 'Answer from evidence.')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_configs SET instructions='Gather evidence first.' WHERE agent=%s AND server_id IS NULL",
                     (copy_id,))
        conn.execute('DELETE FROM ka.skills_agents WHERE agent_id=%s', (copy_id,))
        per_server(conn, server, skill, mcp, copy_id)
        conn.execute("""UPDATE ka.agents SET name='Experiment',description='Evidence first',when_to_use='Payments',
            enabled=false WHERE id=%s""", (copy_id,))
        # settings of the original in another (archived) project, which the clone has none for: Apply leaves no stale row
        other = store.project(conn, 'other')
        conn.execute("INSERT INTO ka.operations_settings(project_id,agent) VALUES (%s,'operations')", (other,))
        conn.execute('UPDATE ka.projects SET archived_at=now() WHERE id=%s', (other,))
        credentials = conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n']
    value = preview(client, copy_id)
    assert value['original'] == {'id': 'operations', 'name': original['name'], 'revision': original['revision']}
    assert value['changes'] == ['Prompt', 'Instructions', 'When to use', 'Skills', 'SSH']
    applied = apply(client, copy_id, value)
    assert applied.status_code == 200, applied.text
    assert applied.json() == {'id': 'operations', 'revision': original['revision'] + 1}
    assert sections(get_agent_config('operations', conninfo=store.conninfo)) == \
        sections(get_agent_config(copy_id, conninfo=store.conninfo))
    with store.connect() as conn:
        assert rows(conn, 'operations') == rows(conn, copy_id)
        assert rows(conn, copy_id)['agent_config_mcp_servers']  # the per-server rows were copied
        # the saved key stays, referenced by both; nothing duplicated
        assert conn.execute('SELECT count(*) AS n FROM ka.credentials').fetchone()['n'] == credentials
        assert len({row['credential_id'] for row in conn.execute(
            'SELECT credential_id FROM ka.agent_server_connections WHERE agent_id IN (%s,%s)',
            ('operations', copy_id))}) == 1
    after = client.get('/api/agents/operations', headers=HEADERS).json()
    assert (after['name'], after['description'], after['enabled'], after['cloned_from']) == (
        original['name'], original['description'], original['enabled'], None)
    assert preview(client, copy_id)['changes'] == []
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.config.applied'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': 'operations', 'clone_id': copy_id}]


def test_apply_is_refused_when_the_original_changed_or_is_working(store, saved_agent):
    client, model = saved_agent[0], saved_agent[6]
    copy_id = clone(client)['id']
    edit_prompt(client, copy_id, 'Answer from evidence.')
    value = preview(client, copy_id)
    with store.connect() as conn:  # an instructions save keeps agents.revision but changes what runs
        conn.execute("UPDATE ka.agent_configs SET instructions='Edited meanwhile.' WHERE agent='operations' AND server_id IS NULL")
    stale = apply(client, copy_id, value)
    assert stale.status_code == 409 and stale.json()['error']['message'] == ORIGINAL_CHANGED
    value = preview(client, copy_id)
    edit_prompt(client, 'operations', 'Newer.')
    assert apply(client, copy_id, value).json()['error']['message'] == ORIGINAL_CHANGED
    value = preview(client, copy_id)
    session = uuid4()
    with store.connect() as conn:  # a documentation session of the original is working
        conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder,status,agent_config) SELECT %s,p.id,'operations',(SELECT id FROM ka.principals LIMIT 1),
            %s,'Payments','{}','{}','Payments','payments','working','{"id": "operations"}' FROM ka.projects p WHERE p.archived_at IS NULL''',
            (session, model['id']))
    working = apply(client, copy_id, value)
    assert working.status_code == 409 and working.json()['error']['message'] == APPLY_WORKING
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='stopped' WHERE id=%s", (session,))
    assert apply(client, copy_id, value).status_code == 200


def test_apply_needs_a_clone_whose_original_still_exists(store, saved_agent):
    client = saved_agent[0]
    copy_id = clone(client)['id']
    plain = client.get('/api/agents/operations/apply', headers=HEADERS)
    assert plain.status_code == 404 and plain.json()['error']['message'] == NOT_A_CLONE
    assert client.get(f'/api/agents/{copy_id}/apply').status_code == 403
    revision = client.get('/api/agents/operations', headers=HEADERS).json()['revision']
    assert client.request('DELETE', '/api/agents/operations', headers=HEADERS,
                          json={'revision': revision, 'confirmed': True, 'with_history': True}).status_code == 204
    assert client.get(f'/api/agents/{copy_id}/apply', headers=HEADERS).status_code == 404
    gone = client.post(f'/api/agents/{copy_id}/apply', headers=HEADERS, json={'revision': 1, 'fingerprint': '0' * 64})
    assert gone.status_code == 404 and gone.json()['error']['message'] == NOT_A_CLONE


def test_apply_deletes_the_key_only_the_original_used(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    own_connection(client, server)
    copy_id = clone(client)['id']
    with store.connect() as conn:
        old = conn.execute("SELECT credential_id FROM ka.agent_server_connections WHERE agent_id='operations'").fetchone()
        others = {row['id'] for row in conn.execute('SELECT id FROM ka.credentials WHERE id<>%s', (old['credential_id'],))}
    own_connection_of(client, server, copy_id)  # the clone switches to its own key; the original keeps the old one
    with store.connect() as conn:
        new = conn.execute('SELECT credential_id FROM ka.agent_server_connections WHERE agent_id=%s',
                           (copy_id,)).fetchone()['credential_id']
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (old['credential_id'],)).fetchone()
    assert apply(client, copy_id, preview(client, copy_id)).status_code == 200
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (old['credential_id'],)).fetchone() is None
        assert {row['credential_id'] for row in conn.execute(
            'SELECT credential_id FROM ka.agent_server_connections WHERE agent_id IN (%s,%s)', ('operations', copy_id))} == {new}
        assert {row['id'] for row in conn.execute('SELECT id FROM ka.credentials')} == others | {new}


def own_connection_of(client, server, agent_id: str) -> None:
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/{agent_id}/servers/{server['id']}/ssh", username='clone-user', port=2222,
         known_hosts='', allow_sudo=False, private_key=key)


def waits_for_the_agent(store, write) -> None:
    """While another transaction (Apply) holds the original's row, the write waits for it, then succeeds."""
    with store.connect() as holder, store.connect() as watcher:
        watcher.autocommit = True  # pg_stat_activity is a snapshot per transaction
        holder.execute("SELECT id FROM ka.agents WHERE id='operations' FOR UPDATE")
        done: list = []
        worker = threading.Thread(target=lambda: done.append(write()))
        worker.start()
        deadline = time.monotonic() + 20
        while not watcher.execute("""SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock'
                AND query LIKE 'SELECT id FROM ka.agents WHERE id=%%FOR UPDATE%%'""").fetchone():
            assert worker.is_alive() and time.monotonic() < deadline, 'the write did not wait for the agent'
            time.sleep(0.05)
        assert not done
    worker.join(20)
    assert done and done[0].status_code == 200, done and done[0].text


def test_ssh_and_per_server_saves_wait_for_apply(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    path = f"/api/agents/operations/servers/{server['id']}/ssh"
    current = client.get(path, headers=HEADERS).json()  # the forms are read before Apply takes the lock
    ssh = {key: current[key] for key in ('revision', 'server_revision', 'known_hosts', 'allow_sudo')}
    waits_for_the_agent(store, lambda: client.put(path, headers=HEADERS, json={**ssh, 'username': 'agent-user', 'port': 2222}))
    data = client.get('/api/operations/settings', headers=HEADERS).json()
    body = {'settings_revision': data['settings']['revision'], 'revision': None, 'instructions': 'Check only the queue.',
            'skill_ids': None}
    waits_for_the_agent(store, lambda: client.put(f"/api/operations/configs/{server['id']}", headers=HEADERS, json=body))
