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
    created = collection(env, "absent", "docs/absent")
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
    first = client.post(prefix + "/collections", json={"name": "c", "source_path": "docs/c"}).json()["id"]
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


def test_rag_data_collection_folder_is_created(env):
    client, prefix, roots = env[1], env[3], env[6]
    created = collection(env, "jira", "rag-data/jira")
    folder = roots["rag-data"] / "jira"
    assert folder.is_dir() and status(env, created)["status"] == "needs_refresh"
    folder.rmdir()  # removed on the host: the first upload creates it again
    write(env, "DEMO-1.md", "# DEMO-1", name="jira")
    assert (folder / "DEMO-1.md").read_text() == "# DEMO-1"
    collection(env, "repo", "code/repo")
    assert not (roots["code"] / "repo").exists()


def test_file_api_serves_only_indexable_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    repo = roots["code"] / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / ".env").write_text("SECRET=1")
    (repo / ".env.local").write_text("SECRET=2")
    (repo / ".git" / "config").write_text("[remote]")
    (repo / "node_modules" / "x" / "README.md").write_text("# x")
    (repo / "docs" / ".draft.md").write_text("# draft")
    (repo / "app.py").write_text("print(1)")
    (repo / "docs" / "guide.md").write_text("# Guide")
    (repo / "docs" / "old.md").write_text("# Old")
    collection(env, "repo", "code/repo", exclude=["docs/old.md"])
    for path in (".env", ".env.local", ".git/config", "node_modules/x/README.md", "docs/.draft.md", "app.py", "docs/old.md"):
        response = client.get(prefix + "/file", params={"collection": "repo", "path": path})
        assert response.status_code == 404, path
        assert response.json()["error"]["message"] == "Document not found" and "SECRET" not in response.text
    assert client.get(prefix + "/file", params={"collection": "repo", "path": "docs/guide.md"}).json()["content"] == "# Guide"


def test_file_writes_refuse_hidden_and_skipped_paths(env):
    client, prefix = env[1], env[3]
    collection(env)
    for path in (".env", "a/.env.local", ".git/config", "node_modules/a.md", ".hidden/a.md"):
        result = client.put(prefix + "/file", json={"collection": "docs", "path": path, "content": "x", "expected_hash": None})
        assert result.status_code == 400, path
        assert client.delete(prefix + "/file", params={"collection": "docs", "path": path, "expected_hash": "x"}).status_code == 400


def test_list_without_status_skips_the_walk(env, monkeypatch):
    workspace, client, prefix, roots = env[0], env[1], env[3], env[6]
    created = collection(env)
    write(env)
    sync(env, created)
    collection(env, "absent", "docs/absent")

    def no_walk(*args, **kwargs):
        raise AssertionError("status=false must not walk folders")
    monkeypatch.setattr(workspace.areas, "walk", no_walk)
    rows = {r["name"]: r for r in client.get(prefix + "/collections", params={"status": "false"}).json()["collections"]}
    assert rows["docs"]["folder_present"] is True and rows["docs"]["indexed_files"] == 1 and rows["docs"]["chunks"] >= 1
    assert rows["docs"]["embedded_chunks"] == 0 and rows["docs"]["writable"] is True
    assert rows["absent"]["folder_present"] is False and "status" not in rows["docs"]


def test_collection_with_unsynced_revision_needs_refresh(env):
    workspace, roots = env[0], env[6]
    created = collection(env)
    write(env)
    sync(env, created)
    assert status(env, created)["status"] == "up_to_date"
    with workspace.connect() as conn:  # what migration 0063 leaves for migrated folders
        conn.execute("UPDATE ka.rag_collections SET synced_revision=NULL WHERE id=%s", (created,))
    row = status(env, created)
    assert row["status"] == "needs_refresh" and row["last_synced_at"] is not None


def seed_jobs(env):
    """Twelve finished jobs across two collections, newest last, plus one queued."""
    workspace, project = env[0], env[2]
    ids = {name: collection(env, name, f"rag-data/{name}") for name in ("jira", "docs")}
    with workspace.connect() as conn:
        for i in range(12):
            name = "jira" if i % 2 else "docs"
            failed = i % 4 == 3
            conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,error,created_at,started_at,finished_at)
                VALUES (%s,'rag_collection_sync',%s,'{}',%s,%s,now() - make_interval(mins => %s),now() - make_interval(mins => %s),now() - make_interval(mins => %s))""",
                (project, ids[name], "failed" if failed else "succeeded", "Embedding model rejected the token" if failed else None,
                 100 - i, 100 - i, 99 - i))
    return ids


def test_jobs_are_paged_newest_first_with_total(env):
    client, prefix = env[1], env[3]
    seed_jobs(env)
    page = client.get(prefix + "/jobs", params={"limit": 5}).json()
    assert page["total"] == 12 and len(page["jobs"]) == 5
    second = client.get(prefix + "/jobs", params={"limit": 5, "offset": 10}).json()
    assert second["total"] == 12 and len(second["jobs"]) == 2
    created = [j["created_at"] for j in page["jobs"]]
    assert created == sorted(created, reverse=True)
    assert client.get(prefix + "/jobs").json()["total"] == 12  # default page still reports the total
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "exploded"}):
        assert client.get(prefix + "/jobs", params=bad).status_code == 400, bad


def test_jobs_filter_by_status_collection_and_text(env):
    client, prefix = env[1], env[3]
    seed_jobs(env)
    failed = client.get(prefix + "/jobs", params={"status": "failed"}).json()
    assert failed["total"] == 3 and {j["status"] for j in failed["jobs"]} == {"failed"}
    jira = client.get(prefix + "/jobs", params={"collection": "jira"}).json()
    assert jira["total"] == 6 and {j["collection"] for j in jira["jobs"]} == {"jira"}
    both = client.get(prefix + "/jobs", params={"collection": "jira", "status": "failed"}).json()
    assert both["total"] == 3
    text = client.get(prefix + "/jobs", params={"q": "token"}).json()
    assert text["total"] == 3
    assert client.get(prefix + "/jobs", params={"q": "DOC"}).json()["total"] == 6  # case-insensitive collection match
    assert client.get(prefix + "/jobs", params={"q": "100%_"}).json()["total"] == 0  # LIKE wildcards are literal
