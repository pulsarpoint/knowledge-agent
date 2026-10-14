"""The RAG service HTTP app: `/health` plus the workspace collections API.

Every 4xx answer uses the `{"error": {"message"}}` contract: body-validation
failures, unmatched paths and wrong methods are rewritten to that shape.
"""

from __future__ import annotations

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag.errors import error_response as _error
from rag.workspace import Workspace
from rag.workspace_api import build_api as build_workspace_api


def _validation_message(exc: RequestValidationError) -> str:
    """Concise, single-line summary of a body-validation failure."""
    errors = exc.errors()
    if not errors:
        return 'invalid request'
    parts = []
    for err in errors:
        loc = '.'.join(str(p) for p in err.get('loc', ()) if p != 'body')
        msg = err.get('msg', 'invalid value')
        parts.append(f'{loc}: {msg}' if loc else msg)
    return '; '.join(parts)


def create_app(workspace: Workspace | None = None, workspace_key: str = '') -> FastAPI:
    """Build the app; the workspace router is mounted when `workspace` is given."""
    app = FastAPI()

    if workspace is not None:
        app.include_router(build_workspace_api(workspace, workspace_key))

    @app.exception_handler(psycopg.Error)
    async def workspace_database_error(request, exc):
        return _error(503, 'RAG database unavailable')

    @app.exception_handler(OSError)
    async def workspace_file_error(request, exc):
        if isinstance(exc, FileNotFoundError):
            return _error(404, 'Collection folder or file does not exist')
        return _error(400, 'Source path is unavailable; check permissions and remove symbolic links')

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError):
        return _error(400, _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_error(request: Request, exc: StarletteHTTPException):
        # Keep exc.headers: the router's Allow header on 405s.
        return JSONResponse(
            status_code=exc.status_code, content={'error': {'message': str(exc.detail)}}, headers=exc.headers
        )

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    return app
