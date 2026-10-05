"""Write and remove Markdown files in RAG collections through the RAG backoffice API."""
from uuid import UUID

import httpx
from fastapi import HTTPException


class RagFiles:
    """One project's RAG collections, acting for one principal."""

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
            raise HTTPException(502, 'RAG service is unavailable. Retry to check the file') from None
        if response.status_code >= 300:
            raise HTTPException(response.status_code, result.get('error', {}).get('message') or 'RAG could not save the file')
        return result

    def collection(self, name: str) -> dict:
        rows = self.request('GET', 'collections')['collections']
        row = next((r for r in rows if r['name'] == name), None)
        if row is None:
            raise HTTPException(404, f'Unknown collection: {name}. Create it in RAG → Collections.')
        return row

    def get_file(self, collection: str, path: str) -> dict | None:
        try:
            return self.request('GET', 'file', params={'collection': collection, 'path': path})
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise

    def write_markdown(self, collection: str, filename: str, content: str) -> dict:
        """Create or replace a file in a collection. A file whose first line differs belongs to another source and is kept."""
        self.collection(collection)
        previous = self.get_file(collection, filename)
        if previous is not None:
            if previous['content'].partition('\n')[0] != content.partition('\n')[0]:
                raise HTTPException(409, f'{collection}/{filename} belongs to another source. Choose a different collection; the existing file was preserved')
            if previous['content'] == content:
                return {'collection': collection, 'path': filename, 'status': 'unchanged', 'hash': previous['hash']}
        saved = self.request('PUT', 'file', json={'collection': collection, 'path': filename, 'content': content,
                                                  'expected_hash': previous['hash'] if previous is not None else None})
        return {'collection': collection, 'path': filename, 'status': 'updated' if previous is not None else 'created', 'hash': saved['hash']}

    def delete_file(self, collection: str, path: str, first_line: str) -> bool:
        """Delete path when it still starts with first_line; return False when it is already gone."""
        previous = self.get_file(collection, path)
        if previous is None:
            return False
        if previous['content'].partition('\n')[0] != first_line:
            raise HTTPException(409, f'{collection}/{path} belongs to another source; it was not removed')
        self.request('DELETE', 'file', params={'collection': collection, 'path': path, 'expected_hash': previous['hash']})
        return True
