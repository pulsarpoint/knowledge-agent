"""Every catalog tool's access check (check_<tool> next to the tool) against a set of access objects: allowed (None)
or the exact refusal. Rows are built from CATALOG, so a tool without a check or without a row fails. No database, no
agent run."""

import importlib
import inspect

import pytest

from knowledge_admin import access as access_rules
from knowledge_admin.tools.catalog import CATALOG


def item(type_: str, id_: str, name: str, source: str = 'given', **extra) -> dict:
    return {'type': type_, 'id': id_, 'name': name, 'description_short': '', 'source': source, **extra}


def run(*resources: dict, editor: bool = False) -> dict:
    return {'resources': list(resources), 'roles': {'content_editor': editor}}


SERVER = item('server', 's1', 'web-1')
ACCESS = {
    'empty': run(),
    # a plain resource chat on a server
    'server': run(SERVER),
    'server_with_services': run(SERVER, item('service', 'v1', 'api', source='via server s1')),
    'read_only_document': run(item('document', 'd1', 'Runbook')),
    'editable_document': run(item('document', 'd1', 'Runbook', editable=True)),
    'editable_document_editor': run(item('document', 'd1', 'Runbook', editable=True), editor=True),
    # the Propose wizard: a run on a server that may change content
    'wizard': run(SERVER, editor=True),
    # Documents → New: its new document, built for the server
    'new_document': run(
        SERVER,
        item(
            'document',
            'new:n1',
            'New doc',
            editable=True,
            new=True,
            links=[['server', 's1']],
            built_for=[['server', 's1']],
        ),
        editor=True,
    ),
    'code_and_collection': run(
        item('code_project', 'c1', 'payments-api', folder_paths=['/code/payments']),
        item('collection', 'k1', 'payments', paths=['/data/payments']),
    ),
    # one of every kind, none of them the one asked for
    'other': run(
        item('server', 's9', 'db-9'),
        item('service', 'v9', 'other'),
        item('environment', 'e9', 'staging'),
        item('code_project', 'c9', 'other-code'),
        item('collection', 'k9', 'private'),
        item('document', 'd9', 'Other doc', editable=True),
        editor=True,
    ),
}

ARGS = {
    'list_files': {'path': None},
    'read_file': {'path': '/code/payments/a.py'},
    'search_files': {'path': '/code/payments', 'query': 'limit'},
    'shell_run': {'command': 'ls'},
    'code_search': {'path': '/code/payments', 'language': 'Python', 'name': 'limit'},
    'ast_grep': {'path': '/code/payments', 'language': 'Python', 'pattern': 'def $F(): $$$B'},
    'code_graph': {'project_id': 'c1'},
    'search_documents': {'query': 'limits', 'collection': 'payments'},
    'read_document': {'collection': 'payments', 'path': 'limits.md'},
    'report_not_covered': {'reason': 'missing'},
    'ssh_run': {'server_id': 's1', 'command': 'uptime'},
    'get_available_resources': {'resource_type': None},
    'get_resource': {'resource_type': 'server', 'resource_id': 's1'},
    'get_document': {'document_id': 'd1'},
    'propose_service': {'started_from': {'type': 'server', 'id': 's1', 'name': 'web-1'}},
    'propose_document': {'started_from': {'type': 'server', 'id': 's1', 'name': 'web-1'}},
    'save_document': {'document_id': 'd1', 'markdown': '# Runbook'},
    'edit_document': {'document_id': 'd1', 'old_text': 'a', 'new_text': 'b'},
    'search_memory': {'query': 'x'},
    'get_memory': {'name': 'x'},
    'save_memory': {'name': 'x', 'content': 'y'},
    'update_memory': {'name': 'x', 'content': 'y', 'revision': 1},
    'run_subagent': {'task': 'look', 'tools': ['ssh_run'], 'resources': {'server_ids': ['s1']}},
}

NOT_EDITOR = access_rules.NOT_EDITOR
NO_FILES = 'This run has no code projects or collections.'
NO_CODE = 'This run has no code projects.'
NO_COLLECTIONS = 'This run has no collections.'
NO_SERVERS = 'This run has no servers.'
NO_DOCUMENTS = 'This run has no documents.'
NOT_SERVER = 'This server is not available in this run.'
NOT_CODE = 'This code project is not available in this run.'
NOT_COLLECTION = 'This collection is not available in this run.'
NOT_DOCUMENT = 'This document is not available in this run.'
WITH_SERVER = ('server', 'server_with_services', 'wizard', 'new_document')
WITHOUT_EDITOR = ('empty', 'server', 'server_with_services', 'read_only_document', 'editable_document')


def row(default, **cells) -> dict:
    """The expected result for every access object: `default` unless named."""
    return {name: cells.get(name, default) for name in ACCESS}


def some(names, value) -> dict:
    return dict.fromkeys(names, value)


FILES = row(NO_FILES, code_and_collection=None, other=None)
CODE = row(NO_CODE, code_and_collection=None, other=None)
COLLECTION = row(NO_COLLECTIONS, code_and_collection=None, other=NOT_COLLECTION)
SERVER_ROW = row(NO_SERVERS, other=NOT_SERVER, **some(WITH_SERVER, None))
PROPOSE = row(
    NOT_SERVER, wizard=None, new_document=None, **some(WITHOUT_EDITOR, NOT_EDITOR), code_and_collection=NOT_EDITOR
)
WRITE = row(
    NOT_EDITOR,
    editable_document_editor=None,
    wizard=NO_DOCUMENTS,
    new_document=f'{NOT_DOCUMENT} Documents you may change: New doc (new:n1).',
    other=f'{NOT_DOCUMENT} Documents you may change: Other doc (d9).',
)
ALWAYS = row(None)

EXPECTED = {
    'list_files': FILES,
    'read_file': FILES,
    'search_files': FILES,
    'shell_run': FILES,
    'code_search': CODE,
    'ast_grep': CODE,
    'code_graph': row(NO_CODE, code_and_collection=None, other=NOT_CODE),
    'search_documents': COLLECTION,
    'read_document': COLLECTION,
    'report_not_covered': ALWAYS,
    'ssh_run': SERVER_ROW,
    'get_available_resources': ALWAYS,
    'get_resource': SERVER_ROW,
    'get_document': row(NOT_DOCUMENT, read_only_document=None, editable_document=None, editable_document_editor=None),
    'propose_service': PROPOSE,
    'propose_document': PROPOSE,
    'save_document': WRITE,
    'edit_document': WRITE,
    'search_memory': ALWAYS,
    'get_memory': ALWAYS,
    'save_memory': ALWAYS,
    'update_memory': ALWAYS,
    'run_subagent': row(NOT_SERVER, **some(WITH_SERVER, None)),
}


def check_of(name: str):
    """check_<name> in the module of the tool's function (the @tool registration wraps it)."""
    function = inspect.getclosurevars(CATALOG[name].run).nonlocals['function']
    return getattr(importlib.import_module(function.__module__), f'check_{name}', None)


def test_every_catalog_tool_has_a_check_and_a_row():
    assert [name for name in CATALOG if check_of(name) is None] == []
    assert sorted(set(CATALOG) - set(EXPECTED)) == []
    assert sorted(set(CATALOG) - set(ARGS)) == []
    assert sorted(set(EXPECTED) - set(CATALOG)) == []


CELLS = [(tool, name) for tool in CATALOG for name in ACCESS]


@pytest.mark.parametrize('tool,access_name', CELLS, ids=[f'{tool}-{name}' for tool, name in CELLS])
def test_check(tool, access_name):
    check = check_of(tool)
    assert check is not None, f'{tool} has no check_{tool}'
    assert check(ACCESS[access_name], **ARGS[tool]) == EXPECTED[tool][access_name]


def test_a_new_document_is_created_only_for_resources_in_the_list():
    from knowledge_admin.capabilities.document_tools import check_save_document

    assert check_save_document(ACCESS['new_document'], document_id='new:n1') is None
    elsewhere = run(
        item('document', 'new:n1', 'New doc', editable=True, new=True, built_for=[['service', 'v1']]), editor=True
    )
    assert check_save_document(elsewhere, document_id='new:n1') == 'This service is not available in this run.'
    off = run(*ACCESS['new_document']['resources'])
    assert check_save_document(off, document_id='new:n1') == NOT_EDITOR


def test_a_document_linked_to_a_resource_in_the_list_is_readable():
    from knowledge_admin.capabilities.document_tools import check_get_document

    assert check_get_document(ACCESS['server'], document_id='d7', linked=[('server', 's1')]) is None
    assert check_get_document(ACCESS['server'], document_id='d7', linked=[('service', 'v1')]) == NOT_DOCUMENT
    assert check_get_document(ACCESS['server_with_services'], document_id='d7', linked=[('service', 'v1')]) is None


def test_a_subagent_given_all_of_its_parents_resources_is_allowed():
    from knowledge_admin.capabilities.subagents import check_run_subagent

    assert check_run_subagent(ACCESS['empty'], resources=None) is None
    assert check_run_subagent(ACCESS['code_and_collection'], resources={'collection_ids': ['k1']}) is None
    assert check_run_subagent(ACCESS['code_and_collection'], resources={'code_project_ids': ['c9']}) == NOT_CODE


def test_proposals_outside_a_resource_run_are_left_to_the_tool():
    from knowledge_admin.capabilities.discovery import check_propose_document

    assert check_propose_document(ACCESS['wizard'], started_from=None) is None
    assert check_propose_document(ACCESS['server'], started_from=None) == NOT_EDITOR
