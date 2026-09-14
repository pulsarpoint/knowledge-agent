"""Conversation context and source boundaries for the search agent."""
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from docs_wiki.code_client import CodeAgentClient, CodeAgentError
from docs_wiki.rag_client import RagNotFound
from docs_wiki.workspace_client import WorkspaceRagClient

FolderPath = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$', max_length=500)]
RepositoryId = Annotated[str, Field(min_length=1, max_length=200)]


class AgentContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # None includes all available sources; [] explicitly includes none.
    folders: list[FolderPath] | None = Field(default=None, max_length=200)
    include_subfolders: StrictBool = True
    repositories: list[RepositoryId] | None = Field(default=None, max_length=200)

    def normalized(self):
        return self.model_copy(update={
            'folders': sorted(set(self.folders)) if self.folders is not None else None,
            'repositories': sorted(set(self.repositories)) if self.repositories is not None else None,
        })


class ScopedDocuments:
    def __init__(self, client: WorkspaceRagClient, context: AgentContext):
        self.client = client
        self.context = context

    def allows(self, path: str) -> bool:
        # Never normalize an untrusted path into an allowed directory.
        parts = path.split('/')
        if any(part in ('', '.', '..') for part in parts) or '\\' in path:
            return False
        folder = str(PurePosixPath(path).parent)
        return self.allows_folder(folder)

    def allows_folder(self, folder: str) -> bool:
        return self.context.folders is None or any(
            folder == selected or (self.context.include_subfolders and folder.startswith(selected + '/'))
            for selected in self.context.folders)

    def list_documents(self, collection: str) -> list[dict]:
        if self.context.folders == []:
            return []
        return [row for row in self.client.list_documents(collection) if self.allows(row['path'])]

    def get_document(self, collection: str, path: str) -> dict:
        if not self.allows(path):
            raise RagNotFound('Document is outside this conversation context')
        return self.client.get_document(collection, path)

    def search(self, collection: str, query: str, top: int = 8, mode: str = "hybrid") -> list[dict]:
        if self.context.folders == []:
            return []
        selectors = None if self.context.folders is None else [
            path.replace('/', '.') + ('.*' if self.context.include_subfolders else '')
            for path in self.context.folders]
        return [row for row in self.client.search_folders(query, selectors, top, mode)
                if self.allows_folder(row['folder'].replace('.', '/'))]


class ScopedRepositories:
    def __init__(self, client: CodeAgentClient, repositories: list[str]):
        self.client = client
        self.allowed = frozenset(repositories)

    def repos(self) -> list[dict]:
        return [row for row in self.client.repos() if row['name'] in self.allowed]

    def ask(self, repo: str, question: str, context: str | None = None):
        if repo not in self.allowed:
            raise CodeAgentError('Repository is outside this conversation context')
        return self.client.ask(repo, question, context)
