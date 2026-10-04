"""Publish documents into the RAG knowledge base and keep published copies on the latest version."""
import os
from posixpath import dirname
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.rag_files import RagFiles

FOLDER_DESCRIPTION = 'Documents published from Docs → Documents.'


class PublishInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    folder: str = Field(default='documents', min_length=1, max_length=500, pattern=r'^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$')


def marker(document_id: UUID | str) -> str:
    """First line of every published file; proves the file belongs to this document."""
    return f'<!-- knowledge-agent document {document_id} -->'


def rag_files(client: httpx.Client | None, project: UUID, actor: UUID | None) -> RagFiles:
    key = os.environ.get('RAG_BACKOFFICE_KEY')
    if client is None or not key:
        raise HTTPException(503, 'RAG publishing is not configured.')
    return RagFiles(client, key, project, actor)


def _document(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute('''SELECT d.id,d.slug,d.current_version_id,d.published_path,v.markdown
        FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id
        WHERE d.id=%s AND d.project_id=%s''', (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, 'Document not found')
    return row


def publish(store, client: httpx.Client | None, project: UUID, actor: UUID | None,
            document_id: UUID, folder: str) -> dict:
    """Write the current version to folder/<slug>.md, queue indexing and record the outcome."""
    with store.connect() as conn:
        document = _document(conn, project, document_id)
    files = rag_files(client, project, actor)
    first_line = marker(document_id)
    written = files.write_markdown(folder, f"{document['slug']}.md", first_line + '\n' + document['markdown'], FOLDER_DESCRIPTION)
    previous = document['published_path']
    if previous and previous != written['path']:
        # A new folder or slug: the old copy must not stay searchable.
        if files.delete_file(previous, first_line):
            files.queue_index(files.ensure_folder(dirname(previous), FOLDER_DESCRIPTION)['id'], allow_empty=True)
    queued = files.queue_index(written['folder_id'])
    with store.connect() as conn:
        conn.execute('''UPDATE ka.documents SET published_path=%s,published_version_id=%s,published_at=now(),
            publish_error=NULL,index_pending=%s WHERE id=%s''', (written['path'], document['current_version_id'], not queued, document_id))
    return {'path': written['path'], 'status': written['status'], 'index_pending': not queued}


def unpublish(store, client: httpx.Client | None, project: UUID, actor: UUID | None, document_id: UUID) -> dict:
    """Remove the published file, re-index its folder and clear the publication."""
    with store.connect() as conn:
        document = _document(conn, project, document_id)
    path = document['published_path']
    if path is None:
        raise HTTPException(409, 'This document is not published.')
    files = rag_files(client, project, actor)
    if files.delete_file(path, marker(document_id)):
        files.queue_index(files.ensure_folder(dirname(path), FOLDER_DESCRIPTION)['id'], allow_empty=True)
    with store.connect() as conn:
        conn.execute('''UPDATE ka.documents SET published_path=NULL,published_version_id=NULL,published_at=NULL,
            publish_error=NULL,index_pending=false WHERE id=%s''', (document_id,))
    return {'path': path}


def republish(store, client: httpx.Client | None, project: UUID, actor: UUID | None, document_ids: list[UUID]) -> None:
    """Bring published documents up to their current version; failures are recorded, never raised."""
    with store.connect() as conn:
        stale = conn.execute('''SELECT id,published_path FROM ka.documents WHERE id=ANY(%s) AND project_id=%s
            AND published_path IS NOT NULL AND published_version_id IS DISTINCT FROM current_version_id''',
            (list(document_ids), project)).fetchall()
    for row in stale:
        try:
            publish(store, client, project, actor, row['id'], dirname(row['published_path']))
        except HTTPException as error:
            with store.connect() as conn:
                conn.execute('UPDATE ka.documents SET publish_error=%s WHERE id=%s', (str(error.detail)[:2000], row['id']))


def retry_pending(store, client: httpx.Client | None, app_name: str) -> None:
    """Queue indexing for published files whose indexing was busy when they were written."""
    with store.connect() as conn:
        project = store.project(conn, app_name)
        pending = conn.execute('SELECT id,published_path FROM ka.documents WHERE project_id=%s AND index_pending',
                               (project,)).fetchall()
    if not pending:
        return
    files = rag_files(client, project, None)
    for folder in sorted({dirname(row['published_path']) for row in pending}):
        if not files.queue_index(files.ensure_folder(folder, FOLDER_DESCRIPTION)['id']):
            return  # still busy; try again on the next pass
        with store.connect() as conn:
            conn.execute('UPDATE ka.documents SET index_pending=false WHERE id=ANY(%s)',
                         ([row['id'] for row in pending if dirname(row['published_path']) == folder],))
