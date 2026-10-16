"""Search and read the RAG knowledge base within the agent's document collections, recording citable excerpts."""

import os
from typing import Annotated, Any

import httpx
from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.sessions.activity import redact
from knowledge_admin.sessions.grounding import empty_state
from knowledge_admin.tools.registry import tool

RAG_TRANSPORT: httpx.AsyncBaseTransport | None = None
MAX_DOCUMENT_BYTES = 65536


def _state(context: RunContext) -> dict[str, Any]:
    return context.setdefault('grounding', empty_state())


def _collections(config: dict[str, Any]) -> list[str]:
    """The names of the run's collections (its access entries)."""
    return sorted({item['name'] for item in access_rules.listing(config['access'], 'collection')})


def _named(access: dict, collection: str | None) -> bool:
    return collection is None or any(
        item['name'] == collection.strip() for item in access_rules.listing(access, 'collection')
    )


def check_search_documents(access: dict, collection: str | None = None, **_) -> str | None:
    """None when allowed, else the refusal text. A collection is named by its name."""
    if not access_rules.has_any(access, 'collection'):
        return access_rules.refuse_none('collection')
    if not _named(access, collection):
        return access_rules.refuse_use('collection')
    return None


def check_read_document(access: dict, collection: str, **_) -> str | None:
    """None when allowed, else the refusal text."""
    return check_search_documents(access, collection)


NOT_GROUNDED = 'This run does not answer only from documents.'


def check_report_not_covered(access: dict, grounded: bool = False, **_) -> str | None:
    """None when allowed, else the refusal text: only a grounded run ("Answer only from documents") reports that the
    documents do not answer; it reads nothing."""
    return None if grounded else NOT_GROUNDED


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
    if refusal := check_search_documents(context['config']['access'], collection=collection):
        if not access_rules.has_any(context['config']['access'], 'collection'):
            # a search with nothing to search: counted, so a grounded answer reads as not covered (grounding.py)
            state = _state(context)
            state['searches'] += 1
            state['empty_searches'] += 1
        return refusal
    names = _collections(context['config'])
    if collection is not None:
        names = [collection.strip()]
    state = _state(context)
    try:
        async with _client() as client:
            body = {'project_id': await _project(client), 'query': query[:4000], 'top': 8}
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
    if refusal := check_read_document(context['config']['access'], collection=collection):
        return refusal
    collection, path = collection.strip(), path.strip()
    parts = path.split('/')
    if not path or path.startswith('/') or '\\' in path or any(part in ('', '.', '..') for part in parts):
        return 'Use a document path returned by search_documents.'
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
    documents = context['config']['documents']
    grounded = bool(documents['enabled'] and documents.get('grounded', False))
    if refusal := check_report_not_covered(context['config']['access'], grounded=grounded, reason=reason):
        return refusal
    _state(context)['reported'] = reason.strip()[:1000] or 'Not covered by the documentation.'
    return 'Recorded. Tell the user the documentation does not cover this and what is missing.'
