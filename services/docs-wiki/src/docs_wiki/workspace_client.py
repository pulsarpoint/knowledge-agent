"""Adapt the folder RAG API to the search agent's document tools."""
from pathlib import PurePosixPath

import httpx

from docs_wiki.rag_client import RagClient, RagError, RagNotFound


class WorkspaceRagClient(RagClient):
    def __init__(self, base_url: str, key: str, transport=None):
        super().__init__(base_url, transport)
        self._client.headers['X-Rag-Key'] = key

    def _prefix(self) -> str:
        row = self._request('GET', '/backoffice/workspace')
        return f"/backoffice/projects/{row['project_id']}"

    def search(self, collection: str, query: str, top: int = 8) -> list[dict]:
        return self.search_folders(query, None, top)

    def folders(self) -> list[dict]:
        return [row for row in self._request('GET', self._prefix() + '/tree')['folders'] if row['present']]

    def search_folders(self, query: str, folders: list[str] | None, top: int = 8, mode: str = "hybrid") -> list[dict]:
        project = self._request('GET', '/backoffice/workspace')['project_id']
        data = self._request('POST', '/search', json={
            'project_id': project, 'query': query, 'folder': '*', 'mode': mode, 'top': top,
            **({'folders': folders} if folders is not None else {})})
        return [{**hit, 'title': PurePosixPath(hit['path']).name,
                 'heading_path': hit['folder'], 'anchor': None} for hit in data['results']]

    def get_document(self, collection: str, path: str) -> dict:
        try:
            row = self._client.get(self._prefix() + '/file', params={'path': path})
            row.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RagNotFound('Document not found') from exc
            raise RagError('Unable to read the RAG document') from exc
        except httpx.HTTPError as exc:
            raise RagError('RAG service unavailable') from exc
        return {**row.json(), 'markdown': row.json()['content'], 'title': PurePosixPath(path).name, 'headings': [], 'front_matter': {}}

    def list_documents(self, collection: str) -> list[dict]:
        rows = self._request('GET', self._prefix() + '/documents')
        return [{**row, 'quadrant': row['folder'], 'title': PurePosixPath(row['path']).name} for row in rows]
