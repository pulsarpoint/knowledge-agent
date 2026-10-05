"""Collections and their source paths in the RAG service, for sources that own folders (Jira, code projects)."""
from dataclasses import dataclass
from uuid import UUID

from knowledge_admin.rag_files import RagFiles


@dataclass(frozen=True)
class Owner:
    """The source that owns a path: kind 'documents' | 'jira' | 'code_project', plus an optional id and key."""
    kind: str
    id: UUID | None = None
    key: str | None = None

    def params(self) -> dict[str, str]:
        values = {'owner_kind': self.kind}
        if self.id is not None:
            values['owner_id'] = str(self.id)
        if self.key is not None:
            values['owner_key'] = self.key
        return values


class RagCollections(RagFiles):
    """One project's collections, acting for one principal. Collection names are validated slugs."""

    def ensure_collection(self, name: str, description: str | None = None) -> dict:
        return self.request('PUT', f'collections/by-name/{name}', json={} if description is None else {'description': description})

    def add_path(self, collection: str, source_path: str, owner: Owner | None = None) -> dict:
        """Add a folder to `collection` (created if missing). The same owner's path is re-pointed, also from another collection."""
        return self.request('PUT', f'collections/by-name/{collection}/paths',
                            json={'source_path': source_path, **(owner.params() if owner else {})})

    def remove_path(self, collection: str, owner: Owner) -> int:
        return self.request('DELETE', f'collections/by-name/{collection}/paths', params=owner.params())['removed']

    def remove_paths(self, owner: Owner) -> int:
        """Remove the owner's paths from every collection; without a key, every path of that owner id."""
        return self.request('DELETE', 'paths', params=owner.params())['removed']

    def owned_paths(self, kind: str, owner_id: UUID) -> list[dict]:
        rows = self.request('GET', 'collections', params={'status': 'false'})['collections']
        return [{**path, 'collection': row['name']} for row in rows for path in row['paths']
                if path['owner_kind'] == kind and path['owner_id'] == str(owner_id)]
