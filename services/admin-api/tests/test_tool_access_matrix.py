"""Every catalog tool's access check (check_<tool> next to the tool) against a set of access objects: allowed (None)
or the exact refusal. Rows are built from CATALOG, so a tool without a check or without a row fails. No database, no
agent run."""

import importlib

import pytest

from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.registry import REGISTRY


def item(type_: str, id_: str, name: str, source: str = 'given', **extra) -> dict:
    return {'type': type_, 'id': id_, 'name': name, 'description_short': '', 'source': source, **extra}


def run(*resources: dict) -> dict:
    return {'resources': list(resources)}


SERVER = item('server', 's1', 'web-1')
ACCESS = {
    'empty': run(),
    # a plain resource chat on a server
    'server': run(SERVER),
    'server_with_services': run(SERVER, item('service', 'v1', 'api', source='via server s1')),
    # a document page's Ask agent run
    'document': run(item('document', 'd1', 'Runbook')),
    # Documents → New: its new document, built for the server
    'new_document': run(
        SERVER,
        item(
            'document',
            'new:n1',
            'New doc',
            new=True,
            built_for=[['server', 's1']],
        ),
    ),
    'code_and_collection': run(
        item('code_project', 'c1', 'payments-api', folder_paths=['/code/payments']),
        item('collection', 'k1', 'payments', paths=['/data/payments']),
    ),
    # a saved agent's skill (skills are always given)
    'skill': run(item('skill', 'k1', 'inspect')),
    # a run on an environment (its server and service come with it): it may write drafts
    'environment': run(
        item('environment', 'e1', 'prod'),
        item('server', 's1', 'web-1', source='via environment e1'),
        item('service', 'v1', 'api', source='via environment e1'),
    ),
    # one of every kind, none of them the one asked for
    'other': run(
        item('server', 's9', 'db-9'),
        item('service', 'v9', 'other'),
        item('environment', 'e9', 'staging'),
        item('code_project', 'c9', 'other-code'),
        item('collection', 'k9', 'private'),
        item('document', 'd9', 'Other doc'),
        item('skill', 'k9', 'other-skill'),
    ),
}

# The rows a tool loads for the deployment it names (capabilities/inventory_tools._facts).
DEPLOYMENT_ROWS = {
    ('deployment', 'p1'): {'server_id': 's1', 'service_id': 'v1', 'environment_id': 'e1', 'status': 'draft'}
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
    'propose_document': {'started_from': {'type': 'server', 'id': 's1', 'name': 'web-1'}},
    'list_documents': {'resource': {'type': 'server', 'id': 's1'}},
    'new_document': {'resource': {'type': 'server', 'id': 's1'}, 'title': 'Disks', 'content': '# Disks'},
    'update_document': {'document_id': 'd1', 'content': '# Runbook'},
    'edit_document': {'document_id': 'd1', 'old_text': 'a', 'new_text': 'b'},
    'search_memory': {'query': 'x'},
    'get_memory': {'name': 'x'},
    'save_memory': {'name': 'x', 'content': 'y'},
    'update_memory': {'name': 'x', 'content': 'y', 'revision': 1},
    'run_subagent': {'task': 'look', 'resources': {'server_ids': ['s1']}},
    'load_skill': {'name': 'inspect'},
    'schedule_wakeup': {'message': 'Check the deploy.', 'in_minutes': 30},
    'list_wakeups': {},
    'cancel_wakeup': {'id': 1},
    'get_environment_model': {'environment_id': 'e1'},
    'list_servers': {'environment_id': 'e1'},
    'list_deployments': {'server_id': 's1'},
    'list_connections': {'service_id': 'v1'},
    'save_environment': {'name': 'staging'},
    'save_server': {'name': 'cache-1', 'hostname': 'cache-1.internal', 'environment_id': 'e1'},
    'save_service': {'id': 'v1', 'owner': 'ops'},
    'save_deployment': {'service_id': 'v1', 'server_id': 's1', 'environment_id': 'e1'},
    'save_connection': {'from_deployment_id': 'p1', 'to_service_id': 'v1', 'port': 5432, 'rows': DEPLOYMENT_ROWS},
    'save_relation': {'from_service_id': 'v1', 'kind': 'built_from', 'to_code_project_id': 'c1'},
    'remove_inventory_item': {'kind': 'deployment', 'id': 'p1', 'rows': DEPLOYMENT_ROWS},
    'save_inventory_draft': {'environment': 'prod'},
}

NO_FILES = 'This run has no code projects or collections.'
NO_CODE = 'This run has no code projects.'
NO_COLLECTIONS = 'This run has no collections.'
NO_SERVERS = 'This run has no servers.'
NO_DOCUMENTS = 'This run has no documents.'
NOT_SERVER = 'This server is not available in this run.'
NOT_CODE = 'This code project is not available in this run.'
NOT_COLLECTION = 'This collection is not available in this run.'
NOT_DOCUMENT = 'This document is not available in this run.'
NOT_SKILL = 'This skill is not available in this run.'
NOT_GROUNDED = 'This run does not answer only from documents.'
WITH_SERVER = ('server', 'server_with_services', 'new_document', 'environment')
LINKED_TOO = (
    'Documents linked to a server, service or environment in this run are usable too: list_documents lists them.'
)
NOT_ENVIRONMENT = 'This environment is not available in this run.'
NOT_SERVICE = 'This service is not available in this run.'
NOT_DEPLOYMENT = 'This deployment is not available in this run.'
NO_INVENTORY = 'This run has no environments or servers or services.'


def row(default, **cells) -> dict:
    """The expected result for every access object: `default` unless named."""
    return {name: cells.get(name, default) for name in ACCESS}


def some(names, value) -> dict:
    return dict.fromkeys(names, value)


FILES = row(NO_FILES, code_and_collection=None, other=None)
CODE = row(NO_CODE, code_and_collection=None, other=None)
COLLECTION = row(NO_COLLECTIONS, code_and_collection=None, other=NOT_COLLECTION)
SERVER_ROW = row(NO_SERVERS, other=NOT_SERVER, **some(WITH_SERVER, None))
PROPOSE = row(NOT_SERVER, **some(WITH_SERVER, None))
# usable = readable: a document in the run's list, or one linked to a resource in it (the tool loads the links)
USE = row(
    NOT_DOCUMENT,
    document=None,
    **some(('server', 'server_with_services', 'environment'), f'{NOT_DOCUMENT} {LINKED_TOO}'),
    new_document=f'{NOT_DOCUMENT} Documents you may use: New doc (new:n1). {LINKED_TOO}',
    other=f'{NOT_DOCUMENT} Documents you may use: Other doc (d9). {LINKED_TOO}',
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
    'report_not_covered': row(NOT_GROUNDED),  # a grounded run passes grounded=True (below)
    'ssh_run': SERVER_ROW,
    'get_available_resources': ALWAYS,
    'get_resource': SERVER_ROW,
    'get_document': USE,
    'propose_document': PROPOSE,
    'list_documents': SERVER_ROW,
    'new_document': SERVER_ROW,
    'update_document': USE,
    'edit_document': USE,
    'search_memory': ALWAYS,
    'get_memory': ALWAYS,
    'save_memory': ALWAYS,
    'update_memory': ALWAYS,
    'run_subagent': row(NOT_SERVER, **some(WITH_SERVER, None)),
    'load_skill': row(NOT_SKILL, skill=None),
    'schedule_wakeup': ALWAYS,  # a session's own wake-ups; the time is checked in test_wakeups
    'list_wakeups': ALWAYS,
    'cancel_wakeup': ALWAYS,
    'get_environment_model': row(NOT_ENVIRONMENT, environment=None),
    'list_servers': row(NOT_ENVIRONMENT, environment=None),
    'list_deployments': row(NOT_SERVER, **some(WITH_SERVER, None)),
    'list_connections': row(NOT_SERVICE, server_with_services=None, environment=None),
    # writes (drafts, no role): a new entry needs an environment, server or service in the run, then every item named
    # must be usable (code project c1 is not in the environment run)
    'save_environment': row(NO_INVENTORY, **some((*WITH_SERVER, 'other'), None)),
    'save_server': row(NO_INVENTORY, **{**some((*WITH_SERVER, 'other'), NOT_ENVIRONMENT), 'environment': None}),
    'save_service': row(NOT_SERVICE, server_with_services=None, environment=None),
    'save_deployment': row(NOT_SERVICE, server_with_services=NOT_ENVIRONMENT, environment=None),
    'save_connection': row(NOT_DEPLOYMENT, environment=None),
    'save_relation': row(NOT_SERVICE, server_with_services=NOT_CODE, environment=NOT_CODE),
    'remove_inventory_item': row(NOT_DEPLOYMENT, environment=None),
    'save_inventory_draft': ALWAYS,  # names, not ids: resolve_draft decides over the rows (test_inventory_draft_batch)
}


def check_of(name: str):
    """check_<name> in the module that defines the tool."""
    return getattr(importlib.import_module(REGISTRY[name].function.__module__), f'check_{name}', None)


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
    from knowledge_admin.capabilities.document_tools import check_new_document, check_update_document

    assert check_update_document(ACCESS['new_document'], document_id='new:n1') is None
    elsewhere = run(item('document', 'new:n1', 'New doc', new=True, built_for=[['service', 'v1']]))
    assert check_update_document(elsewhere, document_id='new:n1') == 'This service is not available in this run.'
    server = {'type': 'server', 'name': 'WEB-1'}  # by name: a resource of the run with that name
    assert check_new_document(ACCESS['server'], resource=server) is None
    assert check_new_document(ACCESS['server'], resource={'type': 'server', 'name': 'db-9'}) == NOT_SERVER
    assert check_new_document(ACCESS['environment'], resource={'type': 'service', 'id': 'v1'}) is None


def test_update_document_links_only_a_resource_in_the_list():
    from knowledge_admin.capabilities.document_tools import check_update_document

    given = ACCESS['document']
    assert check_update_document(given, document_id='d1', resource={'type': 'server', 'id': 's1'}) == NO_SERVERS
    both = run(item('document', 'd1', 'Runbook'), SERVER)
    assert check_update_document(both, document_id='d1', resource={'type': 'server', 'id': 's1'}) is None
    linked = check_update_document(ACCESS['server'], document_id='d7', linked=[('server', 's1')])
    assert linked is None


def test_a_document_linked_to_a_resource_in_the_list_is_readable():
    from knowledge_admin.capabilities.document_tools import check_get_document

    assert check_get_document(ACCESS['server'], document_id='d7', linked=[('server', 's1')]) is None
    assert check_get_document(ACCESS['server'], document_id='d7', linked=[('service', 'v1')]) == (
        f'{NOT_DOCUMENT} {LINKED_TOO}'
    )
    assert check_get_document(ACCESS['server_with_services'], document_id='d7', linked=[('service', 'v1')]) is None


def test_a_subagent_given_all_of_its_parents_resources_is_allowed():
    from knowledge_admin.capabilities.subagents import check_run_subagent

    assert check_run_subagent(ACCESS['empty'], resources=None) is None
    assert check_run_subagent(ACCESS['code_and_collection'], resources={'collection_ids': ['k1']}) is None
    assert check_run_subagent(ACCESS['code_and_collection'], resources={'code_project_ids': ['c9']}) == NOT_CODE


def test_report_not_covered_is_allowed_in_a_grounded_run_whatever_its_access():
    from knowledge_admin.capabilities.documents import check_report_not_covered

    for access in ACCESS.values():
        assert check_report_not_covered(access, grounded=True, reason='missing') is None


def test_proposals_outside_a_resource_run_are_left_to_the_tool():
    from knowledge_admin.capabilities.discovery import check_propose_document

    assert check_propose_document(ACCESS['server'], started_from=None) is None
