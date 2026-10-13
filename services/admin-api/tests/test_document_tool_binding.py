"""The edit tools list only the run's editable documents in their schema; the prompt lists the run's documents."""
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import ENUM_LIMIT, function_tool

ACCESS = [{'id': 'd1', 'title': 'Runbook', 'editable': True}, {'id': 'd2', 'title': 'Overview', 'editable': False}]


def test_the_edit_tools_schema_lists_only_the_editable_documents():
    tool = function_tool(CATALOG['save_document'], 'Save.', ACCESS)
    assert tool.params_json_schema['properties']['document_id']['enum'] == ['d1']
    assert 'Runbook (d1)' in tool.description and 'Overview' not in tool.description


def test_without_choices_or_above_the_limit_there_is_no_enum():
    assert 'enum' not in function_tool(CATALOG['save_document'], 'Save.').params_json_schema['properties']['document_id']
    many = [{'id': f'd{n}', 'title': f'T{n}', 'editable': True} for n in range(ENUM_LIMIT + 1)]
    assert 'enum' not in function_tool(CATALOG['save_document'], 'Save.', many).params_json_schema['properties']['document_id']
