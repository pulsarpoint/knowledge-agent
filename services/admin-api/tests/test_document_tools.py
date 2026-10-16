"""Document tools search only the agent's folders and record citable excerpts."""

import asyncio
import json

import httpx
import pytest
from access_helpers import with_access
from agents.tool_context import ToolContext
from implied_tools import implied_tools
from knowledge_admin.agents.runner import get_paths, get_prompt
from knowledge_admin.capabilities import documents
from knowledge_admin.sessions import grounding
from tool_helpers import tools

read_document, report_not_covered, search_documents = tools('read_document', 'report_not_covered', 'search_documents')


@pytest.fixture
def rag(monkeypatch):
    calls = []
    state = {
        'hits': [{'path': 'retries.md', 'collection': 'payments', 'text': 'Retries stop after 3 attempts.'}],
        'fail': False,
    }

    def handle(request):
        calls.append(request)
        if state['fail']:
            return httpx.Response(503, json={'error': {'message': 'down'}})
        if request.url.path == '/backoffice/workspace':
            return httpx.Response(200, json={'project_id': 'p1'})
        if request.url.path == '/search':
            return httpx.Response(200, json={'results': state['hits']})
        if request.url.path == '/backoffice/projects/p1/file':
            if request.url.params['path'] == 'secret.md':
                return httpx.Response(404, json={'error': {'message': 'Document not found'}})
            return httpx.Response(200, json={'content': 'Full retry policy.'})
        raise AssertionError(request.url)

    monkeypatch.setenv('DOCS_WIKI_RAG_URL', 'http://rag.test')
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    monkeypatch.setattr(documents, 'RAG_TRANSPORT', httpx.MockTransport(handle))
    return calls, state


def config(**documents_settings):
    base = {
        'enabled': True,
        'grounded': True,
        'all_collections': False,
        'collections': [{'id': '1', 'name': 'payments', 'description': '', 'path': '/work/rag-data/payments'}],
    }
    value = {
        'enabled': True,
        'name': 'Docs',
        'description': '',
        'instructions': '',
        'system_prompt': {'content': 'Answer questions.'},
        'documents': {**base, **documents_settings},
        'code': {'enabled': False, 'projects': []},
        'skills': {'enabled': False, 'items': []},
        'services': {'enabled': False, 'items': []},
        'ssh': {'enabled': False, 'servers': []},
        'mcp': {'enabled': False, 'servers': []},
    }
    value['tools'] = implied_tools(value)  # every saved configuration has its tools
    return value


def invoke(tool, context, **arguments):
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id='t1', tool_arguments=payload)
    return asyncio.run(tool.on_invoke_tool(wrapped, payload))


def run_context(cfg):
    return {'config': with_access(cfg), 'credentials': {}, 'workspace': None, 'shell_enabled': False}


def test_search_numbers_excerpts_and_limits_collections(rag):
    calls, _ = rag
    context = run_context(config())
    output = invoke(search_documents, context, query='retry limit')
    assert output.startswith('[1] payments/retries.md\nRetries stop after 3 attempts.')
    body = json.loads(calls[-1].content)
    assert body['collections'] == ['payments'] and 'mode' not in body and body['project_id'] == 'p1'
    assert body['ignore_unknown'] is True
    assert calls[-1].headers['x-rag-key'] == 'rag-key'
    assert context['grounding']['searches'] == 1
    assert context['grounding']['excerpts'][0] == {
        'n': 1,
        'path': 'retries.md',
        'collection': 'payments',
        'text': 'Retries stop after 3 attempts.',
    }


def test_empty_collection_scope_never_searches_everything(rag):
    calls, _ = rag
    context = run_context(config(collections=[]))
    assert invoke(search_documents, context, query='anything') == 'This run has no collections.'
    assert calls == []  # counted: a grounded answer after it reads as not covered
    assert context['grounding'] == {'excerpts': [], 'searches': 1, 'empty_searches': 1, 'reported': None}


def test_search_is_limited_to_granted_collections(rag):
    calls, _ = rag
    context = run_context(
        config(collections=[{'id': 'jira', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'}])
    )
    invoke(search_documents, context, query='x')
    body = json.loads(calls[-1].content)
    assert body['collections'] == ['jira'] and 'mode' not in body
    assert (
        invoke(search_documents, context, query='x', collection='docs')
        == 'This collection is not available in this run.'
    )


def test_collection_argument_narrows_to_one_granted_collection(rag):
    calls, _ = rag
    context = run_context(
        config(
            collections=[
                {'id': 'jira', 'name': 'jira', 'description': '', 'path': ''},
                {'id': 'docs', 'name': 'docs', 'description': '', 'path': ''},
            ]
        )
    )
    invoke(search_documents, context, query='x', collection='jira')
    assert json.loads(calls[-1].content)['collections'] == ['jira']


def test_all_collections_searches_the_collections_the_run_lists(rag):
    """ "All collections" is resolved into the list when the configuration loads; the run searches that list."""
    calls, _ = rag
    invoke(search_documents, run_context(config(all_collections=True)), query='x')
    assert json.loads(calls[-1].content)['collections'] == ['payments']
    refused = invoke(search_documents, run_context(config(all_collections=True)), query='x', collection='any')
    assert refused == 'This collection is not available in this run.' and len(calls) == 2


def test_a_legacy_folder_config_has_no_collections(rag):
    calls, _ = rag
    legacy = config()
    legacy['documents'] = {
        'enabled': True,
        'grounded': True,
        'all_folders': False,
        'folders': [{'path': '/work/rag-data/demo/payments', 'search_folder': 'demo.payments'}],
    }
    # folder-era collections carry no id: they are not in the run's access object
    assert invoke(search_documents, run_context(legacy), query='x') == 'This run has no collections.'
    assert calls == []


def test_rag_failure_is_reported_to_model_and_not_counted(rag):
    _, state = rag
    state['fail'] = True
    context = run_context(config())
    assert 'unavailable' in invoke(search_documents, context, query='x')
    assert context.get('grounding', {}).get('searches', 0) == 0


def test_read_document_uses_collection_and_path(rag):
    calls, _ = rag
    context = run_context(config(collections=[{'id': 'jira', 'name': 'jira', 'description': '', 'path': ''}]))
    assert (
        invoke(read_document, context, collection='jira', path='DEMO-1.md') == '[1] jira/DEMO-1.md\nFull retry policy.'
    )
    assert dict(calls[-1].url.params) == {'collection': 'jira', 'path': 'DEMO-1.md'}
    assert context['grounding']['excerpts'][0]['collection'] == 'jira'
    assert (
        invoke(read_document, context, collection='docs', path='a.md')
        == 'This collection is not available in this run.'
    )
    assert 'Use a document path' in invoke(read_document, context, collection='jira', path='../x')


def test_read_document_never_reads_hidden_or_unindexed_files(rag):
    calls, _ = rag
    context = run_context(config(collections=[{'id': 'jira', 'name': 'jira', 'description': '', 'path': ''}]))
    for path in ('.env', '.env.local', 'sub/.git/config', 'node_modules/x/README.md'):
        assert invoke(read_document, context, collection='jira', path=path) == 'Document not found.'
    assert calls == []
    # RAG answers 404 for files it would not index; the agent sees "not found".
    assert invoke(read_document, context, collection='jira', path='secret.md') == 'Document not found.'
    assert 'grounding' not in context or context['grounding']['excerpts'] == []


def test_search_tolerates_renamed_or_deleted_collections(rag):
    calls, state = rag
    context = run_context(
        config(
            collections=[
                {'id': 'payments', 'name': 'payments', 'description': '', 'path': ''},
                {'id': 'renamed', 'name': 'renamed', 'description': '', 'path': ''},
            ]
        )
    )
    output = invoke(search_documents, context, query='retry')
    assert output.startswith('[1] payments/retries.md')
    body = json.loads(calls[-1].content)
    assert body['collections'] == ['payments', 'renamed'] and body['ignore_unknown'] is True


def test_report_not_covered_records_reason(rag):
    context = run_context(config())
    invoke(report_not_covered, context, reason='No refund SLA documented.')
    assert context['grounding']['reported'] == 'No refund SLA documented.'


def test_result_lists_only_cited_existing_excerpts():
    state = {
        'excerpts': [
            {'n': 1, 'path': 'x.md', 'collection': 'a', 'text': 't'},
            {'n': 2, 'path': 'y.md', 'collection': 'a', 'text': 't'},
        ],
        'searches': 1,
        'empty_searches': 0,
        'reported': None,
    }
    summary = grounding.result('Use x [2][2], not [7].\n```\ncode [1]\n```', state)
    assert summary['sources'] == [{'n': 2, 'path': 'a/y.md', 'heading_path': 'a', 'anchor': None}]
    assert summary['not_covered'] is False and summary['results'] == 2
    assert grounding.result('hello', None) is None


@pytest.mark.parametrize(
    'state, expected',
    [
        ({'excerpts': [], 'searches': 2, 'empty_searches': 2, 'reported': None}, True),
        ({'excerpts': [], 'searches': 0, 'empty_searches': 0, 'reported': None}, False),
        (
            {
                'excerpts': [{'n': 1, 'path': 'x.md', 'collection': 'a', 'text': 't'}],
                'searches': 1,
                'empty_searches': 0,
                'reported': 'gap',
            },
            True,
        ),
    ],
)
def test_not_covered_rule(state, expected):
    assert grounding.result('answer', state)['not_covered'] is expected


def test_grounded_agents_get_the_policy():
    """Grounded runs get every tool like any run (test_every_tool.py); the policy is in their prompt."""
    assert grounding.POLICY in get_prompt(config())
    assert grounding.POLICY not in get_prompt(config(grounded=False))


def test_get_paths_skips_document_collections_for_grounded_agents():
    missing = [{'id': '1', 'name': 'payments', 'description': '', 'path': '/nonexistent/rag-data/payments'}]
    assert get_paths(config(collections=missing)) == []
    assert [str(path) for path in get_paths(config(grounded=False, collections=missing))] == [
        '/nonexistent/rag-data/payments'
    ]


def test_get_paths_treats_legacy_config_without_grounded_as_ungrounded():
    legacy = config()
    del legacy['documents']['grounded']
    assert [str(path) for path in get_paths(legacy)] == ['/work/rag-data/payments']


def test_read_document_passes_full_path(rag):
    calls, _ = rag
    context = run_context(config(collections=[{'id': 'jira', 'name': 'jira', 'description': '', 'paths': []}]))
    result = invoke(read_document, context, collection='jira', path='jira/DEMO-1.md')
    assert result == '[1] jira/jira/DEMO-1.md\nFull retry policy.'
    assert dict(calls[-1].url.params) == {'collection': 'jira', 'path': 'jira/DEMO-1.md'}
    assert context['grounding']['excerpts'][0]['path'] == 'jira/DEMO-1.md'
    assert invoke(read_document, context, collection='jira', path='jira/.env') == 'Document not found.'
