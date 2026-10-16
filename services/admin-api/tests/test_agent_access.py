"""Phase 2: a saved agent stores its access object (ka.agents.access) and instructions (ka.agents.instructions); 0113
converts the per-type tables into it; every run of the agent narrows it (its whole access, or a picked subset; never
more); handlers and experts use it."""

from pathlib import Path
from uuid import uuid4

from access_helpers import give
from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.events import processor as event_processor
from knowledge_admin.knowledge_agents import experts
from knowledge_admin.knowledge_agents.config import get_agent_config
from test_discovery_runs import ssh_ready
from test_event_processor import a_handler, an_event, runs
from test_profiles import create
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0113_agent_access.up.sql', '0113_agent_access.down.sql'
DROP_UP, DROP_DOWN = '0114_drop_agent_resource_tables.up.sql', '0114_drop_agent_resource_tables.down.sql'
EMPTY = {'resources': [], 'roles': {'content_editor': False}}


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_saved_agent(world, name: str = 'Operator') -> str:
    agent = create(world.client, name)
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s,enabled=true WHERE id=%s', (world.model, agent['id']))
    return agent['id']


def start(world, agent_id: str, **body):
    return world.client.post(
        '/api/sessions', headers=HEADERS, json={'agent_id': agent_id, 'message': 'Check it.', **body}
    )


def stored_access(world, session_id) -> dict:
    with world.store.connect() as conn:
        return conn.execute('SELECT access FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()['access']


def entry(type_: str, id_, name: str, description: str, source: str = 'given', **extra) -> dict:
    return {'type': type_, 'id': str(id_), 'name': name, 'description_short': description, 'source': source, **extra}


# --- 0113 ----------------------------------------------------------------------------------------------------------


def test_the_migration_converts_each_agent_and_goes_down(world):
    w = world
    fixed, per_run, everything, off, docs = (
        a_saved_agent(w, name) for name in ('Fixed', 'Per run', 'All', 'Off', 'Docs')
    )
    other = uuid4()
    skill = w.client.post(
        '/api/skills', headers=HEADERS, json={'name': 'inspect', 'description': 'Inspect', 'instructions': 'Look.'}
    ).json()['id']
    mcp = w.client.post(
        '/api/mcp/servers', headers=HEADERS, json={'name': 'Docs', 'url': 'http://mock.test/mcp'}
    ).json()['id']
    with w.store.connect() as conn:
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (w.project,))
        collections = sorted(
            str(
                conn.execute(
                    'INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,%s) RETURNING id', (w.project, name)
                ).fetchone()['id']
            )
            for name in ('runbooks', 'notes')
        )
        gone = conn.execute(
            "INSERT INTO ka.rag_collections(project_id,name,deleted_at) VALUES (%s,'old',now()) RETURNING id",
            (w.project,),
        ).fetchone()['id']
        run(conn, DROP_DOWN)  # the old tables back; these agents have no access yet
        run(conn, DOWN)
        conn.execute(
            "INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'other','Other')", (other, w.project)
        )
        conn.execute(
            """UPDATE ka.agents SET resources=CASE WHEN id=%s THEN 'per_run' ELSE 'fixed' END,ssh_enabled=true,
            services_enabled=true,repositories_enabled=true,all_repositories=false,documents_enabled=false,
            skills_enabled=false,mcp_enabled=false""",
            (per_run,),
        )
        for agent in (fixed, per_run, off):
            conn.execute(  # the settings row operations_servers needs (the wizard wrote it before phase 2)
                'INSERT INTO ka.operations_settings(project_id,agent) VALUES (%s,%s)', (w.project, agent)
            )
            conn.execute(
                'INSERT INTO ka.operations_servers(project_id,agent,server_id) VALUES (%s,%s,%s)',
                (w.project, agent, w.server['id']),
            )
            conn.execute('INSERT INTO ka.agent_services(agent_id,service_id) VALUES (%s,%s)', (agent, w.nginx))
            conn.execute('INSERT INTO ka.agent_repositories(agent_id,repository_id) VALUES (%s,%s)', (agent, w.shop))
        conn.execute('UPDATE ka.agents SET all_repositories=true WHERE id=%s', (everything,))
        conn.execute('UPDATE ka.agents SET ssh_enabled=false,repositories_enabled=false WHERE id=%s', (off,))
        # all collections (a deleted one is left out), a skill and an agent-level MCP server
        conn.execute(
            """UPDATE ka.agents SET ssh_enabled=false,services_enabled=false,repositories_enabled=false,
            documents_enabled=true,all_collections=true,skills_enabled=true,mcp_enabled=true WHERE id=%s""",
            (docs,),
        )
        conn.execute('INSERT INTO ka.agent_collections(agent_id,collection_id) VALUES (%s,%s)', (docs, gone))
        conn.execute(
            'INSERT INTO ka.skills_agents(agent_id,skill_id,project_id) VALUES (%s,%s,%s)', (docs, skill, w.project)
        )
        conn.execute(
            """INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills)
            VALUES (%s,%s,'',false) ON CONFLICT DO NOTHING""",
            (w.project, docs),
        )
        conn.execute(
            """INSERT INTO ka.agent_config_mcp_servers(config_id,project_id,mcp_server_id)
            SELECT id,project_id,%s FROM ka.agent_configs WHERE agent=%s AND server_id IS NULL""",
            (mcp, docs),
        )
        conn.execute(
            """INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills)
            VALUES (%s,%s,'Check the disk first.',false) ON CONFLICT DO NOTHING""",
            (w.project, fixed),
        )
        conn.execute(
            "UPDATE ka.agent_configs SET instructions='Check the disk first.' WHERE agent=%s AND server_id IS NULL",
            (fixed,),
        )
        run(conn, UP)
        rows = {row['id']: row for row in conn.execute('SELECT id,access,instructions FROM ka.agents').fetchall()}
    server, service = {'type': 'server', 'id': w.server['id']}, {'type': 'service', 'id': str(w.nginx)}
    shop, other_project = {'type': 'code_project', 'id': str(w.shop)}, {'type': 'code_project', 'id': str(other)}
    both = sorted([shop, other_project], key=lambda item: item['id'])
    assert rows[fixed]['access'] == {'resources': [server, service, shop], 'roles': {'content_editor': False}}
    assert rows[per_run]['access'] == rows[fixed]['access']  # fixed or per run: the same list
    assert rows[everything]['access'] == {'resources': both, 'roles': {'content_editor': False}}
    assert rows[off]['access'] == {'resources': [service], 'roles': {'content_editor': False}}  # switched off: left out
    assert rows[docs]['access'] == {
        'resources': [
            *({'type': 'collection', 'id': item} for item in collections),
            {'type': 'skill', 'id': skill},
            {'type': 'mcp_server', 'id': mcp},
        ],
        'roles': {'content_editor': False},
    }
    assert rows[fixed]['instructions'] == 'Check the disk first.' and rows[off]['instructions'] == ''
    with w.store.connect() as conn:
        run(conn, DOWN)
        columns = {
            row['column_name']
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'"
            )
        }
        run(conn, UP)  # the tests' database stays at the latest schema
        run(conn, DROP_UP)
    assert not {'access', 'instructions'} & columns


# --- runs of a saved agent ------------------------------------------------------------------------------------------


def test_a_saved_agent_run_is_given_its_whole_access_with_children(world):
    w = world
    ssh_ready(w)
    agent = a_saved_agent(w)
    with w.store.connect() as conn:
        give(conn, agent, [('service', w.nginx), ('server', w.server['id']), ('code_project', w.shop)])
        conn.execute("UPDATE ka.agents SET instructions='Look at nginx.' WHERE id=%s", (agent,))
    config = get_agent_config(agent, conninfo=w.store.conninfo)
    assert config['instructions'] == 'Look at nginx.'
    assert [item['id'] for item in config['services']['items']] == [str(w.nginx)] and config['services']['enabled']
    assert config['access']['resources'][0] == {'type': 'service', 'id': str(w.nginx)}
    response = start(w, agent)
    assert response.status_code == 201, response.text
    assert stored_access(w, response.json()['id']) == {
        'resources': [
            entry('server', w.server['id'], 'web-1', 'Shop front end'),
            entry('service', w.nginx, 'nginx', 'Front proxy for the shop'),
            entry('code_project', w.shop, 'Shop', '', folder_paths=[]),
        ],
        'roles': {'content_editor': False},
    }
    picked = start(w, agent, resources={'service_ids': [str(w.nginx)]})  # a subset: its children come with it
    assert picked.status_code == 201, picked.text
    via = f'via service {w.nginx}'
    session = picked.json()
    assert stored_access(w, session['id'])['resources'] == [
        entry('service', w.nginx, 'nginx', 'Front proxy for the shop'),
        entry('code_project', w.shop, 'Shop', '', via, folder_paths=[]),
        entry('server', w.server['id'], 'web-1', 'Shop front end', via),
    ]
    assert [item['id'] for item in session['config']['services']['items']] == [str(w.nginx)]


def test_a_pick_outside_the_agents_access_is_refused_with_the_narrowing_text(world):
    w = world
    agent = a_saved_agent(w)
    with w.store.connect() as conn:
        give(conn, agent, [('service', w.nginx)])
        before = conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n']
    for resources, message in (
        ({'server_ids': [w.server['id']]}, f"server {w.server['id']} is not in the agent's access"),
        ({'environment_ids': [str(w.environment)]}, f"environment {w.environment} is not in the agent's access"),
    ):
        for path in ('/preview', ''):
            response = w.client.post(
                f'/api/sessions{path}',
                headers=HEADERS,
                json={'agent_id': agent, 'message': 'Check it.', 'resources': resources},
            )
            assert response.status_code == 400, response.text
            assert response.json()['error']['message'] == message
    with w.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == before


def test_environments_and_documents_may_be_picked_when_the_agent_has_them(world):
    w = world
    agent = a_saved_agent(w)
    with w.store.connect() as conn:
        document = conn.execute("SELECT id FROM ka.live_documents WHERE title='nginx runbook'").fetchone()['id']
        give(
            conn,
            agent,
            [
                ('environment', w.environment),
                ('service', w.nginx),
                {'type': 'document', 'id': document, 'editable': True},
            ],
        )
    response = start(
        w,
        agent,
        resources={'environment_ids': [str(w.environment)], 'documents': [{'id': str(document), 'editable': True}]},
    )
    assert response.status_code == 201, response.text
    access = stored_access(w, response.json()['id'])
    assert access['resources'] == [
        entry('environment', w.environment, 'Production', 'Live'),
        {**entry('document', document, 'nginx runbook', ''), 'editable': True},
        entry('service', w.nginx, 'nginx', 'Front proxy for the shop', f'via environment {w.environment}'),
    ]
    read_only = start(w, agent, resources={'documents': [{'id': str(document), 'editable': False}]})
    assert read_only.status_code == 201, read_only.text  # a read-only pick narrows the editable entry
    assert stored_access(w, read_only.json()['id'])['resources'] == [
        {**entry('document', document, 'nginx runbook', ''), 'editable': False}
    ]


def test_the_content_editor_role_comes_from_the_agent(world):
    w = world
    agent = a_saved_agent(w)
    with w.store.connect() as conn:
        give(conn, agent, [('service', w.nginx)])
    off = start(w, agent)
    assert off.status_code == 201 and stored_access(w, off.json()['id'])['roles'] == {'content_editor': False}
    with w.store.connect() as conn:
        give(conn, agent, [('service', w.nginx)], content_editor=True)
    on = start(w, agent)
    assert on.status_code == 201 and stored_access(w, on.json()['id'])['roles'] == {'content_editor': True}


def test_a_deleted_resource_in_the_access_is_skipped(world):
    w = world
    agent = a_saved_agent(w)
    gone = str(uuid4())
    with w.store.connect() as conn:
        give(conn, agent, [('service', w.nginx), ('code_project', gone), ('collection', gone), ('server', gone)])
    config = get_agent_config(agent, conninfo=w.store.conninfo)
    assert config['code'] == {'enabled': False, 'all_projects': False, 'projects': [], 'graphs': []}
    assert not config['ssh']['enabled'] and not config['documents']['enabled']
    response = start(w, agent)
    assert response.status_code == 201, response.text
    assert [item['id'] for item in stored_access(w, response.json()['id'])['resources']] == [str(w.nginx)]


def test_an_agent_without_access_runs_with_nothing(world):
    w = world
    agent = a_saved_agent(w)
    response = start(w, agent)
    assert response.status_code == 201, response.text
    assert stored_access(w, response.json()['id']) == EMPTY


# --- handlers and experts -------------------------------------------------------------------------------------------


def test_a_handler_can_send_to_any_enabled_agent_with_its_whole_access(world):
    w = world
    agent = a_saved_agent(w)
    with w.store.connect() as conn:
        give(conn, agent, [('service', w.nginx)])
    listed = w.client.get('/api/event-handlers/agents', headers=HEADERS).json()['agents']
    assert agent in [row['id'] for row in listed]
    a_handler(w.store, action='agent', emit=None, agent_id=agent, prompt='Triage {{ event.subject }}')
    first = an_event(w.store)
    event_processor.run_once(w.store)
    [handler_run] = runs(w.store, first['id'])
    assert handler_run['status'] == 'succeeded', handler_run['message']
    access = stored_access(w, handler_run['agent_session_id'])
    assert [item['id'] for item in access['resources']] == [str(w.nginx)]


def test_an_expert_narrows_its_own_access(world):
    w = world
    ssh_ready(w)
    expert = a_saved_agent(w, 'Payments expert')
    with w.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET expert=true,when_to_use='Payments.' WHERE id=%s", (expert,))
        give(conn, expert, [('service', w.nginx), ('server', w.server['id'])])
        [offered] = [item for item in experts.offered(conn, None) if item['id'] == expert]
    assert offered['accepts'] == ['server_ids', 'service_ids']
    assert 'it accepts: servers, services' in offered['description']

    whole, _ = experts.load(w.store, w.project, offered, None)
    assert [(item['type'], item['source']) for item in whole['access']['resources']] == [
        ('server', 'given'),
        ('service', 'given'),
    ]
    narrowed, _ = experts.load(w.store, w.project, offered, {'service_ids': [str(w.nginx)]})
    assert [(item['type'], item['source']) for item in narrowed['access']['resources']] == [
        ('service', 'given'),
        ('server', f'via service {w.nginx}'),  # what it picked brings its children
    ]
    assert not narrowed['access']['roles']['content_editor']
    try:
        experts.load(w.store, w.project, offered, {'code_project_ids': [str(w.shop)]})
    except ValueError as exc:
        assert str(exc) == f"code project {w.shop} is not in the agent's access"
    else:
        raise AssertionError('a resource outside the expert access was given')


def test_a_server_without_usable_ssh_stays_in_the_run_and_may_be_picked(world):
    w = world  # web-1 has no SSH key: ssh_run fails on the server's own settings, the run still holds it
    agent = a_saved_agent(w)
    with w.store.connect() as conn:
        give(conn, agent, [('server', w.server['id'])])
    for resources in ({}, {'server_ids': [w.server['id']]}):
        response = start(w, agent, resources=resources)
        assert response.status_code == 201, response.text
        [server] = stored_access(w, response.json()['id'])['resources']
        assert (server['type'], server['id'], server['source']) == ('server', w.server['id'], 'given')


def test_an_expert_is_a_content_editor_only_when_it_and_its_caller_are(world):
    w = world
    expert = a_saved_agent(w, 'Writer expert')
    with w.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET expert=true,when_to_use='Writing.' WHERE id=%s", (expert,))
        [offered] = [item for item in experts.offered(conn, None) if item['id'] == expert]
    for own, caller, expected in ((True, True, True), (True, False, False), (False, True, False)):
        with w.store.connect() as conn:
            give(conn, expert, [('service', w.nginx)], content_editor=own)
        config, _ = experts.load(w.store, w.project, offered, None, caller)
        assert config['access']['roles'] == {'content_editor': expected}, (own, caller)


# --- 0114 ----------------------------------------------------------------------------------------------------------

OLD_TABLES = {
    'agent_repositories',
    'agent_collections',
    'agent_services',
    'skills_agents',
    'operations_servers',
    'operations_settings',
    'agent_configs',
    'agent_config_skills',
    'agent_config_mcp_servers',
    'agent_server_connections',
}
OLD_COLUMNS = {
    'documents_enabled',
    'repositories_enabled',
    'ssh_enabled',
    'skills_enabled',
    'mcp_enabled',
    'services_enabled',
    'all_collections',
    'all_repositories',
    'resources',
}


def old_names(conn) -> tuple[set, set]:
    tables = {
        row['table_name']
        for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='ka'")
    } & OLD_TABLES
    columns = {
        row['column_name']
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'"
        )
    } & OLD_COLUMNS
    return tables, columns


def test_the_old_storage_is_dropped_and_down_converts_the_access_back(world):
    w = world
    agent, empty = a_saved_agent(w, 'Operator'), a_saved_agent(w, 'Empty')
    skill = w.client.post(
        '/api/skills', headers=HEADERS, json={'name': 'inspect', 'description': 'Inspect', 'instructions': 'Look.'}
    ).json()['id']
    mcp = w.client.post(
        '/api/mcp/servers', headers=HEADERS, json={'name': 'Docs', 'url': 'http://mock.test/mcp'}
    ).json()['id']
    with w.store.connect() as conn:
        assert old_names(conn) == (set(), set())
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (w.project,))
        collection = conn.execute(
            "INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'runbooks') RETURNING id", (w.project,)
        ).fetchone()['id']
        give(
            conn,
            agent,
            [
                ('server', w.server['id']),
                ('service', w.nginx),
                ('environment', w.environment),  # no old table: left out
                ('code_project', w.shop),
                ('collection', collection),
                ('collection', uuid4()),  # deleted for good: left out
                ('skill', skill),
                ('mcp_server', mcp),
            ],
        )
        conn.execute("UPDATE ka.agents SET instructions='Check the disk first.' WHERE id=%s", (agent,))
        run(conn, DROP_DOWN)
        assert old_names(conn) == (OLD_TABLES, OLD_COLUMNS)
        flags = conn.execute(
            """SELECT id,documents_enabled,repositories_enabled,ssh_enabled,skills_enabled,mcp_enabled,
            services_enabled,all_collections,all_repositories,resources FROM ka.agents WHERE id=ANY(%s)""",
            ([agent, empty],),
        ).fetchall()
        selections = {
            table: [tuple(str(value) for value in row.values()) for row in conn.execute(query, (agent,))]
            for table, query in {
                'servers': 'SELECT server_id FROM ka.operations_servers WHERE agent=%s',
                'settings': 'SELECT project_id FROM ka.operations_settings WHERE agent=%s',
                'services': 'SELECT service_id FROM ka.agent_services WHERE agent_id=%s',
                'repositories': 'SELECT repository_id FROM ka.agent_repositories WHERE agent_id=%s',
                'collections': 'SELECT collection_id FROM ka.agent_collections WHERE agent_id=%s',
                'skills': 'SELECT skill_id FROM ka.skills_agents WHERE agent_id=%s',
                'configs': 'SELECT instructions FROM ka.agent_configs WHERE agent=%s AND server_id IS NULL',
                'mcp': """SELECT m.mcp_server_id FROM ka.agent_config_mcp_servers m
                    JOIN ka.agent_configs g ON g.id=m.config_id WHERE g.agent=%s""",
            }.items()
        }
        run(conn, DROP_UP)
        assert old_names(conn) == (set(), set())
    switches = {row['id']: row for row in flags}
    assert {key: value for key, value in switches[agent].items() if key != 'id'} == {
        **dict.fromkeys(
            (
                'documents_enabled',
                'repositories_enabled',
                'ssh_enabled',
                'skills_enabled',
                'mcp_enabled',
                'services_enabled',
            ),
            True,
        ),
        'all_collections': False,
        'all_repositories': False,
        'resources': 'fixed',
    }
    assert not any(value for key, value in switches[empty].items() if key not in ('id', 'resources'))
    assert selections == {
        'servers': [(w.server['id'],)],
        'settings': [(str(w.project),)],
        'services': [(str(w.nginx),)],
        'repositories': [(str(w.shop),)],
        'collections': [(str(collection),)],
        'skills': [(skill,)],
        'configs': [('Check the disk first.',)],
        'mcp': [(mcp,)],
    }


def test_an_agent_with_access_can_be_deleted(world):
    agent = a_saved_agent(world)
    with world.store.connect() as conn:
        give(conn, agent, [('server', world.server['id']), ('service', world.nginx), ('code_project', world.shop)])
    revision = world.client.get(f'/api/agents/{agent}', headers=HEADERS).json()['revision']
    deleted = world.client.request(
        'DELETE', f'/api/agents/{agent}', headers=HEADERS, json={'revision': revision, 'confirmed': True}
    )
    assert deleted.status_code == 204, deleted.text
    assert world.client.get(f'/api/agents/{agent}', headers=HEADERS).status_code == 404
