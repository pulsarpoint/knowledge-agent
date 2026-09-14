import pytest
from fastapi.testclient import TestClient

from rag import api, config, embed_client, ingest
from rag.store import Store


@pytest.fixture()
def corpus(tmp_path, fake_embed):
    transport, state = fake_embed
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "refunds.md").write_text(
        "---\nupdated: 2026-08-01\n---\n# Refunds\n\n## Requesting\n"
        "Refunds are asynchronous via the refund-events topic.\n",
        encoding="utf-8")
    (docs / "index.md").write_text("# Overview\n\nThe map.\n", encoding="utf-8")
    client = embed_client.EmbedClient(
        config.EmbedConfig(base_url="http://embed.local/v1", api_key=None,
                           embed_model="e"), transport=transport)
    ingest.index_collection(docs, embed=client)
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs)], llm=None)
    return TestClient(api.create_app(cfg, embed=client)), docs


def test_health_and_collections(corpus):
    client, _docs = corpus
    assert client.get("/health").json() == {"status": "ok"}
    body = client.get("/collections").json()
    assert body == [{"name": "shop", "indexed": True, "documents": 2,
                     "retrieval": "hybrid", "kind": "docs"}]


def test_collections_report_bm25_only_without_embed(corpus, tmp_path):
    # An operator must be able to see from one GET /collections call that
    # the serving process has no embed client (the silent-degradation
    # incident: BM25-only was invisible until gap telemetry exposed it).
    _client, docs = corpus
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs)], llm=None)
    client = TestClient(api.create_app(cfg, embed=None))
    assert client.get("/collections").json()[0]["retrieval"] == "bm25-only"


def test_collections_with_different_kinds(tmp_path, fake_embed):
    # Two collections with different kinds should report their respective kind values.
    transport, state = fake_embed
    docs1 = tmp_path / "docs-collection"
    docs1.mkdir()
    (docs1 / "index.md").write_text("# Doc\n", encoding="utf-8")
    docs2 = tmp_path / "code-collection"
    docs2.mkdir()
    (docs2 / "index.md").write_text("# Code\n", encoding="utf-8")
    client = embed_client.EmbedClient(
        config.EmbedConfig(base_url="http://embed.local/v1", api_key=None,
                           embed_model="e"), transport=transport)
    ingest.index_collection(docs1, embed=client)
    ingest.index_collection(docs2, embed=client)
    cfg = config.Config(
        collections=[
            config.Collection(name="docs", path=docs1, kind="docs"),
            config.Collection(name="code", path=docs2, kind="code")
        ], llm=None)
    test_client = TestClient(api.create_app(cfg, embed=client))
    body = test_client.get("/collections").json()
    assert len(body) == 2
    docs_entry = next(e for e in body if e["name"] == "docs")
    code_entry = next(e for e in body if e["name"] == "code")
    assert docs_entry["kind"] == "docs"
    assert code_entry["kind"] == "code"


def test_collections_and_search_with_db_dir_outside_content_folder(
        tmp_path, fake_embed):
    # Relocatable databases: every Store.open/exists call the HTTP API
    # makes must honor Collection.db_dir -- both the /collections listing
    # and the search round trip -- and the content folder must stay clean.
    transport, _state = fake_embed
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "refunds.md").write_text(
        "---\nupdated: 2026-08-01\n---\n# Refunds\n\n## Requesting\n"
        "Refunds are asynchronous via the refund-events topic.\n",
        encoding="utf-8")
    db_dir = tmp_path / "elsewhere" / "shop-db"
    client = embed_client.EmbedClient(
        config.EmbedConfig(base_url="http://embed.local/v1", api_key=None,
                           embed_model="e"), transport=transport)
    ingest.index_collection(docs, embed=client, db_dir=db_dir)
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs,
                                       db_dir=db_dir)],
        llm=None)
    test_client = TestClient(api.create_app(cfg, embed=client))

    body = test_client.get("/collections").json()
    assert body == [{"name": "shop", "indexed": True, "documents": 1,
                     "retrieval": "hybrid", "kind": "docs"}]
    assert not (docs / ".docs-wiki").exists()
    assert (db_dir / "index.db").is_file()

    hits = test_client.post("/collections/shop/search",
                            json={"query": "refund topic"}).json()
    assert hits and hits[0]["path"] == "how-to/refunds.md"


def test_documents_catalog_and_get(corpus):
    client, _docs = corpus
    listing = client.get("/collections/shop/documents").json()
    paths = {d["path"] for d in listing}
    assert paths == {"index.md", "how-to/refunds.md"}
    doc = next(d for d in listing if d["path"] == "how-to/refunds.md")
    assert doc["title"] == "Refunds" and doc["quadrant"] == "how-to"
    assert doc["front_matter"]["updated"] == "2026-08-01"
    full = client.get("/collections/shop/documents/how-to/refunds.md").json()
    assert full["markdown"].startswith("# Refunds")
    assert {"text": "Requesting", "anchor": "requesting",
            "level": 2} in full["headings"]


def test_search_returns_scored_chunks_with_anchors(corpus):
    client, _docs = corpus
    hits = client.post("/collections/shop/search",
                       json={"query": "refund topic"}).json()
    assert hits and hits[0]["path"] == "how-to/refunds.md"
    assert hits[0]["anchor"] == "requesting"
    assert "score" in hits[0] and "text" in hits[0]
    assert set(hits[0]) == {"path", "title", "heading_path", "anchor",
                            "text", "score"}


def test_unknown_collection_404_and_error_shape(corpus):
    client, _docs = corpus
    for response in [client.get("/collections/ghost/documents"),
                     client.post("/collections/ghost/search",
                                 json={"query": "x"}),
                     client.get("/collections/shop/documents/nope.md")]:
        assert response.status_code == 404
        assert response.json()["error"]["message"]


def test_unindexed_collection_degrades(tmp_path):
    docs = tmp_path / "fresh"
    docs.mkdir()
    cfg = config.Config(
        collections=[config.Collection(name="fresh", path=docs)], llm=None)
    client = TestClient(api.create_app(cfg, embed=None))
    assert client.get("/collections").json()[0]["indexed"] is False
    assert client.post("/collections/fresh/search",
                       json={"query": "anything"}).json() == []


def test_search_without_embed_client_is_bm25_only(corpus, tmp_path, fake_embed):
    transport, state = fake_embed
    _client, docs = corpus
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs)], llm=None)
    client = TestClient(api.create_app(cfg, embed=None))
    hits = client.post("/collections/shop/search",
                       json={"query": "asynchronous"}).json()
    assert hits and hits[0]["path"] == "how-to/refunds.md"
    assert set(hits[0]) == {"path", "title", "heading_path", "anchor",
                            "text", "score"}


def test_embed_endpoint_failure_degrades_to_bm25(corpus, fake_embed):
    """Retrieval's own guard against a malformed-but-HTTP-200 embed
    response (empty `data`) raises EmbedError, which the search route
    must catch and turn into a BM25-only fallback -- never a 500."""
    client, _docs = corpus
    _transport, state = fake_embed
    state["truncate_embeddings"] = True
    hits = client.post("/collections/shop/search",
                       json={"query": "asynchronous"}).json()
    assert hits and hits[0]["path"] == "how-to/refunds.md"
    assert set(hits[0]) == {"path", "title", "heading_path", "anchor",
                            "text", "score"}


def test_search_body_validation_returns_error_shape(corpus):
    # FastAPI's own 422 {"detail": [...]} would violate the API's 4xx
    # contract of {"error": {"message": ...}}; both a type mismatch and
    # a wholly malformed JSON body must land on our shape at 400.
    client, _docs = corpus
    bad_type = client.post("/collections/shop/search", json={"query": 123})
    assert bad_type.status_code == 400
    assert bad_type.json()["error"]["message"]
    bad_json = client.post("/collections/shop/search", content=b"not json",
                           headers={"content-type": "application/json"})
    assert bad_json.status_code == 400
    assert bad_json.json()["error"]["message"]


def test_search_top_is_bounded(corpus):
    # SQLite treats LIMIT -1 as unlimited, so an unvalidated negative
    # `top` would dump the whole corpus on the BM25 path; out-of-range
    # values must be rejected on the 400 error shape instead.
    client, _docs = corpus
    for top in [-1, 0, 51]:
        response = client.post("/collections/shop/search",
                               json={"query": "refund", "top": top})
        assert response.status_code == 400
        assert response.json()["error"]["message"]
    hits = client.post("/collections/shop/search",
                       json={"query": "refund topic", "top": 1}).json()
    assert len(hits) == 1


def test_embed_failure_fallback_logs_warning(corpus, fake_embed, caplog):
    # The query-path fallback used to be silent; an operator watching the
    # logs could not tell hybrid responses from degraded ones.
    import logging
    client, _docs = corpus
    _transport, state = fake_embed
    state["truncate_embeddings"] = True
    with caplog.at_level(logging.WARNING, logger="rag.api"):
        client.post("/collections/shop/search",
                    json={"query": "asynchronous"})
    assert any("BM25" in rec.message for rec in caplog.records)


def _index_bare(tmp_path, body: str):
    """Index a single doc with no embed client; return (client, path)."""
    docs = tmp_path / "anchor-docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "guide.md").write_text(body, encoding="utf-8")
    ingest.index_collection(docs, embed=None)
    cfg = config.Config(
        collections=[config.Collection(name="shop", path=docs)], llm=None)
    return TestClient(api.create_app(cfg, embed=None)), "how-to/guide.md"


def test_doc_anchor_survives_h1_slug_collision(tmp_path):
    # The renderer (UI Markdown.tsx / render_html) assigns ids over ALL
    # headings in document order, so the H1 "Setup" takes the bare "setup"
    # slug and the H2 renders as id="setup-2". Anchors must agree or the
    # TOC/search link scrolls to the H1.
    client, path = _index_bare(
        tmp_path, "# Setup\n\nIntro text.\n\n## Setup\n\nBody text.\n")
    doc = client.get(f"/collections/shop/documents/{path}").json()
    entry = next(h for h in doc["headings"] if h["text"] == "Setup")
    assert entry["anchor"] == "setup-2"
    hits = client.post("/collections/shop/search",
                       json={"query": "body"}).json()
    assert hits[0]["anchor"] == "setup-2"


def test_doc_anchor_counts_h4_in_slug_namespace(tmp_path):
    # H4+ headings render with ids too, consuming the slug namespace even
    # though chunking never tracks them.
    client, path = _index_bare(
        tmp_path,
        "# Doc\n\n## Guide\n\nText.\n\n#### Notes\n\nMore.\n\n"
        "## Notes\n\nBody.\n")
    doc = client.get(f"/collections/shop/documents/{path}").json()
    entry = next(h for h in doc["headings"] if h["text"] == "Notes")
    assert entry["anchor"] == "notes-2"


def test_unmatched_routes_keep_error_shape(corpus):
    # README promises errors are ALWAYS {"error": {"message"}}; FastAPI's
    # default 404/405 {"detail"} shape must not leak.
    client, _docs = corpus
    missing = client.get("/collectionz")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"message": "Not Found"}}
    wrong_method = client.get("/collections/shop/search")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["message"]
    # Starlette attaches an Allow header to router 405s; the rewrap must
    # not drop it.
    assert "POST" in wrong_method.headers.get("allow", "")


def test_anchor_ignores_headings_inside_fences(tmp_path):
    # The renderer never sees fenced lines as headings, so a "# Setup"
    # inside a code block must not consume the slug namespace.
    client, path = _index_bare(
        tmp_path,
        "# Doc\n\n```\n# Setup\n```\n\n## Setup\n\nBody.\n")
    doc = client.get(f"/collections/shop/documents/{path}").json()
    entry = next(h for h in doc["headings"] if h["text"] == "Setup")
    assert entry["anchor"] == "setup"


def test_anchor_falls_back_when_markdown_missing(tmp_path, caplog):
    # Legacy migration case: a doc whose file vanished before the markdown
    # backfill keeps markdown = ''. Anchors must fall back to the old
    # label-derived slugs (possibly collision-prone, same as pre-fix)
    # rather than silently becoming None -- and say so in the log.
    import logging
    client, path = _index_bare(
        tmp_path, "# Doc\n\n## Section One\n\nBody.\n")
    docs = tmp_path / "anchor-docs"
    with Store.open(docs) as store:
        store.conn.execute("UPDATE docs SET markdown = ''")
        store.conn.commit()
    with caplog.at_level(logging.WARNING, logger="rag.anchors"):
        doc = client.get(f"/collections/shop/documents/{path}").json()
    entry = next(h for h in doc["headings"] if h["text"] == "Section One")
    assert entry["anchor"] == "section-one"
    assert any("reindex" in rec.message for rec in caplog.records)
