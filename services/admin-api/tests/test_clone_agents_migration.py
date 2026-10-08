"""0080: every active configuration version becomes its agent's configuration (what ran before runs after), then the
versions, the prompt history and the version references go. Down restores the empty schema."""
import copy
from pathlib import Path
from uuid import uuid4

import asyncssh
from psycopg.types.json import Jsonb

from knowledge_admin.knowledge_agents.config import get_agent_config, sections
from knowledge_admin.tools.catalog import implied_tools
from test_agent_ssh import save
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0080_clone_agents.up.sql', '0080_clone_agents.down.sql'
LATER = '0081_test_sessions.down.sql'  # 0080 down re-adds agent_sessions.agent_version_id
TABLES = ('agent_versions', 'agent_prompts', 'agent_prompt_versions', 'agent_prompt_version_details')
COLUMNS = (('agents', 'active_config_version_id'), ('agents', 'next_config_version'),
           ('agent_sessions', 'agent_version_id'), ('agents', 'cloned_from'))
AFTER = {'tables': [], 'columns': ['agents.cloned_from'], 'function': 0}
BEFORE = {'tables': list(TABLES), 'function': 1, 'columns': [
    'agent_sessions.agent_version_id', 'agents.active_config_version_id', 'agents.next_config_version']}


def run(conn, name: str):
    if name == DOWN and conn.execute("SELECT to_regclass('ka.operations_runs') AS r").fetchone()['r'] is None:
        conn.execute((MIGRATIONS / LATER).read_text(), prepare=False)  # newest first, as golang-migrate does
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def schema(conn) -> dict:
    present = {(row['table_name'], row['column_name']) for row in conn.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='ka'")}
    return {
        'tables': [t for t in TABLES if conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{t}',)).fetchone()['r']],
        'columns': sorted(f'{table}.{column}' for table, column in COLUMNS if (table, column) in present),
        'function': conn.execute("""SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='ka' AND p.proname='guard_prompt_version'""").fetchone()['n'],
    }


def as_saved(config: dict) -> dict:
    """The configuration as a version stored it (0034…0079): no live graphs, the prompt version's fields."""
    saved = copy.deepcopy(config)
    saved['code'].pop('graphs', None)
    saved['tools']['items'] = [entry for entry in saved['tools']['items'] if entry['tool'] != 'run_subagent']  # not yet a tool
    saved['system_prompt'] = {'id': str(uuid4()), 'title': 'Tuned', 'version': 2, 'revision': 1,
                              'content': saved['system_prompt']['content']}
    return saved


def ran_before(saved: dict, agent: dict) -> dict:
    """What get_agent_config returned before 0080 for an agent whose active version this was."""
    config = copy.deepcopy(saved)
    config['enabled'] = agent['enabled']
    config.setdefault('resources', 'fixed')  # versions saved before 0075
    config['tools'] = config.get('tools', implied_tools(config))
    return config


def activate(conn, agent_id: str, saved: dict, actor) -> dict:
    version = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by,published_at)
        VALUES (%s,1,'Active',%s,%s,now()) RETURNING id''', (agent_id, Jsonb(saved), actor)).fetchone()['id']
    conn.execute('UPDATE ka.agents SET active_config_version_id=%s,next_config_version=2 WHERE id=%s', (version, agent_id))
    return conn.execute('SELECT enabled FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()


def test_every_agent_runs_what_it_ran_before(store, saved_agent):
    client, server = saved_agent[0], saved_agent[1]
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/operations/servers/{server['id']}/ssh", username='agent-user', port=2222,
         known_hosts='', allow_sudo=False, private_key=key)
    working = {agent_id: sections(get_agent_config(agent_id, conninfo=store.conninfo)) for agent_id in ('search', 'code')}
    saved = as_saved(get_agent_config('operations', conninfo=store.conninfo))
    saved['system_prompt']['content'] = 'Answer from evidence only.'
    saved['instructions'] = 'Gather evidence first.'
    saved['when_to_use'] = 'When payments fail.'
    for entry in saved['ssh']['servers']:
        if entry['instructions_source'] == 'agent':
            entry['instructions'] = saved['instructions']
    saved['ssh']['servers'][0].update(instructions='Check only the queue.', instructions_source='agent_version')
    saved['services']['enabled'] = False
    with store.connect() as conn:
        taken = conn.execute("SELECT name FROM ka.agents WHERE id='search'").fetchone()['name']
    own, saved['name'] = saved['name'], taken  # another agent has this name now: the agent keeps its own
    for key_name in ('tools', 'resources'):  # saved before 0075 and 0078
        saved.pop(key_name)
    with store.connect() as conn:  # the working copy moved on after the version was activated
        conn.execute("UPDATE ka.agent_configs SET instructions='Working copy edits' WHERE agent='operations' AND server_id IS NULL")
        conn.execute("UPDATE ka.agent_server_connections SET port=2200 WHERE agent_id='operations'")
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute("UPDATE ka.agents SET services_enabled=true,when_to_use='Later' WHERE id='operations'")
        for table in ('agent_collections', 'agent_repositories', 'agent_services', 'skills_agents'):
            conn.execute(f"DELETE FROM ka.{table} WHERE agent_id='operations'")
        conn.execute('''DELETE FROM ka.agent_config_mcp_servers s USING ka.agent_configs c
            WHERE s.config_id=c.id AND c.agent='operations' ''')
        conn.execute("UPDATE ka.agents SET model_id=NULL,system_prompt='Working prompt' WHERE id='operations'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        agent = activate(conn, 'operations', saved, store.actor(conn, 'migration-admin', 'migration@test'))
        conn.commit()
        run(conn, UP)
        conn.commit()
    after = get_agent_config('operations', conninfo=store.conninfo)
    assert sections(after) == sections(ran_before(saved, agent))
    assert after['system_prompt'] == {'content': 'Answer from evidence only.'}
    host = after['ssh']['servers'][0]
    assert (host['instructions'], host['instructions_source'], host['port']) == ('Check only the queue.', 'server_override', 2222)
    assert after['services']['enabled'] is False and after['when_to_use'] == 'When payments fail.'
    assert {item['tool'] for item in after['tools']['items'] if item['mode'] == 'on'} == {
        item['tool'] for item in implied_tools(saved)['items'] if item['mode'] == 'on'}
    for agent_id, before in working.items():  # agents without a version keep their own configuration
        assert sections(get_agent_config(agent_id, conninfo=store.conninfo)) == before, agent_id
    assert after['name'] == own


def test_a_version_saved_after_0078_runs_after_although_every_section_changed_since(store, saved_agent):
    client, server, _, _, mcp, repo, _ = saved_agent
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/operations/servers/{server['id']}/ssh", username='agent-user', port=2222,
         known_hosts='', allow_sudo=False, private_key=key)
    with store.connect() as conn:
        runbooks = str(conn.execute("SELECT id FROM ka.rag_collections WHERE name='runbooks'").fetchone()['id'])
        unselected = str(conn.execute('SELECT id FROM ka.repositories WHERE id<>%s', (repo['id'],)).fetchone()['id'])
    saved = as_saved(get_agent_config('operations', conninfo=store.conninfo))
    saved.update(name='Payments operator', description='Investigates payment failures.', when_to_use='Payments fail.',
                 instructions='Gather evidence first.', resources='fixed')
    saved['system_prompt']['content'] = 'Answer from evidence only.'
    saved['documents']['collections'].append({'id': runbooks, 'name': 'runbooks'})  # deleted before 0080
    saved['code']['projects'].append({'id': unselected, 'name': 'unselected'})  # archived before 0080
    for entry in saved['tools']['items']:  # tool rows with usage text, a per-run tool and an MCP tool
        if entry['tool'] == 'read_file':
            entry['mode'], entry['usage'] = 'on', 'Read only configs.'
        if entry['tool'] == 'ssh_run':
            entry['mode'] = 'per_run'
    saved['tools']['items'].append({'tool': f"mcp:{mcp['id']}:lookup", 'group': 'mcp', 'mode': 'off', 'usage': 'never',
                                    'subagent_allowed': True})
    host = saved['ssh']['servers'][0]  # every per-server override, and the agent's own connection
    assert host['connection_source'] == 'agent'
    host.update(instructions='Check only the queue.', instructions_source='server_override',
                skills_source='server_override', skills=[], mcp_source='server_override', mcp_servers=[{'id': mcp['id']}])
    other = client.post('/api/servers', headers=HEADERS, json={'name': 'Other host', 'hostname': 'other.test',
                                                                 'ssh_allowed': True}).json()
    save(client, f"/api/agents/operations/servers/{other['id']}/ssh", username='other-user', port=2022,
         known_hosts='', allow_sudo=False, private_key=key)  # a connection for a server the version does not select
    with store.connect() as conn:  # the working copy changes every section after the version was activated
        conn.execute("UPDATE ka.agent_configs SET instructions='Working copy edits' WHERE agent='operations' AND server_id IS NULL")
        for table in ('agent_collections', 'agent_repositories', 'agent_services', 'skills_agents', 'agent_tools'):
            conn.execute(f"DELETE FROM ka.{table} WHERE agent_id='operations'")
        conn.execute("DELETE FROM ka.agent_config_mcp_servers")
        conn.execute("DELETE FROM ka.operations_servers WHERE agent='operations'")
        conn.execute("""UPDATE ka.agents SET model_id=NULL,system_prompt='Working prompt',when_to_use='Later',
            documents_enabled=false,repositories_enabled=false,ssh_enabled=false,
            skills_enabled=false,mcp_enabled=false,services_enabled=false,resources='per_run' WHERE id='operations'""")
    drifted = sections(get_agent_config('operations', conninfo=store.conninfo))
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        agent = activate(conn, 'operations', saved, store.actor(conn, 'migration-admin', 'migration@test'))
        conn.execute('UPDATE ka.rag_collections SET deleted_at=now() WHERE id=%s', (runbooks,))
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (unselected,))
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert [str(row['server_id']) for row in conn.execute(
            "SELECT server_id FROM ka.agent_server_connections WHERE agent_id='operations'")] == [server['id']]
        audit = conn.execute("""SELECT details FROM ka.audit_events WHERE action='agent.version.materialized'""").fetchall()
    expected = sections(ran_before(saved, agent))
    expected['documents']['collections'].remove(runbooks)  # deleted and archived items are dropped
    expected['code']['projects'].remove(unselected)
    assert [key for key in expected if drifted[key] == expected[key]] == []  # the working copy differed everywhere
    after = get_agent_config('operations', conninfo=store.conninfo)
    migrated = sections(after)
    migrated['tools'] = [entry for entry in migrated['tools'] if entry['tool'] != 'run_subagent']  # not in old versions
    assert migrated == expected
    assert (after['name'], after['description']) == ('Payments operator', 'Investigates payment failures.')
    assert runbooks not in [c['id'] for c in after['documents']['collections']]
    assert unselected not in [p['id'] for p in after['code']['projects']]
    assert [(d['agent_id'], d['label'], d['config']['name']) for d in (row['details'] for row in audit)] == [
        ('operations', 'Active', 'Payments operator')]


def test_pinned_conversations_keep_their_configuration(store, saved_agent):
    pinned = {'id': 'operations', 'instructions': 'Pinned instructions',
              'config_version': {'id': str(uuid4()), 'version': 3, 'label': 'Old'}}
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'migration-admin', 'migration@test')
        version = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by)
            VALUES ('operations',3,'Old','{}',%s) RETURNING id''', (actor,)).fetchone()['id']
        session = conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test,agent_version_id)
            VALUES (%s,%s,'operations',%s,'Old chat',%s,true,%s) RETURNING id''',
            (uuid4(), project, actor, Jsonb(pinned), version)).fetchone()['id']
        conn.commit()
        run(conn, UP)
        conn.commit()
        saved = conn.execute('SELECT config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        assert saved['config'] == pinned
        assert conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='agent_sessions' AND column_name='agent_version_id'""").fetchone() is None


def test_up_drops_versions_and_prompt_history_and_down_restores_the_empty_schema(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        assert schema(conn) == AFTER
        run(conn, DOWN)
        conn.commit()
        assert schema(conn) == BEFORE
        for table in TABLES[:3]:
            assert conn.execute(f'SELECT count(*) AS n FROM ka.{table}').fetchone()['n'] == 0, table
        run(conn, UP)
        conn.commit()
        assert schema(conn) == AFTER
    created = create(client, 'After 0080')  # new agents get their prompt directly
    assert (created['system_prompt'], created['cloned_from']) == ('Instructions for After 0080.', None)
