from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from docs_wiki import rag_client


@pytest.fixture()
def inline_fake_rag():
    """A minimal, purpose-built fake distinct from the full `fake_rag`
    fixture in conftest.py (which lands in this same step but isn't
    exercised by every assertion below) — this one just records the
    request paths/bodies RagClient sends and can be toggled to fail."""
    state = {"paths": [], "bodies": [], "fail": False}
    api = FastAPI()

    def _error(status_code: int, message: str) -> JSONResponse:
        return JSONResponse(status_code=status_code,
                            content={"error": {"message": message}})

    @api.get("/collections/{name}/documents")
    async def documents(name: str, request: Request):
        state["paths"].append(request.url.path)
        if state["fail"]:
            return _error(500, "boom")
        return [{"path": "how-to/a.md", "title": "A",
                 "quadrant": "how-to", "front_matter": {}}]

    @api.get("/collections/{name}/documents/{path:path}")
    async def get_document(name: str, path: str, request: Request):
        state["paths"].append(request.url.path)
        if state["fail"]:
            return _error(500, "boom")
        if path == "missing.md":
            return _error(404, "no such document")
        return {"path": path, "title": "A", "quadrant": "how-to",
                "front_matter": {}, "markdown": "# A", "headings": []}

    @api.post("/collections/{name}/search")
    async def search(name: str, request: Request):
        state["paths"].append(request.url.path)
        body = await request.json()
        state["bodies"].append(body)
        if state["fail"]:
            return _error(500, "boom")
        return [{"path": "how-to/a.md", "title": "A", "heading_path": [],
                 "anchor": "a", "text": "excerpt", "score": 1.0}]

    @api.get("/collections")
    async def collections(request: Request):
        state["paths"].append(request.url.path)
        if state["fail"]:
            return _error(500, "boom")
        return [{"name": "shop", "kind": "docs", "retrieval": "hybrid"}]

    return httpx.ASGITransport(app=api), state


class _BrokenTransport(httpx.BaseTransport):
    """Simulates the RAG service being unreachable at the TCP level."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)


class _MalformedJsonTransport(httpx.BaseTransport):
    """Simulates a 200 response whose body isn't valid JSON."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", request=request)


def test_list_documents_hits_expected_path(inline_fake_rag):
    transport, state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    docs = client.list_documents("shop")
    assert state["paths"] == ["/collections/shop/documents"]
    assert docs[0]["path"] == "how-to/a.md"


def test_get_document_hits_expected_path(inline_fake_rag):
    transport, state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    doc = client.get_document("shop", "how-to/a.md")
    assert state["paths"][-1] == "/collections/shop/documents/how-to/a.md"
    assert doc["markdown"] == "# A"


def test_search_hits_expected_path_and_body(inline_fake_rag):
    transport, state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    hits = client.search("shop", "refund policy", top=3)
    assert state["paths"][-1] == "/collections/shop/search"
    assert state["bodies"][-1] == {"query": "refund policy", "top": 3}
    assert hits[0]["anchor"] == "a"


def test_search_default_top_is_eight(inline_fake_rag):
    transport, state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    client.search("shop", "refund policy")
    assert state["bodies"][-1] == {"query": "refund policy", "top": 8}


def test_collections_hits_expected_path(inline_fake_rag):
    transport, state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    rows = client.collections()
    assert state["paths"] == ["/collections"]
    assert rows[0] == {"name": "shop", "kind": "docs", "retrieval": "hybrid"}


def test_collections_wraps_server_error(inline_fake_rag):
    transport, state = inline_fake_rag
    state["fail"] = True
    client = rag_client.RagClient("http://rag.local", transport=transport)
    with pytest.raises(rag_client.RagError, match="boom"):
        client.collections()


def test_path_segments_url_encoded(inline_fake_rag):
    transport, state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    client.get_document("my shop", "how-to/getting started & more.md")
    # A correct implementation percent-encodes each segment but keeps "/"
    # as the literal separator; a naive whole-string quote() would turn
    # "/" into "%2F" and break the server's {path:path} match. Seeing the
    # exact decoded literal back proves segment-wise encoding happened.
    assert state["paths"][-1] == (
        "/collections/my shop/documents/how-to/getting started & more.md")


def test_get_document_404_raises_not_found(inline_fake_rag):
    transport, _state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    with pytest.raises(rag_client.RagNotFound):
        client.get_document("shop", "missing.md")


def test_rag_not_found_is_a_rag_error(inline_fake_rag):
    transport, _state = inline_fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    with pytest.raises(rag_client.RagError):
        client.get_document("shop", "missing.md")


def test_server_error_message_includes_body(inline_fake_rag):
    transport, state = inline_fake_rag
    state["fail"] = True
    client = rag_client.RagClient("http://rag.local", transport=transport)
    with pytest.raises(rag_client.RagError, match="boom"):
        client.list_documents("shop")


def test_connection_error_wraps_in_rag_error():
    client = rag_client.RagClient("http://rag.local",
                                  transport=_BrokenTransport())
    with pytest.raises(rag_client.RagError):
        client.list_documents("shop")


def test_malformed_json_wraps_in_rag_error():
    client = rag_client.RagClient("http://rag.local",
                                  transport=_MalformedJsonTransport())
    with pytest.raises(rag_client.RagError):
        client.list_documents("shop")


def test_close_is_safe_to_call():
    client = rag_client.RagClient("http://rag.local")
    client.close()


def test_fake_rag_fixture_happy_path(fake_rag):
    transport, state = fake_rag
    state["documents"]["shop"] = [
        {"path": "how-to/a.md", "title": "A", "quadrant": "how-to",
         "front_matter": {}}]
    state["contents"][("shop", "how-to/a.md")] = {
        "path": "how-to/a.md", "title": "A", "quadrant": "how-to",
        "front_matter": {}, "markdown": "# A", "headings": []}
    state["search_results"]["shop"] = [
        {"path": "how-to/a.md", "title": "A", "heading_path": [],
         "anchor": "a", "text": "excerpt", "score": 1.0}]
    client = rag_client.RagClient("http://rag.local", transport=transport)
    assert client.list_documents("shop")[0]["path"] == "how-to/a.md"
    assert client.get_document("shop", "how-to/a.md")["markdown"] == "# A"
    hits = client.search("shop", "refund", top=5)
    assert hits[0]["anchor"] == "a"
    assert state["search_requests"] == [
        {"collection": "shop", "query": "refund", "top": 5}]
    client.close()


def test_fake_rag_fixture_unknown_collection_404s(fake_rag):
    transport, _state = fake_rag
    client = rag_client.RagClient("http://rag.local", transport=transport)
    with pytest.raises(rag_client.RagError):
        client.list_documents("nope")


def test_fake_rag_fixture_fail_flag_500s_every_route(fake_rag):
    transport, state = fake_rag
    state["documents"]["shop"] = []
    state["fail"] = True
    client = rag_client.RagClient("http://rag.local", transport=transport)
    with pytest.raises(rag_client.RagError):
        client.list_documents("shop")
    with pytest.raises(rag_client.RagError):
        client.get_document("shop", "x.md")
    with pytest.raises(rag_client.RagError):
        client.search("shop", "q")
