from pathlib import Path

import pytest

from knowledge_admin import access


def run_access():
    return {
        'resources': [
            {'type': 'server', 'id': 's1', 'name': 'web', 'description_short': 'web box', 'source': 'given'},
            {'type': 'document', 'id': 'd1', 'name': 'Doc', 'description_short': '', 'source': 'given'},
            {'type': 'document', 'id': 'd2', 'name': 'Other', 'description_short': '', 'source': 'given'},
        ]
    }


def test_entry_and_can_use_compare_ids_as_strings():
    a = run_access()
    assert access.entry(a, 'server', 's1')['name'] == 'web'
    assert access.entry(a, 'server', 'nope') is None
    assert access.entry(a, 'service', 's1') is None
    a = {'resources': [{'type': 'server', 'id': 7}]}
    assert access.can_use(a, 'server', '7')
    assert access.can_use(a, 'server', 7)
    assert not access.can_use(a, 'server', '8')


def test_can_create_document_needs_every_built_for():
    a = run_access()
    assert access.can_create_document(a, [('server', 's1'), ('document', 'd1')])
    assert not access.can_create_document(a, [('server', 's1'), ('server', 'gone')])
    assert access.can_create_document(a, [])


def test_has_any():
    a = run_access()
    assert access.has_any(a, 'server')
    assert not access.has_any(a, 'service')


def test_refusal_texts_are_exact():
    assert access.refuse_use('code_project') == 'This code project is not available in this run.'
    assert access.refuse_use('mcp_server') == 'This MCP server is not available in this run.'
    assert access.refuse_none('service') == 'This run has no services.'
    assert access.refuse_none('code_project', 'server') == 'This run has no code projects or servers.'
    assert set(access.TYPES) == set(access.TYPE_WORDS)
    assert access.TYPES == (
        'server',
        'service',
        'environment',
        'code_project',
        'document',
        'mcp_server',
    )  # the collection type is gone; skills are not access (config['skills'])


def test_narrow_none_keeps_agent_list():
    agent = run_access()
    out = access.narrow(agent, None)
    assert out == agent
    assert out is not agent


def test_narrow_picks_subset_and_refuses_widening():
    agent = run_access()
    out = access.narrow(agent, [{'type': 'server', 'id': 's1'}])
    assert [r['id'] for r in out['resources']] == ['s1']
    with pytest.raises(ValueError, match='server x'):
        access.narrow(agent, [{'type': 'server', 'id': 'x'}])
    with pytest.raises(ValueError, match='document s1'):
        access.narrow(agent, [{'type': 'document', 'id': 's1'}])


def test_narrow_ignores_duplicate_picks():
    out = access.narrow(run_access(), [{'type': 'server', 'id': 's1'}, {'type': 'server', 'id': 's1'}])
    assert [(r['type'], r['id']) for r in out['resources']] == [('server', 's1')]


def test_expand_marks_sources_and_does_not_duplicate():
    a = run_access()
    kid = {'type': 'service', 'id': 'v1', 'name': 'api', 'description_short': 'x'}
    dup = {'type': 'server', 'id': 's1', 'name': 'web', 'description_short': 'web box'}
    out = access.expand(a, {('server', 's1'): [kid, dup], ('document', 'd1'): [kid]})
    ids = [(r['type'], r['id'], r['source']) for r in out['resources']]
    assert ids[:3] == [('server', 's1', 'given'), ('document', 'd1', 'given'), ('document', 'd2', 'given')]
    assert ids[3:] == [('service', 'v1', 'via server s1')]
    assert a == run_access()  # input untouched


def test_listing_filters_and_keeps_order():
    a = access.expand(
        run_access(), {('server', 's1'): [{'type': 'service', 'id': 'v1', 'name': 'api', 'description_short': ''}]}
    )
    assert [r['id'] for r in access.listing(a)] == ['s1', 'd1', 'd2', 'v1']
    assert [r['id'] for r in access.listing(a, 'document')] == ['d1', 'd2']
    assert access.listing(a, 'environment') == []


def test_expand_marks_a_grandchild_via_its_direct_parent():
    a = {'resources': [{'type': 'environment', 'id': 'e1', 'name': 'Prod', 'description_short': ''}]}
    service = {'type': 'service', 'id': 'v1', 'name': 'api', 'description_short': ''}
    project = {'type': 'code_project', 'id': 'c1', 'name': 'API', 'description_short': ''}
    out = access.expand(a, {('environment', 'e1'): [service], ('service', 'v1'): [project]})
    assert [(r['id'], r['source']) for r in out['resources']] == [
        ('e1', 'given'),
        ('v1', 'via environment e1'),
        ('c1', 'via service v1'),
    ]


def test_narrow_refusal_names_the_type_in_words():
    with pytest.raises(ValueError, match="^code project x is not in the agent's access$"):
        access.narrow(run_access(), [{'type': 'code_project', 'id': 'x'}])


def test_an_old_access_object_with_roles_and_editable_loads_without_them():
    """No roles: an older stored object's roles (content_editor, inventory_editor) and a document entry's editable are
    ignored; narrowing keeps only the resources."""
    old = {
        'resources': [{**run_access()['resources'][1], 'editable': True}],
        'roles': {'content_editor': True, 'inventory_editor': True},
    }
    for name in ('ROLES', 'NOT_EDITOR', 'is_content_editor', 'can_write_document', 'can_propose'):
        assert not hasattr(access, name)
    assert access.narrow(old, None) == {'resources': old['resources']}
    assert access.narrow(old, [{'type': 'document', 'id': 'd1', 'editable': False}]) == {'resources': old['resources']}
    assert 'editable' not in access.listing(old)[0]


def test_no_inventory_editor_role_is_left_in_the_code():
    """The role went with inventory drafts: only old stored access objects may still carry it (ignored)."""
    services = Path(__file__).resolve().parents[2]
    places = [services / 'admin-api' / 'src', services / 'admin-ui' / 'app']
    suffixes = {'.py', '.json', '.md', '.ts', '.tsx'}
    found = [
        str(path)
        for place in places
        for path in place.rglob('*')
        if path.suffix in suffixes
        and 'node_modules' not in path.parts
        and path.is_file()
        and any(word in path.read_text(errors='ignore') for word in ('inventory_editor', 'Inventory editor'))
    ]
    assert found == []
