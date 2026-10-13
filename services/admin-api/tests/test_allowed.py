"""allowed(): the one rule every per-resource tool uses — the argument must be pinned in the run's config."""
import pytest

from knowledge_admin.capabilities.context import NOT_AVAILABLE, allowed

CONFIG = {'ssh': {'enabled': True, 'servers': [{'id': 's1', 'name': 'web-1'}]},
          'document_access': [{'id': 'd1', 'title': 'Runbook', 'editable': True},
                              {'id': 'd2', 'title': 'Overview', 'editable': False}]}


def test_a_pinned_entry_is_returned():
    assert allowed(CONFIG, 'server', 's1')['name'] == 'web-1'
    assert allowed(CONFIG, 'document', 'd2')['title'] == 'Overview'
    assert allowed(CONFIG, 'document', 'd1', write=True)['editable']


@pytest.mark.parametrize('kind,item,write', [('server', 's9', False), ('document', 'd9', False), ('document', 'd2', True)])
def test_anything_else_is_refused(kind, item, write):
    with pytest.raises(ValueError, match=f'^{NOT_AVAILABLE.format(kind=kind)}$'):
        allowed(CONFIG, kind, item, write=write)


def test_ssh_switched_off_or_no_documents_allow_nothing():
    with pytest.raises(ValueError):
        allowed({**CONFIG, 'ssh': {'enabled': False, 'servers': CONFIG['ssh']['servers']}}, 'server', 's1')
    with pytest.raises(ValueError):
        allowed({'ssh': {'enabled': False, 'servers': []}}, 'document', 'd1')
