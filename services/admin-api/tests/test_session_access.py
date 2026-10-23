"""Every run stores its access object (ka.agent_sessions.access, also config['access']): what it was given, the children
of what it was given (marked 'via <type> <id>') and its roles; get_available_resources lists it; the prompt no longer
carries the resource block."""

from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from access_helpers import give, of
from knowledge_admin.access import listing
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
        **change,
    }
    created = world.client.post('/api/sessions', headers=HEADERS, json=body)
    assert created.status_code == 201, created.text
    return created.json()['id']


def public(access: dict) -> dict:
    """The access object as get_available_resources and the Debug tab show it: no connections (access.listing)."""
    return {**access, 'resources': listing(access)}


def stored(store, session_id) -> tuple[dict, dict]:
    """(the access column, the pinned configuration)."""
    with store.connect() as conn:
        row = conn.execute('SELECT access,agent_config FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()
    return row['access'], row['agent_config']


def entry(type_: str, id_, name: str, description: str, source: str = 'given', **extra) -> dict:
    return {'type': type_, 'id': str(id_), 'name': name, 'description_short': description, 'source': source, **extra}


def test_a_server_run_is_given_its_server_and_the_services_deployed_on_it(world):
    ssh_ready(world)
    sid = start(world, {'server_id': world.server['id']})
    access, config = stored(world.store, sid)
    server = world.server['id']
    assert config['access'] == access
    access = public(access)
    assert access == {
        'resources': [
            entry('server', server, 'web-1', 'Shop front end'),
            entry('service', world.nginx, 'nginx', 'Front proxy for the shop', f'via server {server}'),
        ],
    }


def test_a_service_run_is_given_its_service_its_code_and_its_servers(world):
    ssh_ready(world)
    access = public(stored(world.store, start(world, {'service_id': str(world.nginx)}))[0])
    via = f'via service {world.nginx}'
    assert access['resources'] == [
        entry('service', world.nginx, 'nginx', 'Front proxy for the shop'),
        entry('code_project', world.shop, 'Shop', '', via, folder_paths=[]),
        entry('server', world.server['id'], 'web-1', 'Shop front end', via),
    ]
    assert 'roles' not in access


def test_an_environment_run_is_given_the_environment_and_what_it_contains(world):
    """Each child names its direct parent: the environment's service brings its code project."""
    ssh_ready(world)
    access = public(stored(world.store, start(world, {'environment_id': str(world.environment)}))[0])
    via = f'via environment {world.environment}'
    assert access['resources'] == [
        entry('environment', world.environment, 'Production', 'Live'),
        entry('service', world.nginx, 'nginx', 'Front proxy for the shop', via),
        entry('server', world.server['id'], 'web-1', 'Shop front end', via),
        entry('code_project', world.shop, 'Shop', '', f'via service {world.nginx}', folder_paths=[]),
    ]


def test_no_run_has_a_role_and_documents_carry_no_editable_flag(world):
    ssh_ready(world)
    plain, _ = stored(world.store, start(world, {'server_id': world.server['id']}))
    assert 'roles' not in plain
    new, config = stored(
        world.store,
        start(
            world,
            {'new_document': NEW},
            resources={'documents': [{'new': NEW}]},
        ),
    )
    [document] = of(config, 'document')
    assert new == {
        'resources': [
            {
                **entry('document', document['id'], 'Shop overview', ''),
                'new': True,  # the new document carries where it goes and what it is built for
                'folder': document['folder'],
                'slug': document['slug'],
                'tags': document['tags'],
                'built_for': [],
            }
        ],
    }
    with world.store.connect() as conn:
        live = conn.execute("SELECT id FROM ka.live_documents WHERE title='web-1 overview'").fetchone()['id']
    revision, _ = stored(
        world.store,
        start(
            world,
            {'document_id': str(live)},
            resources={'documents': [{'id': str(live)}]},
        ),
    )
    assert revision == {
        'resources': [{**entry('document', live, 'web-1 overview', '')}],
    }


def test_a_saved_agent_run_is_given_what_it_picked_inside_its_list_never_more(analyst):
    a = analyst
    session = agent_start(a, resources={'code_project_ids': [a.app]})
    access, config = stored(a.store, session['id'])
    assert access == {
        'resources': [entry('code_project', a.app, 'App', '', folder_paths=[str(a.root / 'app')])],
    }
    assert config['access'] == access
    everything, _ = stored(a.store, agent_start(a)['id'])  # nothing picked: everything it has
    assert [item['id'] for item in everything['resources']] == [a.app, a.worker]
    with a.store.connect() as conn:
        give(conn, a.agent, [('code_project', a.app)])
        before = conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n']
    assert refused(a, 400, resources={'code_project_ids': [a.worker]}) == (
        f"code project {a.worker} is not in the agent's access"
    )
    with a.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == before


def test_a_widening_pick_is_refused_with_the_narrowing_text(analyst, monkeypatch):
    """The run's access is the agent's narrowed by the pick (access.narrow): a pick outside it is a 400 with its text,
    even when nothing before it refused."""
    from knowledge_admin.sessions import run_access

    a = analyst
    real = run_access.run_access

    def without_worker(conn, project, holds, *rest, **change):
        return real(conn, project, [item for item in holds if item['id'] != a.worker], *rest, **change)

    monkeypatch.setattr(run_access, 'run_access', without_worker)
    message = refused(a, 400, resources={'code_project_ids': [a.worker]})
    assert message == f"code project {a.worker} is not in the agent's access"


def test_get_available_resources_lists_the_run_and_filters_by_type(world):
    ssh_ready(world)
    sid = start(world, {'server_id': world.server['id']})
    access, config = stored(world.store, sid)
    access = public(access)
    assert call(config, 'get_available_resources') == access['resources']
    assert call(config, 'get_available_resources', resource_type='service') == [access['resources'][1]]
    assert call(config, 'get_available_resources', resource_type='document') == []


def test_the_prompt_no_longer_carries_the_resource_block(world):
    ssh_ready(world)
    _, config = stored(world.store, start(world, {'server_id': world.server['id']}))
    prompt = get_prompt(config)
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
    }
    preview = world.client.post('/api/sessions/preview', headers=HEADERS, json=body)
    assert preview.status_code == 200, preview.text
    sid = start(world, {'server_id': world.server['id']})
    access, _ = stored(world.store, sid)
    shown = world.client.get(f'/api/sessions/{sid}/debug', headers=HEADERS).json()
    assert shown['access'] == public(access) and preview.json()['access'] == public(access)
    assert preview.json()['config']['access'] == access  # the pinned configuration: the tools read the connections


def test_the_migration_builds_access_for_existing_runs_and_goes_down(world):
    ssh_ready(world)
    sid = start(world, {'service_id': str(world.nginx)})
    with world.store.connect() as conn:
        conn.execute((MIGRATIONS / '0116_drop_run_resources.down.sql').read_text(), prepare=False)
        recorded = {  # as a run started before 0116 recorded what it was given
            'code_project_ids': [str(world.shop)],
            'collection_ids': [],
            'server_ids': [world.server['id']],
            'service_ids': [str(world.nginx)],
        }
        conn.execute('UPDATE ka.agent_sessions SET run_resources=%s WHERE id=%s', (Jsonb(recorded), sid))
        conn.execute((MIGRATIONS / '0112_session_access.down.sql').read_text(), prepare=False)
        row = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
        assert 'access' not in row['agent_config']
        conn.execute((MIGRATIONS / '0112_session_access.up.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0116_drop_run_resources.up.sql').read_text(), prepare=False)
    access, config = stored(world.store, sid)
    names = [(item['type'], item['name'], item['source']) for item in access['resources']]
    assert names == [('code_project', 'Shop', 'given'), ('server', 'web-1', 'given'), ('service', 'nginx', 'given')]
    # migrated access is as the migration wrote it
    assert access['roles'] == {'content_editor': False} and config['access'] == access


def test_code_projects_carry_their_folders_and_graph_and_collections_their_paths():
    from knowledge_admin.knowledge_agents.config import resource_entries

    items = {
        'code_project': [{'id': 'p1', 'name': 'API', 'description': '', 'folder_paths': ['/code/api']}],
        'collection': [{'id': 'c1', 'name': 'docs', 'description': '', 'paths': ['/data/docs']}],
    }
    graphs = [{'id': 'p1', 'name': 'API', 'graph_path': '/graphs/p1', 'code_root': '/code/api'}]
    assert resource_entries(items, graphs) == [
        {
            'type': 'code_project',
            'id': 'p1',
            'name': 'API',
            'description_short': '',
            'folder_paths': ['/code/api'],
            'graph_path': '/graphs/p1',
            'code_root': '/code/api',
        },
        {'type': 'collection', 'id': 'c1', 'name': 'docs', 'description_short': '', 'paths': ['/data/docs']},
    ]


def shape(access: dict) -> list[tuple[str, str, str]]:
    return [(item['type'], item['id'], item['source']) for item in access['resources']]


@pytest.mark.parametrize('kind', ['environment', 'service', 'server'])
def test_an_ad_hoc_run_and_a_saved_agent_run_are_built_alike(world, kind):
    """One way to build a run: an environment, a service or a server given to a saved agent's run brings what a chat
    on it brings, with the same sources; a brought server is held by both, with or without a usable SSH connection
    (a server chat needs SSH to its server)."""
    from access_helpers import give
    from test_generations import native_agent

    resource = {'environment': world.environment, 'service': world.nginx, 'server': world.server['id']}[kind]
    agent = native_agent(world.client, 'Resource agent', model_id=world.model)
    with world.store.connect() as conn:
        give(conn, agent, [(kind, resource)])

    def saved() -> dict:
        body = {'agent_id': agent, 'message': 'Hi', 'resources': {f'{kind}_ids': [str(resource)]}}
        response = world.client.post('/api/sessions', headers=HEADERS, json=body)
        assert response.status_code == 201, response.text
        return response.json()['config']['access']

    for ready in (False, True) if kind != 'server' else (True,):
        if ready:
            ssh_ready(world)
        ad_hoc, _ = stored(world.store, start(world, {f'{kind}_id': str(resource)}))
        assert shape(saved()) == shape(ad_hoc)
        assert ('server', world.server['id']) in [(type_, id_) for type_, id_, _ in shape(ad_hoc)]


def test_the_session_detail_returns_what_the_run_was_given(world):
    """Start new chat with these settings restores what the person chose: an environment, not what it brings."""
    ssh_ready(world)
    session = world.client.get(
        f'/api/sessions/{start(world, {"environment_id": str(world.environment)})}', headers=HEADERS
    ).json()
    assert session['given_resources'] == [{'type': 'environment', 'id': str(world.environment)}]
    assert 'run_resources' not in session and 'chosen_resources' not in session


def test_a_run_has_no_roles(world):
    ssh_ready(world)
    plain, _ = stored(world.store, start(world, {'server_id': world.server['id']}))
    assert 'roles' not in plain
    body = {
        'started_from': {'server_id': world.server['id']},
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        'inventory_editor': True,
    }
    refused = world.client.post('/api/sessions', headers=HEADERS, json=body)
    assert refused.status_code == 400, refused.text
    assert refused.json()['error']['message'] == 'Extra inputs are not permitted'  # the role is gone
    body = {**body, 'content_editor': True}
    del body['inventory_editor']
    assert world.client.post('/api/sessions', headers=HEADERS, json=body).status_code == 400
