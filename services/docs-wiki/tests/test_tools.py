import asyncio
import json

from agents.tool_context import ToolContext

from docs_wiki.workspace_client import RagNotFound
from docs_wiki.tools.search import SearchContext, search_docs, read_doc, list_docs


class Documents:
    def search(self, collection, query):
        return [{'title': 'Guide', 'heading_path': 'Retries', 'path': 'payments/guide.md', 'text': 'Retry once.'}]
    def get_document(self, collection, path):
        if path != 'payments/guide.md':
            raise RagNotFound('not found')
        return {'markdown': '# Guide'}
    def list_documents(self, collection):
        return [{'quadrant': 'reference', 'path': 'payments/guide.md', 'title': 'Guide'}]


def test_retained_document_function_tools():
    context = SearchContext(Documents(), 'docs')
    def invoke(tool, values):
        return asyncio.run(tool.on_invoke_tool(ToolContext(context, tool_name=tool.name, tool_call_id='test', tool_arguments=json.dumps(values)), json.dumps(values)))
    assert 'Retry once.' in invoke(search_docs, {'query': 'retry'})
    assert context.excerpts[0]['n'] == 1
    assert invoke(read_doc, {'path': 'payments/guide.md'}) == '# Guide'
    assert 'not part' in invoke(read_doc, {'path': 'outside.md'})
    assert 'payments/guide.md' in invoke(list_docs, {})
    assert context.used == {'docs'}
