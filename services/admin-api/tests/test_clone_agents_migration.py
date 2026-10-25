"""0080: every active configuration version becomes its agent's configuration (what ran before runs after), then the
versions, the prompt history and the version references go. Down restores the empty schema."""

import copy
from pathlib import Path
from uuid import uuid4

from implied_tools import implied_tools
from knowledge_admin.knowledge_agents import config as agent_config
from knowledge_admin.knowledge_agents.config import get_agent_config
from psycopg.types.json import Jsonb
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_api import _client
from test_profiles import create
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0080_clone_agents.up.sql', '0080_clone_agents.down.sql'
REMOVED = '0084_remove_claude_code.down.sql'  # renames ka.agent_sessions back to agent_sessions_cc
LATER = '0081_test_sessions.down.sql'  # 0080 down re-adds agent_sessions.agent_version_id
DROPPED = '0114_drop_agent_resource_tables.down.sql'  # the per-type tables 0080 materialised versions into
TOOLS_DROPPED = '0115_tools_always_on.down.sql'  # ka.agent_tools, which 0080 materialised versions' tools into
GROUNDED_DROPPED = '0133_document_search.down.sql'  # agents.documents_grounded, which 0080 reads and writes
RAG_GONE = '0134_rag_gone.down.sql'  # rag_collections, which 0114 down and 0080 read
TABLES = ('agent_versions', 'agent_prompts', 'agent_prompt_versions', 'agent_prompt_version_details')
COLUMNS = (
    ('agents', 'active_config_version_id'),
    ('agents', 'next_config_version'),
    ('agent_sessions', 'agent_version_id'),
    ('agents', 'cloned_from'),
)
AFTER = {'tables': [], 'columns': ['agents.cloned_from'], 'function': 0}
BEFORE = {
    'tables': list(TABLES),
    'function': 1,
    'columns': ['agent_sessions.agent_version_id', 'agents.active_config_version_id', 'agents.next_config_version'],
}


def with_old_tables(conn) -> None:
    """0115 and 0114 down, once: these tests write and read ka.agent_tools and the per-type tables (0114 down fills
    them from each agent's access; 0115 down recreates ka.agent_tools empty)."""
    if conn.execute("SELECT to_regclass('ka.rag_collections') AS r").fetchone()['r'] is None:
        conn.execute((MIGRATIONS / RAG_GONE).read_text(), prepare=False)
    if conn.execute("SELECT to_regclass('ka.documents_search') AS r").fetchone()['r'] is not None:
        conn.execute((MIGRATIONS / GROUNDED_DROPPED).read_text(), prepare=False)
    if conn.execute("SELECT to_regclass('ka.agent_tools') AS r").fetchone()['r'] is None:
        conn.execute((MIGRATIONS / TOOLS_DROPPED).read_text(), prepare=False)
    if conn.execute("SELECT to_regclass('ka.agent_configs') AS r").fetchone()['r'] is None:
        conn.execute((MIGRATIONS / DROPPED).read_text(), prepare=False)


def run(conn, name: str):
    with_old_tables(conn)
    if name == DOWN and conn.execute("SELECT to_regclass('ka.agent_sessions_cc') AS r").fetchone()['r'] is None:
        conn.execute((MIGRATIONS / REMOVED).read_text(), prepare=False)  # newest first, as golang-migrate does
    if name == DOWN and conn.execute("SELECT to_regclass('ka.operations_runs') AS r").fetchone()['r'] is None:
        conn.execute((MIGRATIONS / LATER).read_text(), prepare=False)
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def schema(conn) -> dict:
    present = {
        (row['table_name'], row['column_name'])
        for row in conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='ka'")
    }
    return {
        'tables': [t for t in TABLES if conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{t}',)).fetchone()['r']],
        'columns': sorted(f'{table}.{column}' for table, column in COLUMNS if (table, column) in present),
        'function': conn.execute("""SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='ka' AND p.proname='guard_prompt_version'""").fetchone()['n'],
    }


SERVER = ('hostname', 'port', 'username', 'allow_sudo', 'known_hosts', 'enabled', 'ssh_allowed', 'private_key_ref')


def legacy(config: dict) -> dict:
    """A configuration loaded today (its access object holds the loaded items) in the shape versions and
    configurations had before the access object (per-kind sections), to compare and to store as an old version."""
    config = copy.deepcopy(config)

    def items(type_: str, *fields: str) -> list[dict]:
        return [
            {
                'id': item['id'],
                'name': item['name'],
                'description': item['description_short'],
                **{key: item[key] for key in fields if key in item},
            }
            for item in config['access']['resources']
            if item['type'] == type_
        ]

    collections, projects = items('collection', 'paths'), items('code_project', 'folder_paths')
    skills = [{**item, 'enabled': True} for item in items('skill')]
    mcp, servers, services = (
        items('mcp_server', 'url', 'enabled', 'bearer_token_ref'),
        items('server', *SERVER),
        items('service'),
    )
    return {
        **config,
        'documents': {
            'enabled': bool(collections),
            'grounded': False,  # a configuration loaded today has no grounded mode (0133)
            'all_collections': False,
            'collections': collections,
        },
        'code': {'enabled': bool(projects), 'all_projects': False, 'projects': projects, 'graphs': []},
        'skills': {'enabled': bool(skills), 'items': skills},
        'mcp': {'enabled': bool(mcp), 'servers': mcp},
        'ssh': {'enabled': bool(servers), 'servers': servers},
        'services': {'enabled': bool(services), 'items': services},
        'document_access': [],
    }


def as_saved(config: dict) -> dict:
    """The configuration as a version stored it (0034…0079): no live graphs, the prompt version's fields."""
    saved = legacy(config)
    saved['code'].pop('graphs', None)
    saved['tools'] = {  # the tools section versions had (configurations lost it in 0115); run_subagent: not yet a tool
        'items': [entry for entry in implied_tools(saved)['items'] if entry['tool'] != 'run_subagent']
    }
    saved['system_prompt'] = {
        'id': str(uuid4()),
        'title': 'Tuned',
        'version': 2,
        'revision': 1,
        'content': saved['system_prompt']['content'],
    }
    return saved


def ran_before(saved: dict, agent: dict) -> dict:
    """What get_agent_config returned before 0080 for an agent whose active version this was."""
    config = copy.deepcopy(saved)
    config['enabled'] = agent['enabled']
    config.setdefault('resources', 'fixed')  # versions saved before 0075
    config['tools'] = config.get('tools', implied_tools(config))
    return config


def tool_rows(store, agent_id: str) -> dict[str, tuple]:
    """The agent's ka.agent_tools rows (0080 materialises a version's tools there; nothing reads them after 0115)."""
    with store.connect() as conn:
        return {
            row['tool']: (row['mode'], row['usage'], row['subagent_allowed'])
            for row in conn.execute(
                'SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,)
            )
        }


def _ids(items) -> list[str]:
    return sorted(str(item['id']) for item in items)


def sections(config: dict) -> dict:
    """config.sections and what the agent runs with by kind: the items of each section (a version saved before 0113
    and a configuration loaded from an access object compare alike)."""
    documents, code = config['documents'], config['code']
    return {
        **agent_config.sections(config),
        'skills': {'enabled': config['skills']['enabled'], 'items': _ids(config['skills']['items'])},
        'documents': {
            'enabled': documents['enabled'],
            'all_collections': documents.get('all_collections', False),
            'collections': [] if documents.get('all_collections') else _ids(documents.get('collections', [])),
        },
        'code': {
            'enabled': code['enabled'],
            'all_projects': code.get('all_projects', False),
            'projects': [] if code.get('all_projects') else _ids(code['projects']),
        },
        'ssh': {'enabled': config['ssh']['enabled'], 'servers': _ids(config['ssh']['servers'])},
        'mcp': {'enabled': config['mcp']['enabled'], 'servers': _ids(config['mcp']['servers'])},
        'services': {'enabled': config['services']['enabled'], 'items': _ids(config['services']['items'])},
    }


def held(found: dict) -> dict:
    """sections() as an access object holds them (0113): a kind only with items and only when it was switched on, no
    "all", no per-server overrides or per-agent connections."""
    found = copy.deepcopy(found)
    found.pop('resources')  # the access object itself: compared through the sections it fills
    found.pop('disabled_tools', None)  # 0135: no section before it
    for key, items in (('skills', 'items'), ('mcp', 'servers'), ('services', 'items'), ('ssh', 'servers')):
        if not found[key]['enabled']:
            found[key][items] = []
        found[key]['enabled'] = bool(found[key][items])
    for key, items, every in (('documents', 'collections', 'all_collections'), ('code', 'projects', 'all_projects')):
        if not found[key]['enabled']:
            found[key][items] = []
        found[key].update({'enabled': bool(found[key][items]), every: False})
    return found


def convert(store) -> None:
    """Every agent's access object as 0113 converts the per-type tables (what 0080 materialised into them)."""
    with store.connect() as conn:
        run(conn, '0113_agent_access.down.sql')  # (after 0114 down)
        run(conn, '0113_agent_access.up.sql')


def own_connection(store, server_id: str, username: str, port: int) -> None:
    """A per-agent SSH connection of the operations agent (written before 0113 stopped reading them)."""
    with store.connect() as conn:
        with_old_tables(conn)
        conn.execute(
            """INSERT INTO ka.agent_server_connections(agent_id,server_id,project_id,username,port)
            SELECT 'operations',id,project_id,%s,%s FROM ka.servers WHERE id=%s""",
            (username, port, server_id),
        )


def activate(conn, agent_id: str, saved: dict, actor) -> dict:
    version = conn.execute(
        """INSERT INTO ka.agent_versions(agent,version,label,config,created_by,published_at)
        VALUES (%s,1,'Active',%s,%s,now()) RETURNING id""",
        (agent_id, Jsonb(saved), actor),
    ).fetchone()['id']
    conn.execute(
        'UPDATE ka.agents SET active_config_version_id=%s,next_config_version=2 WHERE id=%s', (version, agent_id)
    )
    return conn.execute('SELECT enabled FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()


def test_a_version_saved_after_0078_runs_after_although_every_section_changed_since(store, saved_agent):
    client, server, _, _, mcp, repo, _ = saved_agent
    own_connection(store, server['id'], 'agent-user', 2222)
    with store.connect() as conn:
        unselected = str(conn.execute('SELECT id FROM ka.repositories WHERE id<>%s', (repo['id'],)).fetchone()['id'])
    saved = as_saved(get_agent_config('operations', conninfo=store.conninfo))
    saved.update(
        name='Payments operator',
        description='Investigates payment failures.',
        when_to_use='Payments fail.',
        instructions='Gather evidence first.',
        resources='fixed',
    )
    saved['system_prompt']['content'] = 'Answer from evidence only.'
    saved['code']['projects'].append({'id': unselected, 'name': 'unselected'})  # archived before 0080
    for entry in saved['tools']['items']:  # tool rows with usage text, a per-run tool and an MCP tool
        if entry['tool'] == 'read_file':
            entry['mode'], entry['usage'] = 'on', 'Read only configs.'
        if entry['tool'] == 'ssh_run':
            entry['mode'] = 'per_run'
    saved['tools']['items'].append(
        {'tool': f'mcp:{mcp["id"]}:lookup', 'group': 'mcp', 'mode': 'off', 'usage': 'never', 'subagent_allowed': True}
    )
    host = saved['ssh']['servers'][0]  # every per-server override, and the agent's own connection
    host.update(
        connection_source='agent',
        instructions='Check only the queue.',
        instructions_source='server_override',
        skills_source='server_override',
        skills=[],
        mcp_source='server_override',
        mcp_servers=[{'id': mcp['id']}],
    )
    other = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Other host', 'hostname': 'other.test', 'ssh_allowed': True}
    ).json()
    own_connection(store, other['id'], 'other-user', 2022)  # a connection for a server the version does not select
    with store.connect() as conn:  # the working copy changes every section after the version was activated
        conn.execute(
            "UPDATE ka.agent_configs SET instructions='Working copy edits' WHERE agent='operations' AND server_id IS NULL"
        )
        for table in ('agent_collections', 'agent_repositories', 'agent_services', 'skills_agents', 'agent_tools'):
            conn.execute(f"DELETE FROM ka.{table} WHERE agent_id='operations'")
        conn.execute('DELETE FROM ka.agent_config_mcp_servers')
        conn.execute("DELETE FROM ka.operations_servers WHERE agent='operations'")
        conn.execute("""UPDATE ka.agents SET model_id=NULL,system_prompt='Working prompt',when_to_use='Later',
            documents_enabled=false,repositories_enabled=false,ssh_enabled=false,
            skills_enabled=false,mcp_enabled=false,services_enabled=false,resources='per_run' WHERE id='operations'""")
    convert(store)
    drifted = held(sections(legacy(get_agent_config('operations', conninfo=store.conninfo))))
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        agent = activate(conn, 'operations', saved, store.actor(conn, 'migration-admin', 'migration@test'))
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (unselected,))
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert [
            str(row['server_id'])
            for row in conn.execute("SELECT server_id FROM ka.agent_server_connections WHERE agent_id='operations'")
        ] == [server['id']]
        audit = conn.execute(
            """SELECT details FROM ka.audit_events WHERE action='agent.version.materialized'"""
        ).fetchall()
    expected = sections(ran_before(saved, agent))
    # archived items are dropped
    expected['code']['projects'].remove(unselected)
    expected = held(expected)
    # the working copy differed everywhere but in skills and documents: skills are not access any more and the
    # collection type is gone, so neither holds any
    assert [key for key in expected if drifted[key] == expected[key]] == ['skills', 'documents']
    convert(store)
    after = get_agent_config('operations', conninfo=store.conninfo)
    migrated = held(sections(legacy(after)))
    assert migrated == expected
    assert tool_rows(store, 'operations') == {
        entry['tool']: (entry['mode'], entry['usage'], entry['subagent_allowed']) for entry in saved['tools']['items']
    }
    assert (after['name'], after['description']) == ('Payments operator', 'Investigates payment failures.')
    assert unselected not in [item['id'] for item in after['access']['resources']]
    assert [(d['agent_id'], d['label'], d['config']['name']) for d in (row['details'] for row in audit)] == [
        ('operations', 'Active', 'Payments operator')
    ]


def test_pinned_conversations_keep_their_configuration(store, saved_agent):
    pinned = {
        'id': 'operations',
        'instructions': 'Pinned instructions',
        'config_version': {'id': str(uuid4()), 'version': 3, 'label': 'Old'},
    }
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'migration-admin', 'migration@test')
        version = conn.execute(
            """INSERT INTO ka.agent_versions(agent,version,label,config,created_by)
            VALUES ('operations',3,'Old','{}',%s) RETURNING id""",
            (actor,),
        ).fetchone()['id']
        session = conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test,agent_version_id)
            VALUES (%s,%s,'operations',%s,'Old chat',%s,true,%s) RETURNING id""",
            (uuid4(), project, actor, Jsonb(pinned), version),
        ).fetchone()['id']
        conn.commit()
        run(conn, UP)
        conn.commit()
        saved = conn.execute('SELECT config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        assert saved['config'] == pinned
        assert (
            conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='agent_sessions' AND column_name='agent_version_id'""").fetchone()
            is None
        )


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
