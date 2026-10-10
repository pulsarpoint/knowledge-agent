"""The System map from the inventory: placements, links, roll-up, drill-in, search."""
from uuid import uuid4

import pytest

from knowledge_admin.inventory_map import Inventory, ViewError, build_view, search

E = {name: uuid4() for name in ('production', 'staging', 'prod-1', 'spare', 'api', 'worker', 'db', 'code', 'old-code')}


def inventory(**overrides) -> Inventory:
    names = {('environment', E['production']): 'production', ('environment', E['staging']): 'staging',
             ('server', E['prod-1']): 'prod-1', ('server', E['spare']): 'spare',
             ('service', E['api']): 'api', ('service', E['worker']): 'worker', ('service', E['db']): 'db',
             ('code_project', E['code']): 'api-code'}
    base = dict(
        entries={key: {'name': name, 'description': ''} for key, name in names.items()},
        environment_servers={(E['production'], E['prod-1']), (E['staging'], E['prod-1'])},
        environment_services={(E['production'], E['api']), (E['production'], E['db']), (E['staging'], E['api'])},
        deployments={(E['api'], E['prod-1'], E['production']), (E['api'], E['prod-1'], E['staging']),
                     (E['worker'], E['prod-1'], None)},
        links=[], documents={})
    base.update(overrides)
    return Inventory(**base)


def names(view) -> dict:
    by_id = {node['id']: node for node in view['nodes']}
    return {node['name'] if node['parent_id'] is None else f"{by_id[node['parent_id']]['name']} › {node['name']}": node
            for node in view['nodes']}


def test_infrastructure_places_servers_under_environments_and_services_under_servers():
    view = build_view(inventory(), 'infrastructure', None, 3)
    tree = names(view)
    assert set(tree) == {'production', 'staging', 'production › prod-1', 'staging › prod-1', 'prod-1',
                         'prod-1 › api', 'prod-1 › worker', 'production › db', 'spare'}
    # prod-1 › api appears twice (production and staging copies); the top-level prod-1 holds the deployment without an environment
    assert sum(1 for n in view['nodes'] if n['name'] == 'api') == 2
    top_prod = next(n for n in view['nodes'] if n['id'] == f"server:top:{E['prod-1']}")
    assert [n['name'] for n in view['nodes'] if n['parent_id'] == top_prod['id']] == ['worker']
    db = next(n for n in view['nodes'] if n['name'] == 'db')
    assert db['parent_id'] == f"environment:{E['production']}" and db['id'] == f"service:{E['production']}:{E['db']}"
    assert {(n['entry_kind'], n['entry_id']) for n in view['nodes'] if n['name'] == 'prod-1'} == {('server', E['prod-1'])}


def test_depth_folds_and_drill_in_shows_inside_with_breadcrumb():
    top = build_view(inventory(), 'infrastructure', None, 1)
    assert [n['name'] for n in top['nodes']] == ['prod-1', 'production', 'spare', 'staging']
    production = next(n for n in top['nodes'] if n['name'] == 'production')
    assert production['expandable'] and production['child_count'] == 2
    inside = build_view(inventory(), 'infrastructure', f"server:{E['production']}:{E['prod-1']}", 1)
    assert [n['name'] for n in inside['nodes']] == ['api']
    assert [c['name'] for c in inside['breadcrumb']] == ['production', 'prod-1']
    assert inside['root']['entry_id'] == E['prod-1']


def test_links_stay_within_one_environment_and_roll_up_with_counts():
    links = [{'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', E['prod-1'])},
             {'id': uuid4(), 'kind': 'connects_to', 'from': ('environment', E['staging']), 'to': ('service', E['db'])},
             {'id': uuid4(), 'kind': 'depends_on', 'from': ('service', E['api']), 'to': ('service', E['db'])}]
    view = build_view(inventory(links=links), 'infrastructure', None, 3)
    by_id = {n['id']: n for n in view['nodes']}
    drawn = {(by_id[e['from']]['id'], by_id[e['to']]['id'], e['type']) for e in view['edges']}
    # api → prod-1 is drawn per environment (production copy → production prod-1, staging copy → staging prod-1),
    # never across environments or to the top-level prod-1; depends_on is not an infrastructure link
    assert all(e['type'] == 'connects_to' for e in view['edges'])
    assert not any(a.startswith('service:') and b.startswith('server:') for a, b, _ in drawn)  # api sits inside its server
    assert (f"environment:{E['staging']}", f"service:{E['production']}:{E['db']}", 'connects_to') in drawn
    top = build_view(inventory(links=links), 'infrastructure', None, 1)
    assert [(e['from'], e['to'], e['count']) for e in top['edges']] == [
        (f"environment:{E['staging']}", f"environment:{E['production']}", 1)]


def test_a_link_between_a_box_and_something_inside_it_counts_as_internal():
    links = [{'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', E['prod-1'])}]
    view = build_view(inventory(links=links), 'infrastructure', None, 3)
    by_id = {n['id']: n for n in view['nodes']}
    assert view['edges'] == []
    assert by_id[f"server:{E['production']}:{E['prod-1']}"]['internal_edges'] == 1
    assert by_id[f"server:{E['staging']}:{E['prod-1']}"]['internal_edges'] == 1


def test_drilled_in_views_show_the_other_end_outside():
    links = [{'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['db']), 'to': ('server', E['spare'])}]
    view = build_view(inventory(links=links), 'infrastructure', f"environment:{E['production']}", 1)
    assert [b['name'] for b in view['boundary']] == ['spare']
    assert view['edges'][0]['to'] == f"server:top:{E['spare']}" and view['edges'][0]['count'] == 1


def test_services_view_is_flat_with_all_link_kinds_and_no_archived_code():
    links = [{'id': uuid4(), 'kind': 'depends_on', 'from': ('service', E['api']), 'to': ('service', E['db'])},
             {'id': uuid4(), 'kind': 'built_from', 'from': ('service', E['api']), 'to': ('code_project', E['code'])},
             {'id': uuid4(), 'kind': 'built_from', 'from': ('service', E['worker']), 'to': ('code_project', E['old-code'])},
             {'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', E['prod-1'])}]
    view = build_view(inventory(links=links), 'services', None, 1)
    assert [n['name'] for n in view['nodes']] == ['api', 'api-code', 'db', 'worker']
    assert sorted(e['type'] for e in view['edges']) == ['built_from', 'depends_on']  # archived code and servers are not here


def test_unknown_root_and_search():
    with pytest.raises(ViewError) as missing:
        build_view(inventory(), 'infrastructure', f"server:{E['staging']}:{E['spare']}", 1)
    assert (missing.value.status, str(missing.value)) == (404, 'Node not found.')
    hits = search(inventory(), 'PRO', None, 10)
    assert [(h['entry_kind'], h['name']) for h in hits] == [('server', 'prod-1'), ('environment', 'production')]  # by name
    assert [h['name'] for h in search(inventory(), 'a', 'service', 10)] == ['api']


def test_documents_come_with_every_placement():
    doc = {'id': uuid4(), 'slug': 'runbook', 'title': 'Runbook'}
    view = build_view(inventory(documents={('server', E['prod-1']): [doc]}), 'infrastructure', None, 3)
    assert [n['documents'] for n in view['nodes'] if n['name'] == 'prod-1'] == [[doc], [doc], [doc]]


def fanned_out(links) -> dict:
    """api is deployed on prod-1 and spare in production; z is a top-level server."""
    z = uuid4()
    base = inventory()
    entries = {**base.entries, ('server', z): {'name': 'z', 'description': ''}}
    deployments = {(E['api'], E['prod-1'], E['production']), (E['api'], E['spare'], E['production'])}
    members = {(E['production'], E['api']), (E['production'], E['db'])}
    return build_view(inventory(entries=entries, deployments=deployments, environment_services=members, links=links(z)),
                      'infrastructure', None, 1), z


def test_a_link_drawn_between_several_placements_counts_once():
    link = uuid4()
    view, z = fanned_out(lambda z: [{'id': link, 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', z)}])
    assert [(e['from'], e['to'], e['count'], e['link_ids']) for e in view['edges']] == [
        (f"environment:{E['production']}", f"server:top:{z}", 1, [link])]


def test_different_links_rolled_up_to_one_pair_are_counted():
    view, z = fanned_out(lambda z: [{'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', z)},
                                    {'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['db']), 'to': ('server', z)}])
    assert [(e['from'], e['to'], e['count'], len(e['link_ids'])) for e in view['edges']] == [
        (f"environment:{E['production']}", f"server:top:{z}", 2, 2)]
