"""The access object is the only description of what a run may use: no pinned configuration carries per-kind
sections; server and MCP entries carry their connection (references only, never secrets), which reaches the tools but
never get_available_resources, the Debug tab or the prompt."""

import asyncio
from pathlib import Path
from uuid import UUID

from access_helpers import give
from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin import access as access_rules
from knowledge_admin.agents import runner
from knowledge_admin.capabilities import code_graph, mcp, ssh
from knowledge_admin.knowledge_agents.config import credential_references
from knowledge_admin.tools.catalog import CATALOG
from test_discovery_runs import call, ssh_ready
from test_repositories import HEADERS
from test_session_access import NEW, start, stored
from test_test_sessions import analyst, start as agent_start, tester  # noqa: F401  (fixtures)

PROJECT = '5f0c2d7e-0000-4000-8000-000000000001'
SECTIONS = ('code', 'documents', 'ssh', 'services', 'skills', 'mcp', 'document_access')
CONNECTION = ('hostname', 'port', 'username', 'allow_sudo', 'known_hosts', 'private_key_ref', 'url', 'bearer_token_ref')


def shown(entries: list[dict]) -> set[str]:
    return {key for item in entries for key in item}


def test_a_saved_agent_run_pins_no_resource_sections(analyst):
    session = agent_start(analyst)
    _, config = stored(analyst.store, session['id'])
    assert not set(SECTIONS) & set(config)
    assert [item['id'] for item in access_rules.listing(config['access'], 'code_project')] == [
        analyst.app,
        analyst.worker,
    ]


def test_ad_hoc_runs_pin_no_resource_sections(world):
    ssh_ready(world)
    for started_from, change in (
        ({'server_id': world.server['id']}, {}),
        ({'environment_id': str(world.environment)}, {}),
        ({'new_document': NEW}, {'resources': {'documents': [{'new': NEW}]}}),
    ):
        _, config = stored(world.store, start(world, started_from, **change))
        assert not set(SECTIONS) & set(config), started_from


def test_server_entries_carry_their_connection_which_only_the_tools_see(world):
    ssh_ready(world)
    sid = start(world, {'server_id': world.server['id']})
    access, config = stored(world.store, sid)
    [server] = [item for item in access['resources'] if item['type'] == 'server']
    assert server['hostname'] == world.server['hostname'] and server['port'] == 22 and server['username'] == 'ops'
    assert server['private_key_ref']['table'] == 'ka.credentials' and 'PRIVATE KEY' not in str(access)
    assert server['enabled'] and server['ssh_allowed'] and server['allow_sudo'] is False
    assert not set(CONNECTION) & shown(call(config, 'get_available_resources'))
    debug = world.client.get(f'/api/sessions/{sid}/debug', headers=HEADERS).json()
    assert not set(CONNECTION) & shown(debug['access']['resources'])
    assert world.server['hostname'] not in debug['instructions'] and 'ops' not in runner.get_prompt(config)
    assert runner.ssh_servers(config) == [server]
    assert credential_references(config)[1:] == [server['private_key_ref']]


def test_mcp_entries_carry_their_url_and_token_reference(analyst):
    a = analyst
    created = a.client.post(
        '/api/mcp/servers', headers=HEADERS, json={'name': 'Docs', 'url': 'http://mock.test/mcp', 'token': 'tok-1'}
    )
    assert created.status_code in (200, 201), created.text
    mcp_id = created.json()['id']
    with a.store.connect() as conn:
        give(conn, a.agent, [('code_project', a.app), ('mcp_server', mcp_id)])
    _, config = stored(a.store, agent_start(a)['id'])
    [entry] = [item for item in config['access']['resources'] if item['type'] == 'mcp_server']
    assert mcp.selected_servers(config) == [entry] and not set(SECTIONS) & set(config)
    assert entry['url'] == 'http://mock.test/mcp' and entry['enabled'] is True
    assert entry['bearer_token_ref']['table'] == 'ka.mcp_servers' and 'tok-1' not in str(config)
    assert not set(CONNECTION) & shown(access_rules.listing(config['access']))
    assert entry['bearer_token_ref'] in credential_references(config)


def config_of(*entries: dict, grounded: bool = False) -> dict:
    """A configuration that is only its access object (and what every run has: a prompt, a model, grounding)."""
    return {
        'id': 'agent',
        'name': 'Agent',
        'model': None,
        'system_prompt': {'content': 'You answer.'},
        'instructions': '',
        'grounded': grounded,
        'project_id': PROJECT,
        'access': {'resources': [{**item, 'source': 'given'} for item in entries]},
    }


SERVER = {
    'type': 'server',
    'id': 's1',
    'name': 'web-1',
    'description_short': '',
    'hostname': '10.0.0.7',
    'port': 2222,
    'username': 'ops',
    'allow_sudo': True,
    'known_hosts': '',
    'enabled': True,
    'ssh_allowed': True,
    'private_key_ref': {'table': 'ka.credentials', 'id': 'k1', 'aad': 'a'},
}


def test_the_tools_work_from_access_entries_alone(monkeypatch, tmp_path):
    code = {
        'type': 'code_project',
        'id': 'p1',
        'name': 'API',
        'description_short': '',
        'folder_paths': [str(tmp_path / 'api')],
    }
    collection = {'type': 'collection', 'id': 'c1', 'name': 'docs', 'description_short': '', 'paths': ['/data/docs']}
    config = config_of(SERVER, code, collection)
    assert runner.code_paths(config) == [tmp_path / 'api']
    assert runner.document_paths(config) == [Path('/data/docs')]
    assert runner.document_paths(config_of(SERVER, code, collection, grounded=True)) == []
    assert runner.get_ssh(config, {'k1': 'KEY'}) == [config['access']['resources'][0]]

    seen = {}

    async def execute(connection, command, **_):
        seen['connection'] = connection
        return {'exit_code': 0, 'stdout': 'up', 'stderr': ''}

    monkeypatch.setattr(ssh, 'execute', execute)
    context = {'config': config, 'credentials': {'k1': 'KEY'}}
    spec = CATALOG['ssh_run']
    asyncio.run(spec.run(context, spec.arguments.model_validate({'server_id': 's1', 'command': 'uptime'})))
    connection = seen['connection']
    assert (connection.hostname, connection.port, connection.username, connection.allow_sudo) == (
        '10.0.0.7',
        2222,
        'ops',
        True,
    )

    loaded = []

    def load_graphs(conn, project_id, ids):
        loaded.append((project_id, list(ids)))
        return [{'id': 'p1', 'name': 'API', 'graph_path': '/g/p1', 'code_root': '/code', 'files': 3}]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(code_graph, 'load_graphs', load_graphs)
    monkeypatch.setattr(code_graph, 'connect', lambda: Connection())
    spec = CATALOG['code_graph']
    text = asyncio.run(spec.run(context, spec.arguments.model_validate({'project_id': 'p1', 'action': 'overview'})))
    assert loaded == [(UUID(PROJECT), ['p1'])] and '"files": 3' in text and '/g/p1' not in text
