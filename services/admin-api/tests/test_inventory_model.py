"""inventory/model.py: the environment model, built purely from rows, and GET /environments/{id}/model."""

from uuid import uuid4

from knowledge_admin.inventory.model import build_model
from test_inventory_api import client, made, world  # noqa: F401  (fixtures)
from test_repositories import HEADERS


def ids(*names: str) -> dict:
    return {name: uuid4() for name in names}


I = ids(
    'prod', 'stage', 'a', 'b', 'c', 'api', 'db', 'web', 'lib', 'repo',
    'api_a', 'db_b', 'web_c', 'api_c', 'c1', 'c2', 'c3', 'c4', 'e1', 'e2', 'r1', 'r2', 'r3', 'r4',
)  # fmt: skip


def rows() -> dict:
    """prod: api on a, db on b; stage: web and api on c. Connections inside prod, prod → stage, stage → prod, prod →
    an external host, and inside stage. Relations api → db, web → api, api → repo, lib → web."""
    environment = {'id': I['prod'], 'name': 'prod', 'description': 'Production', 'revision': 3}
    secret = {'ssh_port': 22, 'ssh_username': 'root', 'access_enabled': True, 'allow_sudo': True, 'ssh_allowed': True}
    servers = [
        {
            'id': I['b'],
            'name': 'b',
            'hostname': 'b.internal',
            'description': 'DB box',
            'tags': ['db'],
            'revision': 2,
            **secret,
        },
        {'id': I['a'], 'name': 'A', 'hostname': 'a.internal', 'description': '', 'tags': [], 'revision': 2, **secret},
        {'id': I['c'], 'name': 'c', 'hostname': 'c.internal', 'description': '', 'tags': [], 'revision': 2, **secret},
    ]
    service = {
        'service_type': 'internal',
        'service_role': 'application',
        'description': '',
        'owner': 'team',
        'revision': 4,
    }
    services = [
        {'id': I['db'], 'name': 'db', **service},
        {'id': I['api'], 'name': 'api', **service},
        {'id': I['web'], 'name': 'web', **service},
        {'id': I['lib'], 'name': 'lib', **service},
    ]

    def deployment(key, service_id, server_id, environment_id, environment_name, port):
        return {
            'id': I[key],
            'service_id': I[service_id],
            'server_id': I[server_id],
            'environment_id': I[environment_id],
            'environment_name': environment_name,
            'runtime': 'docker',
            'runtime_name': key,
            'port': port,
            'log_location': f'/var/log/{key}',
            'deployed_version': '1.2',
            'revision': 1,
        }

    deployments = [
        deployment('api_a', 'api', 'a', 'prod', 'prod', 8080),
        deployment('db_b', 'db', 'b', 'prod', 'prod', 5432),
        deployment('web_c', 'web', 'c', 'stage', 'stage', 80),
        deployment('api_c', 'api', 'c', 'stage', 'stage', 8080),
    ]

    def connection(key, source, target=None, to_host=None, port=None):
        return {
            'id': I[key],
            'from_deployment_id': I[source],
            'to_deployment_id': I[target] if target else None,
            'to_host': to_host,
            'port': port,
            'protocol': 'tcp',
            'description': key,
            'revision': 1,
            'session_id': uuid4(),
        }

    connections = [
        connection('c1', 'api_a', 'db_b', port=5432),
        connection('c2', 'web_c', 'api_a', port=8080),
        connection('c3', 'api_a', to_host='mail.example.com', port=25),
        connection('c4', 'web_c', 'api_c', port=8080),
        connection('e1', 'api_a', to_host='mail.example.com', port=587),
        connection('e2', 'db_b', to_host='backup.example.com'),
    ]

    def relation(key, source, kind, to_service=None, to_code_project=None):
        return {
            'id': I[key],
            'from_service_id': I[source],
            'kind': kind,
            'to_service_id': I[to_service] if to_service else None,
            'to_code_project_id': I[to_code_project] if to_code_project else None,
            'description': key,
            'revision': 1,
        }

    relations = [
        relation('r1', 'api', 'depends_on', to_service='db'),
        relation('r2', 'web', 'depends_on', to_service='api'),
        relation('r3', 'api', 'built_from', to_code_project='repo'),
        relation('r4', 'lib', 'part_of', to_service='web'),
    ]
    code_projects = [{'id': I['repo'], 'name': 'repo', 'remote_url': 'git@example.com:x.git'}]
    return {
        'environment': environment,
        'servers': servers,
        'services': services,
        'deployments': deployments,
        'connections': connections,
        'relations': relations,
        'code_projects': code_projects,
    }


def test_servers_hold_their_deployments_in_the_environment_and_no_secret_fields():
    model = build_model(**rows())
    assert model['environment'] == {'id': I['prod'], 'name': 'prod', 'description': 'Production', 'revision': 3}
    assert [s['name'] for s in model['servers']] == ['A', 'b']  # c has no deployment in prod
    assert model['servers'][0] == {
        'id': I['a'],
        'name': 'A',
        'hostname': 'a.internal',
        'description': '',
        'tags': [],
        'revision': 2,
        'deployments': [
            {
                'id': I['api_a'],
                'service_id': I['api'],
                'service_name': 'api',
                'runtime': 'docker',
                'runtime_name': 'api_a',
                'port': 8080,
                'version': '1.2',
                'log_location': '/var/log/api_a',
                'revision': 1,
            }
        ],
    }
    assert 'root' not in repr(model) and 'ssh' not in repr(model) and 'sudo' not in repr(model)


def test_services_are_those_deployed_in_the_environment():
    model = build_model(**rows())
    assert model['services'] == [
        {
            'id': I['api'],
            'name': 'api',
            'service_type': 'internal',
            'service_role': 'application',
            'description': '',
            'owner': 'team',
            'revision': 4,
        },
        {
            'id': I['db'],
            'name': 'db',
            'service_type': 'internal',
            'service_role': 'application',
            'description': '',
            'owner': 'team',
            'revision': 4,
        },
    ]


def test_connections_touching_the_environment_name_the_end_outside_it():
    model = build_model(**rows())
    by_description = {c['description']: c for c in model['connections']}
    assert list(by_description) == ['c1', 'c2', 'c3', 'e1', 'e2']  # stage-only c4 left out
    assert by_description['c1'] == {
        'id': I['c1'],
        'from_deployment_id': I['api_a'],
        'to_deployment_id': I['db_b'],
        'to_host': None,
        'port': 5432,
        'protocol': 'tcp',
        'description': 'c1',
        'revision': 1,
    }
    assert by_description['c2']['outside'] == {
        'deployment_id': I['web_c'],
        'environment': 'stage',
        'server': 'c',
        'service': 'web',
    }
    assert by_description['c3']['to_host'] == 'mail.example.com' and 'outside' not in by_description['c3']


def test_external_hosts_list_their_ports():
    model = build_model(**rows())
    assert model['external'] == [
        {'host': 'backup.example.com', 'ports': []},
        {'host': 'mail.example.com', 'ports': [25, 587]},
    ]


def test_relations_of_services_in_the_environment_both_directions_with_names():
    model = build_model(**rows())
    by_id = {r['id']: r for r in model['relations']}
    assert set(by_id) == {I['r1'], I['r2'], I['r3']}  # lib part_of web touches no service in prod
    assert by_id[I['r2']] == {
        'id': I['r2'],
        'kind': 'depends_on',
        'from_service_id': I['web'],
        'from_service': 'web',
        'to_service_id': I['api'],
        'to_service': 'api',
        'to_code_project_id': None,
        'to_code_project': None,
        'description': 'r2',
        'revision': 1,
    }
    assert by_id[I['r3']]['to_code_project'] == 'repo' and by_id[I['r3']]['to_service'] is None


def test_an_empty_environment():
    model = build_model(**{**rows(), 'deployments': []})
    assert model['servers'] == [] and model['services'] == [] and model['connections'] == []
    assert model['relations'] == [] and model['external'] == []


# The endpoint


def test_the_model_endpoint_reads_the_rows(client, world, store):  # noqa: F811
    stage = made(client, 'environments', name='stage')
    api_stage = made(
        client,
        'deployments',
        service_id=world['api']['id'],
        server_id=world['b']['id'],
        environment_id=stage['id'],
        runtime_name='api-stage',
    )
    made(client, 'connections', from_deployment_id=world['api_a']['id'], to_deployment_id=world['db_b']['id'])
    made(client, 'connections', from_deployment_id=api_stage['id'], to_deployment_id=world['db_b']['id'])
    made(client, 'connections', from_deployment_id=world['api_a']['id'], to_host='mail.example.com', port=25)
    made(client, 'relations', from_service_id=world['api']['id'], kind='depends_on', to_service_id=world['db']['id'])
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.servers SET username='secret-user',port=2222,enabled=true WHERE id=%s", (world['a']['id'],)
        )
        conn.commit()

    response = client.get(f'/api/environments/{world["prod"]["id"]}/model', headers=HEADERS)
    assert response.status_code == 200, response.text
    assert 'secret-user' not in response.text and '2222' not in response.text
    model = response.json()
    assert model['environment']['name'] == 'prod'
    assert [(s['name'], [d['service_name'] for d in s['deployments']]) for s in model['servers']] == [
        ('a', ['api']),
        ('b', ['db']),
    ]
    assert [s['name'] for s in model['services']] == ['api', 'db']
    assert len(model['connections']) == 3
    items = [*model['servers'], *model['servers'][0]['deployments'], *model['services'], *model['connections']]
    assert all(item['revision'] == 1 for item in [*items, *model['relations']])
    outside = [c['outside'] for c in model['connections'] if 'outside' in c]
    assert outside == [{'deployment_id': api_stage['id'], 'environment': 'stage', 'server': 'b', 'service': 'api'}]
    assert model['external'] == [{'host': 'mail.example.com', 'ports': [25]}]
    assert [(r['from_service'], r['kind'], r['to_service']) for r in model['relations']] == [
        ('api', 'depends_on', 'db')
    ]


def test_the_model_of_an_unknown_environment_is_a_404(client):  # noqa: F811
    response = client.get(f'/api/environments/{uuid4()}/model', headers=HEADERS)
    assert response.status_code == 404
