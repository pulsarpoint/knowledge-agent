"""Environments → System map: views of the inventory (inventory_map.py) as JSON, Mermaid, D2 or SVG."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from knowledge_admin.inventory import store as inventory_store
from knowledge_admin.servers import inventory_map, system_graph_render
from knowledge_admin.repositories import RepositoryStore

SVG_HEADERS = {
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
    'X-Content-Type-Options': 'nosniff',
}


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def inventory():
        if store is None:
            raise HTTPException(503, 'Inventory storage is not configured')
        with store.connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')  # one snapshot of the rows
            return inventory_store.load_project_models(conn, store.project(conn, app))

    @router.get('/map/view/{view}', response_model=None)
    def view(
        view: Literal['infrastructure', 'services'],
        root: str | None = Query(None, max_length=200),
        depth: int = Query(1, ge=1, le=inventory_map.MAX_DEPTH),
        format: Literal['json', 'mermaid', 'd2', 'svg'] = 'json',
        layout: Literal['tala', 'elk', 'dagre'] = 'tala',
    ):
        try:
            graph = inventory_map.build_view(inventory(), view, root, depth)
        except inventory_map.ViewError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        if format == 'json':
            return graph
        if format == 'mermaid':
            return PlainTextResponse(system_graph_render.mermaid(graph))
        text = system_graph_render.d2(graph)
        if format == 'd2':
            return PlainTextResponse(text)
        try:
            return Response(system_graph_render.svg(text, layout), media_type='image/svg+xml', headers=SVG_HEADERS)
        except system_graph_render.RenderError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    return router
