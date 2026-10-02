"""Document tools search only the agent's folders and record citable excerpts."""
import asyncio
import json

import httpx
import pytest
from agents.tool_context import ToolContext

from knowledge_admin import grounding
from knowledge_admin.agents import get_paths, get_prompt, get_tools
from knowledge_admin.tools import documents
from knowledge_admin.tools.documents import read_document, report_not_covered, search_documents


@pytest.fixture
def rag(monkeypatch):
    calls = []
    state = {'hits': [{'path': 'payments/retries.md', 'folder': 'payments', 'text': 'Retries stop after 3 attempts.'}],
             'fail': False}

    def handle(request):
        calls.append(request)
        if state['fail']:
            return httpx.Response(503, json={'error': {'message': 'down'}})
        if request.url.path == '/backoffice/workspace':
            return httpx.Response(200, json={'project_id': 'p1'})
        if request.url.path == '/search':
            return httpx.Response(200, json={'results': state['hits']})
        if request.url.path == '/backoffice/projects/p1/file':
            return httpx.Response(200, json={'content': 'Full retry policy.'})
        raise AssertionError(request.url)

    monkeypatch.setenv('DOCS_WIKI_RAG_URL', 'http://rag.test')
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    monkeypatch.setattr(documents, 'RAG_TRANSPORT', httpx.MockTransport(handle))
    return calls, state


def config(**documents_settings):
    base = {'enabled': True, 'grounded': True, 'all_folders': False,
            'folders': [{'path': '/work/rag-data/payments', 'search_folder': 'payments'}]}
    return {'enabled': True, 'name': 'Docs', 'description': '', 'instructions': '',
            'system_prompt': {'content': 'Answer questions.'},
            'documents': {**base, **documents_settings},
            'code': {'enabled': False, 'projects': []}, 'skills': {'enabled': False, 'items': []},
            'services': {'enabled': False, 'items': []}, 'ssh': {'enabled': False, 'servers': []},
            'mcp': {'enabled': False, 'servers': []}}


def invoke(tool, context, **arguments):
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id='t1', tool_arguments=payload)
    return asyncio.run(tool.on_invoke_tool(wrapped, payload))


def run_context(cfg):
    return {'config': cfg, 'credentials': {}, 'workspace': None, 'shell_enabled': False}


def test_search_numbers_excerpts_and_limits_folders(rag):
    calls, _ = rag
    context = run_context(config())
    output = invoke(search_documents, context, query='retry limit')
    assert output.startswith('[1] payments/retries.md (payments)\nRetries stop after 3 attempts.')
    body = json.loads(calls[-1].content)
    assert body['folders'] == ['payments'] and body['mode'] == 'hybrid' and body['project_id'] == 'p1'
    assert calls[-1].headers['x-rag-key'] == 'rag-key'
    assert context['grounding']['searches'] == 1 and context['grounding']['excerpts'][0]['n'] == 1


def test_empty_folder_scope_never_searches_everything(rag):
    calls, _ = rag
    context = run_context(config(folders=[]))
    assert invoke(search_documents, context, query='anything') == 'No matches in the documentation.'
    assert calls == []
    assert context['grounding'] == {'excerpts': [], 'searches': 1, 'empty_searches': 1, 'reported': None}


def test_folder_argument_must_be_an_allowed_folder(rag):
    context = run_context(config())
    assert 'outside' in invoke(search_documents, context, query='x', folder='people')


def test_all_folders_searches_everything(rag):
    calls, _ = rag
    invoke(search_documents, run_context(config(all_folders=True)), query='x')
    assert 'folders' not in json.loads(calls[-1].content)


def test_rag_failure_is_reported_to_model_and_not_counted(rag):
    _, state = rag
    state['fail'] = True
    context = run_context(config())
    assert 'unavailable' in invoke(search_documents, context, query='x')
    assert context.get('grounding', {}).get('searches', 0) == 0


def test_read_document_registers_excerpt_and_checks_folder(rag):
    context = run_context(config())
    assert invoke(read_document, context, path='payments/retries.md') == '[1] payments/retries.md\nFull retry policy.'
    assert 'outside' in invoke(read_document, context, path='people/salaries.md')
    assert 'Use a document path' in invoke(read_document, context, path='../etc/passwd')


def test_report_not_covered_records_reason(rag):
    context = run_context(config())
    invoke(report_not_covered, context, reason='No refund SLA documented.')
    assert context['grounding']['reported'] == 'No refund SLA documented.'


def test_result_lists_only_cited_existing_excerpts():
    state = {'excerpts': [{'n': 1, 'path': 'a/x.md', 'folder': 'a', 'text': 't'},
                          {'n': 2, 'path': 'a/y.md', 'folder': 'a', 'text': 't'}],
             'searches': 1, 'empty_searches': 0, 'reported': None}
    summary = grounding.result('Use x [2][2], not [7].\n```\ncode [1]\n```', state)
    assert summary['sources'] == [{'n': 2, 'path': 'a/y.md', 'heading_path': 'a', 'anchor': None}]
    assert summary['not_covered'] is False and summary['results'] == 2
    assert grounding.result('hello', None) is None


@pytest.mark.parametrize('state, expected', [
    ({'excerpts': [], 'searches': 2, 'empty_searches': 2, 'reported': None}, True),
    ({'excerpts': [], 'searches': 0, 'empty_searches': 0, 'reported': None}, False),
    ({'excerpts': [{'n': 1, 'path': 'a/x.md', 'folder': 'a', 'text': 't'}], 'searches': 1, 'empty_searches': 0, 'reported': 'gap'}, True),
])
def test_not_covered_rule(state, expected):
    assert grounding.result('answer', state)['not_covered'] is expected


def test_grounded_agents_get_only_citable_tools_and_policy():
    grounded = config()
    names = {tool.name for tool in get_tools(grounded, [])}
    assert names == {'search_documents', 'read_document', 'report_not_covered'}
    assert grounding.POLICY in get_prompt(grounded, [], [])
    ungrounded = config(grounded=False)
    names = {tool.name for tool in get_tools(ungrounded, [])}
    assert {'search_documents', 'read_document', 'list_files', 'shell_run'} <= names
    assert 'report_not_covered' not in names
    assert grounding.POLICY not in get_prompt(ungrounded, [], [])
