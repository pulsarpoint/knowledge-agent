"""MCP facade tests.

Most scenarios run an in-process MCP session against the REAL app
`rag.api.create_app` builds (`run_mcp`, `mcp_client` below) -- ASGI
transport over the actual FastAPI app, with its actual lifespan driven
on the calling event loop. This is deliberate: it is what exercises the
FastMCP "lifespan caveat" (the mounted app's session manager only starts
if its lifespan is wired into the outer app) by construction -- if
`create_app` ever drops `lifespan=mcp_app.lifespan`, every test below
starts failing with fastmcp's own "Task group is not initialized" error
instead of a assertion mismatch.

One direct `build_mcp` test (no ASGI/app, no HTTP layer at all) covers
tool-listing/kind-dynamic registration on its own, using fastmcp's
in-process `Client(mcp)` transport.
"""
import asyncio
import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from rag import api, config, embed_client, ingest, mcp_facade


def _asgi_mcp_client(app) -> Client:
    """An MCP client that talks to the real `/mcp` mount on `app` over an
    in-process ASGI transport -- no socket, no real network, same
    fake-transport spirit as `fake_embed`."""
    def factory(headers=None, auth=None, follow_redirects=True,
               timeout=None, **_kw):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver", headers=headers,
            follow_redirects=follow_redirects,
            timeout=timeout or httpx.Timeout(30.0))

    transport = StreamableHttpTransport("http://testserver/mcp",
                                        httpx_client_factory=factory)
    return Client(transport)


async def _with_mcp_session(app, coroutine):
    # Drives `app`'s actual configured lifespan (which `create_app` wires
    # to `mcp_app.lifespan`) on THIS event loop before any request is
    # made, then runs `coroutine(mcp_client)` inside a live MCP session.
    async with app.router.lifespan_context(app):
        async with _asgi_mcp_client(app) as client:
            return await coroutine(client)


def run_mcp(app, coroutine):
    return asyncio.run(_with_mcp_session(app, coroutine))


def _new_embed(transport) -> embed_client.EmbedClient:
    return embed_client.EmbedClient(
        config.EmbedConfig(base_url="http://embed.local/v1", api_key=None,
                           embed_model="e"), transport=transport)


@pytest.fixture()
def docs_corpus(tmp_path, fake_embed):
    """Single docs-kind collection 'shop', indexed with embeddings --
    mirrors test_api.py's `corpus` fixture."""
    transport, _state = fake_embed
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "refunds.md").write_text(
        "---\nupdated: 2026-08-01\n---\n# Refunds\n\n## Requesting\n"
        "Refunds are asynchronous via the refund-events topic.\n",
        encoding="utf-8")
    (docs / "index.md").write_text("# Overview\n\nThe map.\n",
                                   encoding="utf-8")
    embed = _new_embed(transport)
    ingest.index_collection(docs, embed=embed)
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs, kind="docs")],
        llm=None)
    return cfg, embed, docs


# --- direct build_mcp: tool listing / kind-dynamic registration --------

def test_build_mcp_registers_only_docs_tools_for_docs_only_config(tmp_path):
    docs = tmp_path / "d"
    docs.mkdir()
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs, kind="docs")],
        llm=None)
    mcp = mcp_facade.build_mcp(cfg, None)

    async def go():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert asyncio.run(go()) == {"search_docs", "read_doc", "list_docs"}


def test_build_mcp_adds_search_tools_per_present_kind(tmp_path):
    d1, d2, d3 = tmp_path / "d1", tmp_path / "d2", tmp_path / "d3"
    d1.mkdir(); d2.mkdir(); d3.mkdir()
    cfg = config.Config(collections=[
        config.Collection(name="docs", path=d1, kind="docs"),
        config.Collection(name="code", path=d2, kind="code"),
        config.Collection(name="mem", path=d3, kind="memory"),
    ], llm=None)
    mcp = mcp_facade.build_mcp(cfg, None)

    async def go():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}

    names = asyncio.run(go())
    assert names == {"search_docs", "search_code", "search_memory",
                     "read_doc", "list_docs"}


def test_search_docs_bm25_round_trip_through_real_app(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        res = await client.call_tool("search_docs", {"query": "asynchronous"})
        return res.data

    hits = run_mcp(app, go)
    assert hits and hits[0]["path"] == "how-to/refunds.md"
    assert hits[0]["collection"] == "shop"
    assert set(hits[0]) == {"path", "title", "heading_path", "anchor",
                            "text", "score", "collection"}


def test_search_docs_hybrid_round_trip_through_real_app(docs_corpus):
    cfg, embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=embed)

    async def go(client):
        res = await client.call_tool("search_docs",
                                     {"query": "refund topic"})
        return res.data

    hits = run_mcp(app, go)
    assert hits and hits[0]["path"] == "how-to/refunds.md"
    assert hits[0]["anchor"] == "requesting"
    assert hits[0]["collection"] == "shop"


def test_search_docs_skips_unindexed_collection(tmp_path, fake_embed):
    transport, _state = fake_embed
    indexed = tmp_path / "shop"
    (indexed / "how-to").mkdir(parents=True)
    (indexed / "how-to" / "refunds.md").write_text(
        "# Refunds\n\nRefunds are asynchronous.\n", encoding="utf-8")
    embed = _new_embed(transport)
    ingest.index_collection(indexed, embed=embed)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    cfg = config.Config(collections=[
        config.Collection(name="shop", path=indexed, kind="docs"),
        config.Collection(name="fresh", path=fresh, kind="docs"),
    ], llm=None)
    app = api.create_app(cfg, embed=embed)

    async def go(client):
        res = await client.call_tool("search_docs",
                                     {"query": "asynchronous"})
        return res.data

    hits = run_mcp(app, go)
    assert hits
    assert all(h["collection"] == "shop" for h in hits)


def test_search_docs_fans_out_across_collections_score_desc(tmp_path,
                                                             fake_embed):
    transport, _state = fake_embed
    embed = _new_embed(transport)
    docs_a = tmp_path / "alpha"
    docs_a.mkdir()
    (docs_a / "guide.md").write_text(
        "# Guide A\n\nRefund topic details live here.\n", encoding="utf-8")
    docs_b = tmp_path / "beta"
    docs_b.mkdir()
    (docs_b / "guide.md").write_text(
        "# Guide B\n\nRefund topic details live here too.\n",
        encoding="utf-8")
    ingest.index_collection(docs_a, embed=embed)
    ingest.index_collection(docs_b, embed=embed)
    cfg = config.Config(collections=[
        config.Collection(name="alpha", path=docs_a, kind="docs"),
        config.Collection(name="beta", path=docs_b, kind="docs"),
    ], llm=None)
    app = api.create_app(cfg, embed=None)  # BM25-only: deterministic scores

    async def go(client):
        res = await client.call_tool("search_docs",
                                     {"query": "refund topic", "top": 10})
        return res.data

    hits = run_mcp(app, go)
    assert {h["collection"] for h in hits} == {"alpha", "beta"}
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_search_docs_empty_query_returns_empty_list(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        res = await client.call_tool("search_docs", {"query": "   "})
        return res.data

    assert run_mcp(app, go) == []


def test_search_docs_top_out_of_range_is_tool_error(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        for top in (0, 51):
            with pytest.raises(ToolError, match="50"):
                await client.call_tool("search_docs",
                                       {"query": "refund", "top": top})

    run_mcp(app, go)


# --- read_doc through the real mounted app ------------------------------

def test_read_doc_round_trip(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        res = await client.call_tool(
            "read_doc", {"collection": "shop", "path": "how-to/refunds.md"})
        return res.data

    doc = run_mcp(app, go)
    assert doc["markdown"].startswith("# Refunds")
    assert {"text": "Requesting", "anchor": "requesting",
            "level": 2} in doc["headings"]


def test_read_doc_unknown_collection_is_tool_error(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        with pytest.raises(ToolError, match="unknown collection"):
            await client.call_tool(
                "read_doc", {"collection": "ghost", "path": "x.md"})

    run_mcp(app, go)


def test_read_doc_unknown_path_is_tool_error(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        with pytest.raises(ToolError, match="no such document"):
            await client.call_tool(
                "read_doc", {"collection": "shop", "path": "nope.md"})

    run_mcp(app, go)


def test_read_doc_and_list_docs_with_db_dir_outside_content_folder(
        tmp_path, fake_embed):
    # Relocatable databases: read_doc/list_docs must resolve the
    # collection's db via db_dir, not assume it's inside `docs`.
    transport, _state = fake_embed
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "refunds.md").write_text(
        "# Refunds\n\n## Requesting\nRefunds are asynchronous.\n",
        encoding="utf-8")
    db_dir = tmp_path / "elsewhere"
    embed = _new_embed(transport)
    ingest.index_collection(docs, embed=embed, db_dir=db_dir)
    assert not (docs / ".docs-wiki").exists()
    cfg = config.Config(collections=[
        config.Collection(name="shop", path=docs, kind="docs", db_dir=db_dir)
    ], llm=None)
    app = api.create_app(cfg, embed=embed)

    async def go(client):
        doc_res = await client.call_tool(
            "read_doc", {"collection": "shop", "path": "how-to/refunds.md"})
        list_res = await client.call_tool("list_docs", {"kind": "docs"})
        return doc_res.data, list_res.data

    doc, docs_list = run_mcp(app, go)
    assert doc["markdown"].startswith("# Refunds")
    assert docs_list and docs_list[0]["collection"] == "shop"


# --- list_docs through the real mounted app -----------------------------

def test_list_docs_across_collections_of_a_kind(tmp_path, fake_embed):
    transport, _state = fake_embed
    embed = _new_embed(transport)
    docs_a = tmp_path / "alpha"
    docs_a.mkdir()
    (docs_a / "one.md").write_text("# One\n", encoding="utf-8")
    docs_b = tmp_path / "beta"
    docs_b.mkdir()
    (docs_b / "two.md").write_text("# Two\n", encoding="utf-8")
    ingest.index_collection(docs_a, embed=embed)
    ingest.index_collection(docs_b, embed=embed)
    cfg = config.Config(collections=[
        config.Collection(name="alpha", path=docs_a, kind="docs"),
        config.Collection(name="beta", path=docs_b, kind="docs"),
    ], llm=None)
    app = api.create_app(cfg, embed=embed)

    async def go(client):
        res = await client.call_tool("list_docs", {"kind": "docs"})
        return res.data

    listing = run_mcp(app, go)
    by_collection = {(d["path"], d["collection"]) for d in listing}
    assert by_collection == {("one.md", "alpha"), ("two.md", "beta")}


def test_list_docs_unconfigured_kind_is_tool_error(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        with pytest.raises(ToolError, match="docs"):
            await client.call_tool("list_docs", {"kind": "memory"})

    run_mcp(app, go)


def test_list_docs_invalid_kind_is_tool_error(docs_corpus):
    cfg, _embed, _docs = docs_corpus
    app = api.create_app(cfg, embed=None)

    async def go(client):
        with pytest.raises(ToolError):
            await client.call_tool("list_docs", {"kind": "wiki"})

    run_mcp(app, go)


# --- HTTP untouched -------------------------------------------------------

def test_http_routes_still_work_with_mcp_mounted(docs_corpus):
    from fastapi.testclient import TestClient

    cfg, embed, _docs = docs_corpus
    client = TestClient(api.create_app(cfg, embed=embed))
    assert client.get("/collections").json() == [
        {"name": "shop", "indexed": True, "documents": 2,
         "retrieval": "hybrid", "kind": "docs"}]
    hits = client.post("/collections/shop/search",
                       json={"query": "refund topic"}).json()
    assert hits and hits[0]["path"] == "how-to/refunds.md"
    doc = client.get("/collections/shop/documents/how-to/refunds.md").json()
    assert doc["markdown"].startswith("# Refunds")


def test_mcp_mount_does_not_shadow_http_routes(docs_corpus):
    from fastapi.testclient import TestClient

    cfg, _embed, _docs = docs_corpus
    client = TestClient(api.create_app(cfg, embed=None))
    response = client.get("/collections")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "shop"


def test_unexpected_tool_exception_is_masked(docs_corpus, monkeypatch):
    """Unexpected exceptions (ValueError, etc.) are masked, but deliberate
    ToolError messages are passed through unchanged."""
    cfg, _embed, _docs = docs_corpus

    # Monkeypatch api.run_search to raise ValueError with internal details
    def failing_search(*args, **kwargs):
        raise ValueError("secret internal path /some/data/location")
    monkeypatch.setattr(api, "run_search", failing_search)

    app = api.create_app(cfg, embed=None)

    async def go(client):
        # Unexpected exception should be masked
        try:
            res = await client.call_tool("search_docs", {"query": "test"})
            assert False, "Should have raised ToolError"
        except ToolError as e:
            return str(e)

    error_msg = run_mcp(app, go)
    # Should NOT contain the secret internal path
    assert "secret internal path" not in error_msg
    assert "/some/data/location" not in error_msg

    # Verify that deliberate ToolError messages still carry through
    async def go_tool_error(client):
        try:
            res = await client.call_tool(
                "read_doc", {"collection": "nonexistent", "path": "x.md"})
            assert False, "Should have raised ToolError"
        except ToolError as e:
            return str(e)

    error_msg = run_mcp(app, go_tool_error)
    # ToolError message should be intact
    assert "unknown collection" in error_msg


def test_fan_out_merge_orders_across_collections(tmp_path, fake_embed):
    """Multi-collection search merges results from alpha (2 hits: scores 1.0,
    0.5) and beta (1 hit: score 1.0), sorts descending, and truncates at
    top=N. Both docs in alpha and beta's doc all match query terms (refund AND
    topic) so FTS5 ranks them distinctly within each collection."""
    transport, _state = fake_embed
    embed = _new_embed(transport)

    # Collection A: two matching docs — both contain "refund" and "topic"
    # First one is ranked higher (score 1.0), second ranked lower (score 0.5)
    docs_a = tmp_path / "alpha"
    docs_a.mkdir()
    (docs_a / "high.md").write_text(
        "# High Score\n\nRefund topic details.\n", encoding="utf-8")
    (docs_a / "low.md").write_text(
        "# Low Score\n\nTopic refund policy.\n", encoding="utf-8")

    # Collection B: one matching doc with score 1.0
    docs_b = tmp_path / "beta"
    docs_b.mkdir()
    (docs_b / "another.md").write_text(
        "# Another High\n\nRefund topic section.\n", encoding="utf-8")

    ingest.index_collection(docs_a, embed=embed)
    ingest.index_collection(docs_b, embed=embed)
    cfg = config.Config(collections=[
        config.Collection(name="alpha", path=docs_a, kind="docs"),
        config.Collection(name="beta", path=docs_b, kind="docs"),
    ], llm=None)
    app = api.create_app(cfg, embed=None)  # BM25-only for deterministic scores

    # First call: large top to see all merged results unsorted by top limit
    async def go_all(client):
        res = await client.call_tool("search_docs",
                                     {"query": "refund topic", "top": 10})
        return res.data

    hits_all = run_mcp(app, go_all)
    # All three docs match: alpha's 2 + beta's 1
    assert len(hits_all) == 3
    scores_all = [h["score"] for h in hits_all]
    collections_all = {h["collection"] for h in hits_all}

    # Verify both collections present and scores are descending
    assert collections_all == {"alpha", "beta"}
    assert scores_all == sorted(scores_all, reverse=True)
    assert scores_all == [1.0, 1.0, 0.5]

    # Second call: top=2 truncates to the two highest-scoring hits
    async def go_truncated(client):
        res = await client.call_tool("search_docs",
                                     {"query": "refund topic", "top": 2})
        return res.data

    hits_top2 = run_mcp(app, go_truncated)
    # Should have exactly 2 results (the two with score 1.0)
    assert len(hits_top2) == 2
    scores_top2 = [h["score"] for h in hits_top2]
    assert scores_top2 == [1.0, 1.0]
