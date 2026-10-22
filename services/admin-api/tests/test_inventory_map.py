"""The System map from the inventory: placements from deployments, connection and relation edges, roll-up, drill-in,
search."""

from uuid import uuid4

import pytest

from knowledge_admin.servers.inventory_map import Inventory, ViewError, build_view, search

E = {name: uuid4() for name in ('production', 'staging', 'prod-1', 'spare', 'api', 'worker', 'db', 'code', 'old-code')}
# deployments: api on prod-1 in production and in staging, worker on prod-1 in staging, db on spare in production
D = {name: uuid4() for name in ('api-prod', 'api-staging', 'worker-staging', 'db-prod')}


def inventory(**overrides) -> Inventory:
    names = {
        ('environment', E['production']): 'production',
        ('environment', E['staging']): 'staging',
        ('server', E['prod-1']): 'prod-1',
        ('server', E['spare']): 'spare',
        ('service', E['api']): 'api',
        ('service', E['worker']): 'worker',
        ('service', E['db']): 'db',
        ('code_project', E['code']): 'api-code',
    }
    base = dict(
        entries={key: {'name': name, 'description': ''} for key, name in names.items()},
        deployments={
            D['api-prod']: (E['api'], E['prod-1'], E['production']),
            D['api-staging']: (E['api'], E['prod-1'], E['staging']),
            D['worker-staging']: (E['worker'], E['prod-1'], E['staging']),
            D['db-prod']: (E['db'], E['spare'], E['production']),
        },
        connections=[],
        relations=[],
        documents={},
    )
    base.update(overrides)
    return Inventory(**base)


def connection(start, end=None, host=None) -> dict:
    return {'id': uuid4(), 'from': D[start], 'to': D[end] if end else None, 'host': host}


def relation(kind, start, end, end_kind='service') -> dict:
    return {'id': uuid4(), 'kind': kind, 'from': ('service', E[start]), 'to': (end_kind, E[end])}


def names(view) -> dict:
    by_id = {node['id']: node for node in view['nodes']}
    return {
        node['name'] if node['parent_id'] is None else f'{by_id[node["parent_id"]]["name"]} › {node["name"]}': node
        for node in view['nodes']
    }


def test_infrastructure_places_servers_in_the_environments_of_their_deployments():
    view = build_view(inventory(), 'infrastructure', None, 3)
    assert set(names(view)) == {
        'production',
        'staging',
        'production › prod-1',
        'production › spare',
        'staging › prod-1',
        'prod-1 › api',
        'prod-1 › worker',
        'spare › db',
    }
    assert sum(1 for n in view['nodes'] if n['name'] == 'api') == 2  # production and staging copies
    db = next(n for n in view['nodes'] if n['name'] == 'db')
    assert db['id'] == f'service:{E["production"]}:{E["spare"]}:{E["db"]}'
    assert db['parent_id'] == f'server:{E["production"]}:{E["spare"]}'


def test_a_server_without_deployments_sits_at_the_top_and_an_undeployed_service_is_not_placed():
    deployments = {D['api-prod']: (E['api'], E['prod-1'], E['production'])}
    view = build_view(inventory(deployments=deployments), 'infrastructure', None, 3)
    assert set(names(view)) == {'production', 'staging', 'production › prod-1', 'prod-1 › api', 'spare'}
    assert next(n for n in view['nodes'] if n['name'] == 'spare')['id'] == f'server:top:{E["spare"]}'


def test_depth_folds_and_drill_in_shows_inside_with_breadcrumb():
    top = build_view(inventory(), 'infrastructure', None, 1)
    assert [n['name'] for n in top['nodes']] == ['production', 'staging']
    production = next(n for n in top['nodes'] if n['name'] == 'production')
    assert production['expandable'] and production['child_count'] == 2
    inside = build_view(inventory(), 'infrastructure', f'server:{E["staging"]}:{E["prod-1"]}', 1)
    assert [n['name'] for n in inside['nodes']] == ['api', 'worker']
    assert [c['name'] for c in inside['breadcrumb']] == ['staging', 'prod-1']
    assert inside['root']['entry_id'] == E['prod-1']


def test_infrastructure_edges_are_connections_between_deployment_nodes():
    connections = [connection('api-prod', 'db-prod'), connection('api-staging', 'worker-staging')]
    relations = [relation('depends_on', 'api', 'db')]
    view = build_view(inventory(connections=connections, relations=relations), 'infrastructure', None, 3)
    assert sorted((e['from'], e['to'], e['type'], e['count'], e['ids']) for e in view['edges']) == sorted(
        [
            (
                f'service:{E["production"]}:{E["prod-1"]}:{E["api"]}',
                f'service:{E["production"]}:{E["spare"]}:{E["db"]}',
                'connects_to',
                1,
                [connections[0]['id']],
            ),
            (
                f'service:{E["staging"]}:{E["prod-1"]}:{E["api"]}',
                f'service:{E["staging"]}:{E["prod-1"]}:{E["worker"]}',
                'connects_to',
                1,
                [connections[1]['id']],
            ),
        ]
    )  # exact by deployment; relations are not infrastructure edges
    top = build_view(inventory(connections=connections), 'infrastructure', None, 1)
    assert top['edges'] == []  # both stay inside their environment
    assert {n['name']: n['internal_edges'] for n in top['nodes']} == {'production': 1, 'staging': 1}


def test_a_connection_across_environments_rolls_up_and_shows_the_other_end_outside():
    connections = [connection('api-staging', 'db-prod')]
    top = build_view(inventory(connections=connections), 'infrastructure', None, 1)
    assert [(e['from'], e['to'], e['count']) for e in top['edges']] == [
        (f'environment:{E["staging"]}', f'environment:{E["production"]}', 1)
    ]
    inside = build_view(inventory(connections=connections), 'infrastructure', f'environment:{E["staging"]}', 1)
    assert [b['id'] for b in inside['boundary']] == [f'environment:{E["production"]}']
    assert inside['edges'][0]['from'] == f'server:{E["staging"]}:{E["prod-1"]}'


def test_an_external_host_is_a_boundary_node():
    connections = [connection('api-prod', host='smtp.example.com'), connection('db-prod', host='smtp.example.com')]
    view = build_view(inventory(connections=connections), 'infrastructure', None, 1)
    assert view['boundary'] == [
        {
            'id': 'external_host:smtp.example.com',
            'name': 'smtp.example.com',
            'kind': 'external_host',
            'entry_kind': 'external_host',
            'entry_id': 'smtp.example.com',
        }
    ]
    assert [(e['from'], e['to'], e['count']) for e in view['edges']] == [
        (f'environment:{E["production"]}', 'external_host:smtp.example.com', 2)
    ]
    staging = build_view(inventory(connections=connections), 'infrastructure', f'environment:{E["staging"]}', 3)
    assert staging['boundary'] == [] and staging['edges'] == []  # no visible node connects to it


def test_services_view_draws_relations_and_connections_rolled_up_to_services():
    connections = [
        connection('api-prod', 'db-prod'),
        connection('api-staging', 'worker-staging'),
        connection('worker-staging', host='queue.example.com'),
    ]
    relations = [
        relation('depends_on', 'api', 'db'),
        relation('part_of', 'worker', 'api'),
        relation('built_from', 'api', 'code', 'code_project'),
        relation('built_from', 'worker', 'old-code', 'code_project'),  # archived: not read
    ]
    view = build_view(inventory(connections=connections, relations=relations), 'services', None, 1)
    assert [n['name'] for n in view['nodes']] == ['api', 'api-code', 'db', 'worker']
    by_id = {n['id']: n['name'] for n in [*view['nodes'], *view['boundary']]}
    assert sorted((by_id[e['from']], by_id[e['to']], e['type'], e['count']) for e in view['edges']) == [
        ('api', 'api-code', 'built_from', 1),
        ('api', 'db', 'connects_to', 1),
        ('api', 'db', 'depends_on', 1),
        ('api', 'worker', 'connects_to', 1),
        ('worker', 'api', 'part_of', 1),
        ('worker', 'queue.example.com', 'connects_to', 1),
    ]


def test_connections_between_the_same_services_count_each_connection():
    connections = [connection('api-prod', 'db-prod'), connection('api-staging', 'db-prod')]
    view = build_view(inventory(connections=connections), 'services', None, 1)
    [edge] = view['edges']
    assert (edge['type'], edge['count'], sorted(edge['ids'], key=str)) == (
        'connects_to',
        2,
        sorted((c['id'] for c in connections), key=str),
    )


def test_unknown_root_and_search():
    with pytest.raises(ViewError) as missing:
        build_view(inventory(), 'infrastructure', f'server:{E["staging"]}:{E["spare"]}', 1)
    assert (missing.value.status, str(missing.value)) == (404, 'Node not found.')
    hits = search(inventory(), 'PRO', None, 10)
    assert [(h['entry_kind'], h['name']) for h in hits] == [
        ('server', 'prod-1'),
        ('environment', 'production'),
    ]  # by name
    assert [h['name'] for h in search(inventory(), 'a', 'service', 10)] == ['api']


def test_documents_come_with_every_placement():
    doc = {'id': uuid4(), 'slug': 'runbook', 'title': 'Runbook'}
    view = build_view(inventory(documents={('server', E['prod-1']): [doc]}), 'infrastructure', None, 3)
    assert [n['documents'] for n in view['nodes'] if n['name'] == 'prod-1'] == [[doc], [doc]]
