"""Unit tests for `rag.store.Store` in isolation -- tmp dirs only, no HTTP
layer, no `rag.content` helpers. Exercises every method of the Store
interface directly: schema/migration on open, doc CRUD round-trip, BM25
search (incl. special-char escaping, ported from test_index.py), vector
ops, meta, `exists`, and the context-manager close."""
import sqlite3
from contextlib import closing

import pytest

from rag.structure import Section
from rag.store import CatalogRow, DocRow, HitRow, Store, state_dir


def make_docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    return docs


# ---- open / schema / exists ----------------------------------------------

def test_exists_false_before_open(tmp_path):
    docs = make_docs(tmp_path)
    assert Store.exists(docs) is False


def test_exists_true_after_open(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        pass
    assert Store.exists(docs) is True


def test_open_creates_docswiki_index_db(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        pass
    assert (docs / ".docs-wiki" / "index.db").is_file()
    assert (docs / ".docs-wiki" / ".gitignore").read_text(
        encoding="utf-8").strip() == "*"


def test_open_creates_expected_schema(tmp_path):
    """Compatibility contract with docs-wiki (ported from
    test_index_format_compatible_with_docs_wiki): the exact table/column
    set docs-wiki relies on must exist even before any doc is indexed."""
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        cols = {r[1] for r in store.conn.execute("PRAGMA table_info(docs)")}
        assert {"path", "title", "quadrant", "front_matter", "content_hash",
                "html", "quadrant_order", "markdown"} <= cols
        names = {r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        assert "chunks" in names and "index_meta" in names


def test_open_is_idempotent(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs):
        pass
    # Reopening an already-initialized db must not raise (CREATE TABLE IF
    # NOT EXISTS / idempotent migration).
    with Store.open(docs) as store:
        assert store.doc_count() == 0


def test_migration_adds_markdown_column_on_legacy_db(tmp_path):
    """A Phase 2 database predates the markdown column. Store.open must
    add it in place and backfill from disk for files that still exist."""
    docs = make_docs(tmp_path)
    (docs / "a.md").write_text("# Old\n\nStill here.\n", encoding="utf-8")
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "Old", "index", {}, "digest1",
                         "<h1>Old</h1>", "# Old\n\nStill here.\n")
        store.conn.commit()

    db = docs / ".docs-wiki" / "index.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE docs DROP COLUMN markdown")
        conn.commit()

    with Store.open(docs) as store:
        doc = store.get_doc("a.md")
        assert doc.markdown.startswith("# Old")


def test_migration_backfills_empty_for_vanished_file(tmp_path):
    """A doc whose source file vanished since the last index run keeps
    markdown = '' -- the migration cannot recover text it cannot read."""
    docs = make_docs(tmp_path)
    (docs / "gone.md").write_text("# Gone\n\nBye.\n", encoding="utf-8")
    with Store.open(docs) as store:
        store.upsert_doc("gone.md", "Gone", "index", {}, "digest1",
                         "<h1>Gone</h1>", "# Gone\n\nBye.\n")
        store.conn.commit()

    db = docs / ".docs-wiki" / "index.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE docs DROP COLUMN markdown")
        conn.commit()
    (docs / "gone.md").unlink()

    with Store.open(docs) as store:
        doc = store.get_doc("gone.md")
        assert doc.markdown == ""


# ---- doc CRUD --------------------------------------------------------------

def test_upsert_and_get_doc_round_trip(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("how-to/a.md", "Alpha", "how-to",
                         {"updated": "2026-08-02"}, "digest1",
                         "<h1>Alpha</h1>", "# Alpha\n\nBody.\n")
        doc = store.get_doc("how-to/a.md")
        assert doc == DocRow(path="how-to/a.md", title="Alpha",
                             quadrant="how-to",
                             front_matter={"updated": "2026-08-02"},
                             markdown="# Alpha\n\nBody.\n")


def test_upsert_doc_replaces_existing(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "First", "index", {}, "d1", "<h1/>", "one")
        store.upsert_doc("a.md", "Second", "index", {}, "d2", "<h1/>", "two")
        assert store.doc_count() == 1
        doc = store.get_doc("a.md")
        assert doc.title == "Second" and doc.markdown == "two"


def test_get_doc_missing_returns_none(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        assert store.get_doc("nope.md") is None


def test_get_markdown(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "body")
        assert store.get_markdown("a.md") == "body"
        assert store.get_markdown("nope.md") is None


def test_doc_count(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        assert store.doc_count() == 0
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        assert store.doc_count() == 1


def test_list_docs_ordered_by_quadrant_then_path(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("reference/b.md", "B", "reference", {}, "d1",
                         "<h1/>", "b")
        store.upsert_doc("how-to/a.md", "A", "how-to", {}, "d2", "<h1/>", "a")
        store.upsert_doc("index.md", "Home", "index", {}, "d3", "<h1/>", "h")
        rows = store.list_docs_catalog()
        assert [r.quadrant for r in rows] == ["how-to", "reference", "index"]
        assert rows[0].title == "A"
        assert all(isinstance(r, CatalogRow) for r in rows)


def test_list_docs_catalog_same_order_as_list_docs_minus_markdown(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("reference/b.md", "B", "reference",
                         {"k": "v"}, "d1", "<h1/>", "b" * 5000)
        store.upsert_doc("how-to/a.md", "A", "how-to", {}, "d2",
                         "<h1/>", "a" * 5000)
        rows = store.list_docs_catalog()
        assert [r.quadrant for r in rows] == ["how-to", "reference"]
        assert rows[0].path == "how-to/a.md" and rows[0].title == "A"
        assert rows[1].front_matter == {"k": "v"}
        assert all(isinstance(r, CatalogRow) for r in rows)
        # CatalogRow has no markdown field at all -- not just an empty
        # one -- so a caller can never accidentally read it back.
        assert not hasattr(rows[0], "markdown")


def test_list_docs_catalog_does_not_select_markdown_column(tmp_path):
    """Regression (Task 2 review, IMPORTANT-1): `_documents_catalog`/MCP
    `list_docs` used to call `Store.list_docs()`, which selects and
    materializes every doc's full `markdown` column only to discard it
    per row -- 122x more row bytes, 4.9x latency on a real corpus,
    invisible to response-shape tests. Pin the fix at the SQL level (not
    just the returned type) so a future edit that re-adds `markdown` to
    this query's SELECT list fails here even if `CatalogRow` itself is
    never touched."""
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x" * 5000)
        queries: list[str] = []
        store.conn.set_trace_callback(queries.append)
        try:
            store.list_docs_catalog()
        finally:
            store.conn.set_trace_callback(None)
        assert queries, "list_docs_catalog executed no SQL to trace"
        assert not any("markdown" in q.lower() for q in queries)


def test_remove_docs_not_in(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("keep.md", "Keep", "index", {}, "d1", "<h1/>", "k")
        store.upsert_doc("drop.md", "Drop", "index", {}, "d2", "<h1/>", "d")
        store.replace_chunks("drop.md", [Section("(intro)", "drop text")])
        removed = store.remove_docs_not_in({"keep.md"})
        assert removed == 1
        assert store.get_doc("drop.md") is None
        assert store.get_doc("keep.md") is not None
        assert store.chunk_heading_paths("drop.md") == []


# ---- chunks -----------------------------------------------------------

def test_replace_chunks_returns_rowids(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        rowids = store.replace_chunks("a.md", [
            Section("(intro)", "intro text"),
            Section("Usage", "usage text"),
        ])
        assert len(rowids) == 2
        assert all(isinstance(r, int) for r in rowids)


def test_replace_chunks_replaces_prior_chunks(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        store.replace_chunks("a.md", [Section("Old", "old text")])
        store.replace_chunks("a.md", [Section("New", "new text")])
        assert store.chunk_heading_paths("a.md") == ["New"]


def test_chunk_heading_paths_order_and_intro_included(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        store.replace_chunks("a.md", [
            Section("(intro)", "intro text"),
            Section("Usage", "usage text"),
            Section("Setup", "setup text"),
        ])
        # "(intro)" filtering is left to the caller (anchors) -- Store
        # returns every heading_path, in first-seen (MIN(rowid)) order.
        assert store.chunk_heading_paths("a.md") == [
            "(intro)", "Usage", "Setup"]


def test_chunk_heading_paths_groups_duplicates(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        store.replace_chunks("a.md", [
            Section("Usage", "part one"),
            Section("Usage", "part two"),
        ])
        assert store.chunk_heading_paths("a.md") == ["Usage"]


# ---- bm25 search ------------------------------------------------------

def test_search_bm25_finds_and_snippets(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("how-to/alpha.md", "Alpha", "how-to", {}, "d1",
                         "<h1/>", "x")
        store.replace_chunks("how-to/alpha.md",
                             [Section("Usage", "Run the alpha refund "
                                              "pipeline.")])
        hits = store.search_bm25("refund pipeline", limit=20)
        assert hits
        assert isinstance(hits[0], HitRow)
        assert hits[0].path == "how-to/alpha.md"
        assert "<mark>" in hits[0].snippet


def test_search_bm25_special_characters_safe(tmp_path):
    """Ported from test_search_special_characters_safe: FTS5 query syntax
    characters in the raw query string must never raise -- _fts_query
    quotes every alnum/underscore token and drops everything else."""
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("how-to/alpha.md", "Alpha", "how-to", {}, "d1",
                         "<h1/>", "x")
        store.replace_chunks("how-to/alpha.md",
                             [Section("Usage", "Run the alpha refund "
                                              "pipeline.")])
        assert store.search_bm25('refund "pipeline OR (weird') is not None


def test_search_bm25_empty_query_returns_empty(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        assert store.search_bm25('"""') == []


def test_search_bm25_no_match_returns_empty(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        store.replace_chunks("a.md", [Section("(intro)", "some text")])
        assert store.search_bm25("nonexistentword") == []


# ---- vectors ------------------------------------------------------------

def test_pending_chunks_lists_chunks_without_vectors(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        rowids = store.replace_chunks("a.md", [Section("(intro)", "text")])
        pending = store.pending_chunks()
        assert [row["rowid"] for row in pending] == rowids


def test_store_vectors_then_pending_and_search(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        rowids = store.replace_chunks("a.md", [
            Section("(intro)", "alpha text"),
            Section("More", "beta text"),
        ])
        store.store_vectors([(rowids[0], [1.0, 0.0, 0.0])], dim=3)
        assert [row["rowid"] for row in store.pending_chunks()] == [rowids[1]]

        store.store_vectors([(rowids[1], [0.0, 1.0, 0.0])], dim=3)
        assert store.pending_chunks() == []

        vector_rowids = store.vector_rowids([1.0, 0.0, 0.0], limit=5)
        hits = store.hydrate_chunk_rowids(vector_rowids)
        assert hits
        assert hits[0]["rowid"] == rowids[0]
        assert hits[0]["path"] == "a.md"
        assert set(hits[0]) == {"rowid", "path", "heading_path", "text",
                                "title"}


def test_vector_search_empty_when_no_table(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        assert store.hydrate_chunk_rowids(store.vector_rowids([1.0, 0.0], limit=5)) == []


def test_clear_vectors_drops_table_and_meta(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
        rowids = store.replace_chunks("a.md", [Section("(intro)", "text")])
        store.store_vectors([(rowids[0], [1.0, 0.0])], dim=2)
        store.set_meta("embed_model", "fake-embed")
        store.set_meta("embed_dim", "2")

        store.clear_vectors()

        assert store.hydrate_chunk_rowids(store.vector_rowids([1.0, 0.0], limit=5)) == []
        assert store.get_meta("embed_model") is None
        assert store.get_meta("embed_dim") is None
        # pending_chunks must not choke on a dropped chunk_vectors table.
        assert [row["rowid"] for row in store.pending_chunks()] == rowids


# ---- meta -----------------------------------------------------------------

def test_meta_get_set_round_trip(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        assert store.get_meta("embed_model") is None
        store.set_meta("embed_model", "fake-embed")
        assert store.get_meta("embed_model") == "fake-embed"


def test_meta_set_replaces_existing(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.set_meta("k", "v1")
        store.set_meta("k", "v2")
        assert store.get_meta("k") == "v2"


# ---- context manager / close ----------------------------------------------

def test_context_manager_closes_connection(tmp_path):
    docs = make_docs(tmp_path)
    with Store.open(docs) as store:
        store.upsert_doc("a.md", "A", "index", {}, "d1", "<h1/>", "x")
    with pytest.raises(sqlite3.ProgrammingError):
        store.conn.execute("SELECT 1")


def test_close_is_safe_to_call_directly(tmp_path):
    docs = make_docs(tmp_path)
    store = Store.open(docs)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.conn.execute("SELECT 1")


# ---- db_dir (relocatable databases, backoffice-managed collections) ------

def test_state_dir_without_db_dir_creates_docs_wiki_in_docs(tmp_path):
    docs = make_docs(tmp_path)
    state = state_dir(docs)
    assert state == docs / ".docs-wiki"
    assert state.is_dir()


def test_state_dir_with_db_dir_uses_it_and_leaves_docs_untouched(tmp_path):
    docs = make_docs(tmp_path)
    db_dir = tmp_path / "elsewhere" / "shop-db"
    state = state_dir(docs, db_dir=db_dir)
    assert state == db_dir
    assert state.is_dir()
    assert not (docs / ".docs-wiki").exists()


def test_store_open_with_db_dir_creates_index_db_outside_content_dir(tmp_path):
    docs = make_docs(tmp_path)
    db_dir = tmp_path / "elsewhere"
    with Store.open(docs, db_dir=db_dir):
        pass
    assert (db_dir / "index.db").is_file()
    assert not (docs / ".docs-wiki").exists()


def test_store_exists_honors_db_dir(tmp_path):
    docs = make_docs(tmp_path)
    db_dir = tmp_path / "elsewhere"
    assert Store.exists(docs, db_dir=db_dir) is False
    with Store.open(docs, db_dir=db_dir):
        pass
    assert Store.exists(docs, db_dir=db_dir) is True
    # The in-folder location (no db_dir) must still report unindexed --
    # the database really lives only at db_dir, nowhere in docs.
    assert Store.exists(docs) is False


def test_store_open_with_db_dir_still_reads_content_from_collection_path(
        tmp_path):
    """Store.open's markdown-migration backfill reads source files from
    collection_path, never db_dir -- db_dir only relocates the database
    file itself, not where content is read from."""
    docs = make_docs(tmp_path)
    (docs / "a.md").write_text("# Old\n\nStill here.\n", encoding="utf-8")
    db_dir = tmp_path / "elsewhere"
    with Store.open(docs, db_dir=db_dir) as store:
        store.upsert_doc("a.md", "Old", "index", {}, "digest1",
                         "<h1>Old</h1>", "# Old\n\nStill here.\n")
        store.conn.commit()

    db = db_dir / "index.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE docs DROP COLUMN markdown")
        conn.commit()

    with Store.open(docs, db_dir=db_dir) as store:
        doc = store.get_doc("a.md")
        assert doc.markdown.startswith("# Old")
