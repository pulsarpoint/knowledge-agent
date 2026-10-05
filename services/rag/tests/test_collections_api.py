"""Collections API: validation, freshness, sync queue, read-only areas and deletion."""
import os
import time

from test_workspace import collection, sync, write

# env and workspace_postgres come from conftest.py


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def test_create_list_and_validate(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    rows = client.get(prefix + "/collections").json()["collections"]
    assert [r["name"] for r in rows] == ["docs"]
    row = rows[0]
    assert row["host_path"] == "/srv/rag-data/docs" and row["writable"] is True
    assert row["status"] == "needs_refresh" and row["needs_refresh"] is True and row["last_synced_at"] is None
    assert client.post(prefix + "/collections", json={"name": "docs", "source_path": "rag-data/docs"}).status_code == 409
    for bad in ({"name": "Docs", "source_path": "rag-data/docs"}, {"name": "x", "source_path": "../etc"},
                {"name": "x", "source_path": "rag-data/x", "chunk_size": 300, "chunk_overlap": 300},
                {"name": "x", "source_path": "rag-data/x", "search_mode": "embedding"}):
        assert client.post(prefix + "/collections", json=bad).status_code == 400, bad
    assert status(env, created)["include"] == ["**/*.md", "**/*.markdown", "**/*.txt"]


def test_missing_folder_is_reported(env):
    created = collection(env, "absent", "rag-data/absent")
    row = status(env, created)
    assert row["status"] == "folder_missing" and row["folder_present"] is False
    result = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert result.status_code == 409 and "missing" in result.json()["error"]["message"]


def test_sync_marks_up_to_date_and_file_edit_marks_stale(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert row["status"] == "up_to_date" and row["indexed_files"] == 1 and row["chunks"] >= 1
    time.sleep(0.01)
    write(env, content="# Payments\n\nRetries stop after five attempts.")
    assert status(env, created)["status"] == "needs_refresh"


def test_deleting_a_file_marks_collection_stale(env):
    roots = env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    old = time.time() - 3600
    for path in (folder, folder / "a.md", folder / "b.md"):
        os.utime(path, (old, old))
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET last_synced_at=now() - interval '30 minutes' WHERE id=%s", (created,))
    assert status(env, created)["status"] == "up_to_date"
    (folder / "b.md").unlink()
    assert status(env, created)["status"] == "needs_refresh"


def test_settings_change_marks_collection_stale(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    updated = client.put(prefix + f"/collections/{created}", json={
        "name": "docs", "source_path": "rag-data/docs", "include": ["**/*.txt"], "revision": row["revision"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "needs_refresh"
    stale = client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs", "revision": row["revision"]})
    assert stale.status_code == 409


def test_read_only_area_rejects_writes_and_reads_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    repo = roots["code"] / "payments"
    repo.mkdir()
    (repo / "app.py").write_text("RETRIES = 5\n")
    collection(env, "payments-code", "code/payments", include=["**/*.py"])
    assert client.get(prefix + "/file", params={"collection": "payments-code", "path": "app.py"}).json()["content"] == "RETRIES = 5\n"
    result = client.put(prefix + "/file", json={"collection": "payments-code", "path": "x.py", "content": "x", "expected_hash": None})
    assert result.status_code == 400 and "read-only" in result.json()["error"]["message"]


def test_one_active_sync_per_collection_and_sync_stale(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
        collection(env, name, f"rag-data/{name}")
    first = client.post(prefix + "/collections", json={"name": "c", "source_path": "rag-data/c"}).json()["id"]
    ids = {r["name"]: r["id"] for r in client.get(prefix + "/collections").json()["collections"]}
    assert client.post(prefix + f"/collections/{ids['a']}/sync", json={}).status_code == 202
    assert client.post(prefix + f"/collections/{ids['a']}/sync", json={}).status_code == 409
    assert client.post(prefix + f"/collections/{ids['b']}/sync", json={}).status_code == 202
    queued = client.post(prefix + "/collections/sync-stale").json()["queued"]
    assert queued == []  # a and b are already queued; c's folder is missing, so it is not queued
    assert status(env, first)["status"] == "folder_missing"


def test_delete_keeps_files_and_removes_index(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"]}).status_code == 400
    result = client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True})
    assert result.status_code == 200 and result.json()["deleted_files"] == 1
    assert (roots["rag-data"] / "docs" / "readme.md").exists()
    assert client.get(prefix + "/collections").json()["collections"] == []
    assert collection(env) != created  # the name can be reused


def test_stale_or_cross_project_deletion_cannot_remove_data(env):
    workspace, client, prefix, roots = env[0], env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"] + 1, "confirmed": True}).status_code == 409
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True},
                         headers={"x-rag-key": "wrong"}).status_code == 403
    with workspace.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()["id"]
    assert client.delete(f"/backoffice/projects/{other}/collections/{created}",
                         params={"revision": row["revision"], "confirmed": True}).status_code == 404
    assert status(env, created)["indexed_files"] == 1


def test_files_listing_shows_stale_and_removed_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A\n\nalpha")
    write(env, "b.md", "# B\n\nbeta")
    sync(env, created)
    (folder / "b.md").unlink()
    write(env, "c.md", "# C")
    listing = client.get(prefix + f"/collections/{created}/files").json()
    files = {f["path"]: f for f in listing["files"]}
    assert set(files) == {"a.md", "b.md", "c.md"} and listing["truncated"] is False
    assert files["a.md"]["stale"] is False and files["a.md"]["chunks"] >= 1
    assert files["b.md"]["stale"] is True and files["b.md"]["size"] is None
    assert files["c.md"]["stale"] is True and files["c.md"]["indexed_at"] is None
    texts = client.get(prefix + f"/collections/{created}/chunks", params={"path": "a.md"}).json()["chunks"]
    assert texts and texts[0]["position"] == 0 and "alpha" in texts[0]["text"] and texts[0]["embedded"] is False


def test_jobs_list_and_cancel(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    queued = client.post(prefix + f"/collections/{created}/sync", json={"force": True}).json()
    jobs = client.get(prefix + "/jobs").json()["jobs"]
    assert [(j["id"], j["collection"], j["collection_id"], j["status"], j["input"]) for j in jobs] == [
        (queued["id"], "docs", created, "queued", {"force": True})]
    assert status(env, created)["status"] == "syncing"
    assert client.post(prefix + f"/jobs/{queued['id']}/cancel").status_code == 200
    assert client.post(prefix + f"/jobs/{queued['id']}/cancel").status_code == 409
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202
