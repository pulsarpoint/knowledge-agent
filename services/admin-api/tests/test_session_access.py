"""Every run stores its access object (ka.agent_sessions.access, also config['access']): what it was given, the children
of what it was given (marked 'via <type> <id>') and its roles; get_available_resources lists it; the prompt no longer
carries the resource block."""

from pathlib import Path

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.agents.runner import get_prompt
from test_discovery_runs import call, ssh_ready
from test_repositories import HEADERS
from test_test_sessions import analyst, refused, start as agent_start, tester  # noqa: F401  (fixtures)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
NEW = {'folder': 'shop-docs', 'name': 'Shop overview'}


def start(world, started_from: dict, **change) -> str:
    body = {
        'started_from': started_from,
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        'tools': ['get_document'],
        **change,
    }
    created = world.client.post('/api/sessions', headers=HEADERS, json=body)
    assert created.status_code == 201, created.text
    return created.json()['id']


def stored(store, session_id) -> tuple[dict, dict]:
    """(the access column, the pinned configuration)."""
    with store.connect() as conn:
        row = conn.execute('SELECT access,agent_config FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()
    return row['access'], row['agent_config']


def entry(type_: str, id_, name: str, description: str, source: str = 'given') -> dict:
    return {'type': type_, 'id': str(id_), 'name': name, 'description_short': description, 'source': source}


def test_a_server_run_is_given_its_server_and_the_services_deployed_on_it(world):
    ssh_ready(world)
    sid = start(world, {'server_id': world.server['id']})
    access, config = stored(world.store, sid)
    server = world.server['id']
    assert access == {
        'resources': [
            entry('server', server, 'web-1', 'Shop front end'),
            entry('service', world.nginx, 'nginx', 'Front proxy for the shop', f'via server {server}'),
        ],
        'roles': {'content_editor': False},
    }
    assert config['access'] == access


def test_a_service_run_is_given_its_service_its_code_and_its_servers(world):
    ssh_ready(world)
    access, _ = stored(world.store, start(world, {'service_id': str(world.nginx)}))
    via = f'via service {world.nginx}'
    assert access['resources'] == [
        entry('service', world.nginx, 'nginx', 'Front proxy for the shop'),
        entry('code_project', world.shop, 'Shop', '', via),
        entry('server', world.server['id'], 'web-1', 'Shop front end', via),
    ]
    assert access['roles'] == {'content_editor': False}


def test_an_environment_run_is_given_the_environment_and_what_it_contains(world):
    ssh_ready(world)
    access, _ = stored(world.store, start(world, {'environment_id': str(world.environment)}))
    via = f'via environment {world.environment}'
    assert access['resources'] == [
        entry('environment', world.environment, 'Production', 'Live'),
        entry('service', world.nginx, 'nginx', 'Front proxy for the shop', via),
        entry('server', world.server['id'], 'web-1', 'Shop front end', via),
        entry('code_project', world.shop, 'Shop', '', via),
    ]


def test_writing_runs_are_content_editors_and_a_plain_chat_is_not(world):
    ssh_ready(world)
    plain, _ = stored(world.store, start(world, {'server_id': world.server['id']}))
    assert plain['roles'] == {'content_editor': False}
    wizard, _ = stored(
        world.store,
        start(world, {'server_id': world.server['id']}, tools=['get_document', 'propose_service'], tags=['proposals']),
    )
    assert wizard['roles'] == {'content_editor': True}
    new, config = stored(
        world.store,
        start(
            world,
            {'new_document': NEW},
            resources={'documents': [{'new': NEW, 'editable': True}]},
            tools=['get_document', 'save_document'],
        ),
    )
    document = config['document_access'][0]
    assert new == {
        'resources': [{**entry('document', document['id'], 'Shop overview', ''), 'editable': True}],
        'roles': {'content_editor': True},
    }
    with world.store.connect() as conn:
        live = conn.execute("SELECT id FROM ka.live_documents WHERE title='web-1 overview'").fetchone()['id']
    revision, _ = stored(
        world.store,
        start(
            world,
            {'document_id': str(live)},
            resources={'documents': [{'id': str(live), 'editable': True}]},
            tools=['get_document', 'edit_document'],
        ),
    )
    assert revision == {
        'resources': [{**entry('document', live, 'web-1 overview', ''), 'editable': True}],
        'roles': {'content_editor': True},
    }


def test_a_saved_agent_run_is_given_what_it_picked_inside_its_list_never_more(analyst):
    a = analyst
    session = agent_start(a, run_resources={'code_project_ids': [a.app]})
    access, config = stored(a.store, session['id'])
    assert access == {'resources': [entry('code_project', a.app, 'App', '')], 'roles': {'content_editor': False}}
    assert config['access'] == access
    nothing, _ = stored(a.store, agent_start(a)['id'])
    assert nothing == {'resources': [], 'roles': {'content_editor': False}}
    with a.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET all_repositories=false WHERE id=%s', (a.agent,))
        conn.execute('INSERT INTO ka.agent_repositories(agent_id,repository_id) VALUES (%s,%s)', (a.agent, a.app))
        before = conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n']
    assert refused(a, 400, run_resources={'code_project_ids': [a.worker]}) == (
        'Code project Worker is not allowed for this agent.'
    )
    with a.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == before
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id=%s", (a.agent,))
    fixed, _ = stored(a.store, agent_start(a)['id'])  # a fixed agent: everything it has
    assert fixed == {'resources': [entry('code_project', a.app, 'App', '')], 'roles': {'content_editor': False}}


def test_a_widening_pick_is_refused_with_the_narrowing_text(analyst, monkeypatch):
    """The run's access is the agent's narrowed by the pick (access.narrow): a pick outside it is a 400 with its text,
    even when nothing before it refused."""
    from knowledge_admin.sessions import run_access

    a = analyst
    real = run_access.held

    def without_worker(conn, project, config, environment_ids):
        return [item for item in real(conn, project, config, environment_ids) if item['id'] != a.worker]

    monkeypatch.setattr(run_access, 'held', without_worker)
    message = refused(a, 400, run_resources={'code_project_ids': [a.worker]})
    assert message == f"code_project {a.worker} is not in the agent's access"


def test_get_available_resources_lists_the_run_and_filters_by_type(world):
    ssh_ready(world)
    sid = start(world, {'server_id': world.server['id']})
    access, config = stored(world.store, sid)
    assert call(config, 'get_available_resources') == access['resources']
    assert call(config, 'get_available_resources', resource_type='service') == [access['resources'][1]]
    assert call(config, 'get_available_resources', resource_type='document') == []


def test_the_prompt_no_longer_carries_the_resource_block(world):
    ssh_ready(world)
    _, config = stored(world.store, start(world, {'server_id': world.server['id']}))
    prompt = get_prompt(config, [])
    assert f'Resource: server {world.server["id"]} (web-1)' in prompt
    assert '"ssh_servers"' not in prompt and '"services"' not in prompt and 'Shop front end' not in prompt
    assert 'This run was given only' not in prompt
    assert prompt.startswith('You answer.')


def test_debug_and_preview_return_the_access_object(world):
    ssh_ready(world)
    body = {
        'started_from': {'server_id': world.server['id']},
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        'tools': ['get_document'],
    }
    preview = world.client.post('/api/sessions/preview', headers=HEADERS, json=body)
    assert preview.status_code == 200, preview.text
    sid = start(world, {'server_id': world.server['id']})
    access, _ = stored(world.store, sid)
    shown = world.client.get(f'/api/sessions/{sid}/debug', headers=HEADERS).json()
    assert shown['access'] == access and preview.json()['access'] == access
    assert preview.json()['config']['access'] == access


def test_the_migration_builds_access_for_existing_runs_and_goes_down(world):
    ssh_ready(world)
    sid = start(world, {'service_id': str(world.nginx)})
    with world.store.connect() as conn:
        conn.execute((MIGRATIONS / '0112_session_access.down.sql').read_text(), prepare=False)
        row = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
        assert 'access' not in row['agent_config']
        conn.execute((MIGRATIONS / '0112_session_access.up.sql').read_text(), prepare=False)
    access, config = stored(world.store, sid)
    names = [(item['type'], item['name'], item['source']) for item in access['resources']]
    assert names == [('code_project', 'Shop', 'given'), ('server', 'web-1', 'given'), ('service', 'nginx', 'given')]
    assert access['roles'] == {'content_editor': False} and config['access'] == access
