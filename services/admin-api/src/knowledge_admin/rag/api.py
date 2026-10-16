"""Admin authorization and application scoping for the independent RAG service."""

import json
import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from knowledge_admin.identity import request_actor
from knowledge_admin.db import workspace


ALLOWED = {
    'GET': r'settings|collections|collections/[0-9a-f-]{36}(/files|/chunks|/log)?|file|jobs|folders',
    'PUT': r'settings|collections/[0-9a-f-]{36}',
    'POST': r'collections|collections/sync-stale|collections/[0-9a-f-]{36}/(sync|paths)|jobs/[0-9a-f-]{36}/cancel|test/embedding|search',
    # Admins create collections and connect or disconnect folders; sources only write files.
    'DELETE': r'collections/[0-9a-f-]{36}(/paths/[0-9a-f-]{36})?',
}


def build_api(app_name: str, store, client):
    router = APIRouter()

    @router.api_route('/rag/{resource:path}', methods=list(ALLOWED))
    async def proxy(resource: str, request: Request):
        actor = request_actor(request)
        if re.fullmatch(ALLOWED[request.method], resource) is None:
            raise HTTPException(404, 'Unknown RAG resource')
        key = os.environ.get('RAG_BACKOFFICE_KEY')
        if store is None or client is None or not key:
            raise HTTPException(503, 'RAG administration is not configured')
        with workspace(store, app_name) as (conn, project):
            principal = store.actor(conn, *actor)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 12_000_000:
                raise HTTPException(413, 'RAG request is too large')
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            raise HTTPException(400, 'Invalid JSON') from None
        path = f'/backoffice/projects/{project}/{resource}'
        if resource == 'search':
            if not isinstance(payload, dict):
                raise HTTPException(400, 'A JSON object is required')
            payload['project_id'] = str(project)
            path = '/' + resource
        try:
            # The sync upstream client is shared with Jira and agents. Run it
            # outside the event loop so a model request cannot block other admins.
            response = await run_in_threadpool(
                client.request,
                request.method,
                path,
                params=request.query_params,
                json=payload,
                headers={'x-rag-key': key, 'x-rag-actor': str(principal)},
                timeout=180,
            )
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'RAG service is unavailable') from None
        if response.status_code >= 500:
            # Only the service's explicit safe message contract is forwarded.
            message = result.get('error', {}).get('message') if isinstance(result, dict) else None
            return JSONResponse(
                status_code=response.status_code,
                content={'error': {'message': message or 'RAG service is unavailable'}},
            )
        return JSONResponse(status_code=response.status_code, content=result)

    return router
