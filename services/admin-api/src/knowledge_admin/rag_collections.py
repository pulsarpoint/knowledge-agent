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


def creation(description: str | None, include: list[str] | None, exclude: list[str] | None) -> dict:
    """Creation-only collection settings, leaving out the ones not given."""
    values = {'description': description, 'include': include, 'exclude': exclude}
    return {key: value for key, value in values.items() if value is not None}


class RagCollections(RagFiles):
    """One project's collections, acting for one principal. Collection names are validated slugs."""

    def ensure_collection(self, name: str, description: str | None = None, *, include: list[str] | None = None,
                          exclude: list[str] | None = None) -> dict:
        """Create the collection when missing; description, include and exclude apply only to a new one."""
        return self.request('PUT', f'collections/by-name/{name}', json=creation(description, include, exclude))

    def add_path(self, collection: str, source_path: str, owner: Owner | None = None, *,
                 include: list[str] | None = None, exclude: list[str] | None = None) -> dict:
        """Add a folder to `collection`, created if missing (with include/exclude when given; an existing
        collection keeps its settings). The same owner's path is re-pointed, also from another collection."""
        return self.request('PUT', f'collections/by-name/{collection}/paths',
                            json={'source_path': source_path, **(owner.params() if owner else {}),
                                  **creation(None, include, exclude)})

    def remove_path(self, collection: str, owner: Owner) -> int:
        return self.request('DELETE', f'collections/by-name/{collection}/paths', params=owner.params())['removed']

    def remove_paths(self, owner: Owner) -> int:
        """Remove the owner's paths from every collection; without a key, every path of that owner id."""
        return self.request('DELETE', 'paths', params=owner.params())['removed']

    def owned_paths(self, kind: str, owner_id: UUID | None) -> list[dict]:
        rows = self.request('GET', 'collections', params={'status': 'false'})['collections']
        wanted = None if owner_id is None else str(owner_id)
        return [{**path, 'collection': row['name']} for row in rows for path in row['paths']
                if path['owner_kind'] == kind and path['owner_id'] == wanted]


DOCUMENTS = Owner('documents')
DOCUMENTS_COLLECTION, DOCUMENTS_PATH = 'documents', 'generated-documents'
# Old <slug>/v<n>/ and services/<svc>/<phase>/v<n>/ version folders are not indexed.
DOCUMENTS_EXCLUDE = ['*/v[0-9]*/**', 'services/*/*/v[0-9]*/**']


def ensure_documents(rag: RagCollections) -> bool:
    """Give the Documents source its path: generated-documents in the documents collection.

    Idempotent. When Documents already owns a path (also in another collection, as migration 0063
    names it when `documents` was taken), nothing changes. Returns whether a path was added.
    """
    if rag.owned_paths(DOCUMENTS.kind, None):
        return False
    rag.ensure_collection(DOCUMENTS_COLLECTION, exclude=DOCUMENTS_EXCLUDE)
    rag.add_path(DOCUMENTS_COLLECTION, DOCUMENTS_PATH, DOCUMENTS)
    return True
