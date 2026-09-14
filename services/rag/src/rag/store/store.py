"""All SQLite concerns for a rag collection, behind one `Store` class.

Moved verbatim off the old single-module indexing pipeline (store-package
refactor, see `docs/superpowers/plans/2026-08-04-rag-store-package.md`):
schema DDL, the `_ensure_markdown_column` migration, `_fts_query`
escaping, and the sqlite-vec vector table ops -- everything that used to
be raw SQL scattered across that one module lives here now, and nowhere
else in this package (see that plan's grep gate). Every consumer
(`rag.ingest`, `rag.api`, `rag.retrieval`, `rag.anchors`,
`rag.mcp_facade`) holds a `Store`, never a raw connection.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from rag.content import parse_front_matter

_QUADRANT_ORDER = {"how-to": 0, "reference": 1, "explanation": 2,
                   "tutorials": 3, "index": 4, "other": 5}

# Byte-identical to the original `conn.executescript("""...""")` argument
# in the pre-refactor single-module `open_db` (indentation included) --
# see this task's self-review diff.
_SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS docs(
            path TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            quadrant TEXT NOT NULL,
            front_matter TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            html TEXT NOT NULL,
            quadrant_order INTEGER NOT NULL,
            markdown TEXT NOT NULL DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
            path UNINDEXED, heading_path, text
        );
        CREATE TABLE IF NOT EXISTS index_meta(
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """


@dataclass
class DocRow:
    path: str
    title: str
    quadrant: str
    front_matter: dict
    markdown: str


@dataclass
class HitRow:
    path: str
    title: str
    heading_path: str
    snippet: str


@dataclass
class CatalogRow:
    """Narrow doc-listing projection -- everything `DocRow` has except
    `markdown`. For a real doc corpus `markdown` can be large (a review
    measured 122x more row bytes and 4.9x latency when the catalog path
    was pulling full `DocRow`s just to discard `.markdown` on every one);
    `list_docs_catalog` never selects that column at all. Anything that
    genuinely needs the body (the document-detail route, `get_doc`) still
    goes through `DocRow`/`get_doc`."""
    path: str
    title: str
    quadrant: str
    front_matter: dict


def state_dir(docs: Path, db_dir: Path | None = None) -> Path:
    # Directory name kept for index compatibility with pre-split installs:
    # ".docs-wiki" predates the docs-wiki/rag split, and an index built by
    # either service's code must stay readable by the other's.
    #
    # `db_dir`: relocatable databases (2026-08-05, backoffice-managed
    # collections) -- when given, IT is the state dir verbatim (no
    # ".docs-wiki" subfolder inside it, and none created inside `docs`
    # either), so the database can live entirely outside the content
    # folder. `parents=True` here (vs. the `db_dir is None` branch's
    # plain `exist_ok=True`) because a backoffice data dir's parent
    # (e.g. `<data_path>/collections/`) may not exist yet; the `docs`
    # branch is untouched from before this parameter existed -- `docs`
    # itself is always already a directory by the time any caller gets
    # here, so this is a pure addition, not a behavior change.
    if db_dir is not None:
        state = Path(db_dir)
        state.mkdir(parents=True, exist_ok=True)
    else:
        state = docs / ".docs-wiki"
        state.mkdir(exist_ok=True)
    gitignore = state / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return state


def load_vec(conn: sqlite3.Connection) -> bool:
    # Library deviation (see task-2-report.md): probe first and skip the
    # actual conn.load_extension() call when vec0 is already registered on
    # this connection. Store.open() already loads the extension on every
    # connection it hands out, so by the time sync_vectors() calls
    # load_vec() again the module is already present -- and re-invoking
    # sqlite_vec.load(conn) on a connection with an *uncommitted* write to
    # an FTS5 virtual table (chunks) reproducibly raises
    # "sqlite3.OperationalError: error during initialization: " (verified
    # against installed sqlite-vec 0.1.9 / sqlite_vec.__init__.load, which
    # calls conn.load_extension() unconditionally with no idempotency
    # guard of its own). The probe makes load_vec safe to call any number
    # of times, at any point in an open transaction.
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        pass
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


def _serialize(vector: list[float]) -> bytes:
    import sqlite_vec
    return sqlite_vec.serialize_float32(vector)


def _fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+", query)
    return " ".join(f'"{t}"' for t in terms)


def _ensure_markdown_column(conn: sqlite3.Connection, docs: Path) -> None:
    """Phase 2 databases predate the markdown column. Add it in place and
    backfill from disk where the source file still exists; files that
    vanished since the last index run keep '' (their next index pass or
    removal cleans up). CREATE TABLE IF NOT EXISTS cannot add columns to
    an existing table, so this runs on every open (one cheap PRAGMA)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(docs)")}
    if "markdown" in cols:
        return
    conn.execute("ALTER TABLE docs ADD COLUMN markdown TEXT NOT NULL DEFAULT ''")
    for (path,) in conn.execute("SELECT path FROM docs").fetchall():
        source = docs / path
        if source.is_file():
            _, body = parse_front_matter(source.read_text(encoding="utf-8"))
            conn.execute("UPDATE docs SET markdown = ? WHERE path = ?",
                         (body, path))
    conn.commit()


class Store:
    """All SQLite access for one collection's `.docs-wiki/index.db`."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @classmethod
    def open(cls, collection_path: Path, db_dir: Path | None = None) -> "Store":
        conn = sqlite3.connect(state_dir(collection_path, db_dir) / "index.db")
        conn.row_factory = sqlite3.Row
        # Load sqlite-vec on every connection (best-effort; see Library
        # deviations in task-2-report.md): the chunk_vectors virtual table
        # (created lazily once embeddings are enabled) uses the vec0
        # module, which SQLite only recognizes on a connection where the
        # extension has been loaded. Without this, callers that open a
        # fresh connection (e.g. webapp routes, tests) would get "no such
        # module: vec0" the moment they touch chunk_vectors, even though
        # the table exists on disk. Harmless no-op when sqlite-vec isn't
        # installed.
        load_vec(conn)
        conn.executescript(_SCHEMA_SQL)
        # rag deviation from a byte-for-byte docs-wiki copy: index_meta was
        # created lazily (only once get_meta/set_meta first ran, i.e. only
        # once an embed client was configured). Creating it here too --
        # still idempotent, still no schema change to what docs-wiki
        # writes -- makes it always present, since the plan's
        # compatibility guarantee lists index_meta as part of the format
        # regardless of whether embedding has happened yet (see
        # test_index_format_compatible_with_docs_wiki).
        _ensure_markdown_column(conn, collection_path)
        return cls(conn)

    @staticmethod
    def exists(collection_path: Path, db_dir: Path | None = None) -> bool:
        base = Path(db_dir) if db_dir is not None else collection_path / ".docs-wiki"
        return (base / "index.db").is_file()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def commit(self) -> None:
        """Commit the current transaction."""
        self.conn.commit()

    # ---- doc reads (typed) -------------------------------------------

    @staticmethod
    def _to_doc_row(row: sqlite3.Row) -> DocRow:
        return DocRow(path=row["path"], title=row["title"],
                      quadrant=row["quadrant"],
                      front_matter=json.loads(row["front_matter"]),
                      markdown=row["markdown"])

    def list_docs_catalog(self) -> list[CatalogRow]:
        """Same rows/order as `list_docs`, minus `markdown` -- the
        projection `api._documents_catalog`/`mcp_facade.list_docs` want:
        a listing page never renders full doc bodies, so there is no
        reason to pull them off disk (and, for FTS5/SQLite, out of the
        row cache) just to discard them per row. See `CatalogRow`."""
        rows = self.conn.execute(
            "SELECT path, title, quadrant, front_matter FROM docs "
            "ORDER BY quadrant_order, path")
        return [CatalogRow(path=row["path"], title=row["title"],
                           quadrant=row["quadrant"],
                           front_matter=json.loads(row["front_matter"]))
                for row in rows]

    def _get_doc_row(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM docs WHERE path = ?", (path,)).fetchone()

    def get_doc(self, path: str) -> DocRow | None:
        row = self._get_doc_row(path)
        return self._to_doc_row(row) if row is not None else None

    def doc_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    def chunk_count(self) -> int:
        """Moved verbatim from `sync_vectors`'s sqlite-vec-unavailable
        fallback (`chunk_total = conn.execute("SELECT count(*) FROM
        chunks")...`)."""
        return self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0]

    def get_markdown(self, path: str) -> str | None:
        row = self.conn.execute(
            "SELECT markdown FROM docs WHERE path = ?", (path,)).fetchone()
        return row["markdown"] if row is not None else None

    def content_hashes(self) -> dict[str, str]:
        """path -> content_hash for every indexed doc -- used by the
        ingest pipeline's change-detection pass (moved verbatim from
        `index_collection`'s `existing = {...}` comprehension)."""
        return {row["path"]: row["content_hash"] for row in
                self.conn.execute("SELECT path, content_hash FROM docs")}

    # ---- chunk reads ----------------------------------------------------

    def chunk_heading_paths(self, path: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT heading_path FROM chunks WHERE path = ? "
            "GROUP BY heading_path ORDER BY MIN(rowid)", (path,))
        return [row["heading_path"] for row in rows]

    # ---- bm25 search ------------------------------------------------

    def _search_bm25_rows(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        fts = _fts_query(query)
        if not fts:
            return []
        return list(self.conn.execute(
            "SELECT c.path, d.title, d.quadrant, c.heading_path, "
            "snippet(chunks, 2, '<mark>', '</mark>', '…', 12) AS snippet "
            "FROM chunks c JOIN docs d ON d.path = c.path "
            "WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
            (fts, limit)))

    def search_bm25(self, query: str, limit: int = 20) -> list[HitRow]:
        return [HitRow(path=row["path"], title=row["title"],
                       heading_path=row["heading_path"],
                       snippet=row["snippet"])
                for row in self._search_bm25_rows(query, limit)]

    # ---- vectors ----------------------------------------------------

    def vector_table_exists(self) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'chunk_vectors'"
        ).fetchone() is not None

    def vector_rowids(self, embedding: list[float],
                      limit: int = 20) -> list[int]:
        if not load_vec(self.conn) or not self.vector_table_exists():
            return []
        rows = self.conn.execute(
            "SELECT rowid FROM chunk_vectors WHERE embedding MATCH ? "
            "AND k = ? ORDER BY distance", (_serialize(embedding), limit))
        return [row[0] for row in rows]

    def bm25_rowids(self, query: str, limit: int = 20) -> list[int]:
        """Chunk rowids matching `query` via FTS5, best-`bm25()`-rank
        first -- moved verbatim from `retrieval._bm25_rowids`. Distinct
        from `_search_bm25_rows`: this returns bare rowids (for RRF fusion
        against `vector_rowids` in `retrieval.retrieve`), not the
        joined+snippeted rows the BM25-only fallback in `api.py` renders
        directly."""
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self.conn.execute(
            "SELECT rowid FROM chunks WHERE chunks MATCH ? "
            "ORDER BY bm25(chunks) LIMIT ?", (fts, limit))
        return [row[0] for row in rows]

    def hydrate_chunk_rowids(self, rowids: list[int]) -> list[dict]:
        """rowid/path/heading_path/text/title dicts for exactly these
        `rowids`, in the SAME order as `rowids` (nearest-first for
        `vector_search`, fused-rank order for `retrieval.retrieve`) --
        the JOIN moved verbatim from this method's own pre-refactor body
        (the single-module indexing pipeline's old `vector_search`), now
        shared by both callers instead of duplicated (retrieval.py used
        to run this exact query itself over a raw connection)."""
        if not rowids:
            return []
        placeholders = ",".join("?" for _ in rowids)
        rows = {row["rowid"]: dict(row) for row in self.conn.execute(
            f"SELECT c.rowid AS rowid, c.path AS path, c.heading_path AS "
            f"heading_path, c.text AS text, d.title AS title "
            f"FROM chunks c JOIN docs d ON d.path = c.path "
            f"WHERE c.rowid IN ({placeholders})", rowids)}
        return [rows[rowid] for rowid in rowids if rowid in rows]

    def pending_chunks(self) -> list[sqlite3.Row]:
        """Chunks (rowid, heading_path, text) with no stored vector, after
        dropping any stale chunk_vectors rows whose chunk no longer exists
        (moved verbatim from `sync_vectors`'s missing-chunk computation).
        `heading_path` rides along so `sync_vectors` can build its
        heading-prefixed embedding INPUT without a second query -- the FTS
        `text` column itself stays exactly what's stored, untouched by
        that prefix (see `rag.ingest.sync_vectors`)."""
        chunk_rows = list(self.conn.execute(
            "SELECT rowid, heading_path, text FROM chunks"))
        chunk_ids = {row["rowid"] for row in chunk_rows}

        existing: set[int] = set()
        if self.vector_table_exists():
            existing = {row[0] for row in
                       self.conn.execute("SELECT rowid FROM chunk_vectors")}
            for stale in existing - chunk_ids:
                self.conn.execute(
                    "DELETE FROM chunk_vectors WHERE rowid = ?", (stale,))
            existing &= chunk_ids

        return [row for row in chunk_rows if row["rowid"] not in existing]

    def store_vectors(self, rows: list[tuple[int, list[float]]],
                      dim: int) -> None:
        """`rows`: (rowid, vector) pairs already embedded. Creates
        chunk_vectors at `dim` if it doesn't exist yet (guarded Python-side
        -- no SQL-level IF NOT EXISTS, same as the original), then inserts
        each row."""
        if not self.vector_table_exists():
            self.conn.execute(
                f"CREATE VIRTUAL TABLE chunk_vectors USING vec0("
                f"embedding float[{dim}])")
        for rowid, vector in rows:
            self.conn.execute(
                "INSERT INTO chunk_vectors(rowid, embedding) VALUES (?, ?)",
                (rowid, _serialize(vector)))

    def clear_vectors(self) -> None:
        """Drop chunk_vectors (if present) and the embed_model/embed_dim
        meta keys -- the "start over" step used both on a model change and
        a dimension-drift detection (moved verbatim from the two identical
        blocks in `sync_vectors`)."""
        if self.vector_table_exists():
            self.conn.execute("DROP TABLE chunk_vectors")
        self.conn.execute("DELETE FROM index_meta WHERE key IN "
                          "('embed_model', 'embed_dim')")

    def _drop_chunk_vectors_for_path(self, path: str) -> None:
        """Delete any chunk_vectors rows belonging to `path`'s *current*
        chunks, before those chunks are deleted/replaced by the caller.

        Root fix for stale-vector-via-rowid-reuse: FTS5 (like any SQLite
        rowid table) assigns a freed rowid's slot to the next inserted row
        once the table -- or, for a single-doc corpus, even just the range
        around that rowid -- has no surviving row holding it; verified
        empirically that a single-doc corpus's sole chunk gets the *same*
        rowid back after a DELETE+INSERT edit. If we deleted `chunks`
        first and left the old chunk_vectors row sitting under that
        rowid, the reused rowid would silently inherit the previous
        chunk's (now-wrong) embedding and a later sync would never
        notice, since as far as its rowid-set comparison is concerned
        nothing went missing. Deleting the vector rows *before* the
        chunks rows removes that identity entirely, so a reused rowid
        always starts out with no vector -- the next sync sees it as
        missing and (re-)embeds it from the new text.

        Runs unconditionally (independent of whether embedding is
        enabled), so a no-embed reindex still leaves no stale vectors
        behind for a later embed-enabled run to find.
        """
        if not self.vector_table_exists():
            return
        rowids = [row[0] for row in self.conn.execute(
            "SELECT rowid FROM chunks WHERE path = ?", (path,))]
        for rowid in rowids:
            self.conn.execute(
                "DELETE FROM chunk_vectors WHERE rowid = ?", (rowid,))

    # ---- meta -------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        self.conn.execute("CREATE TABLE IF NOT EXISTS index_meta("
                          "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = self.conn.execute(
            "SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("REPLACE INTO index_meta VALUES (?, ?)",
                          (key, value))

    # ---- writes -------------------------------------------------------

    def upsert_doc(self, path: str, title: str, quadrant: str,
                   front_matter: dict, content_hash: str, html: str,
                   markdown: str) -> None:
        self.conn.execute(
            "REPLACE INTO docs (path, title, quadrant, front_matter, "
            "content_hash, html, quadrant_order, markdown) "
            "VALUES (?,?,?,?,?,?,?,?)", (
                path, title, quadrant, json.dumps(front_matter, default=str),
                content_hash, html, _QUADRANT_ORDER[quadrant], markdown,
            ))

    def replace_chunks(self, path: str, sections: list) -> list[int]:
        """Drop `path`'s chunk_vectors + chunks, insert the given
        (already-chunked) sections, return their new rowids. `sections`:
        objects with `.heading_path`/`.text` (rag.structure.Section)."""
        self._drop_chunk_vectors_for_path(path)
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        rowids = []
        for section in sections:
            cur = self.conn.execute(
                "INSERT INTO chunks VALUES (?,?,?)",
                (path, section.heading_path, section.text))
            rowids.append(cur.lastrowid)
        return rowids

    def remove_docs_not_in(self, paths) -> int:
        """Delete every indexed doc (and its chunks/vectors) whose path is
        not in `paths`. Returns the number removed."""
        existing = {row["path"] for row in
                   self.conn.execute("SELECT path FROM docs")}
        removed = 0
        for path in existing - set(paths):
            self._drop_chunk_vectors_for_path(path)
            self.conn.execute("DELETE FROM docs WHERE path = ?", (path,))
            self.conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
            removed += 1
        return removed
