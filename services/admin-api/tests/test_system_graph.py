"""Graph in graph: a view shows one node's inside, collapses deeper nodes and rolls edges up to what is shown."""
import pytest

from knowledge_admin.system_graph import ViewError, build_view


def n(id, parent=None, layer='software', kind='module', name=None):
    return {'id': id, 'parent_id': parent, 'layer': layer, 'kind': kind, 'name': name or id, 'description': ''}


def e(id, a, b, type='calls'):
    return {'id': id, 'from_id': a, 'to_id': b, 'type': type}


NODES = [
    n('api', kind='service', name='admin-api'),
    n('events', 'api', name='Event processor'),
    n('match', 'events', name='matching'),
    n('tpl', 'events', name='templates'),
    n('tasks', 'api', name='Task runner'),
    n('worker', kind='service', name='repository-worker'),
    n('pull', 'worker', name='pull'),
    n('prod', layer='infrastructure', kind='server', name='prod-1'),
    n('prod-api', 'prod', layer='infrastructure', kind='container', name='admin-api @ prod-1'),
]


def view(edges=(), root=None, depth=1, layer='software', nodes=NODES, documents=None):
    return build_view(list(nodes), list(edges), documents or {}, layer, root, depth)


def ids(rows):
    return [row['id'] for row in rows]


def test_top_of_a_layer_shows_its_top_level_nodes_with_edges_rolled_up():
    result = view([e('e1', 'match', 'pull')])
    assert (result['root'], result['breadcrumb'], result['layer'], result['depth']) == (None, [], 'software', 1)
    assert ids(result['nodes']) == ['api', 'worker']
    api = result['nodes'][0]
    assert (api['parent_id'], api['child_count'], api['expandable'], api['internal_edges'], api['documents']) == (None, 2, True, 0, [])
    assert result['edges'] == [{'from': 'api', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e1']}]
    assert result['boundary'] == []


def test_inside_a_node_edges_group_and_internal_edges_are_counted():
    result = view([e('e1', 'match', 'tasks'), e('e2', 'tpl', 'tasks'), e('e3', 'match', 'tpl'), e('e4', 'match', 'tasks', 'uses')],
                  root='api')
    assert result['root'] == {'id': 'api', 'name': 'admin-api', 'kind': 'service', 'description': ''}
    assert result['breadcrumb'] == [{'id': 'api', 'name': 'admin-api', 'kind': 'service'}]
    assert ids(result['nodes']) == ['events', 'tasks']
    events, tasks = result['nodes']
    assert (events['internal_edges'], events['expandable'], tasks['child_count'], tasks['expandable']) == (1, True, 0, False)
    assert result['edges'] == [
        {'from': 'events', 'to': 'tasks', 'type': 'calls', 'count': 2, 'edge_ids': ['e1', 'e2']},
        {'from': 'events', 'to': 'tasks', 'type': 'uses', 'count': 1, 'edge_ids': ['e4']},
    ]


def test_depth_two_shows_grandchildren_nested_under_their_parent():
    result = view([e('e3', 'match', 'tpl')], root='api', depth=2)
    assert ids(result['nodes']) == ['events', 'match', 'tpl', 'tasks']
    assert [row['parent_id'] for row in result['nodes']] == [None, 'events', 'events', None]
    assert result['nodes'][0]['expandable'] is False  # its children are shown
    assert result['edges'] == [{'from': 'match', 'to': 'tpl', 'type': 'calls', 'count': 1, 'edge_ids': ['e3']}]


def test_boundary_in_the_same_layer_is_the_child_of_the_lowest_common_ancestor():
    result = view([e('e1', 'match', 'tasks'), e('e2', 'match', 'pull'), e('e3', 'tpl', 'worker')], root='events')
    assert ids(result['boundary']) == ['worker', 'tasks']  # by name: repository-worker, Task runner
    assert result['edges'] == [
        {'from': 'match', 'to': 'tasks', 'type': 'calls', 'count': 1, 'edge_ids': ['e1']},
        {'from': 'match', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e2']},
        {'from': 'tpl', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e3']},
    ]
    assert result['boundary'][0] == {'id': 'worker', 'name': 'repository-worker', 'kind': 'service', 'layer': 'software'}


def test_boundary_in_another_layer_is_that_layers_top_level_node():
    result = view([e('e1', 'prod-api', 'tasks', 'instance_of')], root='api')
    assert result['boundary'] == [{'id': 'prod', 'name': 'prod-1', 'kind': 'server', 'layer': 'infrastructure'}]
    assert result['edges'] == [{'from': 'prod', 'to': 'tasks', 'type': 'instance_of', 'count': 1, 'edge_ids': ['e1']}]


def test_boundaries_sort_by_layer_then_name():
    nodes = NODES + [n('biz', layer='business', kind='capability', name='Ticket triage')]
    result = view([e('e1', 'tasks', 'worker'), e('e2', 'prod-api', 'tasks', 'instance_of'), e('e3', 'tasks', 'biz', 'implements')],
                  root='api', nodes=nodes)
    assert ids(result['boundary']) == ['biz', 'worker', 'prod']


def test_edges_to_a_container_count_as_internal():
    # A move can leave an edge between a node and its ancestor; the view never draws it.
    result = view([e('e1', 'match', 'api'), e('e2', 'match', 'events')], root='api')
    assert result['edges'] == [] and result['boundary'] == []
    assert result['nodes'][0]['internal_edges'] == 2
    top = view([e('e1', 'match', 'api')])
    assert top['edges'] == [] and top['nodes'][0]['internal_edges'] == 1


def test_edges_outside_the_view_are_ignored():
    assert view([e('e1', 'pull', 'worker')], root='api')['edges'] == []
    assert view([e('e1', 'prod-api', 'prod')], root='api')['edges'] == []


def test_breadcrumb_and_documents():
    documents = {'match': [{'id': 'd1', 'slug': 'matching', 'title': 'Matching'}]}
    result = view(root='events', documents=documents)
    assert [crumb['id'] for crumb in result['breadcrumb']] == ['api', 'events']
    assert result['nodes'][0]['documents'] == [{'id': 'd1', 'slug': 'matching', 'title': 'Matching'}]


def test_unknown_root():
    with pytest.raises(ViewError) as raised:
        view(root='gone')
    assert (raised.value.status, str(raised.value)) == (404, 'Node not found.')


def test_root_in_another_layer():
    with pytest.raises(ViewError) as raised:
        view(root='prod')
    assert (raised.value.status, str(raised.value)) == (422, 'That node is in the infrastructure layer.')


def test_an_empty_layer_and_a_leaf():
    assert view(layer='data')['nodes'] == []
    leaf = view(root='match')
    assert (leaf['nodes'], leaf['edges'], leaf['boundary']) == ([], [], [])


def test_too_many_nodes():
    many = [n('top', kind='service')] + [n(f'c{i}', 'top') for i in range(501)]
    with pytest.raises(ViewError) as raised:
        view(root='top', nodes=many)
    assert (raised.value.status, str(raised.value)) == (422, 'Too many nodes; choose a deeper root or a smaller depth.')
    assert len(view(root='top', nodes=many[:501])['nodes']) == 500


def test_boundary_counts_toward_the_limit():
    many = [n('top', kind='service')] + [n(f'c{i}', 'top') for i in range(499)] + [n(f'o{i}', kind='service') for i in range(2)]
    edges = [e(f'x{i}', 'c0', f'o{i}') for i in range(2)]
    with pytest.raises(ViewError):
        view(edges, root='top', nodes=many)


def test_edge_ids_are_capped_at_twenty():
    nodes = NODES + [n(f'm{i}', 'events') for i in range(25)]
    edges = [e(f'x{i}', f'm{i}', 'tasks') for i in range(25)]
    [edge] = view(edges, root='api', nodes=nodes)['edges']
    assert edge['count'] == 25 and len(edge['edge_ids']) == 20
