import sqlite3
from contextlib import closing

from rag import ingest
from rag.store import Store, state_dir

DOC_A = """---
updated: 2026-08-02
---
# Alpha

## Usage

Run the alpha refund pipeline.
"""

DOC_B = """# Beta

## Setup

Beta setup text.
"""


def make_docs(tmp_path):
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "reference").mkdir()
    (docs / "how-to" / "alpha.md").write_text(DOC_A, encoding="utf-8")
    (docs / "reference" / "beta.md").write_text(DOC_B, encoding="utf-8")
    (docs / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    return docs


def test_state_dir_self_ignoring(tmp_path):
    docs = make_docs(tmp_path)
    state = state_dir(docs)
    assert state == docs / ".docs-wiki"
    assert (state / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_index_and_list(tmp_path):
    docs = make_docs(tmp_path)
    counts = ingest.index_collection(docs)
    assert counts == {"indexed": 3, "unchanged": 0, "removed": 0,
                      "embedded": 0, "pending": 0}
    with Store.open(docs) as store:
        rows = store.list_docs_catalog()
    assert [r.quadrant for r in rows] == ["how-to", "reference", "index"]
    assert rows[0].title == "Alpha"


def test_incremental_reindex(tmp_path):
    docs = make_docs(tmp_path)
    ingest.index_collection(docs)
    counts = ingest.index_collection(docs)
    assert counts == {"indexed": 0, "unchanged": 3, "removed": 0,
                      "embedded": 0, "pending": 0}
    (docs / "reference" / "beta.md").write_text(
        "# Beta\n\n## Setup\n\nChanged text.\n", encoding="utf-8")
    counts = ingest.index_collection(docs)
    assert counts["indexed"] == 1 and counts["unchanged"] == 2


def test_removed_file_dropped(tmp_path):
    docs = make_docs(tmp_path)
    ingest.index_collection(docs)
    (docs / "reference" / "beta.md").unlink()
    counts = ingest.index_collection(docs)
    assert counts["removed"] == 1
    with Store.open(docs) as store:
        assert store.get_doc("reference/beta.md") is None
        assert store.search_bm25("Beta setup") == []


def test_search_finds_and_snippets(tmp_path):
    docs = make_docs(tmp_path)
    ingest.index_collection(docs)
    with Store.open(docs) as store:
        rows = store.search_bm25("refund pipeline")
    assert rows
    assert rows[0].path == "how-to/alpha.md"
    assert "<mark>" in rows[0].snippet


def test_search_special_characters_safe(tmp_path):
    docs = make_docs(tmp_path)
    ingest.index_collection(docs)
    with Store.open(docs) as store:
        assert store.search_bm25('refund "pipeline OR (weird') is not None


def test_markdown_stored_without_front_matter(tmp_path):
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "a.md").write_text(
        "---\nupdated: 2026-08-01\n---\n# A\n\nBody text.\n", encoding="utf-8")
    ingest.index_collection(docs)
    with Store.open(docs) as store:
        doc = store.get_doc("how-to/a.md")
    assert doc.markdown.startswith("# A")
    assert "updated:" not in doc.markdown


def test_markdown_column_migrates_old_db(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Old\n\nStill here.\n", encoding="utf-8")
    ingest.index_collection(docs)
    # simulate a Phase 2 database: drop the markdown column
    db = docs / ".docs-wiki" / "index.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE docs DROP COLUMN markdown")
        conn.commit()
    with Store.open(docs) as store:
        doc = store.get_doc("a.md")
    assert doc.markdown.startswith("# Old")


def test_index_collection_with_db_dir_round_trips_search_and_leaves_docs_clean(
        tmp_path):
    docs = make_docs(tmp_path)
    db_dir = tmp_path / "elsewhere"
    counts = ingest.index_collection(docs, db_dir=db_dir)
    assert counts == {"indexed": 3, "unchanged": 0, "removed": 0,
                      "embedded": 0, "pending": 0}
    assert not (docs / ".docs-wiki").exists()
    assert (db_dir / "index.db").is_file()
    with Store.open(docs, db_dir=db_dir) as store:
        rows = store.search_bm25("refund pipeline")
    assert rows and rows[0].path == "how-to/alpha.md"


def test_index_format_compatible_with_docs_wiki(tmp_path):
    """An index built here must be readable by the docs-wiki code and
    vice versa: assert the exact table/column set docs-wiki relies on."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    ingest.index_collection(docs)
    with Store.open(docs) as store:
        cols = {r[1] for r in store.conn.execute("PRAGMA table_info(docs)")}
        assert {"path", "title", "quadrant", "front_matter", "content_hash",
                "html", "quadrant_order", "markdown"} <= cols
        names = {r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        assert "chunks" in names and "index_meta" in names
