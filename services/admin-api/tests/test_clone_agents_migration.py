"""0080: every active configuration version becomes its agent's configuration (what ran before runs after), then the
versions, the prompt history and the version references go. Down restores the empty schema."""
import copy
from pathlib import Path
from uuid import uuid4

import asyncssh
from psycopg.types.json import Jsonb

from knowledge_admin.knowledge_agents.config import CLAUDE_CODE_TOOLS, get_agent_config, sections
from knowledge_admin.tools.catalog import implied_tools
from test_agent_ssh import save
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_api import _client
from test_claude_code_agents import claude_code_agent
from test_profiles import create
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0080_clone_agents.up.sql', '0080_clone_agents.down.sql'
TABLES = ('agent_versions', 'agent_prompts', 'agent_prompt_versions', 'agent_prompt_version_details')
COLUMNS = (('agents', 'active_config_version_id'), ('agents', 'next_config_version'),
           ('agent_sessions', 'agent_version_id'), ('agents', 'cloned_from'))
AFTER = {'tables': [], 'columns': ['agents.cloned_from'], 'function': 0}
BEFORE = {'tables': list(TABLES), 'function': 1, 'columns': [
    'agent_sessions.agent_version_id', 'agents.active_config_version_id', 'agents.next_config_version']}


def run(conn, name: str):
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
    saved['system_prompt'] = {'id': str(uuid4()), 'title': 'Tuned', 'version': 2, 'revision': 1,
                              'content': saved['system_prompt']['content']}
    return saved


def ran_before(saved: dict, agent: dict) -> dict:
    """What get_agent_config returned before 0080 for an agent whose active version this was."""
    config = copy.deepcopy(saved)
    config['enabled'], config['runtime'] = agent['enabled'], agent['runtime']
    config.setdefault('claude_code_tools', list(CLAUDE_CODE_TOOLS))  # versions saved before 0072
    config.setdefault('resources', 'fixed')  # versions saved before 0075
    config['tools'] = {'items': []} if agent['runtime'] != 'openai' else config.get('tools', implied_tools(config))
    return config


def activate(conn, agent_id: str, saved: dict, actor) -> dict:
    version = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by,published_at)
        VALUES (%s,1,'Active',%s,%s,now()) RETURNING id''', (agent_id, Jsonb(saved), actor)).fetchone()['id']
    conn.execute('UPDATE ka.agents SET active_config_version_id=%s,next_config_version=2 WHERE id=%s', (version, agent_id))
    return conn.execute('SELECT enabled,runtime FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()


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
    for key_name in ('tools', 'resources', 'claude_code_tools'):  # saved before 0072, 0075 and 0078
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


def test_a_claude_code_agent_keeps_its_versions_claude_code_tools(store, tmp_path, monkeypatch):
    for variable in ('CODE_ROOT', 'DATA_ROOT_DIR'):
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    writer = claude_code_agent(client)
    saved = as_saved(get_agent_config(writer, conninfo=store.conninfo))
    saved['claude_code_tools'] = ['Read']
    saved['system_prompt']['content'] = 'Write runbooks.'
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        agent = activate(conn, writer, saved, store.actor(conn, 'migration-admin', 'migration@test'))
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tools WHERE agent_id=%s', (writer,)).fetchone()['n'] == 0
    after = get_agent_config(writer, conninfo=store.conninfo)
    assert sections(after) == sections(ran_before(saved, agent))
    assert (after['claude_code_tools'], after['tools'], after['system_prompt']) == (
        ['Read'], {'items': []}, {'content': 'Write runbooks.'})


def test_pinned_conversations_keep_their_configuration(store, saved_agent):
    client = saved_agent[0]
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
    detail = client.get(f'/api/agents/operations/sessions/{session}', headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()['config'] == pinned and 'agent_version_id' not in detail.json()
    listed = client.get('/api/agents/operations/sessions', headers=HEADERS).json()['sessions']
    assert [row['id'] for row in listed] == [str(session)] and 'config_version' not in listed[0]


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
