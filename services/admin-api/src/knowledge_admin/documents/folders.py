"""Documentation folders under GENERATED_DOCS_FOLDER, for browsing. Indexing happens in RAG → Collections; these routes
never call RAG."""

from fastapi import APIRouter, HTTPException
from functools import partial

from knowledge_admin.data_root import FOLDER_RULE, documentation_folder, generated_docs_folder
from knowledge_admin.db import storage, workspace
from knowledge_admin.repositories import RepositoryStore


def valid_folder(folder: str) -> str:
    try:
        return documentation_folder(folder)
    except ValueError:
        raise HTTPException(400, FOLDER_RULE) from None


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation/folders')

    database = partial(storage, store, 'Document storage', message='Document storage is not configured')

    @router.get('')
    def listing() -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            rows = conn.execute(
                """SELECT f.folder, f.document_count, f.updated_at,
                (SELECT max(v.created_at) FROM ka.documents d JOIN ka.document_versions v ON v.document_id=d.id
                    WHERE d.project_id=%s AND d.folder=f.folder AND v.origin='generated') AS last_generated_at,
                COALESCE((SELECT array_agg(DISTINCT r.name::text ORDER BY r.name::text) FROM ka.documents d
                    JOIN ka.document_code_projects l ON l.document_id=d.id JOIN ka.repositories r ON r.id=l.code_project_id
                    WHERE d.project_id=%s AND d.folder=f.folder),'{}') AS code_projects
                FROM (SELECT folder, count(*) AS document_count, max(updated_at) AS updated_at FROM ka.documents
                    WHERE project_id=%s AND folder IS NOT NULL GROUP BY folder) f ORDER BY f.folder""",
                (project, project, project),
            ).fetchall()
        try:
            generated = generated_docs_folder()
        except ValueError:
            generated = None  # the export folder is outside DATA_ROOT: RAG cannot see it
        return {
            'generated_folder': generated,
            'folders': [{**row, 'source_path': f'{generated}/{row["folder"]}' if generated else None} for row in rows],
        }

    @router.get('/{folder:path}')
    def folder_documents(folder: str) -> dict:
        folder = valid_folder(folder)
        db = database()
        with workspace(db, app_name) as (conn, project):
            documents = conn.execute(
                """SELECT d.id,d.slug,d.title,d.version,d.updated_at,
                (SELECT max(v.created_at) FROM ka.document_versions v WHERE v.document_id=d.id AND v.origin='generated') AS last_generated_at,
                COALESCE((SELECT array_agg(r.name::text ORDER BY lower(r.name)) FROM ka.repositories r WHERE r.id=ANY(d.code_project_ids)),'{}') AS code_projects
                FROM ka.document_details d WHERE d.project_id=%s AND d.folder=%s ORDER BY lower(d.title),d.id""",
                (project, folder),
            ).fetchall()
        if not documents:
            raise HTTPException(404, 'Documentation folder not found')
        return {'folder': folder, 'documents': documents}

    return router
