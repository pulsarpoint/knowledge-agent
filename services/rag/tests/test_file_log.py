"""The file log: unchanged files are not opened, one bad file never fails a sync, every decision is visible."""
import os
import time
from uuid import uuid4

import pytest
from test_workspace import chunks, collection, configure, search, sync, update, write

import rag.collections
from rag import workspace_worker

# env and workspace_postgres come from conftest.py


def log(env, collection_id) -> dict[str, dict]:
    with env[0].connect() as conn:
        return {r["path"]: r for r in conn.execute("SELECT * FROM ka.rag_file_log WHERE collection_id=%s",
                                                   (collection_id,)).fetchall()}


def spy_reads(env, monkeypatch) -> list[str]:
    """Record every file the worker opens, as a full DATA_ROOT path."""
    reads, original = [], env[0].areas.read

    def read(area, path, limit):
        reads.append(f"{area}/{path}")
        return original(area, path, limit)
    monkeypatch.setattr(env[0].areas, "read", read)
    return reads


def set_limit(env, max_file_bytes):
    client, prefix = env[1], env[3]
    config = client.get(prefix + "/settings").json()
    saved = client.put(prefix + "/settings", json={"revision": config["revision"], "settings": {"max_file_bytes": max_file_bytes},
                                                   "embedding_model_id": config["embedding_model_id"]})
    assert saved.status_code == 200, saved.text


def test_unchanged_files_are_not_opened(env, monkeypatch):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env)
    write(env, "other.md", "# Other\n\nSecond file.")
    first, _ = sync(env, created)
    assert first["hashed_files"] == 2 and first["parsed_files"] == 2
    entries = log(env, created)
    readme = entries["rag-data/docs/readme.md"]
    assert readme["status"] == "indexed" and readme["error"] is None and readme["indexed_at"] is not None
    assert readme["size"] == (folder / "readme.md").stat().st_size and readme["mtime"] == (folder / "readme.md").stat().st_mtime
    assert len(readme["fingerprint"]) == 64 and len(readme["content_hash"]) == 64
    reads = spy_reads(env, monkeypatch)
    second, row = sync(env, created)
    assert reads == []
    assert (second["unopened_files"], second["skipped_files"], second["hashed_files"]) == (2, 2, 0)
    assert row["status"] == "up_to_date" and row["indexed_files"] == 2
    assert log(env, created)["rag-data/docs/readme.md"]["last_seen_at"] > readme["last_seen_at"]


def test_touched_file_with_the_same_content_is_not_reparsed(env, monkeypatch):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    later = time.time() + 5
    os.utime(folder / "readme.md", (later, later))
    monkeypatch.setattr(workspace_worker, "parse_source", lambda *a: pytest.fail("The same content must not be parsed"))
    stats, _ = sync(env, created)
    assert (stats["hashed_files"], stats["parsed_files"], stats["skipped_files"], stats["unopened_files"]) == (1, 0, 1, 0)
    assert log(env, created)["rag-data/docs/readme.md"]["mtime"] == (folder / "readme.md").stat().st_mtime


def test_changed_content_is_reindexed_even_with_an_older_mtime(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, content="# Payments\n\nRetries use exponential backoff.")
    sync(env, created)
    readme = folder / "readme.md"
    old = readme.stat().st_mtime - 86400
    readme.write_text("# Payments\n\nRetries use a fixed 30s backoff.")
    os.utime(readme, (old, old))  # as rsync -a or cp -p leave it
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["indexed_files"] == 1
    assert "fixed 30s" in chunks(env, created)[0]


def test_settings_change_reprocesses_unchanged_files(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    update(env, created, chunk_size=1000)
    stats, _ = sync(env, created)
    assert stats["unopened_files"] == 0 and stats["parsed_files"] == 1
    update(env, created, include=["**/*.md", "**/*.txt", "**/*.json"])
    reads = spy_reads(env, monkeypatch)
    stats, _ = sync(env, created)  # patterns only select files; a file still selected is not opened again
    assert reads == []
    assert (stats["unopened_files"], stats["hashed_files"], stats["parsed_files"]) == (1, 0, 0)


def test_raising_the_size_limit_reprocesses_skipped_files(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    (folder / "big.md").write_text("# Big\n\n" + "x" * 600)
    set_limit(env, 500)
    created = collection(env)
    stats, row = sync(env, created)
    assert stats["skipped_large"] == 1 and row["indexed_files"] == 0
    assert log(env, created)["rag-data/docs/big.md"]["status"] == "skipped_too_large"
    set_limit(env, 5000)  # the file itself is untouched
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["indexed_files"] == 1
    assert log(env, created)["rag-data/docs/big.md"]["status"] == "indexed"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files without permission")
def test_binary_large_and_unreadable_files_are_logged(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    (folder / "ok.md").write_text("# OK\n\nRetries.")
    (folder / "bin.md").write_bytes(b"\x00\x01binary")
    (folder / "big.md").write_text("x" * 600)
    locked = folder / "locked.md"
    locked.write_text("# Locked\n\nPermissions.")
    locked.chmod(0)
    set_limit(env, 500)
    created = collection(env)
    try:
        stats, row = sync(env, created)
        assert row["job"]["status"] == "succeeded" and row["status"] == "up_to_date" and row["indexed_files"] == 1
        entries = log(env, created)
        assert {path.rsplit("/", 1)[1]: entry["status"] for path, entry in entries.items()} == {
            "ok.md": "indexed", "bin.md": "skipped_binary", "big.md": "skipped_too_large", "locked.md": "skipped_unreadable"}
        assert entries["rag-data/docs/locked.md"]["error"].startswith("Cannot read this file")
        assert (stats["skipped_binary"], stats["skipped_large"], stats["skipped_unreadable"]) == (1, 1, 1)
        locked.chmod(0o644)  # fixing permissions does not change the mtime
        stats, row = sync(env, created)
        assert log(env, created)["rag-data/docs/locked.md"]["status"] == "indexed" and row["indexed_files"] == 2
    finally:
        locked.chmod(0o644)


def test_parse_failure_is_logged_and_the_sync_succeeds(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "good.md", "# Good\n\nretries are fine")
    write(env, "bad.md", "# Bad\n\nretries crash the parser")
    parse = workspace_worker.parse_source

    def broken(path, text, settings):
        if path.endswith("bad.md"):
            raise ValueError("unbalanced table")
        return parse(path, text, settings)
    monkeypatch.setattr(workspace_worker, "parse_source", broken)
    stats, row = sync(env, created)
    assert (stats["failed_files"], stats["parsed_files"]) == (1, 1)
    assert row["job"]["status"] == "succeeded" and row["status"] == "up_to_date" and row["indexed_files"] == 1
    bad = log(env, created)["rag-data/docs/bad.md"]
    assert (bad["status"], bad["error"], bad["indexed_at"]) == ("failed", "Could not parse this file: unbalanced table", None)
    assert {hit["path"] for hit in search(env, "retries", ["docs"]).json()["results"]} == {"rag-data/docs/good.md"}
    monkeypatch.setattr(workspace_worker, "parse_source", parse)
    stats, _ = sync(env, created)  # same content and settings: the failure is not retried
    assert stats["failed_files"] == 0 and log(env, created)["rag-data/docs/bad.md"]["status"] == "failed"
    stats, row = sync(env, created, force=True)  # Rebuild retries it
    assert log(env, created)["rag-data/docs/bad.md"]["status"] == "indexed" and row["indexed_files"] == 2


def test_a_deleted_file_is_marked_removed_and_returns_when_restored(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    (folder / "b.md").unlink()
    stats, row = sync(env, created)
    gone = log(env, created)["rag-data/docs/b.md"]
    assert gone["status"] == "removed" and gone["removed_at"] is not None and gone["indexed_at"] is None
    assert stats["removed_entries"] == 1 and row["indexed_files"] == 1
    write(env, "b.md", "# B again")
    sync(env, created)
    back = log(env, created)["rag-data/docs/b.md"]
    assert back["status"] == "indexed" and back["removed_at"] is None


def test_rebuild_rereads_every_file(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    reads = spy_reads(env, monkeypatch)
    stats, _ = sync(env, created, force=True)
    assert sorted(reads) == ["rag-data/docs/a.md", "rag-data/docs/b.md"]
    assert stats["parsed_files"] == 2 and stats["unopened_files"] == 0


def test_a_failed_sync_leaves_the_log_unchanged(env):
    state = env[4]
    (env[6]["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    write(env)
    sync(env, created)
    before = log(env, created)["rag-data/docs/readme.md"]
    write(env, content="# Payments\n\nA retry policy that cannot be embedded right now.")
    state["fail"] = True
    stats, row = sync(env, created)
    assert stats is None and row["job"]["status"] == "failed"
    after = log(env, created)["rag-data/docs/readme.md"]
    assert (after["content_hash"], after["mtime"], after["last_seen_at"]) == (before["content_hash"], before["mtime"], before["last_seen_at"])


def test_files_backfilled_without_size_and_mtime_are_read_once_and_not_parsed(env, monkeypatch):
    """Migration 0066 backfills indexed files with no size, mtime or fingerprint."""
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_file_log SET size=NULL,mtime=NULL,fingerprint=NULL WHERE collection_id=%s", (created,))
    monkeypatch.setattr(workspace_worker, "parse_source", lambda *a: pytest.fail("Backfilled files must not be parsed again"))
    stats, row = sync(env, created)
    assert (stats["hashed_files"], stats["parsed_files"]) == (1, 0) and row["status"] == "up_to_date"
    assert log(env, created)["rag-data/docs/readme.md"]["size"] is not None
    assert sync(env, created)[0]["unopened_files"] == 1


def test_removing_a_folder_or_the_collection_deletes_its_log_rows(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
        (roots["rag-data"] / name / "x.md").write_text(f"# {name}\n\nretries {name}")
    created = collection(env, "both", "rag-data/a", "rag-data/b")
    _, row = sync(env, created)
    path_id = next(p["id"] for p in row["paths"] if p["source_path"] == "rag-data/b")
    assert client.delete(prefix + f"/collections/{created}/paths/{path_id}").status_code == 200
    assert set(log(env, created)) == {"rag-data/a/x.md"}
    revision = client.get(prefix + f"/collections/{created}").json()["revision"]
    assert client.delete(prefix + f"/collections/{created}", params={"revision": revision, "confirmed": True}).status_code == 200
    assert log(env, created) == {}
    assert (roots["rag-data"] / "b" / "x.md").is_file()


def test_backfilled_rows_with_embeddings_reuse_the_index_without_embedding_calls(env):
    """After the 0066 upgrade, unchanged indexed files keep their chunks and vectors: no parse, no embedding call."""
    state = env[4]
    (env[6]["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    write(env)
    first, _ = sync(env, created)
    assert first["embedded_chunks"] > 0
    before = chunks(env, created)
    with env[0].connect() as conn:  # the shape migration 0066 backfills: indexed, content hash only
        conn.execute("UPDATE ka.rag_file_log SET size=NULL,mtime=NULL,fingerprint=NULL WHERE collection_id=%s", (created,))
    state["calls"].clear()
    stats, row = sync(env, created)
    assert state["calls"] == []
    assert (stats["hashed_files"], stats["parsed_files"], stats["embedded_chunks"], stats["reused_vectors"]) == (1, 0, 0, 0)
    assert row["status"] == "up_to_date" and row["indexed_files"] == 1 and chunks(env, created) == before
    entry = log(env, created)["rag-data/docs/readme.md"]
    assert entry["status"] == "indexed" and len(entry["fingerprint"]) == 64 and entry["size"] is not None


def test_the_sync_text_limit_counts_every_walked_file(env, monkeypatch):
    """Unopened files count toward the limit, so incremental syncs and Rebuild agree."""
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "a.md", "# A\n\n" + "a" * 100)
    write(env, "b.md", "# B\n\n" + "b" * 100)
    monkeypatch.setattr(rag.collections, "MAX_BYTES", 1000)
    stats, row = sync(env, created)
    assert stats is not None and row["indexed_files"] == 2
    stats, _ = sync(env, created)
    assert stats["unopened_files"] == 2
    stats, row = sync(env, created, force=True)
    assert stats is not None and stats["parsed_files"] == 2
    monkeypatch.setattr(rag.collections, "MAX_BYTES", 150)  # every file would be unopened
    stats, row = sync(env, created)
    assert stats is None and "200 MB" in row["job"]["error"]
    assert row["indexed_files"] == 2


def test_an_index_row_missing_behind_the_log_is_reindexed(env):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    with env[0].connect() as conn:
        conn.execute("DELETE FROM ka.rag_files WHERE collection_id=%s", (created,))
    stats, row = sync(env, created)
    assert (stats["unopened_files"], stats["parsed_files"]) == (0, 1)
    assert row["indexed_files"] == 1 and chunks(env, created)
    assert log(env, created)["rag-data/docs/readme.md"]["status"] == "indexed"


def test_index_times_are_kept_for_files_the_sync_did_not_reprocess(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    before = log(env, created)
    write(env, "b.md", "# B changed")
    sync(env, created)
    after = log(env, created)
    assert after["rag-data/docs/a.md"]["indexed_at"] == before["rag-data/docs/a.md"]["indexed_at"]
    assert after["rag-data/docs/b.md"]["indexed_at"] > before["rag-data/docs/b.md"]["indexed_at"]


def test_log_api_filters_searches_and_pages(env):
    client, prefix, roots = env[1], env[3], env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    for name in ("a", "b", "c", "d", "e"):
        write(env, f"{name}.md", f"# {name.upper()}\n\nText {name}.")
    (folder / "bin.md").write_bytes(b"\x00binary")
    sync(env, created)
    (folder / "e.md").unlink()
    sync(env, created)
    get = lambda **params: client.get(prefix + f"/collections/{created}/log", params=params)
    default = get().json()
    assert default["total"] == 5
    assert [f["path"].rsplit("/", 1)[1] for f in default["files"]] == ["a.md", "b.md", "bin.md", "c.md", "d.md"]
    first = default["files"][0]
    assert first["status"] == "indexed" and first["chunks"] >= 1 and first["indexed_at"] and first["size"] == (folder / "a.md").stat().st_size
    assert [f["path"] for f in get(status="skipped").json()["files"]] == ["rag-data/docs/bin.md"]
    removed = get(status="removed").json()["files"]
    assert [f["path"] for f in removed] == ["rag-data/docs/e.md"] and removed[0]["chunks"] == 0
    assert get(status="failed").json() == {"files": [], "total": 0}
    page = get(limit=2, offset=2).json()
    assert page["total"] == 5 and [f["path"].rsplit("/", 1)[1] for f in page["files"]] == ["bin.md", "c.md"]
    assert [f["path"] for f in get(q="B").json()["files"]] == ["rag-data/docs/b.md", "rag-data/docs/bin.md"]
    assert get(q="%").json()["total"] == 0  # LIKE wildcards match literally
    for bad in ({"status": "pending"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 201}):
        assert get(**bad).status_code == 400, bad
    assert client.get(prefix + f"/collections/{uuid4()}/log").status_code == 404
    assert client.get(prefix + f"/collections/{created}").json()["log_counts"] == {"indexed": 4, "skipped": 1, "failed": 0}
    summary = client.get(prefix + "/collections", params={"status": "false"}).json()["collections"][0]
    assert summary["log_counts"] == {"indexed": 4, "skipped": 1, "failed": 0}
