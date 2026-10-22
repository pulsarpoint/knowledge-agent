"""inventory/model.py: the environment model, built purely from rows, and GET /environments/{id}/model."""

from uuid import uuid4

from knowledge_admin.inventory.model import build_model, build_project
from test_inventory_api import client, made, world  # noqa: F401  (fixtures)
from test_repositories import HEADERS


def ids(*names: str) -> dict:
    return {name: uuid4() for name in names}


I = ids(
    'prod', 'stage', 'a', 'b', 'c', 'api', 'db', 'web', 'lib', 'repo',
    'api_a', 'db_b', 'web_c', 'api_c', 'c1', 'c2', 'c3', 'c4', 'e1', 'e2', 'r1', 'r2', 'r3', 'r4',
    'mail', 'x1', 'x2', 'x3',
)  # fmt: skip


def rows() -> dict:
    """prod: api on a, db on b; stage: web and api on c. Connections inside prod, prod → stage, stage → prod, prod →
    an external host, and inside stage. Relations api → db, web → api, api → repo, lib → web."""
    environment = {'id': I['prod'], 'name': 'prod', 'description': 'Production', 'revision': 3, 'status': 'confirmed'}
    secret = {'ssh_port': 22, 'ssh_username': 'root', 'access_enabled': True, 'allow_sudo': True, 'ssh_allowed': True}
    servers = [
        {
            'id': I['b'],
            'name': 'b',
            'hostname': 'b.internal',
            'description': 'DB box',
            'tags': ['db'],
            'revision': 2,
            'status': 'confirmed',
            **secret,
        },
        {
            'id': I['a'],
            'name': 'A',
            'hostname': 'a.internal',
            'description': '',
            'tags': [],
            'revision': 2,
            'status': 'confirmed',
            **secret,
        },
        {
            'id': I['c'],
            'name': 'c',
            'hostname': 'c.internal',
            'description': '',
            'tags': [],
            'revision': 2,
            'status': 'confirmed',
            **secret,
        },
    ]
    service = {
        'service_type': 'internal',
        'service_role': 'application',
        'description': '',
        'owner': 'team',
        'revision': 4,
        'status': 'confirmed',
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
            'status': 'confirmed',
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
            'to_service_id': None,
            'port': port,
            'protocol': 'tcp',
            'description': key,
            'revision': 1,
            'status': 'confirmed',
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
            'status': 'confirmed',
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
        'memberships': [
            {'environment_id': d['environment_id'], 'server_id': d['server_id'], 'status': 'confirmed'}
            for d in deployments
        ],
    }


def test_servers_hold_their_deployments_in_the_environment_and_no_secret_fields():
    model = build_model(**rows())
    assert model['environment'] == {
        'id': I['prod'],
        'name': 'prod',
        'description': 'Production',
        'revision': 3,
        'status': 'confirmed',
    }
    assert [s['name'] for s in model['servers']] == ['A', 'b']  # c has no deployment in prod
    assert model['servers'][0] == {
        'id': I['a'],
        'name': 'A',
        'hostname': 'a.internal',
        'description': '',
        'tags': [],
        'revision': 2,
        'status': 'confirmed',
        'membership_status': 'confirmed',
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
                'status': 'confirmed',
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
            'status': 'confirmed',
        },
        {
            'id': I['db'],
            'name': 'db',
            'service_type': 'internal',
            'service_role': 'application',
            'description': '',
            'owner': 'team',
            'revision': 4,
            'status': 'confirmed',
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
        'to_service_id': None,
        'to_service_name': None,
        'port': 5432,
        'protocol': 'tcp',
        'description': 'c1',
        'revision': 1,
        'status': 'confirmed',
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
        'status': 'confirmed',
    }
    assert by_id[I['r3']]['to_code_project'] == 'repo' and by_id[I['r3']]['to_service'] is None


def test_an_empty_environment():
    model = build_model(**{**rows(), 'deployments': [], 'memberships': []})
    assert model['servers'] == [] and model['services'] == [] and model['connections'] == []
    assert model['relations'] == [] and model['external'] == [] and model['external_services'] == []


def test_a_member_server_without_deployments_is_listed_empty():
    model = build_model(
        **{
            **rows(),
            'memberships': [
                *rows()['memberships'],
                {'environment_id': I['prod'], 'server_id': I['c'], 'status': 'confirmed'},
            ],
        }
    )
    assert [(s['name'], len(s['deployments'])) for s in model['servers']] == [('A', 1), ('b', 1), ('c', 0)]
    assert model['servers'][2]['deployments'] == []


def test_external_services_the_connections_reach_with_their_ports():
    r = rows()
    mail = {
        'id': I['mail'],
        'name': 'mail',
        'service_type': 'external',
        'service_role': 'other',
        'description': 'Mail relay',
        'owner': '',
        'address': 'mail.example.com',
        'revision': 2,
        'status': 'confirmed',
    }
    r['services'].append(mail)

    def to_mail(key, source, port):
        return {**r['connections'][0], 'id': I[key], 'from_deployment_id': I[source], 'to_deployment_id': None,
                'to_service_id': I['mail'], 'port': port, 'description': key}  # fmt: skip

    r['connections'] += [to_mail('x1', 'api_a', 25), to_mail('x2', 'db_b', 587), to_mail('x3', 'web_c', 2525)]
    model = build_model(**r)
    assert model['external_services'] == [
        {
            'id': I['mail'],
            'name': 'mail',
            'address': 'mail.example.com',
            'description': 'Mail relay',
            'revision': 2,
            'status': 'confirmed',
            'ports': [25, 587],  # web_c is in stage
        }
    ]
    x1 = next(c for c in model['connections'] if c['id'] == I['x1'])
    assert (x1['to_service_id'], x1['to_service_name'], 'outside' in x1) == (I['mail'], 'mail', False)
    assert [s['name'] for s in model['services']] == ['api', 'db']  # an external service is not deployed here
    assert model['external'] == build_model(**rows())['external']


def test_every_entry_carries_its_status_and_the_model_counts_its_drafts():
    r = rows()
    assert build_model(**r)['drafts'] == 0
    r['environment']['status'] = 'draft'
    r['servers'][0]['status'] = 'draft'  # b
    r['memberships'][1]['status'] = 'draft'  # b in prod
    r['deployments'][1]['status'] = 'draft'  # db_b
    r['services'][0]['status'] = 'draft'  # db
    r['connections'][0]['status'] = 'draft'  # c1
    r['connections'][3]['status'] = 'draft'  # c4, in stage only: not counted
    r['relations'][0]['status'] = 'draft'  # r1
    model = build_model(**r)
    assert model['drafts'] == 7
    assert model['environment']['status'] == 'draft'
    b = next(s for s in model['servers'] if s['name'] == 'b')
    assert (b['status'], b['membership_status'], b['deployments'][0]['status']) == ('draft', 'draft', 'draft')
    assert [(s['name'], s['status']) for s in model['services']] == [('api', 'confirmed'), ('db', 'draft')]
    assert [c['status'] for c in model['connections']][:2] == ['draft', 'confirmed']
    assert {x['id']: x['status'] for x in model['relations']}[I['r1']] == 'draft'


# The endpoint


def test_the_project_is_every_environment_model_and_what_none_holds():
    """Adds a server and a service with no deployment, and a relation between two services with none (lib, lonely)."""
    project_rows = {key: value for key, value in rows().items() if key != 'environment'}
    lonely = {**project_rows['services'][0], 'id': uuid4(), 'name': 'lonely'}
    project_rows['servers'].append({**project_rows['servers'][0], 'id': uuid4(), 'name': 'spare'})
    project_rows['services'].append(lonely)
    project_rows['relations'].append(
        {**project_rows['relations'][0], 'id': uuid4(), 'from_service_id': I['lib'], 'to_service_id': lonely['id']}
    )
    prod, stage = rows()['environment'], {**rows()['environment'], 'id': I['stage'], 'name': 'stage', 'revision': 1}
    project = build_project([stage, prod], **project_rows)
    assert project['models'] == [build_model(environment, **project_rows) for environment in (prod, stage)]
    unplaced = project['unplaced']
    assert [s['name'] for s in unplaced['servers']] == ['spare'] and 'ssh_username' not in unplaced['servers'][0]
    assert [s['name'] for s in unplaced['services']] == ['lib', 'lonely']
    assert [(r['from_service'], r['to_service']) for r in unplaced['relations']] == [('lib', 'lonely')]


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
