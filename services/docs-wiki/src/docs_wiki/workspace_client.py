"""Synchronous HTTP client for the RAG service's collection API, shaped for
the reader routes and the search agent's document tools."""
from pathlib import PurePosixPath

import httpx


class RagError(Exception):
    pass


class RagNotFound(RagError):
    pass


def _error_detail(exc: Exception) -> str:
    """The status line alone ("400 Bad Request") hides the server's actual
    complaint; append the response body so banners and logs say why."""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip()
        if body:
            return f"{exc} — server said: {body[:300]}"
    return str(exc)


class WorkspaceRagClient:
    def __init__(self, base_url: str, key: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=base_url, timeout=10.0, transport=transport,
                                    headers={'X-Rag-Key': key})

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise RagError(f"request failed: {_error_detail(exc)}") from exc

    def _prefix(self) -> str:
        row = self._request('GET', '/backoffice/workspace')
        return f"/backoffice/projects/{row['project_id']}"

    def collections(self) -> list[dict]:
        return [row for row in self._request('GET', self._prefix() + '/collections')['collections'] if row['folder_present']]

    def search(self, collection: str, query: str, top: int = 8) -> list[dict]:
        return self.search_collections(query, None, top)

    def search_collections(self, query: str, collections: list[str] | None, top: int = 8) -> list[dict]:
        project = self._request('GET', '/backoffice/workspace')['project_id']
        data = self._request('POST', '/search', json={'project_id': project, 'query': query, 'top': top,
                                                      **({'collections': collections} if collections is not None else {})})
        return [{**hit, 'path': f"{hit['collection']}/{hit['path']}", 'title': PurePosixPath(hit['path']).name,
                 'heading_path': hit['collection'], 'anchor': None} for hit in data['results']]

    def get_document(self, collection: str, path: str) -> dict:
        name, _, rest = path.partition('/')
        try:
            row = self._client.get(self._prefix() + '/file', params={'collection': name, 'path': rest})
            row.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 404):
                raise RagNotFound('Document not found') from exc
            raise RagError('Unable to read the RAG document') from exc
        except httpx.HTTPError as exc:
            raise RagError('RAG service unavailable') from exc
        return {**row.json(), 'path': path, 'markdown': row.json()['content'], 'title': PurePosixPath(rest).name, 'headings': [], 'front_matter': {}}

    def list_documents(self, collection: str) -> list[dict]:
        rows = self._request('GET', self._prefix() + '/documents')
        return [{**row, 'path': f"{row['collection']}/{row['path']}", 'quadrant': row['collection'],
                 'title': PurePosixPath(row['path']).name} for row in rows]
