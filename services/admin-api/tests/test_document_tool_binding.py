"""Tool schemas are the same in every run: the run's documents are in the prompt (documents_in_this_run), not in the
document tools' schema or description; a document tool given another id names the documents the run may use."""

import asyncio

import pytest
from access_helpers import entry, with_access

from knowledge_admin.agents.runner import instructions_and_tools
from knowledge_admin.capabilities import document_tools
from live_documents import write_live_document
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_world import ask, post  # noqa: F401  (fixture)

ACCESS = [entry('document', 'd1', 'Runbook'), entry('document', 'd2', 'Overview')]
REFUSED = 'This document is not available in this run. Documents you may use: Runbook (d1), Overview (d2).'


def documents(config) -> list[dict]:
    return [item for item in config['access']['resources'] if item['type'] == 'document']


def schemas(config) -> list[tuple]:
    _, tools, _, _ = instructions_and_tools(config, subagents=True)
    return [(tool.name, tool.description, tool.params_json_schema) for tool in tools]


def test_two_runs_with_different_documents_get_identical_tool_schemas(ask):
    other = write_live_document(ask.s.store, 'payments-overview', 'Payments overview', '# Payments overview\n')
    first = post(ask, '/preview')
    second = post(
        ask,
        '/preview',
        started_from={'document_id': other['id']},
        resources={**ask.body['resources'], 'documents': [{'id': other['id']}]},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    one, two = first.json()['config'], second.json()['config']
    assert documents(one) != documents(two)
    assert {'update_document', 'edit_document'} <= {name for name, _, _ in schemas(one)}
    assert schemas(one) == schemas(two)
    edit = [entry for entry in schemas(one) if entry[0] in ('update_document', 'edit_document')]
    assert all('enum' not in schema['properties']['document_id'] for _, _, schema in edit)
    assert 'Payments runbook' not in str(edit) and 'Payments overview' not in str(edit)


@pytest.mark.parametrize('document_id', ['d3', 'not-an-id'])
def test_a_document_tool_given_another_id_names_the_documents_the_run_may_use(document_id):
    config = with_access({}, ACCESS)
    assert asyncio.run(document_tools.update_document({'config': config}, document_id, '# Text')) == REFUSED
    assert asyncio.run(document_tools.edit_document({'config': config}, document_id, 'a', 'b')) == REFUSED
