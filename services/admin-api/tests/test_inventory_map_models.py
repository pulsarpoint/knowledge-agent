"""The System map drawn from the environment models equals the map drawn from the old loader on the same data: a rich
inventory (two environments and an empty one, a cross-environment connection, external hosts, a server and services
with no deployment, relations of each kind, an archived code project, documents) and its views — both views, depths
1–3, several roots — against the JSON captured from the old loader (map_models_expected.json)."""

import json
import re
from pathlib import Path

from live_documents import link_document, live_document
from test_inventory_api import client, made  # noqa: F401  (fixtures)
from test_repositories import HEADERS

EXPECTED = Path(__file__).with_name('map_models_expected.json')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def world(client, store) -> dict:  # noqa: F811
    """name → id of everything made (ids as strings)."""
    w: dict[str, str] = {}

    def make(key: str, segment: str, **values) -> str:
        w[key] = made(client, segment, **values)['id']
        return w[key]

    for name in ('production', 'staging', 'sandbox'):
        make(name, 'environments', name=name, description=f'{name} environment')
    for name in ('prod-1', 'prod-2', 'stage-1', 'shared', 'spare'):
        make(name, 'servers', name=name, hostname=f'{name}.internal', description=f'{name} box')
    for name in ('api', 'worker', 'db', 'web', 'cache', 'lonely', 'Lonely-2'):
        make(name, 'services', name=name, description=f'{name} service')
    for name, service, server, environment, runtime in (
        ('api-prod', 'api', 'prod-1', 'production', 'api'),
        ('api-prod-2', 'api', 'prod-1', 'production', 'api-2'),  # a second runtime: the same node
        ('db-prod', 'db', 'prod-2', 'production', 'db'),
        ('web-prod', 'web', 'shared', 'production', 'web'),
        ('api-stage', 'api', 'stage-1', 'staging', 'api'),
        ('worker-stage', 'worker', 'stage-1', 'staging', 'worker'),
        ('cache-stage', 'cache', 'shared', 'staging', 'cache'),
    ):
        make(
            name,
            'deployments',
            service_id=w[service],
            server_id=w[server],
            environment_id=w[environment],
            runtime_name=runtime,
            port=8000,
        )
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for name in ('code', 'old-code', 'tools'):
            w[name] = str(
                conn.execute(
                    """INSERT INTO ka.repositories(project_id,slug,name,description) VALUES (%s,%s,%s,%s)
                    RETURNING id""",
                    (project, name, name, f'{name} repository'),
                ).fetchone()['id']
            )
    for name, start, end, host, port in (
        ('c-api-db', 'api-prod', 'db-prod', None, None),
        ('c-api2-db', 'api-prod-2', 'db-prod', None, None),
        ('c-web-api', 'web-prod', 'api-prod', None, None),
        ('c-api-worker', 'api-stage', 'worker-stage', None, None),
        ('c-worker-db', 'worker-stage', 'db-prod', None, None),  # across environments
        ('c-api-cache', 'api-stage', 'cache-stage', None, None),
        ('c-web-smtp', 'web-prod', None, 'smtp.example.com', 25),
        ('c-worker-smtp', 'worker-stage', None, 'smtp.example.com', 587),
        ('c-db-backup', 'db-prod', None, 'backup.example.com', 873),
    ):
        values = {'from_deployment_id': w[start], 'port': port, 'protocol': 'tcp'}
        values |= {'to_host': host} if host else {'to_deployment_id': w[end]}
        make(name, 'connections', **values)
    for name, start, kind, service, code_project in (
        ('r-api-db', 'api', 'depends_on', 'db', None),
        ('r-worker-api', 'worker', 'part_of', 'api', None),
        ('r-api-code', 'api', 'built_from', None, 'code'),
        ('r-worker-old', 'worker', 'built_from', None, 'old-code'),  # archived below: not drawn
        ('r-lonely-lonely2', 'lonely', 'depends_on', 'Lonely-2', None),  # between services with no deployment
        ('r-lonely-code', 'lonely', 'built_from', None, 'code'),
        ('r-web-lonely', 'web', 'depends_on', 'lonely', None),
        ('r-cache-web', 'cache', 'part_of', 'web', None),  # across environments
    ):
        values = {'from_service_id': w[start], 'kind': kind}
        values |= {'to_service_id': w[service]} if service else {'to_code_project_id': w[code_project]}
        make(name, 'relations', **values)
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (w['old-code'],))
        for slug, title, links in (
            ('runbook', 'Runbook', [('server_id', 'prod-1'), ('server_id', 'shared')]),
            ('overview', 'Production overview', [('environment_id', 'production')]),
            ('api-guide', 'API guide', [('service_id', 'api'), ('deployment_id', 'api-prod')]),
            ('b-api', 'b API notes', [('service_id', 'api')]),
            ('lonely-doc', 'Lonely', [('service_id', 'lonely')]),
            ('code-doc', 'Code', [('code_project_id', 'code'), ('code_project_id', 'old-code')]),
            ('spare-doc', 'Spare box', [('server_id', 'spare')]),
            ('deployment-only', 'Deployment only', [('deployment_id', 'db-prod')]),  # not on any node
            ('removed', 'Removed link', [('server_id', 'prod-2')]),  # the link removed below
        ):
            saved = live_document(conn, project, slug, title, f'# {title}')
            w[slug] = str(saved['id'])
            for column, target in links:
                link_document(conn, project, saved['id'], column, w[target])
        conn.execute(
            """UPDATE ka.document_resources SET removed_at=now(),removed_by=(SELECT id FROM ka.principals LIMIT 1)
            WHERE document_id=%s""",
            (w['removed'],),
        )
    return w


def normal(view, labels: dict[str, str]):
    """The view with every id named, and edges (and their ids) sorted by name: their order follows random ids."""
    named = json.loads(UUID.sub(lambda m: labels[m.group(0)], json.dumps(view)))
    for edge in named['edges']:
        edge['ids'].sort()
    named['edges'].sort(key=lambda edge: (edge['from'], edge['to'], edge['type']))
    return named


def views(client, w: dict) -> dict:  # noqa: F811
    roots = {
        'infrastructure': {
            'top': None,
            'production': f'environment:{w["production"]}',
            'staging': f'environment:{w["staging"]}',
            'production/prod-1': f'server:{w["production"]}:{w["prod-1"]}',
            'staging/shared': f'server:{w["staging"]}:{w["shared"]}',
            'spare': f'server:top:{w["spare"]}',
        },
        'services': {'top': None, 'api': f'service:{w["api"]}'},
    }
    labels = {value: name for name, value in w.items()}
    found = {}
    for view, by_name in roots.items():
        for name, root in by_name.items():
            for depth in (1, 2, 3):
                params = {'depth': depth} | ({'root': root} if root else {})
                response = client.get(f'/api/map/view/{view}', headers=HEADERS, params=params)
                assert response.status_code == 200, response.text
                found[f'{view} {name} {depth}'] = normal(response.json(), labels)
    return found


def test_the_map_from_the_models_equals_the_map_from_the_old_loader(client, store):  # noqa: F811
    found = views(client, world(client, store))
    expected = json.loads(EXPECTED.read_text())
    assert sorted(found) == sorted(expected)
    for key in expected:
        assert found[key] == expected[key], key


def test_an_empty_member_server_and_an_external_service_are_drawn(client, store):  # noqa: F811
    """Not part of the captured views: a server added to an environment with no deployment, and a connection to a
    named external service."""
    production = made(client, 'environments', name='production')['id']
    spare = made(client, 'servers', name='spare', hostname='spare.internal')['id']
    box = made(client, 'servers', name='box', hostname='box.internal')['id']
    api = made(client, 'services', name='api')['id']
    stripe = made(client, 'services', name='Stripe', service_type='external', address='api.stripe.com')['id']
    assert client.put(f'/api/environments/{production}/servers/{spare}', headers=HEADERS).status_code == 204
    deployment = made(
        client, 'deployments', service_id=api, server_id=box, environment_id=production, runtime_name='api'
    )['id']
    made(client, 'connections', from_deployment_id=deployment, to_service_id=stripe, port=443, protocol='https')

    def view(name: str, **params) -> dict:
        response = client.get(f'/api/map/view/{name}', headers=HEADERS, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    infrastructure = view('infrastructure', depth=2)
    by_id = {node['id']: node for node in infrastructure['nodes']}
    assert f'server:{production}:{spare}' in by_id
    assert by_id[f'server:{production}:{spare}']['parent_id'] == f'environment:{production}'
    assert f'server:top:{spare}' not in by_id
    assert [(b['name'], b['kind'], b['address']) for b in infrastructure['boundary']] == [
        ('Stripe', 'external_service', 'api.stripe.com')
    ]
    assert [(e['to'], e['label']) for e in infrastructure['edges']] == [(f'external-service:{stripe}', 'https:443')]
    services = view('services', depth=1)
    assert [(e['to'], e['label']) for e in services['edges']] == [(f'service:{stripe}', 'https:443')]
