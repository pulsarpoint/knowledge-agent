"""Search and read the RAG knowledge base within the agent's document collections, recording citable excerpts."""

import os
from typing import Annotated, Any

import httpx

from pydantic import Field

from knowledge_admin.sessions.activity import redact
from knowledge_admin.rag.collections import document_collections
from knowledge_admin.sessions.grounding import empty_state
from knowledge_admin.knowledge_agents.config import per_run
from knowledge_admin.capabilities.context import RunContext, require_given
from knowledge_admin.tools.registry import tool

RAG_TRANSPORT: httpx.AsyncBaseTransport | None = None
MAX_DOCUMENT_BYTES = 65536


def _state(context: RunContext) -> dict[str, Any]:
    return context.setdefault('grounding', empty_state())


def _allowed(config: dict[str, Any]) -> list[str] | None:
    """Collection names the agent may use; None means every collection (never for a per-run run)."""
    if not config['enabled'] or not config['documents']['enabled']:
        raise ValueError('Document search is not enabled for this agent.')
    require_given(config, 'documents')
    everything, items = document_collections(config)
    return None if everything and not per_run(config) else sorted({item['name'] for item in items})


def _client() -> httpx.AsyncClient:
    base, key = os.environ.get('DOCS_WIKI_RAG_URL'), os.environ.get('RAG_BACKOFFICE_KEY')
    if not base or not key:
        raise ValueError('Document search is not configured.')
    return httpx.AsyncClient(base_url=base, headers={'X-Rag-Key': key}, timeout=30, transport=RAG_TRANSPORT)


async def _project(client: httpx.AsyncClient) -> str:
    response = await client.get('/backoffice/workspace')
    response.raise_for_status()
    return response.json()['project_id']


def _register(state: dict[str, Any], collection: str, path: str, text: str) -> int:
    number = len(state['excerpts']) + 1
    state['excerpts'].append({'n': number, 'path': path, 'collection': collection, 'text': text})
    return number


@tool
async def search_documents(
    context: RunContext,
    query: Annotated[str, Field(description='Words or a question to search for.')],
    collection: Annotated[
        str | None, Field(description='Optional collection name, such as "jira", to search only that collection.')
    ] = None,
) -> str:
    """Search the knowledge base and return numbered excerpts to cite as [n].

    Use search_documents to find knowledge-base excerpts and read_document for a whole document."""
    allowed = _allowed(context['config'])
    if collection is not None:
        collection = collection.strip()
        if allowed is not None and collection not in allowed:
            return 'This collection is outside the agent’s document collections.'
        names = [collection]
    else:
        names = allowed
    state = _state(context)
    if names == []:
        state['searches'] += 1
        state['empty_searches'] += 1
        return 'No matches in the documentation.'
    try:
        async with _client() as client:
            body = {'project_id': await _project(client), 'query': query[:4000], 'top': 8}
            if names is not None:
                # A renamed or deleted granted collection drops out instead of failing the whole search.
                body['collections'] = names[:200]
                body['ignore_unknown'] = True
            response = await client.post('/search', json=body)
            response.raise_for_status()
            hits = response.json()['results']
    except (httpx.HTTPError, KeyError, ValueError):
        return 'Document search is unavailable right now. Try again, or tell the user the knowledge base could not be searched.'
    state['searches'] += 1
    if not hits:
        state['empty_searches'] += 1
        return 'No matches in the documentation.'
    secrets = context['credentials'].values()
    blocks = []
    for hit in hits:
        text = redact(hit['text'], secrets)
        number = _register(state, hit['collection'], hit['path'], text)
        blocks.append(f'[{number}] {hit["collection"]}/{hit["path"]}\n{text}')
    return '\n\n'.join(blocks)


@tool
async def read_document(
    context: RunContext,
    collection: Annotated[str, Field(description='The collection name shown before the path, such as "jira".')],
    path: Annotated[
        str, Field(description='The document\'s full path as shown after the collection, such as "jira/DEMO-1.md".')
    ],
) -> str:
    """Read one knowledge-base document returned by search_documents; cite it with its [n]."""
    allowed = _allowed(context['config'])
    collection, path = collection.strip(), path.strip()
    parts = path.split('/')
    if not path or path.startswith('/') or '\\' in path or any(part in ('', '.', '..') for part in parts):
        return 'Use a document path returned by search_documents.'
    if allowed is not None and collection not in allowed:
        return 'This document is outside the agent’s document collections.'
    if any(part.startswith('.') or part == 'node_modules' for part in parts):
        return 'Document not found.'  # never indexed (.env*, .git, hidden files); RAG refuses them too
    try:
        async with _client() as client:
            project = await _project(client)
            response = await client.get(
                f'/backoffice/projects/{project}/file', params={'collection': collection, 'path': path}
            )
            if response.status_code == 404:
                return 'Document not found.'
            response.raise_for_status()
            content = response.json()['content']
    except (httpx.HTTPError, KeyError, ValueError):
        return 'The document could not be read right now.'
    encoded = content.encode()
    text = redact(encoded[:MAX_DOCUMENT_BYTES].decode(errors='ignore'), context['credentials'].values())
    number = _register(_state(context), collection, path, text)
    return f'[{number}] {collection}/{path}\n{text}' + ('\n[truncated]' if len(encoded) > MAX_DOCUMENT_BYTES else '')


@tool
async def report_not_covered(
    context: RunContext,
    reason: Annotated[str, Field(description='What information is missing from the documentation.')],
) -> str:
    """Record that the retrieved documents do not answer the question."""
    _state(context)['reported'] = reason.strip()[:1000] or 'Not covered by the documentation.'
    return 'Recorded. Tell the user the documentation does not cover this and what is missing.'
