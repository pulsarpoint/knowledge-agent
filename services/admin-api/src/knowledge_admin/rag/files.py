"""Read and write files in the Jira folder through the RAG backoffice API."""
from uuid import UUID

import httpx
from fastapi import HTTPException


class RagFiles:
    """One project's RAG service, acting for one principal."""

    def __init__(self, client: httpx.Client, key: str, project: UUID, actor: UUID | None):
        self.client, self.prefix = client, f'/backoffice/projects/{project}/'
        self.headers = {'x-rag-key': key, **({'x-rag-actor': str(actor)} if actor else {})}

    def request(self, method: str, resource: str, **kwargs: object) -> dict:
        try:
            response = self.client.request(method, self.prefix + resource, headers=self.headers, timeout=30, **kwargs)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Invalid RAG response')
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'RAG service is unavailable. Retry later.') from None
        if response.status_code >= 300:
            raise HTTPException(response.status_code, result.get('error', {}).get('message') or 'RAG request failed')
        return result

    def get_file(self, path: str) -> dict | None:
        """A file in the Jira folder by its full DATA_ROOT path, or None when it does not exist."""
        try:
            return self.request('GET', 'file', params={'path': path})
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise

    def write_markdown(self, path: str, content: str) -> dict:
        """Create or replace `path` (full, inside the Jira folder). A file whose first line differs belongs to
        another source and is kept."""
        previous = self.get_file(path)
        if previous is not None:
            if previous['content'].partition('\n')[0] != content.partition('\n')[0]:
                raise HTTPException(409, f'{path} belongs to another source. The existing file was preserved')
            if previous['content'] == content:
                return {'path': path, 'status': 'unchanged', 'hash': previous['hash']}
        saved = self.request('PUT', 'file', json={'path': path, 'content': content,
                                                  'expected_hash': previous['hash'] if previous is not None else None})
        return {'path': path, 'status': 'updated' if previous is not None else 'created', 'hash': saved['hash']}
