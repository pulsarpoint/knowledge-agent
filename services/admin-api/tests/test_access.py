import pytest

from knowledge_admin import access


def run_access(editor=False):
    return {
        'resources': [
            {'type': 'server', 'id': 's1', 'name': 'web', 'description_short': 'web box', 'source': 'given'},
            {'type': 'document', 'id': 'd1', 'name': 'Doc', 'description_short': '', 'source': 'given'},
            {
                'type': 'document',
                'id': 'd2',
                'name': 'Editable',
                'description_short': '',
                'source': 'given',
                'editable': True,
            },
        ],
        'roles': {'content_editor': editor},
    }


def test_entry_and_can_use_compare_ids_as_strings():
    a = run_access()
    assert access.entry(a, 'server', 's1')['name'] == 'web'
    assert access.entry(a, 'server', 'nope') is None
    assert access.entry(a, 'service', 's1') is None
    a = {'resources': [{'type': 'server', 'id': 7}], 'roles': {}}
    assert access.can_use(a, 'server', '7')
    assert access.can_use(a, 'server', 7)
    assert not access.can_use(a, 'server', '8')


def test_can_write_document_needs_editor_and_editable():
    assert access.can_write_document(run_access(True), 'd2')
    assert not access.can_write_document(run_access(True), 'd1')  # editable defaults to false
    assert not access.can_write_document(run_access(False), 'd2')
    assert not access.can_write_document(run_access(True), 'missing')
    assert not access.can_write_document({'resources': [{'type': 'document', 'id': 'd'}]}, 'd')  # no roles key


def test_can_propose_needs_editor_and_resource():
    assert access.can_propose(run_access(True), 'server', 's1')
    assert not access.can_propose(run_access(True), 'server', 'x')
    assert not access.can_propose(run_access(False), 'server', 's1')


def test_can_create_document_needs_every_built_for():
    a = run_access(True)
    assert access.can_create_document(a, [('server', 's1'), ('document', 'd1')])
    assert not access.can_create_document(a, [('server', 's1'), ('server', 'gone')])
    assert not access.can_create_document(run_access(False), [('server', 's1')])
    assert access.can_create_document(a, [])


def test_has_any():
    a = run_access()
    assert access.has_any(a, 'server')
    assert not access.has_any(a, 'skill')


def test_refusal_texts_are_exact():
    assert access.refuse_use('code_project') == 'This code project is not available in this run.'
    assert access.refuse_use('mcp_server') == 'This MCP server is not available in this run.'
    assert access.refuse_none('collection') == 'This run has no collections.'
    assert access.NOT_EDITOR == 'This agent may not change content (content editor is off).'
    assert set(access.TYPES) == set(access.TYPE_WORDS)


def test_narrow_none_keeps_agent_list_and_roles():
    agent = run_access(True)
    out = access.narrow(agent, None)
    assert out == agent
    assert out is not agent


def test_narrow_picks_subset_and_refuses_widening():
    agent = run_access(True)
    out = access.narrow(agent, [{'type': 'server', 'id': 's1'}])
    assert [r['id'] for r in out['resources']] == ['s1']
    with pytest.raises(ValueError, match='server x'):
        access.narrow(agent, [{'type': 'server', 'id': 'x'}])
    with pytest.raises(ValueError, match='document s1'):
        access.narrow(agent, [{'type': 'document', 'id': 's1'}])


def test_narrow_never_switches_a_role_on():
    assert access.narrow(run_access(True), None, ['content_editor'])['roles']['content_editor'] is False
    assert access.narrow(run_access(False), None)['roles']['content_editor'] is False
    assert access.narrow(run_access(False), None, [])['roles']['content_editor'] is False


def test_narrow_picked_cannot_make_document_editable():
    out = access.narrow(run_access(True), [{'type': 'document', 'id': 'd1', 'editable': True}])
    assert not access.can_write_document(out, 'd1')


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
    assert access.listing(a, 'skill') == []


def test_expand_marks_a_grandchild_via_its_direct_parent():
    a = {'resources': [{'type': 'environment', 'id': 'e1', 'name': 'Prod', 'description_short': ''}], 'roles': {}}
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
        access.narrow(run_access(True), [{'type': 'code_project', 'id': 'x'}])
