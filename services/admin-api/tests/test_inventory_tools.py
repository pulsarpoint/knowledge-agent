"""capabilities/inventory_tools.py: agent tools that read and change the inventory. Reads need the item in the run's
access; writes need the target in the access or created by this session (no role), create and update drafts only and
refuse a confirmed entry. Writes go through inventory/store.py with the session's person, agent and session."""

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

READS = ['get_environment_model', 'list_servers', 'list_deployments', 'list_connections']
WRITES = [
    'save_environment',
    'save_server',
    'save_service',
    'save_deployment',
    'save_connection',
    'save_relation',
    'remove_inventory_item',
    'save_inventory_draft',
]


def confirmed(kind: str) -> str:
    return (
        f'This {kind} is confirmed; only a person can change it. Add a new draft instead, or describe the change in '
        'your answer.'
    )


def entry(type_: str, id_, source: str = 'given') -> dict:
    return {'type': type_, 'id': str(id_), 'name': type_, 'description_short': '', 'source': source}


def access(*entries: dict) -> dict:
    return {'resources': list(entries), 'roles': {'content_editor': False}}


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
    a = access(entry('environment', 'e1'), entry('server', 's1'), entry('service', 'v1'))
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


def test_writes_need_the_target_in_access_or_created_by_this_session_and_no_role():
    a = access(entry('environment', 'e1'), entry('server', 's1'), entry('service', 'v1'))
    # a new environment, server or service: an environment in the access
    assert tools.check_save_server(a, name='x', hostname='x') is None
    assert tools.check_save_service(a, name='x') is None
    assert tools.check_save_environment(a, name='x') is None
    no_environment = access(entry('server', 's1'))
    assert tools.check_save_server(no_environment, name='x', hostname='x') == 'This run has no environments.'
    assert tools.check_save_environment(no_environment, name='x') == 'This run has no environments.'
    assert tools.check_save_environment(a, id='e9', description='x') == 'This environment is not available in this run.'
    assert tools.check_save_environment(a, id='e9', description='x', created={('environment', 'e9')}) is None
    assert tools.check_save_inventory_draft(no_environment) is None  # the names are resolved over the rows
    # an environment to put the server in: in the access, or created by this session
    assert tools.check_save_server(a, name='x', hostname='x', environment_id='e1') is None
    assert tools.check_save_server(a, id='s1', environment_id='e9') == 'This environment is not available in this run.'
    assert tools.check_save_server(a, id='s1', environment_id='e9', created={('environment', 'e9')}) is None
    # an external service to connect to: in the access, or created by this session
    rows = {('deployment', 'p1'): {'server_id': 's1', 'service_id': 'v1', 'environment_id': 'e1'}}
    connect = {'from_deployment_id': 'p1', 'rows': rows}
    assert tools.check_save_connection(a, to_service_id='v1', **connect) is None
    assert tools.check_save_connection(a, to_service_id='v9', **connect) == 'This service is not available in this run.'
    assert tools.check_save_connection(a, to_service_id='v9', created={('service', 'v9')}, **connect) is None
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


def test_a_confirmed_entry_is_refused_and_a_draft_is_changed():
    a = access(entry('environment', 'e1'), entry('server', 's1'), entry('service', 'v1'))
    rows = {
        ('server', 's1'): {'status': 'confirmed'},
        ('service', 'v1'): {'status': 'draft'},
        ('environment', 'e1'): {'status': 'confirmed'},
        ('deployment', 'p1'): {'server_id': 's1', 'service_id': 'v1', 'environment_id': 'e1', 'status': 'confirmed'},
        ('connection', 'n1'): {'from_deployment_id': 'p1', 'status': 'draft'},
        ('relation', 'r1'): {'from_service_id': 'v1', 'status': 'confirmed'},
    }
    assert tools.check_save_server(a, id='s1', description='x', rows=rows) == confirmed('server')
    assert tools.check_save_server(a, id='s1', environment_id='e1', rows=rows) is None  # joining: a draft membership
    assert tools.check_save_service(a, id='v1', owner='ops', rows=rows) is None
    assert tools.check_save_environment(a, id='e1', description='x', rows=rows) == confirmed('environment')
    assert tools.check_save_deployment(a, id='p1', port=1, rows=rows) == confirmed('deployment')
    assert tools.check_save_connection(a, id='n1', port=1, rows=rows) is None
    assert tools.check_save_relation(a, id='r1', description='x', rows=rows) == confirmed('relation')
    for kind, item_id in [('server', 's1'), ('environment', 'e1'), ('deployment', 'p1'), ('relation', 'r1')]:
        assert tools.check_remove_inventory_item(a, kind=kind, id=item_id, rows=rows) == confirmed(kind)
    assert tools.check_remove_inventory_item(a, kind='service', id='v1', rows=rows) is None
    assert tools.check_remove_inventory_item(a, kind='connection', id='n1', rows=rows) is None
    # outside the access the refusal says so first
    assert tools.check_save_server(a, id='s9', description='x', rows={('server', 's9'): {'status': 'confirmed'}}) == (
        'This server is not available in this run.'
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


def test_any_run_writes_drafts_and_never_changes_a_confirmed_entry(world):
    c = cfg(world, access={**world['access'], 'roles': {'content_editor': False}})
    server = call(c, 'save_server', name='new', hostname='new.internal')  # an old access object's role is ignored
    assert (server['name'], server['status']) == ('new', 'draft')
    assert call(c, 'save_service', id=i(world['api']), revision=1, owner='x') == {'refused': confirmed('service')}
    assert call(c, 'remove_inventory_item', kind='deployment', id=i(world['api_a']), revision=1) == {
        'refused': confirmed('deployment')
    }
    assert call(c, 'save_environment', id=i(world['prod']), revision=1, description='x') == {
        'refused': confirmed('environment')
    }
    with world['store'].connect() as conn:
        api = inventory_store.get(conn, world['project'], 'service', world['api']['id'])
        assert (api['owner'], api['revision']) == ('', 1)
        assert inventory_store.get(conn, world['project'], 'deployment', world['api_a']['id'])['revision'] == 1


def test_an_environment_is_drafted_updated_and_removed(world):
    c = world['config']
    staging = call(c, 'save_environment', name='dr', description='Disaster recovery')
    assert (staging['name'], staging['status'], staging['revision']) == ('dr', 'draft', 1)
    updated = call(c, 'save_environment', id=staging['id'], revision=1, description='DR site')
    assert (updated['description'], updated['status'], updated['revision']) == ('DR site', 'draft', 2)
    server = call(c, 'save_server', id=i(world['a']), revision=1, environment_id=staging['id'])  # created here: usable
    assert staging['id'] in server['environment_ids']
    with world['store'].connect() as conn:
        memberships = conn.execute(
            'SELECT status FROM ka.environment_servers WHERE environment_id=%s', (staging['id'],)
        ).fetchall()
    assert [m['status'] for m in memberships] == ['draft']


def test_writes_need_a_session(world):
    c = cfg(world, session_id=None)
    assert call(c, 'save_server', name='new', hostname='new.internal') == {'refused': tools.NO_SESSION}


# --- the write tools ---


def test_create_a_server_deploy_on_it_and_connect_it_in_one_run(world):
    c = world['config']
    server = call(c, 'save_server', name='cache-1', hostname='cache-1.internal', tags=['cache'])
    assert server['name'] == 'cache-1' and server['revision'] == 1 and 'ssh_username' not in server
    assert server['status'] == 'draft'
    service = call(c, 'save_service', name='redis', service_role='cache')
    deployment = call(
        c,
        'save_deployment',
        service_id=service['id'],
        server_id=server['id'],
        environment_id=i(world['prod']),
        port=6379,
    )
    assert (deployment['port'], deployment['status'], service['status']) == (6379, 'draft', 'draft')
    connection = call(c, 'save_connection', from_deployment_id=i(world['api_a']), to_deployment_id=deployment['id'])
    assert (connection['port'], connection['status']) == (6379, 'draft')  # the target's port
    out = call(c, 'save_connection', from_deployment_id=deployment['id'], to_host='backup.example', port=873)
    assert out['to_host'] == 'backup.example'
    relation = call(c, 'save_relation', from_service_id=i(world['api']), kind='depends_on', to_service_id=service['id'])
    assert (relation['kind'], relation['status']) == ('depends_on', 'draft')
    # the created server changes again within the run, with its revision
    updated = call(c, 'save_server', id=server['id'], revision=1, description='Redis host')
    assert (updated['description'], updated['revision'], updated['hostname']) == ('Redis host', 2, 'cache-1.internal')
    assert updated['status'] == 'draft'  # updating a draft keeps it a draft
    # the model shows it, with its status
    model = call(c, 'get_environment_model', environment_id=i(world['prod']))
    assert {s['name']: s['status'] for s in model['servers']} == {
        'a': 'confirmed',
        'b': 'confirmed',
        'cache-1': 'draft',
    }
    listed = call(c, 'list_deployments', server_id=server['id'])['deployments']
    assert [d['status'] for d in listed] == ['draft']
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


def test_drafts_change_and_store_errors_become_refusals(world):
    c = world['config']
    draft = call(c, 'save_service', name='queue')
    updated = call(c, 'save_service', id=draft['id'], revision=1, owner='payments team')
    assert (updated['owner'], updated['revision'], updated['status']) == ('payments team', 2, 'draft')
    assert call(c, 'save_service', id=draft['id'], revision=1, owner='x')['refused'].startswith(
        'This service changed since you read it (now revision 2)'
    )
    assert call(c, 'save_service', id=draft['id'], owner='x') == {
        'refused': 'revision is required: the revision you read'
    }
    assert call(c, 'save_server', name='a', hostname='a2.internal') == {
        'refused': 'A server with this name already exists'
    }
    assert call(c, 'save_server', name='x', hostname='http://x') == {
        'refused': 'Enter an IP address or hostname without a URL, port, or credentials'
    }
    host = call(c, 'save_server', name='host', hostname='host.internal')
    on_it = call(c, 'save_deployment', service_id=draft['id'], server_id=host['id'], environment_id=i(world['prod']))
    assert call(c, 'remove_inventory_item', kind='server', id=host['id'], revision=1) == {
        'refused': 'This server is used by 1 deployment(s). Remove those deployments first'
    }
    removed = call(c, 'remove_inventory_item', kind='deployment', id=on_it['id'], revision=1)
    assert removed == {'removed': on_it['id']}


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
    skip = {'id', 'name', 'created_at', 'updated_at', 'created_by', 'status'}  # the tool's is a draft
    assert (by_tool['status'], tool_service['status'], by_api.json()['status']) == ('draft', 'draft', 'confirmed')
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


# --- writes on an existing deployment need all of its ends (I-1) ---


def test_a_write_on_a_deployment_needs_its_environment_server_and_service():
    staging = access(entry('environment', 'e2'), entry('server', 's2'), entry('service', 'v1'))
    prod = {('deployment', 'p1'): {'server_id': 's1', 'service_id': 'v1', 'environment_id': 'e1'}}
    connection = {('connection', 'n1'): {'from_deployment_id': 'p1'}, **prod}
    refused = 'This deployment is not available in this run.'
    assert tools.check_save_deployment(staging, id='p1', port=1, rows=prod) == refused
    assert tools.check_remove_inventory_item(staging, kind='deployment', id='p1', rows=prod) == refused
    assert tools.check_save_connection(staging, id='n1', port=1, rows=connection) == (
        'This connection is not available in this run.'
    )
    assert tools.check_remove_inventory_item(staging, kind='connection', id='n1', rows=connection) == (
        'This connection is not available in this run.'
    )
    assert tools.check_save_connection(staging, from_deployment_id='p1', to_host='x.example', port=1, rows=prod) == (
        refused
    )
    # reads keep any one end; a deployment created by this session stays writable
    assert tools.check_list_connections(staging, deployment_id='p1', rows=prod) is None
    assert (
        tools.check_remove_inventory_item(
            staging, kind='deployment', id='p1', rows=prod, created={('deployment', 'p1')}
        )
        is None
    )


def test_an_editor_given_staging_cannot_change_the_prod_deployment_of_a_service_it_holds(world):
    with world['store'].connect() as conn:
        by = {'actor_id': world['actor']}
        inventory_store.create_deployment(
            conn,
            world['project'],
            {'service_id': world['api']['id'], 'server_id': world['c']['id'], 'environment_id': world['staging']['id']},
            by,
        )
        link = inventory_store.create_connection(
            conn, world['project'], {'from_deployment_id': world['api_a']['id'], 'to_host': 'x.example', 'port': 1}, by
        )
    via = f'via environment {world["staging"]["id"]}'
    staging = access(
        entry('environment', world['staging']['id']),
        *[entry(t, world[k]['id'], via) for t, k in [('server', 'c'), ('service', 'web'), ('service', 'api')]],
    )
    c = cfg(world, access=staging)
    assert [d['id'] for d in call(c, 'list_deployments', service_id=i(world['api']))['deployments']]  # reads stay
    refused = {'refused': 'This deployment is not available in this run.'}
    assert call(c, 'save_deployment', id=i(world['api_a']), revision=1, port=1) == refused
    assert call(c, 'remove_inventory_item', kind='deployment', id=i(world['api_a']), revision=1) == refused
    no_connection = {'refused': 'This connection is not available in this run.'}
    assert call(c, 'save_connection', id=i(link), revision=1, port=2) == no_connection
    assert call(c, 'remove_inventory_item', kind='connection', id=i(link), revision=1) == no_connection
    with world['store'].connect() as conn:
        assert inventory_store.get(conn, world['project'], 'deployment', world['api_a']['id'])['revision'] == 1
        assert inventory_store.get(conn, world['project'], 'connection', link['id'])['revision'] == 1


# --- more writes through the tools (M-3) ---


def test_update_a_deployment_and_a_connection_and_relate_part_of(world):
    c = world['config']
    ends = {'service_id': i(world['api']), 'server_id': i(world['b']), 'environment_id': i(world['prod'])}
    draft = call(c, 'save_deployment', **ends, port=8080)
    deployment = call(c, 'save_deployment', id=draft['id'], revision=1, port=9090, deployed_version='1.2')
    assert (deployment['port'], deployment['deployed_version'], deployment['revision']) == (9090, '1.2', 2)
    link = call(c, 'save_connection', from_deployment_id=i(world['api_a']), to_deployment_id=i(world['db_b']))
    assert link['port'] == 5432
    changed = call(c, 'save_connection', id=link['id'], revision=1, protocol='postgres', description='orders')
    assert (changed['protocol'], changed['description'], changed['revision'], changed['port']) == (
        'postgres',
        'orders',
        2,
        5432,
    )
    part = call(c, 'save_relation', from_service_id=i(world['db']), kind='part_of', to_service_id=i(world['api']))
    assert (part['kind'], part['to_service_id']) == ('part_of', i(world['api']))


def test_the_api_and_the_tools_write_identical_deployments_and_connections(world, tmp_path):
    client = _client(tmp_path, repository_store=world['store'])
    ends = {'service_id': i(world['api']), 'server_id': i(world['b']), 'environment_id': i(world['prod'])}
    values = {**ends, 'runtime': 'docker', 'port': 8081, 'log_location': '/var/log/api', 'deployed_version': '2'}
    by_api = client.post('/api/deployments', headers=HEADERS, json={**values, 'runtime_name': 'api-1'})
    assert by_api.status_code == 201, by_api.text
    by_tool = call(world['config'], 'save_deployment', **values, runtime_name='api-2')
    link = {'from_deployment_id': i(world['api_a']), 'port': 443, 'protocol': 'https', 'description': 'payments'}
    api_link = client.post('/api/connections', headers=HEADERS, json={**link, 'to_host': 'pay.example'})
    assert api_link.status_code == 201, api_link.text
    tool_link = call(world['config'], 'save_connection', **link, to_host='pay2.example')
    skip = {
        'id',
        'created_at',
        'updated_at',
        'created_by',
        'created_by_agent',
        'session_id',
        'runtime_name',
        'to_host',
        'status',
    }
    with world['store'].connect() as conn:
        for table, pair in [
            ('service_deployments', (by_api.json(), by_tool)),
            ('connections', (api_link.json(), tool_link)),
        ]:
            first, second = (
                {
                    k: v
                    for k, v in conn.execute(f'SELECT * FROM ka.{table} WHERE id=%s', (uuid_of(item),))
                    .fetchone()
                    .items()
                    if k not in skip
                }
                for item in pair
            )
            assert first == second


def test_a_server_is_put_in_an_environment_and_listed_with_its_environments(world):
    c = world['config']
    server = call(c, 'save_server', name='spare', hostname='spare.internal', environment_id=i(world['prod']))
    assert server['environment_ids'] == [i(world['prod'])]
    listed = call(c, 'list_servers', environment_id=i(world['prod']))['servers']
    assert {s['name']: s['environment_ids'] for s in listed}['spare'] == [i(world['prod'])]
    model = call(c, 'get_environment_model', environment_id=i(world['prod']))
    assert [s['deployments'] for s in model['servers'] if s['name'] == 'spare'] == [[]]
    # an existing server joins too; the environment outside the access is refused and nothing is added
    own = call(c, 'save_server', id=i(world['a']), revision=1, environment_id=i(world['prod']))
    assert own['environment_ids'] == [i(world['prod'])]
    assert own['revision'] == 1  # only the membership was added: the server row is not updated
    assert call(c, 'save_server', id=i(world['a']), revision=1, description='x') == {'refused': confirmed('server')}
    refused = {'refused': 'This environment is not available in this run.'}
    assert call(c, 'save_server', name='x', hostname='x', environment_id=i(world['staging'])) == refused
    assert call(c, 'save_server', id=server['id'], revision=1, environment_id=i(world['staging'])) == refused


def test_a_connection_to_an_external_service(world):
    c = world['config']
    stripe = call(c, 'save_service', name='Stripe', service_type='external', address='api.stripe.com')
    assert stripe['address'] == 'api.stripe.com'  # created by this session: usable
    connection = call(
        c,
        'save_connection',
        from_deployment_id=i(world['api_a']),
        to_service_id=stripe['id'],
        port=443,
        protocol='https',
    )
    assert connection['to_service_id'] == stripe['id']
    model = call(c, 'get_environment_model', environment_id=i(world['prod']))
    assert [(s['name'], s['address'], s['ports']) for s in model['external_services']] == [
        ('Stripe', 'api.stripe.com', [443])
    ]
    # a custom service is not a target; a service outside the access is not available
    custom = 'Connect to an external service, or to a deployment of this service.'
    assert call(c, 'save_connection', from_deployment_id=i(world['api_a']), to_service_id=i(world['db']), port=1) == {
        'refused': custom
    }
    assert call(c, 'save_connection', from_deployment_id=i(world['api_a']), to_service_id=i(world['web']), port=1) == {
        'refused': 'This service is not available in this run.'
    }
