"""capabilities/inventory_tools.py: agent tools that read and change the inventory. Reads need the item in the run's
access; writes need the inventory editor role and the target in the access or created by this session. Writes go
through inventory/store.py with the session's person, agent and session."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from knowledge_admin.agents.runner import instructions_and_tools
from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities import inventory_tools as tools
from knowledge_admin.inventory import store as inventory_store
from knowledge_admin.knowledge_agents.subagents import subagent_config
from knowledge_admin.tools.catalog import CATALOG, INVENTORY_WRITES
from session_rows import a_model, add_test_session
from test_api import _client
from test_repositories import HEADERS
from test_tool_catalog import config as run_config

OFF = 'This agent may not change the inventory (inventory editor is off).'
READS = ['get_environment_model', 'list_servers', 'list_deployments', 'list_connections']
WRITES = ['save_server', 'save_service', 'save_deployment', 'save_connection', 'save_relation', 'remove_inventory_item']


def entry(type_: str, id_, source: str = 'given') -> dict:
    return {'type': type_, 'id': str(id_), 'name': type_, 'description_short': '', 'source': source}


def access(*entries: dict, editor: bool = True) -> dict:
    return {'resources': list(entries), 'roles': {'content_editor': False, 'inventory_editor': editor}}


def call(config: dict, tool: str, /, **arguments) -> dict:
    spec = CATALOG[tool]
    return json.loads(asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments))))


@pytest.fixture
def world(store, monkeypatch):
    """Project shop: environment prod with server a running api (deployment api_a) and server b running db (db_b);
    environment staging with server c running web (web_c); a session of agent ops-agent started by a person."""
    monkeypatch.setattr(inventory, '_store', store)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'ops', 'ops@example.test')
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('ops-agent','Ops agent','')")
        sid = add_test_session(conn, project, actor, 'ops-agent', a_model(conn, actor))
        by = {'actor_id': actor}
        w = {'project': project, 'actor': actor, 'sid': sid}
        for name in ('a', 'b', 'c'):
            w[name] = inventory_store.create_server(conn, project, {'name': name, 'hostname': f'{name}.internal'}, by)
        for name in ('api', 'db', 'web'):
            w[name] = inventory_store.create_service(conn, project, {'name': name}, by)
        for name in ('prod', 'staging'):
            w[name] = inventory_store.create_environment(conn, project, {'name': name}, by)
        for key, service, server, env, port in [
            ('api_a', 'api', 'a', 'prod', 8080),
            ('db_b', 'db', 'b', 'prod', 5432),
            ('web_c', 'web', 'c', 'staging', 80),
        ]:
            w[key] = inventory_store.create_deployment(
                conn,
                project,
                {
                    'service_id': w[service]['id'],
                    'server_id': w[server]['id'],
                    'environment_id': w[env]['id'],
                    'port': port,
                },
                by,
            )
    # the run's access: environment prod with its children, as a run start expands it
    prod = entry('environment', w['prod']['id'])
    via = f'via environment {w["prod"]["id"]}'
    children = [
        entry(t, w[k]['id'], via) for t, k in [('server', 'a'), ('server', 'b'), ('service', 'api'), ('service', 'db')]
    ]
    w['access'] = access(prod, *children)
    w['config'] = {'project_id': str(project), 'session_id': str(sid), 'access': w['access']}
    w['store'] = store
    return w


def cfg(w, **changes) -> dict:
    return {**w['config'], **changes}


def i(item) -> str:
    return str(item['id'])


# --- the pure checks ---


def test_reads_need_the_item_in_the_runs_access():
    a = access(entry('environment', 'e1'), entry('server', 's1'), entry('service', 'v1'), editor=False)
    assert tools.check_get_environment_model(a, environment_id='e1') is None
    assert tools.check_get_environment_model(a, environment_id='e9') == 'This environment is not available in this run.'
    assert tools.check_list_servers(a, environment_id='e9') == 'This environment is not available in this run.'
    assert tools.check_list_deployments(a, server_id='s1') is None
    assert tools.check_list_deployments(a, service_id='v9') == 'This service is not available in this run.'
    assert tools.check_list_deployments(a) == 'Give either server_id or service_id.'
    assert tools.check_list_connections(a, service_id='v1') is None
    rows = {('deployment', 'p1'): {'server_id': 's9', 'service_id': 'v9', 'environment_id': 'e1'}}
    assert tools.check_list_connections(a, deployment_id='p1', rows=rows) is None  # its environment is usable
    rows = {('deployment', 'p1'): {'server_id': 's9', 'service_id': 'v9', 'environment_id': 'e9'}}
    assert (
        tools.check_list_connections(a, deployment_id='p1', rows=rows)
        == 'This deployment is not available in this run.'
    )
    assert tools.check_list_connections(a, deployment_id='p2') == 'This deployment is not available in this run.'


def test_writes_need_the_role_then_the_target_in_access_or_created_by_this_session():
    a = access(entry('environment', 'e1'), entry('server', 's1'), entry('service', 'v1'))
    off = {**a, 'roles': {'inventory_editor': False}}
    assert tools.check_save_server(off, name='x', hostname='x') == OFF
    assert tools.check_remove_inventory_item(off, kind='server', id='s1') == OFF
    # a new server or service: the role and an environment in the access
    assert tools.check_save_server(a, name='x', hostname='x') is None
    assert tools.check_save_service(a, name='x') is None
    no_environment = access(entry('server', 's1'))
    assert tools.check_save_server(no_environment, name='x', hostname='x') == 'This run has no environments.'
    # an update: in access, or created by this session
    assert tools.check_save_server(a, id='s1') is None
    assert tools.check_save_server(a, id='s9') == 'This server is not available in this run.'
    assert tools.check_save_server(a, id='s9', created={('server', 's9')}) is None
    # a deployment: its environment, server and service
    assert tools.check_save_deployment(a, service_id='v1', server_id='s9', environment_id='e1') == (
        'This server is not available in this run.'
    )
    assert (
        tools.check_save_deployment(a, service_id='v1', server_id='s9', environment_id='e1', created={('server', 's9')})
        is None
    )
    assert tools.check_save_deployment(a, service_id='v1', server_id='s1', environment_id='e9') == (
        'This environment is not available in this run.'
    )
    # a connection: from (and to) a usable deployment; a deployment created here is usable
    rows = {('deployment', 'p1'): {'server_id': 's1', 'service_id': 'v1', 'environment_id': 'e1'}}
    assert tools.check_save_connection(a, from_deployment_id='p1', to_host='db.example', port=5432, rows=rows) is None
    assert tools.check_save_connection(a, from_deployment_id='p9', to_host='db.example', port=5432) == (
        'This deployment is not available in this run.'
    )
    assert (
        tools.check_save_connection(
            a, from_deployment_id='p9', to_deployment_id='p1', rows=rows, created={('deployment', 'p9')}
        )
        is None
    )
    connection = {('connection', 'n1'): {'from_deployment_id': 'p1'}, **rows}
    assert tools.check_save_connection(a, id='n1', description='x', rows=connection) is None
    assert tools.check_remove_inventory_item(a, kind='connection', id='n1', rows=connection) is None
    # a relation: from a usable service, to a usable service or code project
    assert tools.check_save_relation(a, from_service_id='v1', kind='depends_on', to_service_id='v9') == (
        'This service is not available in this run.'
    )
    assert tools.check_save_relation(a, from_service_id='v1', kind='built_from', to_code_project_id='c1') == (
        'This code project is not available in this run.'
    )
    relation = {('relation', 'r1'): {'from_service_id': 'v1'}}
    assert tools.check_remove_inventory_item(a, kind='relation', id='r1', rows=relation) is None
    assert (
        tools.check_remove_inventory_item(a, kind='relation', id='r2') == 'This relation is not available in this run.'
    )


def test_subagents_and_experts_get_the_reads_but_not_the_writes():
    parent = run_config()
    parent['session_id'] = 'abc'

    def names(c, subagents=True):
        return [tool.name for tool in instructions_and_tools(c, subagents=subagents)[1]]

    assert set(INVENTORY_WRITES) == set(WRITES)
    assert set(READS + WRITES) <= set(names(parent))  # a session turn
    for offered in (names(subagent_config(parent, None), subagents=False), names({**parent, 'expert': True})):
        assert set(READS) <= set(offered) and set(WRITES).isdisjoint(offered)
    assert all(CATALOG[name].group == 'inventory' for name in READS + WRITES)


# --- the read tools ---


def test_the_reads_answer_in_access_and_refuse_outside(world):
    c = world['config']
    model = call(c, 'get_environment_model', environment_id=i(world['prod']))
    assert [s['name'] for s in model['servers']] == ['a', 'b']
    assert call(c, 'get_environment_model', environment_id=i(world['staging'])) == {
        'refused': 'This environment is not available in this run.'
    }
    servers = call(c, 'list_servers', environment_id=i(world['prod']))['servers']
    assert [s['name'] for s in servers] == ['a', 'b']
    assert not {'ssh_username', 'ssh_port', 'allow_sudo', 'access_enabled'} & servers[0].keys()
    deployments = call(c, 'list_deployments', server_id=i(world['a']))['deployments']
    assert [d['id'] for d in deployments] == [i(world['api_a'])]
    assert call(c, 'list_deployments', service_id=i(world['web'])) == {
        'refused': 'This service is not available in this run.'
    }
    assert call(c, 'list_connections', service_id=i(world['api'])) == {'connections': [], 'truncated': False}
    assert call(c, 'list_connections', deployment_id=i(world['web_c'])) == {
        'refused': 'This deployment is not available in this run.'
    }
    assert call(c, 'list_connections', deployment_id='not-an-id') == {
        'refused': 'This deployment is not available in this run.'
    }


def test_a_run_without_the_role_reads_but_never_writes(world):
    c = cfg(world, access={**world['access'], 'roles': {'inventory_editor': False}})
    assert call(c, 'list_servers', environment_id=i(world['prod']))['servers']
    assert call(c, 'save_server', name='new', hostname='new.internal') == {'refused': OFF}
    assert call(c, 'save_service', id=i(world['api']), revision=1, owner='x') == {'refused': OFF}
    assert call(c, 'remove_inventory_item', kind='deployment', id=i(world['api_a']), revision=1) == {'refused': OFF}
    with world['store'].connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.servers WHERE name='new'").fetchone()['n'] == 0


def test_writes_need_a_session(world):
    c = cfg(world, session_id=None)
    assert call(c, 'save_server', name='new', hostname='new.internal') == {'refused': tools.NO_SESSION}


# --- the write tools ---


def test_create_a_server_deploy_on_it_and_connect_it_in_one_run(world):
    c = world['config']
    server = call(c, 'save_server', name='cache-1', hostname='cache-1.internal', tags=['cache'])
    assert server['name'] == 'cache-1' and server['revision'] == 1 and 'ssh_username' not in server
    service = call(c, 'save_service', name='redis', service_role='cache')
    deployment = call(
        c,
        'save_deployment',
        service_id=service['id'],
        server_id=server['id'],
        environment_id=i(world['prod']),
        port=6379,
    )
    assert deployment['port'] == 6379
    connection = call(c, 'save_connection', from_deployment_id=i(world['api_a']), to_deployment_id=deployment['id'])
    assert connection['port'] == 6379  # the target's port
    out = call(c, 'save_connection', from_deployment_id=deployment['id'], to_host='backup.example', port=873)
    assert out['to_host'] == 'backup.example'
    relation = call(c, 'save_relation', from_service_id=i(world['api']), kind='depends_on', to_service_id=service['id'])
    assert relation['kind'] == 'depends_on'
    # the created server changes again within the run, with its revision
    updated = call(c, 'save_server', id=server['id'], revision=1, description='Redis host')
    assert (updated['description'], updated['revision'], updated['hostname']) == ('Redis host', 2, 'cache-1.internal')
    # the model shows it
    model = call(c, 'get_environment_model', environment_id=i(world['prod']))
    assert 'cache-1' in [s['name'] for s in model['servers']]
    assert model['external'] == [{'host': 'backup.example', 'ports': [873]}]
    # removing what this run created
    assert call(c, 'remove_inventory_item', kind='connection', id=out['id'], revision=1) == {'removed': out['id']}


def test_another_session_cannot_change_what_this_one_created_outside_its_access(world):
    server = call(world['config'], 'save_server', name='cache-1', hostname='cache-1.internal')
    with world['store'].connect() as conn:
        other = add_test_session(conn, world['project'], world['actor'], 'ops-agent', a_model(conn, world['actor']))
    c = cfg(world, session_id=str(other))
    assert call(c, 'save_server', id=server['id'], revision=1, description='x') == {
        'refused': 'This server is not available in this run.'
    }


def test_writes_outside_the_access_are_refused(world):
    c = world['config']
    refused = 'This server is not available in this run.'
    assert call(c, 'save_server', id=i(world['c']), revision=1, description='x') == {'refused': refused}
    assert call(c, 'remove_inventory_item', kind='server', id=i(world['c']), revision=1) == {'refused': refused}
    assert call(
        c,
        'save_deployment',
        service_id=i(world['api']),
        server_id=i(world['c']),
        environment_id=i(world['prod']),
    ) == {'refused': refused}
    assert call(c, 'save_connection', from_deployment_id=i(world['web_c']), to_host='x.example', port=1) == {
        'refused': 'This deployment is not available in this run.'
    }
    assert call(
        c, 'save_relation', from_service_id=i(world['web']), kind='depends_on', to_service_id=i(world['api'])
    ) == {'refused': 'This service is not available in this run.'}


def test_in_access_items_change_and_store_errors_become_refusals(world):
    c = world['config']
    updated = call(c, 'save_service', id=i(world['api']), revision=1, owner='payments team')
    assert (updated['owner'], updated['revision']) == ('payments team', 2)
    assert call(c, 'save_service', id=i(world['api']), revision=1, owner='x')['refused'].startswith(
        'This service changed since you read it (now revision 2)'
    )
    assert call(c, 'save_service', id=i(world['api']), owner='x') == {
        'refused': 'revision is required: the revision you read'
    }
    assert call(c, 'save_server', name='a', hostname='a2.internal') == {
        'refused': 'A server with this name already exists'
    }
    assert call(c, 'save_server', name='x', hostname='http://x') == {
        'refused': 'Enter an IP address or hostname without a URL, port, or credentials'
    }
    assert call(c, 'remove_inventory_item', kind='server', id=i(world['a']), revision=1) == {
        'refused': 'This server is used by 1 deployment(s). Remove those deployments first'
    }
    removed = call(c, 'remove_inventory_item', kind='deployment', id=i(world['api_a']), revision=1)
    assert removed == {'removed': i(world['api_a'])}


def test_tool_writes_are_audited_with_the_person_agent_and_session(world):
    server = call(world['config'], 'save_server', name='cache-1', hostname='cache-1.internal')
    with world['store'].connect() as conn:
        [row] = conn.execute(
            'SELECT actor_id,action,details FROM ka.audit_events WHERE resource_id=%s', (server['id'],)
        ).fetchall()
    assert (row['actor_id'], row['action']) == (world['actor'], 'server.created')
    assert (row['details']['agent_id'], row['details']['session_id']) == ('ops-agent', str(world['sid']))


def test_created_in_session_reads_the_audit(world):
    server = call(world['config'], 'save_server', name='cache-1', hostname='cache-1.internal')
    with world['store'].connect() as conn:
        created = inventory_store.created_in_session(conn, world['project'], world['sid'])
        assert created == {('server', server['id'])}
        assert inventory_store.created_in_session(conn, world['project'], uuid4()) == set()


def test_the_api_and_the_tools_write_identical_rows(world, tmp_path):
    client = _client(tmp_path, repository_store=world['store'])
    values = {'name': 'cache', 'hostname': 'cache.internal', 'description': 'Redis', 'tags': ['cache']}
    by_api = client.post('/api/servers', headers=HEADERS, json=values)
    assert by_api.status_code == 201, by_api.text
    by_tool = call(world['config'], 'save_server', **{**values, 'name': 'cache-2'})
    service = {'name': 'redis', 'service_role': 'cache', 'owner': 'ops'}
    api_service = client.post('/api/services', headers=HEADERS, json=service).json()
    tool_service = call(world['config'], 'save_service', **{**service, 'name': 'redis-2'})
    skip = {'id', 'name', 'created_at', 'updated_at', 'created_by'}
    with world['store'].connect() as conn:
        rows = {
            r['id']: {k: v for k, v in r.items() if k not in skip}
            for r in conn.execute(
                'SELECT * FROM ka.servers WHERE id IN (%s,%s)', (by_api.json()['id'], by_tool['id'])
            ).fetchall()
        }
        assert rows[uuid_of(by_api.json())] == rows[uuid_of(by_tool)]
        services = {
            r['id']: {k: v for k, v in r.items() if k not in skip}
            for r in conn.execute(
                'SELECT * FROM ka.services WHERE id IN (%s,%s)', (api_service['id'], tool_service['id'])
            ).fetchall()
        }
        assert services[uuid_of(api_service)] == services[uuid_of(tool_service)]


def uuid_of(item) -> UUID:
    return UUID(str(item['id']))
